"""Backtest av 200W-strategin på veckokurser.

Testar köpregler mot 200-veckorssnittet för olika urval av aktier (alla, Low Risk High Reward,
Guld = LRHR + stora och dominerande bolag) och räknar ut affärsstatistik, även med 2x hävstång.

Viktigt om metoden: urvalet görs med DAGENS nyckeltal (Yahoo har ingen historik per datum),
så kvalitetsfiltret har facit i hand och dagens vinnare är överrepresenterade (överlevnadsbias).
Själva köp- och säljreglerna mot 200W är däremot testade utan att titta framåt.

  python scripts/backtest.py --data _site/data
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

MA = 200
FIN_COST = 0.05      # årlig ränta på lånade pengar vid hävstång
LEV = 2.0
MIN_TRADES = 5
START = (date(2015, 1, 1) - date(1970, 1, 1)).days  # backtestet börjar 2015 (när 200W finns)

GOLD_MCAP = 200.0    # mdr USD = megabolag
GOLD_LEADER_MCAP = 50.0
GOLD_BETA = 1.3


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def fname(tk):
    return tk.replace("^", "_").replace("/", "_") + ".json"


# ---------------- urval ----------------

def lrhr(d):
    g = lambda k: d.get(k)  # noqa: E731
    cagr = (1.5 ** (1 / 5) - 1) * 100
    try:
        return (not d.get("excluded") and g("revCagr") is not None and g("revCagr") > cagr
                and (g("revGrowth1y") or -1) > 5 and (g("earnGrowth1y") or -1) > 5 and (g("roe") or -1) > 15
                and g("de") is not None and 0 <= g("de") < 1 and not d.get("negEquity")
                and (g("fcfMargin") or -1) > 0 and g("peg") is not None and 0 < g("peg") < 1
                and (g("mcapB") or 0) >= 10)
    except TypeError:
        return False


def leaders(rows):
    """Störst i sin bransch efter omsättning (börsvärde / P/S) bland sidans aktier."""
    by = {}
    for d in rows:
        ind = d.get("industry")
        if not ind or d.get("type", "EQUITY") != "EQUITY":
            continue
        rev = d["mcapB"] / d["ps"] if d.get("mcapB") and d.get("ps") and d["ps"] > 0 else None
        if rev:
            by.setdefault(ind, []).append((rev, d["ticker"]))
    out = {}
    for ind, lst in by.items():
        lst.sort(reverse=True)
        for i, (_, t) in enumerate(lst[:2]):
            out[t] = i + 1
    return out


def gold_quality(d, lead):
    """Guld utan prisvillkor: stort och dominerande, stabilt, lönsamt och växande.

    LRHR:s PEG < 1 släpper nästan aldrig igenom megabolag, så Guld mäter kvalitet med avkastning på
    kapitalet och kassaflöde i stället."""
    if d.get("excluded"):
        return False
    g = lambda k: d.get(k)  # noqa: E731
    big = (g("mcapB") or 0) >= GOLD_MCAP or bool(lead.get(d["ticker"]) and (g("mcapB") or 0) >= GOLD_LEADER_MCAP)
    stable = g("beta") is None or g("beta") <= GOLD_BETA
    ret = max(g("roic") or -99, g("roe") or -99)
    quality = ret >= 15 and (g("fcfMargin") or -1) >= 10 and (g("revGrowth1y") or -99) >= 3
    debt = (not d.get("negEquity") and g("de") is not None and g("de") < 1.5) or (d.get("negEquity") and (g("roic") or 0) >= 20)
    return bool(big and stable and quality and debt)


def mega(d):
    return not d.get("excluded") and (d.get("mcapB") or 0) >= GOLD_MCAP


# ---------------- regler ----------------
# varje regel: entry(dist, prev_dist, close, prev_close) och exit(dist, ret, weeks)

RULES = {
    "orig": {"name": "Original: köp under Cheap (< 20 % över 200W), sälj vid Expensive (40 %)",
             "entry": lambda s: s["dist"] < 20, "exit": lambda s, t: s["dist"] >= 40},
    "deep": {"name": "Djupare köp: under Very Cheap (< 10 %), sälj vid Expensive",
             "entry": lambda s: s["dist"] < 10, "exit": lambda s, t: s["dist"] >= 40},
    "turn": {"name": "Under Cheap + vändning (veckan stänger över förra veckans stängning)",
             "entry": lambda s: s["dist"] < 20 and s["c"] > s["pc"] and s["pc"] < s["ppc"],
             "exit": lambda s, t: s["dist"] >= 40},
    "fair": {"name": "Under Cheap, sälj redan vid Fair Value (30 %)",
             "entry": lambda s: s["dist"] < 20, "exit": lambda s, t: s["dist"] >= 30},
    "stop": {"name": "Under Cheap, sälj vid Expensive eller −20 % stop",
             "entry": lambda s: s["dist"] < 20, "exit": lambda s, t: s["dist"] >= 40 or t["ret"] <= -0.20},
    "fairstop": {"name": "Under Cheap, sälj vid Fair Value (30 %) eller −20 % stop",
                 "entry": lambda s: s["dist"] < 20, "exit": lambda s, t: s["dist"] >= 30 or t["ret"] <= -0.20},
    "fire": {"name": "Diamant (med LRHR): köp i Fire Sale under 200W, sälj vid Expensive (40 %)",
             "entry": lambda s: s["dist"] < 0, "exit": lambda s, t: s["dist"] >= 40},
    "firefair": {"name": "Bara Fire Sale: köp under 200W, sälj vid Fair Value (30 %)",
                 "entry": lambda s: s["dist"] < 0, "exit": lambda s, t: s["dist"] >= 30},
    "firestop": {"name": "Bara Fire Sale, sälj vid Expensive eller −20 % stop",
                 "entry": lambda s: s["dist"] < 0, "exit": lambda s, t: s["dist"] >= 40 or t["ret"] <= -0.20},
}


def series(det):
    w = (det or {}).get("w") or {}
    t, c = w.get("t") or [], w.get("c") or []
    return [(a, b) for a, b in zip(t, c) if b]


def simulate(pts, rule):
    """Affärer på en aktie. pts = [(daycode, close)] per vecka."""
    if len(pts) < MA + 10:
        return []
    closes = [c for _, c in pts]
    pre = [0.0]
    for c in closes:
        pre.append(pre[-1] + c)
    trades, pos = [], None
    entry, ex = RULES[rule]["entry"], RULES[rule]["exit"]
    for i in range(MA + 1, len(pts)):
        ma = (pre[i + 1] - pre[i + 1 - MA]) / MA
        st = {"dist": (closes[i] / ma - 1) * 100, "c": closes[i], "pc": closes[i - 1], "ppc": closes[i - 2]}
        if pos is None:
            if pts[i][0] >= START and entry(st):
                pos = {"i": i, "p": closes[i], "low": closes[i], "lev": 1.0, "levlow": 1.0}
        else:
            r = closes[i] / closes[i - 1] - 1
            pos["low"] = min(pos["low"], closes[i])
            pos["lev"] *= max(0.0, 1 + LEV * r - (LEV - 1) * FIN_COST / 52)
            pos["levlow"] = min(pos["levlow"], pos["lev"])
            tr = {"ret": closes[i] / pos["p"] - 1}
            if ex(st, tr):
                trades.append(_close(pos, i, closes[i], pts, True))
                pos = None
    if pos is not None:
        trades.append(_close(pos, len(pts) - 1, closes[-1], pts, False))
    return trades


def _close(pos, i, price, pts, done):
    weeks = i - pos["i"]
    return {"in": pts[pos["i"]][0], "out": pts[i][0], "w": weeks, "r": price / pos["p"] - 1,
            "dd": pos["low"] / pos["p"] - 1, "lr": pos["lev"] - 1, "ldd": pos["levlow"] - 1, "done": done}


def portfolio(members, rule, data, spy):
    """Portfölj: lika vikt i alla aktier som just nu har en öppen affär, annars kontanter (0 %).

    Ger avkastning per år, max drawdown och Sharpe för hela strategin över tid, inte bara per affär."""
    days = [d for d, _ in spy]
    if len(days) <= MA:
        return None
    idx = {d: i for i, d in enumerate(days)}
    n = len(days)
    rets = [[] for _ in range(n)]
    for r in members:
        pts = series(load(data / "t" / fname(r["ticker"])))
        if len(pts) < MA + 10:
            continue
        for t in simulate(pts, rule):
            # veckoavkastning för varje vecka affären var öppen
            seg = [(d, c) for d, c in pts if t["in"] <= d <= t["out"]]
            for (d0, c0), (d1, c1) in zip(seg, seg[1:]):
                i = idx.get(d1)
                if i is None:
                    i = min(range(n), key=lambda k: abs(days[k] - d1)) if abs(d1 - days[-1]) < 400 else None
                if i is not None and c0:
                    rets[i].append(c1 / c0 - 1)
    start = max(MA + 1, next((i for i, d in enumerate(days) if d >= START), MA + 1))
    eq, eq2, peak, peak2, dd, dd2, wk, inv = 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, [], 0
    for i in range(start, n):
        r = sum(rets[i]) / len(rets[i]) if rets[i] else 0.0
        inv += 1 if rets[i] else 0
        wk.append(r)
        eq *= 1 + r
        eq2 *= max(0.0, 1 + LEV * r - ((LEV - 1) * FIN_COST / 52 if rets[i] else 0))
        peak, peak2 = max(peak, eq), max(peak2, eq2)
        dd, dd2 = min(dd, eq / peak - 1), min(dd2, eq2 / peak2 - 1)
    yrs = (n - start) / 52
    m = sum(wk) / len(wk)
    sd = (sum((x - m) ** 2 for x in wk) / len(wk)) ** 0.5
    sw = [spy[i][1] / spy[i - 1][1] - 1 for i in range(start, n)]
    sm = sum(sw) / len(sw)
    ssd = (sum((x - sm) ** 2 for x in sw) / len(sw)) ** 0.5
    speak, sdd, se = 1.0, 0.0, 1.0
    for x in sw:
        se *= 1 + x
        speak = max(speak, se)
        sdd = min(sdd, se / speak - 1)
    return {"cagr": round((eq ** (1 / yrs) - 1) * 100, 1), "maxdd": round(dd * 100, 1),
            "sharpe": round((m * 52 - 0.03) / (sd * 52 ** 0.5), 2) if sd else None,
            "cagr2": round((eq2 ** (1 / yrs) - 1) * 100, 1) if eq2 > 0 else -100.0, "maxdd2": round(dd2 * 100, 1),
            "invested": round(inv / (n - start) * 100, 0), "years": round(yrs, 1),
            "spy": {"cagr": round((se ** (1 / yrs) - 1) * 100, 1), "maxdd": round(sdd * 100, 1),
                    "sharpe": round((sm * 52 - 0.03) / (ssd * 52 ** 0.5), 2)}}


def stats(trades, bench_cagr):
    if len(trades) < MIN_TRADES:
        return None
    rs = sorted(t["r"] for t in trades)
    n = len(rs)
    yrs = [max(t["w"], 1) / 52 for t in trades]
    ann = [(1 + t["r"]) ** (1 / y) - 1 if t["r"] > -1 else -1 for t, y in zip(trades, yrs)]
    lann = [(1 + t["lr"]) ** (1 / y) - 1 if t["lr"] > -1 else -1 for t, y in zip(trades, yrs)]
    med = lambda a: sorted(a)[len(a) // 2]  # noqa: E731
    return {
        "n": n, "open": sum(1 for t in trades if not t["done"]),
        "win": round(sum(1 for r in rs if r > 0) / n * 100, 1),
        "avg": round(sum(rs) / n * 100, 1), "med": round(med(rs) * 100, 1),
        "ann": round(med(ann) * 100, 1), "worst": round(rs[0] * 100, 1),
        "weeks": round(sum(t["w"] for t in trades) / n, 0),
        "dd": round(sum(t["dd"] for t in trades) / n * 100, 1), "ddWorst": round(min(t["dd"] for t in trades) * 100, 1),
        "dd30": round(sum(1 for t in trades if t["dd"] <= -0.3) / n * 100, 1),
        "lev": {"avg": round(sum(t["lr"] for t in trades) / n * 100, 1), "ann": round(med(lann) * 100, 1),
                "ddWorst": round(min(t["ldd"] for t in trades) * 100, 1),
                "wiped": sum(1 for t in trades if t["ldd"] <= -0.5)},
        "beatSpy": round(sum(1 for a in ann if bench_cagr is not None and a > bench_cagr) / n * 100, 1),
    }


def run(data: Path):
    u = load(data / "universe.json", {})
    rows = [r for r in u.get("rows", []) if r.get("type", "EQUITY") == "EQUITY"]
    lead = leaders(rows)
    groups = {
        "all": ("Alla aktier (utom uteslutna sektorer)", [r for r in rows if not r.get("excluded")]),
        "lrhr": ("Low Risk, High Reward (dagens nyckeltal)", [r for r in rows if lrhr(r)]),
        "mega": ("Megabolag ≥ 200 mdr $", [r for r in rows if mega(r)]),
        "gold": ("Guld: mega eller branschledare, beta ≤ 1,3, ROIC/ROE ≥ 15 %, FCF ≥ 10 %, växer", [r for r in rows if gold_quality(r, lead)]),
    }
    spy = series(load(data / "t" / "SPY.json"))
    bench, s0 = None, MA
    if len(spy) > MA:
        s0 = max(MA, next((i for i, (d, _) in enumerate(spy) if d >= START), MA))
        a, b = spy[s0][1], spy[-1][1]
        bench = (b / a) ** (52 / (len(spy) - 1 - s0)) - 1
    cache = {}
    out = {"asOf": u.get("asOf"), "spyCagr": round(bench * 100, 1) if bench is not None else None,
           "from": (date(1970, 1, 1) + timedelta(days=spy[s0][0])).isoformat() if len(spy) > MA else None,
           "rules": {k: v["name"] for k, v in RULES.items()},
           "groups": {k: {"name": v[0], "n": len(v[1]), "tickers": [r["ticker"] for r in v[1]][:80]} for k, v in groups.items()},
           "res": {}, "trades": {}}
    for gk, (_, members) in groups.items():
        for rk in RULES:
            allt = []
            for r in members:
                key = (r["ticker"], rk)
                if key not in cache:
                    cache[key] = simulate(series(load(data / "t" / fname(r["ticker"]))), rk)
                for t in cache[key]:
                    allt.append({**t, "t": r["ticker"]})
            out["res"][f"{gk}|{rk}"] = stats(allt, bench)
            if rk == "orig" and gk in ("gold", "lrhr"):
                allt.sort(key=lambda t: t["in"], reverse=True)
                out["trades"][gk] = [{"t": t["t"], "in": t["in"], "out": t["out"], "r": round(t["r"] * 100, 1),
                                      "dd": round(t["dd"] * 100, 1), "lr": round(t["lr"] * 100, 1), "done": t["done"]}
                                     for t in allt[:40]]
    out["port"] = {}
    for gk in ("lrhr", "gold", "mega"):
        for rk in ("orig", "fire", "firefair", "fairstop"):
            out["port"][f"{gk}|{rk}"] = portfolio(groups[gk][1], rk, data, spy)
    # bästa regeln per urval (median årstakt, kräver minst 10 affärer)
    out["best"] = {}
    for gk in groups:
        cand = [(out["res"][f"{gk}|{rk}"]["ann"], rk) for rk in RULES
                if out["res"].get(f"{gk}|{rk}") and out["res"][f"{gk}|{rk}"]["n"] >= 10]
        if cand:
            out["best"][gk] = max(cand)[1]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="_site/data")
    a = ap.parse_args()
    res = run(Path(a.data))
    (Path(a.data) / "backtest.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for k, v in res["res"].items():
        if v:
            print(f"{k:12s} n={v['n']:4d} win={v['win']:5.1f}% med={v['med']:6.1f}% ann={v['ann']:6.1f}% "
                  f"dd={v['dd']:6.1f}% ddW={v['ddWorst']:6.1f}% worst={v['worst']:6.1f}% 2x ann={v['lev']['ann']:6.1f}% 2x ddW={v['lev']['ddWorst']:6.1f}% "
                  f"wiped={v['lev']['wiped']} beatSPY={v['beatSpy']}% wk={v['weeks']}")
    print("SPY CAGR", res["spyCagr"], "från", res["from"], {k: v["n"] for k, v in res["groups"].items()})
    print("Guld:", res["groups"]["gold"]["tickers"])
    for k, v in res["port"].items():
        print("PORT", k, v)


if __name__ == "__main__":
    math  # noqa: B018
    main()
