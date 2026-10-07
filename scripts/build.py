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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A  # noqa: E402

SRC = Path(__file__).resolve().parent.parent
MA_WEEKS = 200
DAILY_DAYS = 800       # ~3 år handelsdagar sparas per aktie
WEEKLY_WEEKS = 860     # ~16,5 år veckor sparas per aktie (backtest från 2015 behöver 200 veckor före)
PRICE_START = "2010-06-01"
MAX_EVENTS = 150
WORKERS = 2
EXTRA_ROTATION = 3   # tunga analysanrop (prognoser, nyheter, kassaflöde) görs för en tredjedel av aktierna per natt

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
        if passes_lrhr(d) and dist < 0:
            out.append("diamant")
        if d.get("goldQ") and dist < 20:
            out.append("guld")
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
    tick = wiki_tickers() + read_list(cfg / "stockholm.txt") + read_list(cfg / "egna.txt") + read_list(cfg / "mina.txt")
    if len(tick) < 200 and prev_universe:  # Wikipedia nere – använd förra listan
        tick += [r["ticker"] for r in prev_universe]
    seen, out = set(), []
    for t in tick:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------- kurser ----------------

def download_prices(tickers, period="10y", interval="1d", start=None):
    import yfinance as yf
    res = {}
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        for attempt in range(3):
            try:
                df = yf.download(chunk, period=None if start else period, start=start, interval=interval, group_by="ticker", auto_adjust=True,
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


def momentum(df):
    """Kursbaserade quant-mått: avkastning 6 och 12 månader, 12-1-momentum, volatilitet och glidande snitt."""
    c = df["Close"].dropna()
    n = len(c)
    out = {}

    def ret(h, skip=0):
        if n <= h:
            return None
        a, b = num(c.iloc[-1 - h]), num(c.iloc[-1 - skip])
        return round(b / a - 1, 4) if a and b else None

    out["r126"], out["r252"] = ret(126), ret(252)
    out["r12_1"] = ret(252, 21)  # 12 månader exklusive senaste månaden (klassisk momentumfaktor)
    if n > 60:
        lr = (c / c.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else None).dropna().tail(252)
        sd = num(lr.std())
        out["vol"] = round(sd * math.sqrt(252) * 100, 1) if sd else None
    for w in (50, 200):
        if n >= w:
            out[f"sma{w}"] = sig(c.tail(w).mean(), 6)
    if n >= 252:  # största fall från topp senaste året
        last = c.tail(252)
        out["mdd1y"] = round(num((last / last.cummax() - 1).min()) * 100, 1)
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


import threading  # noqa: E402

_gate = threading.Lock()
_state = {"next": 0.0, "cool": 0.0, "limited": 0, "news": 0, "nonews": 0}
MIN_GAP = 0.35  # sekunder mellan anrop till Yahoo (alla trådar tillsammans)


def _wait_turn():
    with _gate:
        now = time.time()
        t = max(now, _state["next"], _state["cool"])
        _state["next"] = t + MIN_GAP
    if t > now:
        time.sleep(t - now)


def _is_limit(e):
    m = f"{type(e).__name__} {e}"
    return "RateLimit" in m or "Too Many" in m or "429" in m


def retry(fn, tries=4, base=4):
    """Anropa Yahoo med jämn takt. Vid strypning (429) pausar alla trådar en stund och försöker igen."""
    for i in range(tries):
        _wait_turn()
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            if _is_limit(e):
                with _gate:
                    _state["limited"] += 1
                    _state["cool"] = max(_state["cool"], time.time() + 30 * (i + 1))
            else:
                time.sleep(base)
    return None


def call(fn, default=None):
    """Som retry men returnerar default istället för att krascha."""
    try:
        v = retry(fn)
        return default if v is None else v
    except Exception:  # noqa: BLE001
        return default


def safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def fundamentals(tk: str, extras: bool = True):
    import yfinance as yf
    t = yf.Ticker(tk)
    info = retry(lambda: t.info) or {}
    if not info or (info.get("quoteType") is None and info.get("shortName") is None):
        raise ValueError("ingen info")
    qtype = info.get("quoteType", "EQUITY")
    eq = qtype == "EQUITY"
    inc_a = call(lambda: t.income_stmt) if eq else None
    inc_q = call(lambda: t.quarterly_income_stmt) if eq else None
    bal = call(lambda: t.balance_sheet) if eq else None
    recs = call(lambda: t.recommendations) if eq else None
    ud = call(lambda: t.upgrades_downgrades) if eq else None
    ex = eq and extras
    cf = call(lambda: t.cashflow) if ex else None
    eps_trend = call(lambda: t.eps_trend) if ex else None
    eps_rev = call(lambda: t.eps_revisions) if ex else None
    rev_est = call(lambda: t.revenue_estimate) if ex else None
    eps_est = call(lambda: t.earnings_estimate) if ex else None
    earn_hist = call(lambda: t.earnings_history) if ex else None
    earn_dates = call(lambda: t.get_earnings_dates(limit=48)) if ex else None
    ins_tx = call(lambda: t.insider_transactions) if ex and not tk.endswith(".ST") else None
    inst_h = call(lambda: t.institutional_holders) if ex else None
    news = (call(lambda: t.news) or []) if ex else []
    if eq and not news:  # Yahoos nyhets-API ger ofta tomt till GitHubs servrar, ta RSS istället
        news = rss_news(tk, info.get("shortName") or info.get("longName") or tk)

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
        "nextEarnings": next_earn, "sectorEn": info.get("sector"),
        "shortPct": rnd(num(info.get("shortPercentOfFloat")) * 100) if num(info.get("shortPercentOfFloat")) is not None else None,
        "shortDays": rnd(info.get("shortRatio"), 1),
        "instPct": rnd(num(info.get("heldPercentInstitutions")) * 100, 1) if num(info.get("heldPercentInstitutions")) is not None else None,
        "insPct": rnd(num(info.get("heldPercentInsiders")) * 100, 1) if num(info.get("heldPercentInsiders")) is not None else None,
    }
    detail = {
        "about": (info.get("longBusinessSummary") or "")[:1400], "web": info.get("website"),
        "emp": info.get("fullTimeEmployees"), "country": info.get("country"), "city": info.get("city"),
        "finCur": fin_cur, "fin": None, "recs": [], "ud": [],
    }
    if inc_a is not None and not inc_a.empty:
        years_lbl = [str(c)[:4] for c in inc_a.columns][:5]
        dates_lbl = [str(c)[:10] for c in inc_a.columns][:5]
        detail["fin"] = {"y": years_lbl[::-1], "d": dates_lbl[::-1], "rev": [sig(v, 4) for v in (rev_a or [])[:5]][::-1],
                         "ni": [sig(v, 4) for v in (ni_a or [])[:5]][::-1],
                         "op": [sig(v, 4) for v in (row(inc_a, "Operating Income", "EBIT") or [])[:5]][::-1],
                         "eps": [sig(v, 4) for v in (row(inc_a, "Diluted EPS", "Basic EPS") or [])[:5]][::-1]}
    detail["fq"] = safe(lambda: fin_rows(inc_q, None)) or None   # kvartal
    detail["fa"] = safe(lambda: fin_rows(inc_a, cf)) or None     # år
    if recs is not None and not getattr(recs, "empty", True):
        for _, r in recs.head(4).iterrows():
            detail["recs"].append({k: (int(r[k]) if k != "period" else str(r[k])) for k in
                                   ("period", "strongBuy", "buy", "hold", "sell", "strongSell") if k in r})
    if ud is not None and not getattr(ud, "empty", True):
        for idx, r in ud.head(12).iterrows():
            detail["ud"].append({"date": str(idx)[:10], "firm": r.get("Firm"), "to": r.get("ToGrade"),
                                 "from": r.get("FromGrade"), "action": r.get("Action"),
                                 "pt": num(r.get("currentPriceTarget")), "ptPrev": num(r.get("priorPriceTarget"))})
    est = safe(lambda: A.estimates(eps_trend, eps_rev, rev_est, eps_est, earn_hist), {}) if ex else None
    detail["est"] = est
    detail["epsq"] = safe(lambda: eps_quarters(earn_dates)) or None
    detail["ins"] = safe(lambda: insiders(ins_tx)) if ins_tx is not None else None
    detail["holders"] = safe(lambda: holders(inst_h)) if inst_h is not None else None
    detail["news"] = parse_news(news) or None
    _state["news" if detail["news"] else "nonews"] += 1
    raw = {"fcf": first(row(cf, "Free Cash Flow")), "sbc": first(row(cf, "Stock Based Compensation")),
           "cash": num(info.get("totalCash")) if num(info.get("totalCash")) is not None else cash,
           "debt": num(info.get("totalDebt")) if num(info.get("totalDebt")) is not None else debt,
           "mcap": mcap, "beta": num(info.get("beta")), "finCur": fin_cur, "cur": cur,
           "epsFwd": num(info.get("forwardEps")),
           "revG": (((est or {}).get("rev") or {}).get("+1y") or {}).get("growth"),
           "epsG": (((est or {}).get("eps") or {}).get("+1y") or {}).get("growth"), "extras": ex}
    return rec, detail, raw


