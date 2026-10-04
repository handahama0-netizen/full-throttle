"""Hämtar nyckeltal och veckokurser för bolagen i tickers.json.

Skriver:
  data.json     nyckeltal, 200-veckorssnitt, zon och händelselogg (läses av sidan)
  history.json  veckokurser senaste ~6 åren (för diagrammet i sidan)
  alerts.md     bara om nya signaler uppstått, används för mejllarm via GitHub-issue

Körs automatiskt av GitHub Actions men går att köra lokalt:
  pip install -r requirements.txt && python scripts/update.py

Datakälla: Yahoo Finance via yfinance (gratis, ingen API-nyckel).
Om ett bolag inte går att hämta behålls förra körningens siffror för just det bolaget.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TICKERS_FILE = ROOT / "tickers.json"
DATA_FILE = ROOT / "data.json"
HISTORY_FILE = ROOT / "history.json"
ALERTS_FILE = ROOT / "alerts.md"
MIN_SUCCESS_SHARE = 0.5
MA_WEEKS = 200
HISTORY_WEEKS = 360
MAX_EVENTS = 120

# Zoner enligt Universal Value Zones (avstånd över 200W-snittet i %)
ZONES = [  # (övre gräns, kod, namn)
    (0, "fire", "Fire Sale"),
    (10, "vcheap", "Very Cheap"),
    (20, "cheap", "Cheap"),
    (30, "fair", "Fair Value"),
    (40, "exp", "Expensive"),
    (float("inf"), "vexp", "Very Expensive"),
]

# Standardkriterier (samma som sidans förval). Används för händelser och mejllarm.
LRHR_CAGR = (1.5 ** (1 / 5) - 1) * 100  # >50 % på 5 år ≈ 8,45 %/år


def passes_ft_plus(d):
    return all([
        _ge(d.get("revCagr"), 15), _ge(d.get("revGrowth1y"), 20),
        _between(d.get("de"), 0, 1) and not d.get("negEquity"),
        _gt(d.get("peg"), 0) and _le(d.get("peg"), 1.5),
        _ge(d.get("fcfMargin"), 5), _ge(d.get("roic"), 10), _ge(d.get("grossMargin"), 35),
        d.get("shareChange") is None or d["shareChange"] <= 5,
        _ge(d.get("mcapB"), 2),
    ])


def passes_lrhr(d):
    return all([
        _gt(d.get("revCagr"), LRHR_CAGR), _gt(d.get("revGrowth1y"), 5), _gt(d.get("earnGrowth1y"), 5),
        _gt(d.get("roe"), 15),
        _ge(d.get("de"), 0) and _lt(d.get("de"), 1) and not d.get("negEquity"),
        _gt(d.get("fcfMargin"), 0),
        _gt(d.get("peg"), 0) and _lt(d.get("peg"), 1),
        _ge(d.get("mcapB"), 10),
    ])


def signals(d):
    dist = d.get("dist200w")
    out = []
    if dist is not None:
        if passes_lrhr(d) and dist < 20:
            out.append("swing")
        if passes_ft_plus(d) and dist < 30:
            out.append("rea")
    return out


def _gt(x, v): return x is not None and x > v
def _ge(x, v): return x is not None and x >= v
def _lt(x, v): return x is not None and x < v
def _le(x, v): return x is not None and x <= v
def _between(x, a, b): return x is not None and a <= x <= b


# ---------- hjälpfunktioner (rena, testbara) ----------

def num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def rnd(x, dp=2):
    x = num(x)
    return None if x is None else round(x, dp)


def row(df, *names):
    """Första raden som finns i en yfinance-tabell, som lista nyast→äldst."""
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


def revenue_cagr(annual_rev):
    pts = [v for v in (annual_rev or []) if v is not None and v > 0]
    if len(pts) < 3:
        return None, None
    years = len(pts) - 1
    return round(((pts[0] / pts[-1]) ** (1 / years) - 1) * 100, 1), years


def ttm_growth(quarterly, annual):
    """Tillväxt senaste 12 mån mot 12 mån innan (%). Faller tillbaka på senaste helår."""
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


def ma_and_zone(closes, price):
    """200-veckorssnitt, avstånd i % och zon. closes = äldst→nyast."""
    c = [v for v in closes if v is not None]
    if len(c) < MA_WEEKS or not price:
        return None, None, None
    ma = sum(c[-MA_WEEKS:]) / MA_WEEKS
    dist = (price / ma - 1) * 100
    zone = next(code for lim, code, _ in ZONES if dist < lim)
    return round(ma, 4), round(dist, 2), zone


def zone_name(code):
    return next((n for _, c, n in ZONES if c == code), code)


# ---------- hämtning ----------

_fx_cache: dict[str, float] = {}


def usd_rate(currency):
    """Kurs för att räkna om valuta → USD (för börsvärde)."""
    if not currency or currency.upper() == "USD":
        return 1.0
    cur = currency.upper()
    if cur in _fx_cache:
        return _fx_cache[cur]
    import yfinance as yf
    rate = None
    try:
        h = yf.Ticker(f"{cur}USD=X").history(period="5d")
        rate = num(h["Close"].dropna().iloc[-1]) if not h.empty else None
    except Exception:  # noqa: BLE001
        rate = None
    _fx_cache[cur] = rate
    return rate


def fetch(entry: dict) -> tuple[dict, dict | None]:
    import yfinance as yf

    t = yf.Ticker(entry["ticker"])
    info = t.info or {}
    inc_a, inc_q, bal = t.income_stmt, t.quarterly_income_stmt, t.balance_sheet

    rev_a = row(inc_a, "Total Revenue", "Operating Revenue")
    rev_q = row(inc_q, "Total Revenue", "Operating Revenue")
    eps_a = row(inc_a, "Diluted EPS", "Basic EPS") or row(inc_a, "Net Income")
    eps_q = row(inc_q, "Diluted EPS", "Basic EPS") or row(inc_q, "Net Income")
    cagr, years = (None, None) if entry.get("skipCagr") else revenue_cagr(rev_a)

    debt = first(row(bal, "Total Debt"))
    equity = first(row(bal, "Stockholders Equity", "Common Stock Equity"))
    cash = first(row(bal, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    de, neg = debt_to_equity(debt, equity)

    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    currency = info.get("currency") or "USD"
    mcap = num(info.get("marketCap"))
    rate = usd_rate(currency)
    mcap_usd = mcap * rate if mcap and rate else None
    fcf = num(info.get("freeCashflow"))
    total_rev = num(info.get("totalRevenue"))
    gm = num(info.get("grossMargins"))
    peg = num(info.get("trailingPegRatio")) or num(info.get("pegRatio"))
    roe_info = num(info.get("returnOnEquity"))
    ni = first(row(inc_a, "Net Income"))
    roe = roe_info * 100 if roe_info is not None else (ni / equity * 100 if ni is not None and equity and equity > 0 else None)

    # Veckokurser för 200W
    hist = None
    closes = []
    try:
        h = t.history(period="8y", interval="1wk", auto_adjust=True)
        if h is not None and not h.empty:
            s = h["Close"].dropna()
            closes = [round(float(v), 4) for v in s.tolist()]
            dates = [d.strftime("%Y-%m-%d") for d in s.index]
            hist = {"s": dates[-HISTORY_WEEKS:][0], "c": [round(v, 2) for v in closes[-HISTORY_WEEKS:]], "cur": currency}
    except Exception:  # noqa: BLE001
        hist = None
    if price is None and closes:
        price = closes[-1]
    ma, dist, zone = ma_and_zone(closes, price)

    rec = {
        "ticker": entry["ticker"], "name": entry["name"], "sector": entry["sector"], "group": entry["group"],
        "note": entry.get("note"), "currency": currency,
        "price": rnd(price), "mcapB": rnd(mcap_usd / 1e9, 2) if mcap_usd else None,
        "revCagr": cagr, "revCagrYears": years, "revGrowth1y": ttm_growth(rev_q, rev_a),
        "earnGrowth1y": ttm_growth(eps_q, eps_a), "roe": rnd(roe),
        "peg": rnd(peg), "de": de, "negEquity": neg,
        "roic": roic(first(row(inc_a, "EBIT", "Operating Income")), first(row(inc_a, "Tax Rate For Calcs")), debt, equity, cash),
        "grossMargin": rnd(gm * 100) if gm is not None else None,
        "fcfMargin": rnd(fcf / total_rev * 100) if fcf is not None and total_rev else None,
        "fwdPe": rnd(info.get("forwardPE")),
        "shareChange": share_change(row(bal, "Ordinary Shares Number", "Share Issued")),
        "ma200w": ma, "dist200w": dist, "zone": zone,
    }
    if rec["price"] is None and rec["revGrowth1y"] is None:
        raise ValueError("ingen data")
    return rec, hist


def merge(new: dict, old: dict | None) -> dict:
    """Behåll gamla värden där nya saknas."""
    if not old:
        return new
    out = dict(new)
    for k, v in old.items():
        if out.get(k) is None and v is not None and k not in ("note", "signals", "stale"):
            out[k] = v
    return out


def diff_events(old: dict | None, new: dict, today: str) -> list[dict]:
    ev = []
    tk = new["ticker"]
    if old and old.get("zone") and new.get("zone") and old["zone"] != new["zone"]:
        ev.append({"date": today, "ticker": tk, "type": "zone", "from": old["zone"], "to": new["zone"],
                   "text": f"{zone_name(old['zone'])} → {zone_name(new['zone'])}"})
    before, after = set((old or {}).get("signals") or []), set(new.get("signals") or [])
    label = {"swing": "Swing-läge", "rea": "Tillväxt på rea"}
    for s in sorted(after - before):
        ev.append({"date": today, "ticker": tk, "type": "in", "signal": s, "text": f"Ny signal: {label[s]}"})
    for s in sorted(before - after):
        if old is not None:
            ev.append({"date": today, "ticker": tk, "type": "out", "signal": s, "text": f"Signal borta: {label[s]}"})
    return ev


def alerts_markdown(new_signals: list[dict], today: str) -> str:
    lines = [f"Nya signaler {today}:", ""]
    label = {"swing": "Swing-läge (Low Risk, High Reward + 200W)", "rea": "Tillväxt på rea (Full Throttle+ + 200W)"}
    for e in new_signals:
        d = e["rec"]
        lines.append(f"- **{d['ticker']}** {d['name']}: {label[e['signal']]}. "
                     f"Kurs {d.get('price')} {d.get('currency','')}, {d.get('dist200w')} % över 200W ({zone_name(d.get('zone'))}).")
    lines += ["", "Öppna sidan för detaljer."]
    return "\n".join(lines)


def main() -> int:
    universe = json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
    prev_doc = {}
    if DATA_FILE.exists():
        try:
            prev_doc = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev_doc = {}
    previous = {s["ticker"]: s for s in prev_doc.get("stocks", [])}
    first_run_with_signals = not any("signals" in s for s in previous.values())
    prev_hist = {}
    if HISTORY_FILE.exists():
        try:
            prev_hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev_hist = {}

    today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
    out, hist_out, events, new_sig = [], {}, [], []
    ok, failed = 0, []
    for entry in universe:
        tk = entry["ticker"]
        rec = hist = None
        err = None
        for _ in range(2):
            try:
                rec, hist = fetch(entry)
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2)
        if rec is None:
            failed.append(f"{tk} ({err})")
            if tk in previous:
                out.append({**previous[tk], "stale": True})
            if tk in prev_hist:
                hist_out[tk] = prev_hist[tk]
            continue
        old = previous.get(tk)
        rec = merge(rec, old)
        rec["signals"] = signals(rec)
        rec["asOf"] = today
        rec["stale"] = False
        if not first_run_with_signals:
            evs = diff_events(old, rec, today)
            events += evs
            new_sig += [{"rec": rec, "signal": e["signal"]} for e in evs if e["type"] == "in"]
        out.append(rec)
        hist_out[tk] = hist or prev_hist.get(tk)
        ok += 1
        time.sleep(0.4)

    print(f"Uppdaterade {ok}/{len(universe)} bolag.")
    if failed:
        print("Misslyckades:", ", ".join(failed))
    if ok < len(universe) * MIN_SUCCESS_SHARE:
        print("För få bolag lyckades – behåller förra datan.")
        return 1

    all_events = (events + prev_doc.get("events", []))[:MAX_EVENTS]
    DATA_FILE.write_text(json.dumps({
        "asOf": today,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance",
        "events": all_events,
        "stocks": out,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps({k: v for k, v in hist_out.items() if v}, separators=(",", ":")), encoding="utf-8")

    if new_sig:
        ALERTS_FILE.write_text(alerts_markdown(new_sig, today), encoding="utf-8")
        print(f"{len(new_sig)} nya signaler – alerts.md skriven.")
    elif ALERTS_FILE.exists():
        ALERTS_FILE.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
