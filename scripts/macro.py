"""Makro: Fed, räntor, inflation, arbetsmarknad, oro och råvaror, plus larm när ny statistik släpps.

Källor (ingen API-nyckel behövs):
- FRED (Federal Reserve Bank of St. Louis) som CSV: räntor, KPI, PCE, arbetslöshet, jobb, kreditspread.
  FRED lägger ut ny statistik några minuter efter att myndigheten (BLS, BEA, Fed) har släppt den.
- Federal Reserve, RSS för penningpolitiska pressmeddelanden: räntebesked (FOMC statement) och protokoll (minutes).
- Yahoo Finance via yfinance: VIX, dollarindex, olja, guld och koppar.

Körs i varje datakörning (var 10:e minut under börsdagen). Under fönstret då USA släpper statistik
(kl. 12–16 UTC) hämtas statistikserierna i varje körning, annars räcker var sjätte timme. När en serie
får ett nytt värde läggs det i "releases" och build.py skickar en notis till mobilen (ntfy).

Varje serie: {"n": namn, "u": enhet, "f": d/w/m, "d": datum, "v": värden, "src": källa}.
"""
from __future__ import annotations

import io
import time
from datetime import date, datetime, timedelta, timezone

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
FED_RSS = "https://www.federalreserve.gov/feeds/press_monetary.xml"

# nyckel, FRED-id, namn, enhet, hur serien räknas (lvl = som den är, yoy = årstakt i %, mom = förändring
# mot månaden innan i %, chg = förändring mot månaden innan, k = delat med 1000), släpps som statistik
FRED = [
    ("fed", "DFEDTARU", "Styrräntan (övre gräns)", "%", "lvl", True),
    ("y10", "DGS10", "10-årsräntan (USA)", "%", "lvl", False),
    ("y2", "DGS2", "2-årsräntan (USA)", "%", "lvl", False),
    ("y3m", "DGS3MO", "3-månadersräntan (USA)", "%", "lvl", False),
    ("curve", "T10Y2Y", "10 år minus 2 år", "%-enheter", "lvl", False),
    ("be5", "T5YIE", "Marknadens inflationsförväntan 5 år", "%", "lvl", False),
    ("cpi", "CPIAUCSL", "Inflation (KPI)", "% på ett år", "yoy", True),
    ("cpim", "CPIAUCSL", "KPI per månad", "% på en månad", "mom", True),
    ("core", "CPILFESL", "Kärninflation (KPI utan mat och energi)", "% på ett år", "yoy", True),
    ("corem", "CPILFESL", "Kärn-KPI per månad", "% på en månad", "mom", True),
    ("pce", "PCEPILFE", "Kärn-PCE (Feds favoritmått)", "% på ett år", "yoy", True),
    ("ppi", "PPIFIS", "Producentpriser (PPI)", "% på ett år", "yoy", True),
    ("unemp", "UNRATE", "Arbetslöshet", "%", "lvl", True),
    ("pay", "PAYEMS", "Nya jobb per månad", "tusental", "chg", True),
    ("claims", "ICSA", "Nyanmälda arbetslösa per vecka", "tusental", "k", True),
    ("retail", "RSAFS", "Detaljhandelns försäljning", "% på en månad", "mom", True),
    ("sent", "UMCSENT", "Konsumentförtroende (Michigan)", "index", "lvl", True),
    ("hy", "BAMLH0A0HYM2", "Kreditspread, högriskobligationer", "%-enheter", "lvl", False),
]
YAHOO = [
    ("vix", "^VIX", "VIX (rädsloindex)", "punkter"),
    ("dxy", "DX-Y.NYB", "Dollarindex (DXY)", "index"),
    ("usdsek", "SEK=X", "Dollarn i kronor (USD/SEK)", "kr"),
    ("oil", "CL=F", "Olja (WTI)", "USD/fat"),
    ("gold", "GC=F", "Guld", "USD/uns"),
    ("copper", "HG=F", "Koppar", "USD/pund"),
]
# namn i notiserna
REL = {"cpi": "KPI (inflation USA)", "core": "Kärn-KPI (USA)", "pce": "Kärn-PCE (Feds inflationsmått)", "ppi": "Producentpriser (PPI)",
       "unemp": "Arbetslöshet (USA)", "pay": "Jobbrapporten (nya jobb, USA)", "claims": "Nyanmälda arbetslösa (USA)",
       "retail": "Detaljhandeln (USA)", "sent": "Konsumentförtroendet (Michigan)", "fed": "Feds styrränta"}
MON = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"]


def _get(url, timeout=30):
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (full-throttle; +https://github.com/handahama0-netizen/full-throttle)"})
            if r.ok:
                return r.text
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 * (attempt + 1))
    return None


def parse_csv(text: str):
    """FRED-CSV: första raden rubrik, sedan datum,värde. Saknade värden är '.' eller tomma."""
    out = []
    for line in io.StringIO(text).read().splitlines()[1:]:
        p = line.split(",")
        if len(p) < 2:
            continue
        try:
            out.append((p[0].strip(), float(p[1])))
        except ValueError:
            continue
    return out