def eps_quarters(df):
    """Rapporterad och förväntad EPS per kvartal från Yahoos rapportkalender, äldst först."""
    if df is None or getattr(df, "empty", True):
        return []
    out = {}
    for idx, r in df.iterrows():
        a = num(r.get("Reported EPS"))
        if a is None:
            continue  # kommande rapport
        out[str(idx)[:10]] = {"d": str(idx)[:10], "a": round(a, 4), "e": rnd(r.get("EPS Estimate"), 4)}
    return [out[k] for k in sorted(out)]


def insiders(df):
    """Insiders köp och försäljningar senaste 12 månaderna (bara riktiga köp/sälj, inte tilldelningar)."""
    if df is None or getattr(df, "empty", True):
        return []
    cut = datetime.now(timezone.utc).date().toordinal() - 370
    out = []
    for _, r in df.iterrows():
        txt = str(r.get("Text") or r.get("Transaction") or "")
        low = txt.lower()
        kind = "S" if "sale" in low or "sold" in low else "B" if "purchase" in low or "buy" in low or "bought" in low else None
        if not kind:
            continue
        try:
            d = str(r.get("Start Date"))[:10]
            if datetime.strptime(d, "%Y-%m-%d").date().toordinal() < cut:
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append({"d": d, "k": kind, "v": rnd(r.get("Value"), 0), "sh": rnd(r.get("Shares"), 0),
                    "who": str(r.get("Insider") or "")[:40].title(), "pos": str(r.get("Position") or "")[:40]})
    out.sort(key=lambda x: x["d"], reverse=True)
    return out[:60]


