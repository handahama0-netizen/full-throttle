"""Makro: Fed, räntor, inflation, arbetsmarknad, oro och råvaror.

FRED (Federal Reserve Bank of St. Louis) som CSV, ingen API-nyckel. Marknadsdata (VIX, dollar, olja, guld,
koppar) från Yahoo Finance via yfinance. Skriver data/macro.json som sidans makro-story läser.

Varje serie: {"n": namn, "u": enhet, "f": d/w/m, "d": datum, "v": värden, "src": källa}. Månadsserier
med index (KPI, PCE) räknas om till årstakt i procent, så att sidan visar inflationen direkt.
"""
from __future__ import annotations

import io
import time
from datetime import date, datetime, timedelta, timezone

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"

# nyckel, FRED-id, namn, enhet, hur serien räknas (lvl = som den är, yoy = årstakt i %, chg = förändring mot månaden innan, k = delat med 1000)
FRED = [
    ("fed", "DFEDTARU", "Styrräntan (övre gräns)", "%", "lvl"),
    ("y10", "DGS10", "10-årsräntan (USA)", "%", "lvl"),
    ("y2", "DGS2", "2-årsräntan (USA)", "%", "lvl"),
    ("y3m", "DGS3MO", "3-månadersräntan (USA)", "%", "lvl"),
    ("curve", "T10Y2Y", "10 år minus 2 år", "%-enheter", "lvl"),
    ("be5", "T5YIE", "Marknadens inflationsförväntan 5 år", "%", "lvl"),
    ("cpi", "CPIAUCSL", "Inflation (KPI)", "% på ett år", "yoy"),
    ("core", "CPILFESL", "Kärninflation (KPI utan mat och energi)", "% på ett år", "yoy"),
    ("pce", "PCEPILFE", "Kärn-PCE (Feds favoritmått)", "% på ett år", "yoy"),
    ("unemp", "UNRATE", "Arbetslöshet", "%", "lvl"),
    ("pay", "PAYEMS", "Nya jobb per månad", "tusental", "chg"),
    ("claims", "ICSA", "Nyanmälda arbetslösa per vecka", "tusental", "k"),
    ("sent", "UMCSENT", "Konsumentförtroende (Michigan)", "index", "lvl"),
    ("hy", "BAMLH0A0HYM2", "Kreditspread, högriskobligationer", "%-enheter", "lvl"),
]
YAHOO = [
    ("vix", "^VIX", "VIX (rädsloindex)", "punkter"),
    ("dxy", "DX-Y.NYB", "Dollarindex (DXY)", "index"),
    ("oil", "CL=F", "Olja (WTI)", "USD/fat"),
    ("gold", "GC=F", "Guld", "USD/uns"),
    ("copper", "HG=F", "Koppar", "USD/pund"),
]


def _fred(sid: str, start: str):
    import requests
    for attempt in range(3):
        try:
            r = requests.get(FRED_URL.format(id=sid, start=start), timeout=30, headers={"User-Agent": "Mozilla/5.0 full-throttle"})
            if r.ok and "," in r.text:
                return parse_csv(r.text)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 * (attempt + 1))
    return []


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
    if kind == "chg":
        return [(obs[i][0], round(obs[i][1] - obs[i - 1][1], 1)) for i in range(1, len(obs))]
    if kind == "k":
        return [(d, round(v / 1000, 1)) for d, v in obs]
    return [(d, round(v, 3)) for d, v in obs]


def series(obs, n, u, f, src):
    return {"n": n, "u": u, "f": f, "d": [d for d, _ in obs], "v": [v for _, v in obs], "src": src}


def fred_all(log=print):
    start = (date.today() - timedelta(days=365 * 5)).isoformat()
    res = {}
    for key, sid, n, u, kind in FRED:
        obs = _fred(sid, start)
        if not obs:
            log("makro: FRED saknas", sid)
            continue
        obs = transform(obs, kind)
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
        time.sleep(0.3)
    return res


def yahoo_all(log=print):
    try:
        import yfinance as yf
    except Exception:  # noqa: BLE001
        return {}
    res = {}
    for key, tk, n, u in YAHOO:
        try:
            df = yf.Ticker(tk).history(period="3y", interval="1d", auto_adjust=False)
            df = df.dropna(subset=["Close"])
            obs = [(i.strftime("%Y-%m-%d"), round(float(c), 3)) for i, c in zip(df.index, df["Close"])]
            if obs:
                res[key] = series(weekly(obs), n, u, "d", "Yahoo " + tk)
        except Exception as e:  # noqa: BLE001
            log("makro: Yahoo saknas", tk, e)
    return res


def run(prev: dict | None = None, fred: bool = True, log=print):
    """Hämtar allt. FRED bara när fred=True (en gång per dag räcker), annars återanvänds förra datan.

    Saknas en serie behålls den från förra körningen, så att en tillfällig störning inte tömmer sidan."""
    prev = prev or {}
    old = prev.get("s") or {}
    s = dict(old)
    if fred:
        s.update(fred_all(log))
    s.update(yahoo_all(log))
    if not s:
        return None
    now = datetime.now(timezone.utc)
    return {"asOf": now.date().isoformat(), "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fredAt": now.date().isoformat() if fred else prev.get("fredAt"), "s": s}
