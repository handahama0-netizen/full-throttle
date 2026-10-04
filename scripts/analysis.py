"""Systematisk fundamental analys: kursattribution, prognosrevideringar och värdering.

Alla beräkningar är deterministiska och bygger bara på data från Yahoo Finance.
Formlerna visas också på sidan under "Källor och metod".
"""
from __future__ import annotations

import math

ERP = 0.05            # marknadens riskpremie
TERMINAL_G = 0.025    # evig tillväxt efter år 10
SECTOR_ETF = {
    "Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Healthcare": "XLV", "Industrials": "XLI", "Financial Services": "XLF",
    "Basic Materials": "XLB", "Energy": "XLE", "Utilities": "XLU", "Real Estate": "XLRE",
}
BENCHMARKS = ["SPY", "^OMX", "^TNX"] + sorted(set(SECTOR_ETF.values()))


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _r(x, dp=4):
    x = _num(x)
    return None if x is None else round(x, dp)


# ---------------- kursattribution ----------------

def attribution(close, market, sector=None, windows=(5, 21, 63)):
    """Delar upp kursrörelsen i marknad (beta × index), sektor (sektor-ETF minus index) och bolagsspecifikt.

    close, market, sector: pandas Series med stängningskurser (index = datum).
    """
    import pandas as pd
    cols = {"s": close, "m": market}
    if sector is not None:
        cols["x"] = sector
    df = pd.concat(cols, axis=1).dropna()
    if len(df) < 80:
        return None
    rets = df.pct_change().dropna().tail(252)
    var = rets["m"].var()
    beta = float(rets["s"].cov(rets["m"]) / var) if var and var > 0 else 1.0
    beta = max(-1.0, min(3.5, beta))
    vol = float(rets["s"].std())
    out = {"beta": round(beta, 2), "vol": round(vol, 5), "w": []}
    for h in windows:
        if len(df) <= h:
            continue
        a, b = df.iloc[-1 - h], df.iloc[-1]
        rs, rm = b["s"] / a["s"] - 1, b["m"] / a["m"] - 1
        rx = (b["x"] / a["x"] - 1) if "x" in df else None
        mkt = beta * rm
        sec = (rx - rm) if rx is not None else 0.0
        idio = rs - mkt - sec
        z = idio / (vol * math.sqrt(h)) if vol > 0 else 0.0
        out["w"].append({"h": h, "from": str(df.index[-1 - h].date()), "r": _r(rs), "rm": _r(rm), "rx": _r(rx),
                         "mkt": _r(mkt), "sec": _r(sec), "idio": _r(idio), "z": _r(z, 2)})
    return out


# ---------------- prognoser ----------------

def _row(df, idx):
    if df is None or getattr(df, "empty", True) or idx not in df.index:
        return None
    return df.loc[idx]


def _get(row, *names):
    if row is None:
        return None
    low = {str(k).lower(): k for k in row.index}
    for n in names:
        k = low.get(n.lower())
        if k is not None:
            return _num(row[k])
    return None


def estimates(eps_trend, eps_rev, rev_est, eps_est, earn_hist):
    out = {"trend": None, "rev": {}, "eps": {}, "revisions": None, "hist": []}
    for per in ("+1y", "0y"):
        r = _row(eps_trend, per)
        if r is not None and _get(r, "current") is not None:
            out["trend"] = {"period": per, "cur": _get(r, "current"), "d7": _get(r, "7daysAgo"),
                            "d30": _get(r, "30daysAgo"), "d60": _get(r, "60daysAgo"), "d90": _get(r, "90daysAgo")}
            rv = _row(eps_rev, per)
            if rv is not None:
                out["revisions"] = {"up7": _get(rv, "upLast7days"), "up30": _get(rv, "upLast30days"),
                                    "down7": _get(rv, "downLast7Days", "downLast7days"),
                                    "down30": _get(rv, "downLast30days", "downLast30Days")}
            break
    for per in ("0y", "+1y"):
        r = _row(rev_est, per)
        if r is not None:
            out["rev"][per] = {"avg": _get(r, "avg"), "growth": _get(r, "growth"), "n": _get(r, "numberOfAnalysts")}
        r = _row(eps_est, per)
        if r is not None:
            out["eps"][per] = {"avg": _get(r, "avg"), "growth": _get(r, "growth"), "n": _get(r, "numberOfAnalysts")}
    if earn_hist is not None and not getattr(earn_hist, "empty", True):
        for idx, r in earn_hist.tail(4).iterrows():
            sp = _get(r, "surprisePercent")
            out["hist"].append({"q": str(idx)[:10], "est": _get(r, "epsEstimate"), "act": _get(r, "epsActual"),
                                "surp": sp})
    return out