def holders(df):
    """De största institutionella ägarna med förändring senaste rapporterade kvartalet (13F)."""
    if df is None or getattr(df, "empty", True):
        return None
    out = []
    for _, r in df.head(10).iterrows():
        pct, chg = num(r.get("pctHeld")), num(r.get("pctChange"))
        out.append({"n": str(r.get("Holder") or "")[:50], "d": str(r.get("Date Reported") or "")[:10],
                    "p": round(pct * 100, 2) if pct is not None else None,
                    "c": round(chg * 100, 1) if chg is not None else None, "v": rnd(r.get("Value"), 0)})
    return out or None


def merge_own(old, rec, today):
    """Ägarhistorik: en punkt per dag med institutionernas och insiders andel, byggs på över tid."""
    h = [x for x in (old or []) if isinstance(x, dict) and x.get("d")]
    if rec.get("instPct") is None and rec.get("insPct") is None:
        return h or None
    pt = {"d": today, "i": rec.get("instPct"), "n": rec.get("insPct")}
    if h and h[-1]["d"] == today:
        h[-1] = pt
    elif not h or h[-1].get("i") != pt["i"] or h[-1].get("n") != pt["n"] or len(h) < 2:
        h.append(pt)
    return h[-400:]


def fin_rows(inc, cf):
    """Resultaträkning per period som lista [{d, rev, op, ni, eps, fcf}], äldst först."""
    if inc is None or getattr(inc, "empty", True):
        return []
    out = {}
    for c in inc.columns:
        col = inc[c]

        def g(*names):
            for n in names:
                if n in inc.index:
                    return num(col.get(n))
            return None
        d = str(c)[:10]
        r = {"d": d, "rev": sig(g("Total Revenue", "Operating Revenue"), 5), "op": sig(g("Operating Income", "EBIT"), 5),
             "ni": sig(g("Net Income", "Net Income Common Stockholders"), 5), "eps": rnd(g("Diluted EPS", "Basic EPS"), 4)}
        if cf is not None and not getattr(cf, "empty", True) and c in cf.columns and "Free Cash Flow" in cf.index:
            r["fcf"] = sig(num(cf[c].get("Free Cash Flow")), 5)
        if any(r.get(k) is not None for k in ("rev", "ni", "eps")):
            out[d] = r
    return [out[k] for k in sorted(out)]


def merge_rows(old, new, keep=80):
    """Bygg på historik per period: gamla perioder behålls, nya värden vinner."""
    m = {q["d"]: q for q in (old or []) if isinstance(q, dict) and q.get("d")}
    for q in new or []:
        m[q["d"]] = {**m.get(q["d"], {}), **{k: v for k, v in q.items() if v is not None}}
    return [m[k] for k in sorted(m)][-keep:] or None


def merge_eps(old, new):
    """Behåll äldre kvartal från förra körningen, nya värden vinner."""
    m = {q["d"]: q for q in (old or []) if isinstance(q, dict) and q.get("d")}
    m.update({q["d"]: q for q in (new or [])})
    return [m[k] for k in sorted(m)][-60:] or None


_rss_gate = threading.Lock()
_rss_next = [0.0]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _rss_get(url):
    import requests
    with _rss_gate:
        w = _rss_next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _rss_next[0] = time.time() + 0.6
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    return r.content


def _rss_items(xml, strip_source=False):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    out = []
    for it in ET.fromstring(xml).iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        pub = src.text.strip() if src is not None and src.text else None
        if strip_source and pub and title.endswith(" - " + pub):
            title = title[: -len(pub) - 3]
        date = ""
        try:
            date = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            pass
        desc = it.findtext("description") or ""
        if "<" in desc:  # Google lägger HTML i beskrivningen
            desc = ""
        if title and link:
            out.append({"title": title, "link": link, "publisher": pub or "Yahoo Finance",
                        "pubDate": date, "summary": desc.strip()})
    out.sort(key=lambda x: x["pubDate"], reverse=True)
    return out


def _clean_name(name):
    import re
    n = re.sub(r"[,.]?\s+(Inc|Incorporated|Corp|Corporation|Co|Company|Ltd|Limited|plc|PLC|AB|ASA|A/S|Oyj|N\.V|NV|SA|S\.A|Holdings?|Group|Class [A-C]|\(publ\)|ser\. ?[A-C])\.?\b.*$", "", name or "")
    return n.strip() or name