def weekly(obs, years=3):
    """Dagsdata blir en punkt per vecka (veckans sista), men den allra senaste dagen behålls."""
    if not obs:
        return obs
    cut = (date.today() - timedelta(days=365 * years)).isoformat()
    obs = [o for o in obs if o[0] >= cut]
    out, last_wk = [], None
    for d, v in obs:
        wk = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if wk == last_wk:
            out[-1] = (d, v)
        else:
            out.append((d, v))
            last_wk = wk
    return out


def transform(obs, kind):
    if kind == "yoy":
        by = {d: v for d, v in obs}
        res = []
        for d, v in obs:
            y = f"{int(d[:4]) - 1}{d[4:]}"
            if y in by and by[y]:
                res.append((d, round((v / by[y] - 1) * 100, 2)))
        return res
    if kind == "mom":
        return [(obs[i][0], round((obs[i][1] / obs[i - 1][1] - 1) * 100, 2)) for i in range(1, len(obs)) if obs[i - 1][1]]
    if kind == "chg":
        return [(obs[i][0], round(obs[i][1] - obs[i - 1][1], 1)) for i in range(1, len(obs))]
    if kind == "k":
        return [(d, round(v / 1000, 1)) for d, v in obs]
    return [(d, round(v, 3)) for d, v in obs]


def series(obs, n, u, f, src):
    return {"n": n, "u": u, "f": f, "d": [d for d, _ in obs], "v": [v for _, v in obs], "src": src}