def revision_pct(trend, days):
    """Ändring i vinstprognos (EPS) i procent jämfört med för `days` dagar sedan."""
    if not trend:
        return None
    cur, old = trend.get("cur"), trend.get({5: "d7", 21: "d30", 63: "d90"}.get(days, "d30"))
    if cur is None or old is None or old == 0:
        return None
    return (cur / old - 1) * (1 if old > 0 else -1)


# ---------------- värdering ----------------

def dcf_value(fcf0, g1, r, gt=TERMINAL_G, years=10):
    """Tvåstegs-DCF: g1 i år 1–5, linjär avtrappning till gt år 6–10, sedan evig tillväxt."""
    if fcf0 is None or fcf0 <= 0 or r <= gt:
        return None
    pv, f = 0.0, fcf0
    for y in range(1, years + 1):
        g = g1 if y <= 5 else g1 + (gt - g1) * (y - 5) / 5
        f *= 1 + g
        pv += f / (1 + r) ** y
    tv = f * (1 + gt) / (r - gt)
    return pv + tv / (1 + r) ** years


def implied_growth(target_ev, fcf0, r):
    """Omvänd DCF: vilken tillväxt år 1–5 som dagens kurs förutsätter."""
    if fcf0 is None or fcf0 <= 0 or target_ev is None or target_ev <= 0:
        return None
    lo, hi = -0.5, 1.5
    if dcf_value(fcf0, hi, r) < target_ev:
        return hi
    if dcf_value(fcf0, lo, r) > target_ev:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if dcf_value(fcf0, mid, r) < target_ev:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def valuation(*, fcf, sbc, cash, debt, mcap_trading, price, fx_fin_to_trading, beta, rf, rev_growth_est,
              rev_cagr, eps_fwd, eps_growth_est, aaa):
    """Värde per aktie i handelsvalutan. Använder börsvärdet för att räkna per aktie (fungerar för ADR:er)."""
    out = {"rf": _r(rf), "erp": ERP, "gt": TERMINAL_G}
    b = max(0.6, min(2.0, beta if beta is not None else 1.0))
    r = max(0.07, min(0.14, rf + b * ERP))
    out.update({"beta": round(b, 2), "r": _r(r)})
    gs = [g for g in (rev_growth_est, (rev_cagr / 100 if rev_cagr is not None else None)) if g is not None]
    g1 = max(-0.05, min(0.30, sum(gs) / len(gs))) if gs else 0.05
    out["g1"] = _r(g1)
    owner_fcf = None
    if fcf is not None:
        owner_fcf = fcf - (sbc or 0)
    out.update({"fcf": fcf, "sbc": sbc, "ownerFcf": owner_fcf, "cash": cash, "debt": debt})
    net_cash = (cash or 0) - (debt or 0)
    out["dcf"] = None
    out["impliedG"] = None
    if owner_fcf and owner_fcf > 0 and mcap_trading and price and fx_fin_to_trading:
        mcap_fin = mcap_trading / fx_fin_to_trading
        res = {}
        for name, g, rr in (("bear", g1 * 0.5, r + 0.01), ("base", g1, r), ("bull", min(0.40, g1 * 1.4 if g1 > 0 else g1 + 0.05), r - 0.01)):
            ev = dcf_value(owner_fcf, g, rr)
            if ev is None:
                continue
            eq = ev + net_cash
            res[name] = _r(price * eq / mcap_fin, 4) if eq > 0 else 0.0
        out["dcf"] = res or None
        ig = implied_growth(mcap_fin - net_cash, owner_fcf, r)
        out["impliedG"] = _r(ig)
    out["graham"] = None
    if eps_fwd and eps_fwd > 0:
        g = eps_growth_est * 100 if eps_growth_est is not None else (rev_cagr if rev_cagr is not None else 5)
        g = max(0.0, min(25.0, g))
        out["graham"] = _r(eps_fwd * (8.5 + 2 * g) * 4.4 / max(aaa, 3.0), 4)
        out["grahamG"] = _r(g, 2)
        out["aaa"] = _r(aaa, 2)
    return out