_rss_stat = {"yahoo": 0, "yahooFail": 0, "google": 0, "empty": 0, "errors": []}


def _rss_err(src, e):
    if len(_rss_stat["errors"]) < 6:
        _rss_stat["errors"].append(f"{src}: {type(e).__name__} {str(e)[:120]}")


def rss_news(tk, name):
    """Nyheter utan nyckel: Yahoos RSS-flöde, annars Google News (fungerar även för svenska bolag)."""
    from urllib.parse import quote
    swe = tk.upper().endswith(".ST")
    if not swe and (_rss_stat["yahoo"] > 0 or _rss_stat["yahooFail"] < 5):  # Yahoos RSS svarar ofta 429 till GitHub: ge upp efter 5 misslyckanden
        try:
            items = _rss_items(_rss_get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(tk)}&region=US&lang=en-US"))
            if items:
                _rss_stat["yahoo"] += 1
                return items
        except Exception as e:  # noqa: BLE001
            _rss_stat["yahooFail"] += 1
            _rss_err("yahoo", e)
    q = f'"{_clean_name(name)}" aktie' if swe else f'"{_clean_name(name)}" {tk} stock'
    loc = "hl=sv&gl=SE&ceid=SE:sv" if swe else "hl=en-US&gl=US&ceid=US:en"
    try:
        items = _rss_items(_rss_get(f"https://news.google.com/rss/search?q={quote(q + ' when:30d')}&{loc}"), True)
        _rss_stat["google" if items else "empty"] += 1
        return items
    except Exception as e:  # noqa: BLE001
        _rss_err("google", e)
        _rss_stat["empty"] += 1
        return []


def parse_news(items):
    out = []
    for it in items[:20]:
        c = it.get("content") if isinstance(it, dict) and isinstance(it.get("content"), dict) else it
        if not isinstance(c, dict):
            continue
        title = c.get("title")
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link"))
        pub = (c.get("provider") or {}).get("displayName") or c.get("publisher")
        date = c.get("pubDate") or c.get("displayTime")
        if not date and c.get("providerPublishTime"):
            date = datetime.fromtimestamp(int(c["providerPublishTime"]), tz=timezone.utc).isoformat()
        if title and url:
            out.append({"t": title, "p": pub, "d": (date or "")[:10], "u": url, "s": (c.get("summary") or "")[:280]})
    return out[:12]


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
    # Mina aktier: listan i config/mina.txt syns på alla enheter
    write_json(out / "mina.json", {"tickers": read_list(SRC / "config" / "mina.txt")})


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


def apply_sec(rec, det, raw, s):
    """Siffror från SEC: 5-års omsättningstillväxt och fritt kassaflöde, fyller luckor från Yahoo."""
    det["sec"] = {"cik": s["cik"], "years": [{k: (sig(v, 5) if isinstance(v, float) else v) for k, v in r.items()}
                                             for r in s["years"]]}
    if s.get("revCagr") is not None:
        rec["revCagr"], rec["revCagrYears"], rec["revCagrSrc"] = s["revCagr"], s["revCagrYears"], "SEC"
    for k in ("revGrowth1y", "fcfMargin"):
        if rec.get(k) is None and s.get(k) is not None:
            rec[k] = s[k]
    usd = raw and (raw.get("finCur") or "USD") == "USD"
    if usd and raw.get("fcf") is None and s.get("fcf") is not None:
        raw["fcf"], raw["sbc"], raw["fcfSrc"] = s["fcf"], s.get("sbc"), "SEC"
    if raw and raw.get("revG") is None:  # prognos från tidigare natt om den finns
        e = det.get("est") or {}
        raw["revG"] = ((e.get("rev") or {}).get("+1y") or {}).get("growth")
        raw["epsG"] = raw.get("epsG") or ((e.get("eps") or {}).get("+1y") or {}).get("growth")


