"""Hämtar nyckeltal för bolagen i tickers.json och skriver data.json.

Körs automatiskt av GitHub Actions (se .github/workflows/update.yml),
men går också att köra lokalt:  pip install -r requirements.txt && python scripts/update.py

Datakälla: Yahoo Finance via biblioteket yfinance (gratis, ingen API-nyckel).
Om ett bolag inte går att hämta behålls förra dagens siffror för just det bolaget.
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
MIN_SUCCESS_SHARE = 0.5  # skriv inte över datan om färre än hälften lyckas


# ---------- hjälpfunktioner (rena, testbara) ----------

def num(x):
    """Gör om till float, eller None om värdet saknas/är ogiltigt."""
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
            vals = [num(v) for v in df.loc[n].tolist()]
            return vals
    return None


def first(vals):
    if not vals:
        return None
    for v in vals:
        if v is not None:
            return v
    return None


def revenue_cagr(annual_rev):
    """Årlig tillväxt (%) mellan äldsta och senaste helår. annual_rev = nyast→äldst."""
    if not annual_rev:
        return None, None
    pts = [v for v in annual_rev if v is not None and v > 0]
    if len(pts) < 3:
        return None, None
    latest, oldest = pts[0], pts[-1]
    years = len(pts) - 1
    return round(((latest / oldest) ** (1 / years) - 1) * 100, 1), years


def ttm_growth(quarterly_rev, annual_rev):
    """Omsättningstillväxt senaste 12 mån (%). Faller tillbaka på senaste helår."""
    q = [v for v in (quarterly_rev or []) if v is not None]
    if len(q) >= 8:
        now, before = sum(q[:4]), sum(q[4:8])
        if before > 0:
            return round((now / before - 1) * 100, 2)
    a = [v for v in (annual_rev or []) if v is not None]
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
    """Returnerar (D/E, negativt_eget_kapital)."""
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


# ---------- hämtning ----------

def fetch(entry: dict) -> dict:
    import yfinance as yf

    t = yf.Ticker(entry["ticker"])
    info = t.info or {}
    inc_a = t.income_stmt
    inc_q = t.quarterly_income_stmt
    bal = t.balance_sheet

    rev_a = row(inc_a, "Total Revenue", "Operating Revenue")
    rev_q = row(inc_q, "Total Revenue", "Operating Revenue")
    cagr, years = (None, None) if entry.get("skipCagr") else revenue_cagr(rev_a)

    debt = first(row(bal, "Total Debt"))
    equity = first(row(bal, "Stockholders Equity", "Common Stock Equity"))
    cash = first(row(bal, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    de, neg = debt_to_equity(debt, equity)

    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    mcap = num(info.get("marketCap"))
    fcf = num(info.get("freeCashflow"))
    total_rev = num(info.get("totalRevenue"))
    gm = num(info.get("grossMargins"))
    peg = num(info.get("trailingPegRatio")) or num(info.get("pegRatio"))

    rec = {
        "ticker": entry["ticker"],
        "name": entry["name"],
        "sector": entry["sector"],
        "group": entry["group"],
        "note": entry.get("note"),
        "price": rnd(price),
        "mcapB": rnd(mcap / 1e9, 2) if mcap else None,
        "revCagr": cagr,
        "revCagrYears": years,
        "revGrowth1y": ttm_growth(rev_q, rev_a),
        "peg": rnd(peg),
        "de": de,
        "negEquity": neg,
        "roic": roic(first(row(inc_a, "EBIT", "Operating Income")),
                     first(row(inc_a, "Tax Rate For Calcs")), debt, equity, cash),
        "grossMargin": rnd(gm * 100, 2) if gm is not None else None,
        "fcfMargin": rnd(fcf / total_rev * 100, 2) if fcf is not None and total_rev else None,
        "fwdPe": rnd(info.get("forwardPE")),
        "shareChange": share_change(row(bal, "Ordinary Shares Number", "Share Issued")),
    }
    if rec["price"] is None and rec["revGrowth1y"] is None:
        raise ValueError("ingen data")
    return rec


def merge(new: dict, old: dict | None) -> dict:
    """Behåll gamla värden där nya saknas, så ett glapp hos källan inte nollar datan."""
    if not old:
        return new
    out = dict(new)
    for k, v in old.items():
        if out.get(k) is None and v is not None and k not in ("note",):
            out[k] = v
    return out


def main() -> int:
    universe = json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
    previous = {}
    if DATA_FILE.exists():
        try:
            previous = {s["ticker"]: s for s in json.loads(DATA_FILE.read_text(encoding="utf-8")).get("stocks", [])}
        except Exception:
            previous = {}

    today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
    out, ok, failed = [], 0, []
    for entry in universe:
        tk = entry["ticker"]
        rec = None
        for attempt in range(2):
            try:
                rec = fetch(entry)
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2)
        if rec is None:
            failed.append(f"{tk} ({err})")
            if tk in previous:
                out.append({**previous[tk], "stale": True})
            continue
        rec = merge(rec, previous.get(tk))
        rec["asOf"] = today
        rec["stale"] = False
        out.append(rec)
        ok += 1
        time.sleep(0.5)

    print(f"Uppdaterade {ok}/{len(universe)} bolag.")
    if failed:
        print("Misslyckades:", ", ".join(failed))
    if ok < len(universe) * MIN_SUCCESS_SHARE:
        print("För få bolag lyckades – behåller förra datan.")
        return 1

    DATA_FILE.write_text(json.dumps({
        "asOf": today,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance",
        "stocks": out,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
