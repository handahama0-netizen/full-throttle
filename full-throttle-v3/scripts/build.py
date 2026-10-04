"""Bygger sidans data och publiceringsmapp.

Lägen:
  full    hela universumet: kurser 10 år, nyckeltal, analytiker, 200W (körs varje natt)
  quotes  bara senaste kurserna, uppdaterar förra körningens data (körs under börsdagen)
  site    bara själva sidan, behåller förra körningens data (körs när index.html ändras)

  python scripts/build.py --mode full --prev _prev --out _site

Datakälla: Yahoo Finance via yfinance. Ingen API-nyckel.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SRC = Path(__file__).resolve().parent.parent
MA_WEEKS = 200
DAILY_DAYS = 800       # ~3 år handelsdagar sparas per aktie
WEEKLY_WEEKS = 530     # ~10 år veckor sparas per aktie
MAX_EVENTS = 150
WORKERS = 3

SECTOR_SV = {
    "Technology": "Teknik", "Communication Services": "Kommunikation", "Consumer Cyclical": "Sällanköp",
    "Consumer Defensive": "Dagligvaror", "Healthcare": "Hälsovård", "Industrials": "Industri",
    "Financial Services": "Finans", "Basic Materials": "Råvaror", "Energy": "Energi",
    "Utilities": "Kraftbolag", "Real Estate": "Fastigheter",
}
EXCLUDED = {"Finans", "Råvaror", "Energi", "Kraftbolag", "Fastigheter"}
ZONES = [(0, "fire", "Fire Sale"), (10, "vcheap", "Very Cheap"), (20, "cheap", "Cheap"),
         (30, "fair", "Fair Value"), (40, "exp", "Expensive"), (float("inf"), "vexp", "Very Expensive")]
LRHR_CAGR = (1.5 ** (1 / 5) - 1) * 100


def log(*a):
    print(*a, flush=True)


# ---------------- små hjälpare ----------------

def num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def rnd(x, dp=2):
    x = num(x)
    return None if x is None else round(x, dp)


def sig(x, digits=5):
    """Avrunda till ett rimligt antal värdesiffror (mindre filer)."""
    x = num(x)
    if x is None or x == 0:
        return x
    return round(x, max(0, digits - int(math.floor(math.log10(abs(x)))) - 1))


def row(df, *names):
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return [num(v) for v in df.loc[n].tolist()]
    return None


def first(vals):
    for v in vals or []:
        if v is not None:
            return v
    return None


def revenue_cagr(annual):
    pts = [v for v in (annual or []) if v is not None and v > 0]
    if len(pts) < 3:
        return None, None
    years = len(pts) - 1
    return round(((pts[0] / pts[-1]) ** (1 / years) - 1) * 100, 1), years


def ttm_growth(quarterly, annual):
    q = [v for v in (quarterly or []) if v is not None]
    if len(q) >= 8:
        now, before = sum(q[:4]), sum(q[4:8])
        if before > 0:
            return round((now / before - 1) * 100, 2)
    a = [v for v in (annual or []) if v is not None]
    if len(a) >= 2 and a[1] > 0:
        return round((a[0] / a[1] - 1) * 100, 2)
    return None


def roic(ebit, tax_rate, debt, equity, cash):
    if ebit is None or equity is None:
        return None
    invested = (debt or 0) + equity - (cash or 0)
    if invested <= 0:
        return None
    t = tax_rate if tax_rate is not None and 0 <= tax_rate < 0.6 else 0.21
    return round(ebit * (1 - t) / invested * 100, 2)


def debt_to_equity(debt, equity):
    if equity is None:
        return None, False
    if equity <= 0:
        return None, True
    return round((debt or 0) / equity, 2), False


def share_change(shares):
    s = [v for v in (shares or []) if v]
    if len(s) < 2:
        return None
    return round((s[0] / s[1] - 1) * 100, 2)


def zone_of(dist):
    if dist is None:
        return None
    return next(code for lim, code, _ in ZONES if dist < lim)


def zone_name(code):
    return next((n for _, c, n in ZONES if c == code), code or "")


def ma_from_weekly(closes, price=None):
    """200W-snitt där senaste veckan räknas med aktuell kurs (som TradingView)."""
    c = [v for v in closes if v is not None]
    if price is not None and c:
        c = c[:-1] + [price]
    if len(c) < MA_WEEKS:
        return None
    return sum(c[-MA_WEEKS:]) / MA_WEEKS


# ---------------- strategier (standardgränser, för signaler och mejllarm) ----------------

def _gt(x, v): return x is not None and x > v
def _ge(x, v): return x is not None and x >= v
def _lt(x, v): return x is not None and x < v
def _le(x, v): return x is not None and x <= v


def passes_ftp(d):
    if d.get("excluded"):
        return False
    return all([_ge(d.get("revCagr"), 15), _ge(d.get("revGrowth1y"), 20),
                _ge(d.get("de"), 0) and _le(d.get("de"), 1) and not d.get("negEquity"),
                _gt(d.get("peg"), 0) and _le(d.get("peg"), 1.5), _ge(d.get("fcfMargin"), 5),
                _ge(d.get("roic"), 10), _ge(d.get("grossMargin"), 35),
                d.get("shareChange") is None or d["shareChange"] <= 5, _ge(d.get("mcapB"), 2)])


def passes_lrhr(d):
    if d.get("excluded"):
        return False
    return all([_gt(d.get("revCagr"), LRHR_CAGR), _gt(d.get("revGrowth1y"), 5), _gt(d.get("earnGrowth1y"), 5),
                _gt(d.get("roe"), 15), _ge(d.get("de"), 0) and _lt(d.get("de"), 1) and not d.get("negEquity"),
                _gt(d.get("fcfMargin"), 0), _gt(d.get("peg"), 0) and _lt(d.get("peg"), 1), _ge(d.get("mcapB"), 10)])


def signals(d):
    dist = d.get("dist200w")
    out = []
    if dist is not None:
        if passes_lrhr(d) and dist < 20:
            out.append("swing")
        if passes_ftp(d) and dist < 30:
            out.append("rea")
    return out


# ---------------- universum ----------------

def read_list(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.upper())
    return out


def wiki_tickers():
    import pandas as pd
    import requests
    out = []
    hdr = {"User-Agent": "Mozilla/5.0 (full-throttle-screener)"}
    pages = [("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol",)),
             ("https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Symbol"))]
    for url, cols in pages:
        try:
            html = requests.get(url, headers=hdr, timeout=30).text
            for t in pd.read_html(io.StringIO(html)):
                col = next((c for c in cols if c in t.columns), None)
                if col and len(t) > 90:
                    out += [str(s).strip().replace(".", "-").upper() for s in t[col].tolist() if str(s).strip()]
                    break
        except Exception as e:  # noqa: BLE001
            log("Kunde inte läsa", url, e)
    return out


def universe(prev_universe):
    cfg = SRC / "config"
    tick = wiki_tickers() + read_list(cfg / "stockholm.txt") + read_list(cfg / "egna.txt")
    if len(tick) < 200 and prev_universe:  # Wikipedia nere – använd förra listan
        tick += [r["ticker"] for r in prev_universe]
    seen, out = set(), []
    for t in tick:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------- kurser ----------------

def download_prices(tickers, period="10y", interval="1d"):
    import yfinance as yf
    res = {}
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        for attempt in range(3):
            try:
                df = yf.download(chunk, period=period, interval=interval, group_by="ticker", auto_adjust=True,
                                 threads=True, progress=False, multi_level_index=True)
                break
            except Exception as e:  # noqa: BLE001
                log("download fel", e)
                time.sleep(10 * (attempt + 1))
                df = None
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                sub = df[t] if t in df.columns.get_level_values(0) else None
            except Exception:  # noqa: BLE001
                sub = None
            if sub is None:
                continue
            sub = sub.dropna(subset=["Close"])
            if not sub.empty:
                res[t] = sub
        time.sleep(1)
    return res


def daycode(ts):
    return int(ts.timestamp() // 86400)


def pack_bars(df, n, with_vol=True):
    df = df.tail(n)
    out = {"t": [daycode(i) for i in df.index], "o": [sig(v) for v in df["Open"]], "h": [sig(v) for v in df["High"]],
           "l": [sig(v) for v in df["Low"]], "c": [sig(v) for v in df["Close"]]}
    if with_vol and "Volume" in df:
        out["v"] = [int((num(v) or 0) // 1000) for v in df["Volume"]]
    return out


def weekly(df):
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    return w.dropna(subset=["Close"])


# ---------------- nyckeltal och analytiker ----------------

_fx: dict = {}


def usd_rate(cur):
    if not cur or cur.upper() == "USD":
        return 1.0
    cur = cur.upper()
    if cur not in _fx:
        import yfinance as yf
        try:
            h = yf.Ticker(f"{cur}USD=X").history(period="5d")
            _fx[cur] = num(h["Close"].dropna().iloc[-1]) if not h.empty else None
        except Exception:  # noqa: BLE001
            _fx[cur] = None
    return _fx[cur]


def retry(fn, tries=3, base=4):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if i == tries - 1:
                raise
            time.sleep(base * (3 ** i) if "Too Many" in msg or "429" in msg else base)
    return None


def safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def fundamentals(tk: str):
    import yfinance as yf
    t = yf.Ticker(tk)
    info = retry(lambda: t.info) or {}
    if not info or (info.get("quoteType") is None and info.get("shortName") is None):
        raise ValueError("ingen info")
    qtype = info.get("quoteType", "EQUITY")
    inc_a = safe(lambda: t.income_stmt) if qtype == "EQUITY" else None
    inc_q = safe(lambda: t.quarterly_income_stmt) if qtype == "EQUITY" else None
    bal = safe(lambda: t.balance_sheet) if qtype == "EQUITY" else None
    recs = safe(lambda: t.recommendations) if qtype == "EQUITY" else None
    ud = safe(lambda: t.upgrades_downgrades) if qtype == "EQUITY" else None

    rev_a = row(inc_a, "Total Revenue", "Operating Revenue")
    rev_q = row(inc_q, "Total Revenue", "Operating Revenue")
    eps_a = row(inc_a, "Diluted EPS", "Basic EPS") or row(inc_a, "Net Income")
    eps_q = row(inc_q, "Diluted EPS", "Basic EPS") or row(inc_q, "Net Income")
    ni_a = row(inc_a, "Net Income")
    cagr, years = revenue_cagr(rev_a)
    debt = first(row(bal, "Total Debt"))
    equity = first(row(bal, "Stockholders Equity", "Common Stock Equity"))
    cash = first(row(bal, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    de, neg = debt_to_equity(debt, equity)

    cur = info.get("currency") or "USD"
    fin_cur = info.get("financialCurrency") or cur
    rate = usd_rate(cur)
    mcap = num(info.get("marketCap"))
    fcf, trev = num(info.get("freeCashflow")), num(info.get("totalRevenue"))
    gm = num(info.get("grossMargins"))
    roe_i = num(info.get("returnOnEquity"))
    ni = first(ni_a)
    sector = SECTOR_SV.get(info.get("sector") or "", info.get("sector") or ("ETF" if qtype == "ETF" else "Övrigt"))
    earn_ts = num(info.get("earningsTimestampStart") or info.get("earningsTimestamp"))
    next_earn = datetime.fromtimestamp(earn_ts, tz=timezone.utc).date().isoformat() if earn_ts else None

    rec = {
        "ticker": tk, "name": info.get("shortName") or info.get("longName") or tk, "longName": info.get("longName"),
        "type": qtype, "exch": info.get("fullExchangeName") or info.get("exchange"), "currency": cur,
        "sector": sector, "industry": info.get("industry") or ("ETF" if qtype == "ETF" else ""),
        "excluded": sector in EXCLUDED or qtype != "EQUITY",
        "mcapB": rnd(mcap * rate / 1e9) if mcap and rate else None,
        "revCagr": cagr, "revCagrYears": years, "revGrowth1y": ttm_growth(rev_q, rev_a),
        "earnGrowth1y": ttm_growth(eps_q, eps_a),
        "roe": rnd(roe_i * 100) if roe_i is not None else (rnd(ni / equity * 100) if ni is not None and equity and equity > 0 else None),
        "peg": rnd(num(info.get("trailingPegRatio")) or num(info.get("pegRatio"))),
        "de": de, "negEquity": neg,
        "roic": roic(first(row(inc_a, "EBIT", "Operating Income")), first(row(inc_a, "Tax Rate For Calcs")), debt, equity, cash),
        "grossMargin": rnd(gm * 100) if gm is not None else None,
        "fcfMargin": rnd(fcf / trev * 100) if fcf is not None and trev else None,
        "opMargin": rnd(num(info.get("operatingMargins")) * 100) if num(info.get("operatingMargins")) is not None else None,
        "netMargin": rnd(num(info.get("profitMargins")) * 100) if num(info.get("profitMargins")) is not None else None,
        "shareChange": share_change(row(bal, "Ordinary Shares Number", "Share Issued")),
        "pe": rnd(info.get("trailingPE")), "fwdPe": rnd(info.get("forwardPE")),
        "ps": rnd(info.get("priceToSalesTrailing12Months")), "pb": rnd(info.get("priceToBook")),
        "evEbitda": rnd(info.get("enterpriseToEbitda")), "beta": rnd(info.get("beta")),
        "divYield": rnd(num(info.get("dividendYield"))) if num(info.get("dividendYield")) is not None else None,
        "hi52": rnd(info.get("fiftyTwoWeekHigh")), "lo52": rnd(info.get("fiftyTwoWeekLow")),
        "targetMean": rnd(info.get("targetMeanPrice")), "targetHigh": rnd(info.get("targetHighPrice")),
        "targetLow": rnd(info.get("targetLowPrice")), "recKey": info.get("recommendationKey"),
        "recMean": rnd(info.get("recommendationMean")), "nAnalysts": info.get("numberOfAnalystOpinions"),
        "nextEarnings": next_earn,
    }
    detail = {
        "about": (info.get("longBusinessSummary") or "")[:1400], "web": info.get("website"),
        "emp": info.get("fullTimeEmployees"), "country": info.get("country"), "city": info.get("city"),
        "finCur": fin_cur, "fin": None, "recs": [], "ud": [],
    }
    if inc_a is not None and not inc_a.empty:
        years_lbl = [str(c)[:4] for c in inc_a.columns][:5]
        detail["fin"] = {"y": years_lbl[::-1], "rev": [sig(v, 4) for v in (rev_a or [])[:5]][::-1],
                         "ni": [sig(v, 4) for v in (ni_a or [])[:5]][::-1],
                         "eps": [sig(v, 4) for v in (row(inc_a, "Diluted EPS", "Basic EPS") or [])[:5]][::-1]}
    if recs is not None and not getattr(recs, "empty", True):
        for _, r in recs.head(4).iterrows():
            detail["recs"].append({k: (int(r[k]) if k != "period" else str(r[k])) for k in
                                   ("period", "strongBuy", "buy", "hold", "sell", "strongSell") if k in r})
    if ud is not None and not getattr(ud, "empty", True):
        for idx, r in ud.head(12).iterrows():
            detail["ud"].append({"date": str(idx)[:10], "firm": r.get("Firm"), "to": r.get("ToGrade"),
                                 "from": r.get("FromGrade"), "action": r.get("Action"),
                                 "pt": num(r.get("currentPriceTarget")), "ptPrev": num(r.get("priorPriceTarget"))})
    return rec, detail


# ---------------- huvudflöden ----------------

def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def fname(tk):
    return tk.replace("^", "_").replace("/", "_") + ".json"


def copy_site(out: Path):
    site = SRC / "site"
    for f in site.iterdir():
        if f.is_file():
            shutil.copy(f, out / f.name)
    (out / ".nojekyll").write_text("")


def diff_events(old, new, today):
    ev, tk = [], new["ticker"]
    if old and old.get("zone") and new.get("zone") and old["zone"] != new["zone"]:
        ev.append({"date": today, "ticker": tk, "type": "zone", "from": old["zone"], "to": new["zone"]})
    before, after = set((old or {}).get("signals") or []), set(new.get("signals") or [])
    for s in sorted(after - before):
        ev.append({"date": today, "ticker": tk, "type": "in", "signal": s})
    if old is not None:
        for s in sorted(before - after):
            ev.append({"date": today, "ticker": tk, "type": "out", "signal": s})
    return ev


def run_full(prev: Path, out: Path, limit: int | None = None):
    prev_u = load_json(prev / "data" / "universe.json", {})
    prev_rows = {r["ticker"]: r for r in prev_u.get("rows", [])}
    first_time = not prev_rows
    tickers = universe(prev_u.get("rows"))
    if limit:
        tickers = tickers[:limit]
    log(f"Universum: {len(tickers)} symboler")

    prices = download_prices(tickers)
    log(f"Kurser: {len(prices)} st")

    funds = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fundamentals, t): t for t in tickers if t in prices}
        for i, f in enumerate(as_completed(futs), 1):
            t = futs[f]
            try:
                funds[t] = f.result()
            except Exception as e:  # noqa: BLE001
                log("nyckeltal fel", t, str(e)[:120])
            if i % 50 == 0:
                log(f"  nyckeltal {i}/{len(futs)}")

    today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
    rows, events, alerts = [], [], []
    (out / "data" / "t").mkdir(parents=True, exist_ok=True)
    for t in tickers:
        df = prices.get(t)
        if df is None:
            if t in prev_rows:  # behåll förra
                rows.append({**prev_rows[t], "stale": True})
                pf = prev / "data" / "t" / fname(t)
                if pf.exists():
                    shutil.copy(pf, out / "data" / "t" / fname(t))
            continue
        rec, det = funds.get(t, (None, None))
        old = prev_rows.get(t)
        if rec is None:
            if old:
                rec = {k: v for k, v in old.items() if k not in ("price", "prevClose", "chg", "ma200w", "dist200w", "zone", "signals", "stale")}
                old_det = load_json(prev / "data" / "t" / fname(t), {})
                det = {k: old_det.get(k) for k in ("about", "web", "emp", "country", "city", "finCur", "fin", "recs", "ud")}
            else:
                rec = {"ticker": t, "name": t, "type": "EQUITY", "currency": "USD", "sector": "Övrigt", "excluded": True}
                det = {}
        wk = weekly(df)
        closes = [num(v) for v in wk["Close"]]
        price = num(df["Close"].iloc[-1])
        prev_close = num(df["Close"].iloc[-2]) if len(df) > 1 else None
        ma = ma_from_weekly(closes, price)
        dist = (price / ma - 1) * 100 if ma and price else None
        rec.update({"price": sig(price, 6), "prevClose": sig(prev_close, 6),
                    "chg": rnd((price / prev_close - 1) * 100) if price and prev_close else None,
                    "ma200w": sig(ma, 6) if ma else None, "dist200w": rnd(dist), "zone": zone_of(dist),
                    "asOf": today, "stale": False})
        if rec.get("hi52") is None:
            last = df.tail(252)
            rec["hi52"], rec["lo52"] = sig(last["High"].max()), sig(last["Low"].min())
        rec["signals"] = signals(rec)
        if not first_time:
            evs = diff_events(old, rec, today)
            events += evs
            alerts += [(rec, e["signal"]) for e in evs if e["type"] == "in"]
        rows.append(rec)
        write_json(out / "data" / "t" / fname(t), {**(det or {}), "d": pack_bars(df, DAILY_DAYS),
                                                    "w": pack_bars(wk, WEEKLY_WEEKS, with_vol=False)})

    if len([r for r in rows if not r.get("stale")]) < max(20, len(tickers) * 0.3):
        log("För få aktier lyckades – avbryter och behåller förra datan.")
        return 1
    write_json(out / "data" / "universe.json", {
        "asOf": today, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quotesAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance", "rows": rows,
        "events": (events + prev_u.get("events", []))[:MAX_EVENTS]})
    write_alerts(alerts, today)
    log(f"Klart: {len(rows)} aktier, {len(events)} händelser, {len(alerts)} nya signaler")
    return 0


def run_quotes(prev: Path, out: Path):
    u = load_json(prev / "data" / "universe.json", None)
    if not u:
        log("Ingen tidigare data – kör full.")
        return run_full(prev, out)
    shutil.copytree(prev / "data", out / "data", dirs_exist_ok=True)
    rows = u["rows"]
    prices = download_prices([r["ticker"] for r in rows], period="5d", interval="1d")
    today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
    events, alerts, n = [], [], 0
    for r in rows:
        df = prices.get(r["ticker"])
        if df is None or df.empty:
            continue
        old = dict(r)
        price = num(df["Close"].iloc[-1])
        prev_close = num(df["Close"].iloc[-2]) if len(df) > 1 else r.get("prevClose")
        last_day = daycode(df.index[-1])
        r["price"], r["prevClose"] = sig(price, 6), sig(prev_close, 6)
        r["chg"] = rnd((price / prev_close - 1) * 100) if price and prev_close else None
        r["lastBar"] = {"t": last_day, "o": sig(df["Open"].iloc[-1]), "h": sig(df["High"].iloc[-1]),
                        "l": sig(df["Low"].iloc[-1]), "c": sig(price)}
        if r.get("ma200w") and price:
            r["dist200w"] = rnd((price / r["ma200w"] - 1) * 100)
            r["zone"] = zone_of(r["dist200w"])
        r["signals"] = signals(r)
        ev = diff_events(old, r, today)
        events += ev
        alerts += [(r, e["signal"]) for e in ev if e["type"] == "in"]
        n += 1
    u["quotesAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    u["events"] = (events + u.get("events", []))[:MAX_EVENTS]
    write_json(out / "data" / "universe.json", u)
    write_alerts(alerts, today)
    log(f"Kurser uppdaterade för {n} aktier")
    return 0


def write_alerts(alerts, today):
    p = Path("alerts.md")  # i arbetsmappen; workflowet skapar en issue av den
    if not alerts:
        return
    label = {"swing": "Swing-läge (Low Risk, High Reward + 200W)", "rea": "Tillväxt på rea (Full Throttle+ + 200W)"}
    lines = [f"Nya signaler {today}:", ""]
    for d, s in alerts:
        lines.append(f"- **{d['ticker']}** {d.get('name','')}: {label[s]}. Kurs {d.get('price')} {d.get('currency','')}, "
                     f"{d.get('dist200w')} % över 200W ({zone_name(d.get('zone'))}).")
    p.write_text("\n".join(lines + ["", "Öppna sidan för detaljer."]), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=["full", "quotes", "site"])
    ap.add_argument("--prev", default="_prev")
    ap.add_argument("--out", default="_site")
    ap.add_argument("--limit", type=int, default=None, help="bara de första N aktierna (test)")
    a = ap.parse_args()
    prev, out = Path(a.prev), Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    has_prev = (prev / "data" / "universe.json").exists()
    if a.mode == "site" and has_prev:
        shutil.copytree(prev / "data", out / "data")
        code = 0
    elif a.mode == "quotes" and has_prev:
        code = run_quotes(prev, out)
    else:
        code = run_full(prev, out, a.limit)
    if code != 0:
        if has_prev:  # publicera förra datan istället för att lämna sidan tom
            shutil.rmtree(out)
            shutil.copytree(prev, out, ignore=shutil.ignore_patterns(".git"))
        else:
            return code
    copy_site(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