def analyse(t, df, rec, det, raw, bench, rf_usd):
    """Kursattribution och värdering för en aktie (läggs i detaljfilen och sammanfattningen)."""
    se = t.endswith(".ST")
    mkt = bench.get("^OMX" if se else "SPY")
    sec = None if se else bench.get(A.SECTOR_ETF.get(rec.get("sectorEn") or ""))
    att = safe(lambda: A.attribution(df["Close"], mkt, sec)) if mkt is not None else None
    if att:
        att["mkt"] = "OMX Stockholm 30" if se else "S&P 500 (SPY)"
        att["sec"] = None if sec is None else A.SECTOR_ETF.get(rec.get("sectorEn") or "")
        det["att"] = att
        for w in att["w"]:  # avkastning 1 vecka, 1 månad, 3 månader (för tema- och branschjämförelse)
            rec[{5: "r5", 21: "r21", 63: "r63"}[w["h"]]] = w["r"]
            if w["h"] == 21:
                rec["z21"] = w["z"]
    trend = (det.get("est") or {}).get("trend")
    rv = A.revision_pct(trend, 21)
    rec["epsRev30"] = round(rv * 100, 2) if rv is not None else None
    if raw and raw.get("fcf") is None and det.get("val"):  # inget nytt kassaflöde i natt: behåll senaste värderingen
        base = (det["val"].get("dcf") or {}).get("base")
        rec["fairValue"] = base
        rec["fairUpside"] = round((base / rec["price"] - 1) * 100, 1) if base and rec.get("price") else None
        rec["impliedG"] = det["val"].get("impliedG")
    elif raw:
        fx_t, fx_f = usd_rate(raw.get("cur")), usd_rate(raw.get("finCur"))
        fx = fx_f / fx_t if fx_t and fx_f else None
        rf = rf_usd if (raw.get("finCur") or "USD") == "USD" else 0.025
        val = safe(lambda: A.valuation(fcf=raw["fcf"], sbc=raw["sbc"], cash=raw["cash"], debt=raw["debt"],
                                       mcap_trading=raw["mcap"], price=rec.get("price"), fx_fin_to_trading=fx,
                                       beta=raw["beta"], rf=rf, rev_growth_est=raw["revG"], rev_cagr=rec.get("revCagr"),
                                       eps_fwd=raw["epsFwd"], eps_growth_est=raw["epsG"], aaa=rf_usd * 100 + 1.0))
        if val:
            val["finCur"] = raw.get("finCur")
            val["fcfSrc"] = raw.get("fcfSrc") or "Yahoo Finance"
            det["val"] = val
            base = (val.get("dcf") or {}).get("base")
            rec["fairValue"] = base
            rec["fairUpside"] = round((base / rec["price"] - 1) * 100, 1) if base and rec.get("price") else None
            rec["impliedG"] = val.get("impliedG")
    dcf = (det.get("val") or {}).get("dcf") or {}
    if dcf.get("base") and dcf["base"] > 0:  # bear/bull-värde från kassaflödesvärderingen, för bear case i listorna
        rec["bearV"], rec["bullV"] = dcf.get("bear"), dcf.get("bull")