def fred_all(keys=None, log=print):
    start = (date.today() - timedelta(days=365 * 5)).isoformat()
    raw, res = {}, {}
    for key, sid, n, u, kind, _rel in FRED:
        if keys is not None and key not in keys:
            continue
        if sid not in raw:
            txt = _get(FRED_URL.format(id=sid, start=start))
            raw[sid] = parse_csv(txt) if txt and "," in txt else []
            time.sleep(0.2)
        obs = raw[sid]
        if not obs:
            log("makro: FRED saknas", sid)
            continue
        obs = transform(obs, kind)
        if not obs:
            continue
        # frekvens: dagsserier glesas ut till vecka, månadsserier behåller 4 år
        gaps = [(datetime.strptime(obs[i][0], "%Y-%m-%d") - datetime.strptime(obs[i - 1][0], "%Y-%m-%d")).days for i in range(max(1, len(obs) - 6), len(obs))]
        g = sorted(gaps)[len(gaps) // 2] if gaps else 30
        if g <= 4:
            obs, fr = weekly(obs), "d"
        elif g <= 8:
            obs, fr = obs[-160:], "w"
        else:
            obs, fr = obs[-48:], "m"
        res[key] = series(obs, n, u, fr, "FRED " + sid)
    return res


def yahoo_all(old, full, log=print):
    """Full = 3 års historik. Annars bara de senaste dagarna, som läggs till i förra körningens serie."""
    try:
        import yfinance as yf
    except Exception:  # noqa: BLE001
        return {}
    res = {}
    for key, tk, n, u in YAHOO:
        try:
            prev = old.get(key)
            quick = not full and prev and prev.get("d")
            df = yf.Ticker(tk).history(period="5d" if quick else "3y", interval="1d", auto_adjust=False).dropna(subset=["Close"])
            obs = [(i.strftime("%Y-%m-%d"), round(float(c), 3)) for i, c in zip(df.index, df["Close"])]
            if not obs:
                continue
            if quick:
                pts = list(zip(prev["d"], prev["v"]))
                last = obs[-1]
                if pts and datetime.strptime(pts[-1][0], "%Y-%m-%d").isocalendar()[:2] == datetime.strptime(last[0], "%Y-%m-%d").isocalendar()[:2]:
                    pts[-1] = last
                elif not pts or last[0] > pts[-1][0]:
                    pts.append(last)
                res[key] = series(pts[-170:], n, u, "d", "Yahoo " + tk)
            else:
                res[key] = series(weekly(obs), n, u, "d", "Yahoo " + tk)
        except Exception as e:  # noqa: BLE001
            log("makro: Yahoo saknas", tk, e)
    return res


def fed_rss(log=print):
    """Feds penningpolitiska pressmeddelanden: räntebesked, protokoll och liknande. Nyast först."""
    txt = _get(FED_RSS, timeout=20)
    if not txt:
        return []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(txt.encode("utf-8") if isinstance(txt, str) else txt)
        out = []
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            if t and link:
                out.append({"t": t, "u": link, "pub": pub})
        return out[:15]
    except Exception as e:  # noqa: BLE001
        log("makro: Fed RSS kunde inte läsas", e)
        return []


def fed_sv(title):
    t = title.lower()
    if "minutes" in t:
        return "Fed-protokollet (FOMC minutes) har släppts"
    if "fomc statement" in t or "issues fomc statement" in t:
        return "Fed har fattat räntebeslut (FOMC statement)"
    if "implementation note" in t:
        return "Fed: genomförande av räntebeslutet"
    if "discount rate" in t:
        return "Fed: protokoll om diskontoräntan"
    return "Fed: " + title


def period_label(key, d):
    y, m = int(d[:4]), int(d[5:7])
    if key == "claims":
        return f"veckan till {int(d[8:10])} {MON[m - 1][:3]}"
    if key == "fed":
        return f"från {int(d[8:10])} {MON[m - 1]}"
    return f"{MON[m - 1]} {y}"


def fmt(key, v):
    if v is None:
        return "–"
    if key in ("pay", "claims"):
        return f"{v:+,.0f} 000".replace(",", " ") if key == "pay" else f"{v:,.0f} 000".replace(",", " ")
    if key == "sent":
        return f"{v:.1f}".replace(".", ",")
    return f"{v:.1f} %".replace(".", ",") if key != "fed" else f"{v:.2f} %".replace(".", ",")


def detect(old_s, new_s, expect, now_iso, log=print):
    """Nya värden i statistikserierna sedan förra körningen. Ingen notis första gången en serie hämtas."""
    rel = []
    for key in REL:
        a, b = old_s.get(key), new_s.get(key)
        if not a or not b or not b.get("d") or not a.get("d"):
            continue
        if b["d"][-1] <= a["d"][-1] and not (key == "fed" and b["v"][-1] != a["v"][-1]):
            continue
        if key == "fed" and b["v"][-1] == a["v"][-1]:
            continue  # styrräntan: bara när den faktiskt ändras
        v, pv = b["v"][-1], (b["v"][-2] if len(b["v"]) > 1 else None)
        r = {"key": key, "name": REL[key], "period": period_label(key, b["d"][-1]), "date": b["d"][-1],
             "value": v, "prev": pv, "at": now_iso, "src": b.get("src")}
        mk = {"cpi": "cpim", "core": "corem"}.get(key)
        if mk and new_s.get(mk) and new_s[mk].get("v"):
            r["mom"] = new_s[mk]["v"][-1]
        e = expect.get(key)
        if e is not None:
            r["exp"] = e
        txt = f"{REL[key]} {r['period']}: {fmt(key, v)}"
        if key in ("cpi", "core", "pce", "ppi"):
            txt += " på ett år"
            if "mom" in r:
                txt += f", {r['mom']:+.1f} % på en månad".replace(".", ",")
        if pv is not None:
            txt += f" (förra {fmt(key, pv)}"
            txt += f", väntat {fmt(key, e)})" if e is not None else ")"
        if e is not None:
            diff = v - e
            if abs(diff) >= (0.05 if key not in ("pay", "claims") else 5):
                hot = diff > 0 if key not in ("unemp",) else diff < 0
                r["vs"] = "över" if diff > 0 else "under"
                txt += f". {'Högre' if diff > 0 else 'Lägre'} än väntat" + (" (negativt för räntorna)" if hot and key in ("cpi", "core", "pce", "ppi") else "")
            else:
                r["vs"] = "som"
                txt += ". I linje med förväntan"
        r["text"] = txt + "."
        rel.append(r)
    return rel


def in_release_window(now):
    """USA släpper det mesta kl. 8.30 och 10.00 New York-tid: 12.30–15.00 UTC beroende på sommartid."""
    return now.weekday() < 5 and 12 <= now.hour < 16


def run(prev: dict | None = None, mode: str = "quotes", expect: dict | None = None, log=print):
    """Hämtar och returnerar (macro, nya_releaser). Saknas en serie behålls den från förra körningen."""
    prev = prev or {}
    old = prev.get("s") or {}
    s = dict(old)
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    last_full = prev.get("fredAt") or ""
    stale = not last_full or (now - datetime.strptime(last_full[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() > 6 * 3600 if len(last_full) >= 19 else True
    full = mode == "full" or stale
    rel_keys = {k for k, *_r in FRED if _r[-1]}
    fred_at = prev.get("fredAt")
    if full:
        s.update(fred_all(None, log))
        fred_at = now_iso
    elif in_release_window(now):
        s.update(fred_all(rel_keys, log))
    s.update(yahoo_all(old, full, log))
    if not s:
        return None, []
    new = detect(old, s, expect or {}, now_iso, log)
    # Feds pressmeddelanden
    seen = prev.get("fedSeen")
    items = fed_rss(log)
    fed = prev.get("fed") or []
    if items:
        links = [x["u"] for x in items]
        if seen is not None:  # första körningen: bara spara, inga notiser
            for x in items:
                if x["u"] not in seen and ("fomc" in x["t"].lower() or "minutes" in x["t"].lower() or "federal reserve" in x["t"].lower()):
                    new.append({"key": "fomc", "name": fed_sv(x["t"]), "period": x.get("pub", "")[:16], "at": now_iso,
                                "url": x["u"], "src": "Federal Reserve", "text": fed_sv(x["t"]) + ". " + x["t"]})
        seen = (links + (seen or []))[:40]
        fed = items[:8]
    releases = (new + (prev.get("releases") or []))[:40]
    return {"asOf": now.date().isoformat(), "updatedAt": now_iso, "fredAt": fred_at, "s": s,
            "releases": releases, "fed": fed, "fedSeen": seen}, new