def run_full(prev: Path, out: Path, limit: int | None = None):
    prev_u = load_json(prev / "data" / "universe.json", {})
    prev_rows = {r["ticker"]: r for r in prev_u.get("rows", [])}
    first_time = not prev_rows
    tickers = universe(prev_u.get("rows"))
    if limit:
        tickers = tickers[:limit]
    log(f"Universum: {len(tickers)} symboler")

    prices = download_prices(tickers, start=PRICE_START)
    log(f"Kurser: {len(prices)} st")
    bench = {k: v["Close"] for k, v in download_prices(A.BENCHMARKS, start=PRICE_START).items()}
    tnx = bench.get("^TNX")
    rf_usd = float(tnx.iloc[-1]) / 100 if tnx is not None and len(tnx) else 0.043
    try:  # officiella siffror från amerikanska årsredovisningar (gratis)
        import sec as SEC
        secd = SEC.load(tickers, log, load_json(prev / "data" / "sec_cik.json", {}),
                        {t: (prev_rows.get(t) or {}).get("longName") or (prev_rows.get(t) or {}).get("name") for t in tickers})
        if SEC.STATUS.get("cikMap"):
            write_json(out / "data" / "sec_cik.json", SEC.STATUS.get("cikMap"))
    except Exception as e:  # noqa: BLE001
        log("SEC hoppas över:", str(e)[:150])
        secd = {}

    # Tunga analysanrop: dina listor och aktier med signal varje natt, övriga roterar var tredje natt
    import zlib
    cfg = SRC / "config"
    prio = set(read_list(cfg / "egna.txt")) | set(read_list(cfg / "ai.txt")) | set(read_list(cfg / "mina.txt")) | {r["ticker"] for r in prev_u.get("rows", []) if r.get("signals")}
    doy = datetime.now(ZoneInfo("Europe/Stockholm")).timetuple().tm_yday
    extra_set, missing = set(), 0
    for t in tickers:
        has_prev = (prev / "data" / "t" / fname(t)).exists() and bool(load_json(prev / "data" / "t" / fname(t), {}).get("est"))
        if t in prio or zlib.crc32(t.encode()) % EXTRA_ROTATION == doy % EXTRA_ROTATION:
            extra_set.add(t)
        elif not has_prev and missing < 150:
            extra_set.add(t)
            missing += 1
    log(f"Analysdata hämtas för {len(extra_set)} aktier i natt")
    funds = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fundamentals, t, t in extra_set): t for t in tickers if t in prices}
        for i, f in enumerate(as_completed(futs), 1):
            t = futs[f]
            try:
                funds[t] = f.result()
            except Exception as e:  # noqa: BLE001
                log("nyckeltal fel", t, str(e)[:120])
            if i % 50 == 0:
                log(f"  nyckeltal {i}/{len(futs)}")

    # Institutionellt ägande (Nasdaq/13F): dina listor och signaler varje natt, övriga roterar med analysdatan
    import inst as INST
    instd = {}
    for t in [t for t in tickers if t in prices and t in extra_set and "." not in t and not t.startswith("^")]:
        r = INST.fetch(t, full=t in prio)
        if r:
            instd[t] = r
        if INST.status()["blocked"]:
            log("Nasdaq spärrar, hoppar över institutionellt ägande i natt")
            break
    log(f"Institutionellt ägande: {len(instd)} aktier ({INST.status()})")

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
        rec, det, raw = funds.get(t, (None, None, None))
        old = prev_rows.get(t)
        if rec is None:
            if old:
                rec = {k: v for k, v in old.items() if k not in ("price", "prevClose", "chg", "ma200w", "dist200w", "zone", "signals", "stale")}
                old_det = load_json(prev / "data" / "t" / fname(t), {})
                det = {k: old_det.get(k) for k in ("about", "web", "emp", "country", "city", "finCur", "fin", "recs", "ud", "est", "news", "val", "att", "epsq", "sec", "ins", "holders", "own", "inst", "instHist", "fq", "fa")}
            else:
                rec = {"ticker": t, "name": t, "type": "EQUITY", "currency": "SEK" if t.endswith(".ST") else "USD",
                       "sector": "Övrigt", "excluded": True}
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
        rec.update(safe(lambda: momentum(df), {}))
        hi = df["High"].dropna()
        if len(hi):  # högsta kurs sedan 2010 (så långt kurshistoriken går) = all-time high för nästan alla bolag
            rec["ath"], rec["athDate"] = sig(hi.max(), 6), str(hi.idxmax())[:10]
        if rec.get("hi52") is None:
            last = df.tail(252)
            rec["hi52"], rec["lo52"] = sig(last["High"].max()), sig(last["Low"].min())
        if det is not None and (det.get("est") is None or det.get("news") is None or (raw and not raw.get("extras"))):
            old_det = load_json(prev / "data" / "t" / fname(t), {})
            for k in ("est", "news", "ins"):
                if det.get(k) is None and old_det.get(k) is not None:
                    det[k] = old_det[k]
            if raw and not raw.get("extras") and old_det.get("val"):
                det["val"] = old_det["val"]
        if det is not None:  # EPS-historiken byggs på över tid
            _old = load_json(prev / "data" / "t" / fname(t), {})
            det["epsq"] = merge_eps(_old.get("epsq"), det.get("epsq"))
            det["fq"] = merge_rows(_old.get("fq"), det.get("fq"))
            det["fa"] = merge_rows(_old.get("fa"), det.get("fa"), 30)
            det["own"] = merge_own(_old.get("own"), rec, today)
            if det.get("holders") is None and _old.get("holders"):
                det["holders"] = _old["holders"]
            det["inst"] = instd.get(t) or _old.get("inst")
            det["instHist"] = INST.merge_hist(_old.get("instHist"), instd.get(t))
            if det.get("inst"):
                rec["instFlow"] = det["inst"].get("verdict")
                rec["instNetp"] = det["inst"].get("netp")
        s = secd.get(t.upper())
        if s and det is not None:
            apply_sec(rec, det, raw, s)
        elif det is not None and not secd and not det.get("sec"):  # SEC nere i natt: behåll förra tabellen
            old_sec = load_json(prev / "data" / "t" / fname(t), {}).get("sec")
            if old_sec:
                det["sec"] = old_sec
        if det is not None and rec.get("type", "EQUITY") == "EQUITY":
            analyse(t, df, rec, det, raw, bench, rf_usd)
        rec["goldQ"] = (old or {}).get("goldQ")  # räknas om när alla bolag är klara (behöver branschledarna)
        rec["signals"] = signals(rec)
        if not first_time:
            evs = diff_events(old, rec, today)
            events += evs
            alerts += [(rec, e["signal"]) for e in evs if e["type"] == "in"]
        rows.append(rec)
        if det is not None and "ai" not in det:  # behåll senaste AI-analysbrevet
            prev_ai = load_json(prev / "data" / "t" / fname(t), {}).get("ai")
            if prev_ai:
                det["ai"] = prev_ai
                rec["aiDate"] = prev_ai.get("date")
        write_json(out / "data" / "t" / fname(t), {**(det or {}), "d": pack_bars(df, DAILY_DAYS),
                                                    "w": pack_bars(wk, WEEKLY_WEEKS, with_vol=False)})

    # Guld kräver branschledare, så det räknas när alla bolag är klara
    try:
        import backtest as BTM
        lead = BTM.leaders([r for r in rows if r.get("type", "EQUITY") == "EQUITY"])
        for rec in rows:
            q = bool(rec.get("type", "EQUITY") == "EQUITY" and BTM.gold_quality(rec, lead))
            if q != bool(rec.get("goldQ")):
                had = "guld" in (rec.get("signals") or [])
                rec["goldQ"] = q
                rec["signals"] = signals(rec)
                has = "guld" in rec["signals"]
                if has and not had:
                    events.append({"date": today, "ticker": rec["ticker"], "type": "in", "signal": "guld"})
                    alerts.append((rec, "guld"))
                elif had and not has:
                    events.append({"date": today, "ticker": rec["ticker"], "type": "out", "signal": "guld"})
    except Exception as e:  # noqa: BLE001
        log("Guld-beräkning misslyckades", e)
    alerts += watch_alerts(rows, prev_rows, today, full=True)
    if len([r for r in rows if not r.get("stale")]) < max(20, len(tickers) * 0.3):
        log("För få aktier lyckades – avbryter och behåller förra datan.")
        return 1
    write_json(out / "data" / "universe.json", {
        "asOf": today, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quotesAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance", "rows": rows, "fx": fx_sek({r.get("currency") for r in rows}),
        "events": (events + prev_u.get("events", []))[:MAX_EVENTS]})
    write_alerts(alerts, today)
    try:  # diagnostik som går att läsa på sidan (jobbloggarna är inte alltid tillgängliga)
        import sec as SEC
        sec_status = {k: v for k, v in SEC.STATUS.items() if k != "cikMap"}
    except Exception:  # noqa: BLE001
        sec_status = None
    write_json(out / "data" / "status.json", {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": len(rows),
        "fresh": len(funds), "limited": _state["limited"], "news": _state["news"], "noNews": _state["nonews"],
        "rss": _rss_stat, "sec": sec_status})
    log(f"Klart: {len(rows)} aktier, {len(funds)} med färska nyckeltal, {len(events)} händelser, "
        f"{len(alerts)} nya signaler, strypt {_state['limited']} gånger, nyheter för {_state['news']} aktier (saknas för {_state['nonews']})")
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
    olds = {}
    for r in rows:
        df = prices.get(r["ticker"])
        if df is None or df.empty:
            continue
        old = dict(r)
        olds[r["ticker"]] = old
        price = num(df["Close"].iloc[-1])
        prev_close = num(df["Close"].iloc[-2]) if len(df) > 1 else r.get("prevClose")
        last_day = daycode(df.index[-1])
        r["price"], r["prevClose"] = sig(price, 6), sig(prev_close, 6)
        r["chg"] = rnd((price / prev_close - 1) * 100) if price and prev_close else None
        hi_today = num(df["High"].iloc[-1])
        if hi_today and r.get("ath") and hi_today > r["ath"]:
            r["ath"], r["athDate"] = sig(hi_today, 6), str(df.index[-1])[:10]
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
    alerts += watch_alerts(rows, olds, today, full=False)
    u["quotesAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    u["fx"] = fx_sek({r.get("currency") for r in rows}) or u.get("fx")
    u["events"] = (events + u.get("events", []))[:MAX_EVENTS]
    write_json(out / "data" / "universe.json", u)
    write_alerts(alerts, today)
    log(f"Kurser uppdaterade för {n} aktier")
    return 0


def fx_sek(currencies):
    """Valutakurser till SEK för portföljen (1 enhet = x kronor)."""
    sek = usd_rate("SEK")
    out = {"SEK": 1.0}
    for c in sorted(x for x in currencies if x):
        r = usd_rate(c)
        if r and sek:
            out[c] = round(r / sek, 6)
    return out


def read_holdings():
    """config/innehav.txt: TICKER KÖPKURS [ANTAL] per rad, plus positioner som Claude lagt in (site/positions.json)."""
    out = {}
    for p in (load_json(SRC / "site" / "positions.json", {}) or {}).get("positions", []):
        try:
            out[str(p["ticker"]).upper()] = {"price": float(p["price"]), "n": p.get("qty")}
        except (KeyError, TypeError, ValueError):
            continue
    for line in read_list(SRC / "config" / "innehav.txt"):
        parts = line.replace(",", ".").split()
        try:
            out[parts[0]] = {"price": float(parts[1]), "n": float(parts[2]) if len(parts) > 2 else None}
        except (IndexError, ValueError):
            continue
    return out


def watch_alerts(rows, prev, today, full):
    """Larm för det du äger och följer: stop, säljzoner, rapporter och institutioner som säljer.

    Larmar bara när en gräns passeras sedan förra körningen, så samma larm kommer inte varje halvtimme."""
    hold = read_holdings()
    watch = set(hold) | set(read_list(SRC / "config" / "mina.txt")) | set(read_list(SRC / "config" / "egna.txt"))
    out = []
    for r in rows:
        t = r["ticker"]
        o = prev.get(t) or {}
        p, op = r.get("price"), o.get("price")
        if t in hold and p and op:
            ep = hold[t]["price"]
            for lvl, txt in ((0.80, "har nått din stop (−20 % från köpkursen)"), (0.85, "närmar sig din stop (−15 % från köpkursen)")):
                if p <= ep * lvl < op:
                    out.append((r, f"⚠️ {t} {txt}: kurs {p:g} mot köp {ep:g}. Stop vid {ep * 0.8:.2f}."))
                    break
            d, od = r.get("dist200w"), o.get("dist200w")
            if d is not None and od is not None:
                for lvl, txt in ((40, "Expensive (40 % över 200W): originalregeln säljer här"), (30, "Fair Value (30 % över 200W): förbättrade regeln säljer här")):
                    if d >= lvl > od:
                        out.append((r, f"💰 {t} har nått {txt}. Kurs {p:g}, {(p / ep - 1) * 100:+.0f} % mot din köpkurs."))
                        break
        if t not in watch:
            continue
        ne, one = r.get("nextEarnings"), o.get("nextEarnings")
        if ne:
            try:
                days = (datetime.strptime(ne[:10], "%Y-%m-%d").date() - datetime.strptime(today, "%Y-%m-%d").date()).days
                odays = (datetime.strptime(one[:10], "%Y-%m-%d").date() - datetime.strptime(o.get("asOf") or today, "%Y-%m-%d").date()).days if one else 99
            except ValueError:
                days, odays = 99, 99
            if 0 <= days <= 7 and (odays > 7 or one != ne):
                out.append((r, f"📅 {t} rapporterar {ne} (om {days} dagar). Kursen kan röra sig kraftigt, fundera på hävstången."))
        if full and r.get("instFlow") == "bear" and o.get("instFlow") not in (None, "bear"):
            out.append((r, f"🏛 Institutionerna har börjat sälja {t} (nettoflöde senaste kvartalet)."))
    return out


SIG_LABEL = {"diamant": "💎 Diamant (Low Risk, High Reward + under 200W)", "guld": "🥇 Guld (stort, stabilt kvalitetsbolag + 200W)",
             "swing": "Swing-läge (Low Risk, High Reward + 200W)", "rea": "Tillväxt på rea (Full Throttle+ + 200W)"}
SITE = "https://handahama0-netizen.github.io/full-throttle/"


def write_alerts(alerts, today):
    """alerts.md blir en GitHub-issue (mejl), och samma larm skickas till mobilen via ntfy om ämne finns i config/larm.txt."""
    p = Path("alerts.md")  # i arbetsmappen; workflowet skapar en issue av den
    if not alerts:
        return
    lines, push = [f"Larm {today}:", ""], []
    for d, s in alerts:
        if s in SIG_LABEL:
            txt = (f"{SIG_LABEL[s]}: {d['ticker']} {d.get('name', '')}. Kurs {d.get('price')} {d.get('currency', '')}, "
                   f"{d.get('dist200w')} % mot 200W ({zone_name(d.get('zone'))}).")
        else:
            txt = s
        lines.append(f"- {txt} [Öppna]({SITE}#/aktie/{d['ticker']})")
        push.append((d["ticker"], txt))
    p.write_text("\n".join(lines + ["", f"Öppna sidan: {SITE}"]), encoding="utf-8")
    topic = None
    for line in read_list(SRC / "config" / "larm.txt"):
        if line.lower().startswith("NTFY:".lower()):
            topic = line.split(":", 1)[1].strip().lower()
    if not topic:
        return
    import requests
    for tk, txt in push[:15]:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=txt.encode("utf-8"), timeout=15,
                          headers={"Title": f"Full Throttle: {tk}", "Click": f"{SITE}#/aktie/{tk}", "Tags": "chart_with_upwards_trend"})
        except Exception as e:  # noqa: BLE001
            log("ntfy misslyckades", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=["full", "quotes", "site", "ai"])
    ap.add_argument("--tickers", default="", help="AI-läge: kommaseparerade symboler, t.ex. SOFI,NVDA")
    ap.add_argument("--prev", default="_prev")
    ap.add_argument("--out", default="_site")
    ap.add_argument("--limit", type=int, default=None, help="bara de första N aktierna (test)")
    a = ap.parse_args()
    prev, out = Path(a.prev), Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    has_prev = (prev / "data" / "universe.json").exists()
    if a.mode in ("site", "ai") and has_prev:
        shutil.copytree(prev / "data", out / "data")
        u = load_json(out / "data" / "universe.json", {})
        if u.get("rows") and not (u.get("fx") or {}).get("USD"):  # äldre data utan valutakurser: portföljen behöver dem
            u["fx"] = fx_sek({r.get("currency") for r in u["rows"]}) or u.get("fx")
            write_json(out / "data" / "universe.json", u)
        code = 0
        if a.mode == "ai":
            import ai_notes
            tk = [x.strip().upper() for x in a.tickers.replace(";", ",").split(",") if x.strip()]
            ai_notes.run(out, tk or None, read_list(SRC / "config" / "ai.txt"), max_n=12, log=log)
    elif a.mode == "quotes" and has_prev:
        code = run_quotes(prev, out)
    else:
        code = run_full(prev, out, a.limit)
        if code == 0:
            try:  # backtest av 200W-strategin på all kurshistorik
                import backtest
                write_json(out / "data" / "backtest.json", backtest.run(out / "data"))
            except Exception as e:  # noqa: BLE001
                log("backtest fel", e)
            import ai_notes
            ai_notes.run(out, None, read_list(SRC / "config" / "ai.txt"), max_n=12, log=log)
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
