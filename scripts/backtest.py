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
COST = 0.001         # courtage + spread per affär och håll (0,1 %)
MC_RATIO = 0.30      # margin call när eget kapital < 30 % av värdet (2x hävstång: när aktien fallit ca 29 %)
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


# ---------------- urval vid varje tidpunkt (point-in-time) ----------------
# Bara det som fanns att veta den veckan: rapporterad vinst per kvartal (från rapportdagen), kursen,
# börsvärdet (dagens antal aktier × kursen då) och beta mot S&P 500. Storleksgränserna skalas med
# S&P 500:s nivå, så att 200 md $ i dag motsvarar ett lika exklusivt urval 2015.

def _day(iso):
    return (date.fromisoformat(iso[:10]) - date(1970, 1, 1)).days


def pit_info(row, det, spy_map):
    """Per vecka: börsvärde (md $, nivåjusterat), vinst 12 mån, vinsttillväxt, P/E, PEG bakåt och beta 2 år."""
    pts = series(det)
    if len(pts) < MA + 10 or not row.get("mcapB") or not pts[-1][1]:
        return None
    shares_b = row["mcapB"] / pts[-1][1]
    spy_now = spy_map.get(max(spy_map)) if spy_map else None
    q = sorted(((_day(x["d"]), x["a"]) for x in (det.get("epsq") or []) if x.get("a") is not None), key=lambda x: x[0])
    out, j = [], 0
    xs = ys = xy = xx = 0.0
    win, rets = 104, []
    prev_spy = None
    for i, (d, c) in enumerate(pts):
        # beta med rullande summor över 104 veckor
        sp = spy_map.get(d)
        if i and sp and prev_spy and pts[i - 1][1]:
            x, y = sp / prev_spy - 1, c / pts[i - 1][1] - 1
            rets.append((x, y))
            xs += x; ys += y; xy += x * y; xx += x * x
            if len(rets) > win:
                ox, oy = rets[-win - 1]
                xs -= ox; ys -= oy; xy -= ox * oy; xx -= ox * ox
        prev_spy = sp or prev_spy
        nwin = min(len(rets), win)
        beta = None
        if nwin >= 52:
            var = xx / nwin - (xs / nwin) ** 2
            beta = ((xy / nwin - xs / nwin * ys / nwin) / var) if var > 0 else None
        while j < len(q) and q[j][0] <= d:
            j += 1
        known = q[:j]
        ttm = prev = None
        if len(known) >= 4 and known[-1][0] - known[-4][0] < 400:
            ttm = sum(a for _, a in known[-4:])
            if len(known) >= 8 and known[-5][0] - known[-8][0] < 400:
                prev = sum(a for _, a in known[-8:-4])
        g = (ttm / prev - 1) if ttm is not None and prev and prev > 0 else None
        pe = c / ttm if ttm and ttm > 0 else None
        peg = pe / (g * 100) if pe and g and g > 0 else None
        scale = (spy_now / sp) if sp and spy_now else 1.0
        out.append({"mc": shares_b * c * scale, "ttm": ttm, "g": g, "pe": pe, "peg": peg, "beta": beta})
    return out


PIT_GROUPS = {
    "gold_pit": ("Guld utan facit: mega (≥ 200 md $ nivåjusterat), beta ≤ 1,3, vinst och vinsttillväxt ≥ 3 % just då",
                 lambda x: x["mc"] >= GOLD_MCAP and x["beta"] is not None and x["beta"] <= GOLD_BETA
                 and x["ttm"] is not None and x["ttm"] > 0 and x["g"] is not None and x["g"] >= 0.03),
    "lrhr_pit": ("LRHR utan facit: ≥ 10 md $, vinst, vinsttillväxt ≥ 5 % och PEG bakåt < 1 just då",
                 lambda x: x["mc"] >= 10 and x["ttm"] is not None and x["ttm"] > 0 and x["g"] is not None
                 and x["g"] >= 0.05 and x["peg"] is not None and x["peg"] < 1),
    "mega_pit": ("Megabolag utan facit: ≥ 200 md $ nivåjusterat just då",
                 lambda x: x["mc"] >= GOLD_MCAP),
}


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


def simulate(pts, rule, mask=None):
    """Affärer på en aktie. pts = [(daycode, close)] per vecka. mask[i] = aktien klarade urvalet vecka i (point-in-time).

    Hävstången räknas som en swing med eget lån: 2x vid köp, lånet växer med räntan och ombalanseras inte.
    Faller det egna kapitalet under MC_RATIO av värdet tvångssäljs positionen (margin call)."""
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
            if pts[i][0] >= START and (mask is None or mask[i]) and entry(st):
                pos = {"i": i, "p": closes[i], "low": closes[i], "loan": LEV - 1, "leq": 1.0, "levlow": 1.0, "mc": None}
        else:
            pos["low"] = min(pos["low"], closes[i])
            if pos["mc"] is None:
                pos["loan"] *= 1 + FIN_COST / 52
                assets = LEV * closes[i] / pos["p"]
                pos["leq"] = assets - pos["loan"]
                pos["levlow"] = min(pos["levlow"], pos["leq"])
                if pos["leq"] < MC_RATIO * assets:
                    pos["mc"] = i
                    pos["leq"] -= assets * COST
            tr = {"ret": closes[i] / pos["p"] - 1}
            if ex(st, tr):
                trades.append(_close(pos, i, closes[i], pts, True))
                pos = None
    if pos is not None:
        trades.append(_close(pos, len(pts) - 1, closes[-1], pts, False))
    return trades


def _close(pos, i, price, pts, done):
    weeks = i - pos["i"]
    r = price * (1 - COST) / (pos["p"] * (1 + COST)) - 1
    leq = pos["leq"] - (0 if pos["mc"] is not None else LEV * price / pos["p"] * COST) - LEV * COST
    return {"in": pts[pos["i"]][0], "out": pts[i][0], "w": weeks, "r": r, "i0": pos["i"], "i1": i,
            "dd": pos["low"] / pos["p"] - 1, "lr": max(-1.0, leq - 1), "ldd": max(-1.0, pos["levlow"] - 1), "done": done,
            "mc": pos["mc"]}


def portfolio(members, rule, data, spy, masks=None):
    """Portfölj: lika vikt i alla aktier som just nu har en öppen affär, annars kontanter (0 %).

    Ger avkastning per år, max drawdown och Sharpe för hela strategin över tid, inte bara per affär.
    2x-raden: varje position har eget lån (2x vid köp) och tvångssäljs vid margin call."""
    days = [d for d, _ in spy]
    if len(days) <= MA:
        return None
    idx = {d: i for i, d in enumerate(days)}
    n = len(days)
    rets = [[] for _ in range(n)]
    rets2 = [[] for _ in range(n)]
    nmc = ntr = 0
    for r in members:
        pts = series(load(data / "t" / fname(r["ticker"])))
        if len(pts) < MA + 10:
            continue
        for t in simulate(pts, rule, (masks or {}).get(r["ticker"])):
            ntr += 1
            nmc += t["mc"] is not None
            p0, loan, eq_prev = pts[t["i0"]][1], LEV - 1.0, 1.0
            for k in range(t["i0"] + 1, t["i1"] + 1):
                i = idx.get(pts[k][0])
                if i is None:
                    continue
                c0, c1 = pts[k - 1][1], pts[k][1]
                rr = c1 / c0 - 1
                if k == t["i0"] + 1:
                    rr -= COST
                if k == t["i1"] and t["done"]:
                    rr -= COST
                rets[i].append(rr)
                if t["mc"] is None or k <= t["mc"]:
                    loan *= 1 + FIN_COST / 52
                    eq = LEV * c1 / p0 - loan
                    if k == t["i0"] + 1:
                        eq -= LEV * COST
                    rets2[i].append(max(-1.0, eq / eq_prev - 1) if eq_prev > 0 else 0.0)
                    eq_prev = eq
    start = max(MA + 1, next((i for i, d in enumerate(days) if d >= START), MA + 1))
    eq, eq2, peak, peak2, dd, dd2, wk, inv = 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, [], 0
    for i in range(start, n):
        r = sum(rets[i]) / len(rets[i]) if rets[i] else 0.0
        r2 = sum(rets2[i]) / len(rets2[i]) if rets2[i] else 0.0
        inv += 1 if rets[i] else 0
        wk.append(r)
        eq *= 1 + r
        eq2 *= max(0.0, 1 + r2)
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
            "trades": ntr, "mc": nmc,
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
                "wiped": sum(1 for t in trades if t["ldd"] <= -0.5),
                "mc": sum(1 for t in trades if t.get("mc") is not None),
                "mcPct": round(sum(1 for t in trades if t.get("mc") is not None) / n * 100, 1)},
        "beatSpy": round(sum(1 for a in ann if bench_cagr is not None and a > bench_cagr) / n * 100, 1),
    }


# ---------------- portfölj med platser: så som man faktiskt handlar ----------------
# Max K aktier samtidigt med lika vikt (10 % var vid K = 10), resten kontanter. Urvalet görs utan facit
# (point-in-time) varje vecka. Köp sker vid veckans stängning när reglerna uppfylls, de djupast rabatterade först.

SLOT_K = 10
TRAIN_END = (date(2021, 1, 1) - date(1970, 1, 1)).days
SLOTS = {
    "smart": {"name": "Smart: LRHR eller Guld, köp i köpzonen (under Cheap) efter en uppvecka, sälj vid Expensive, "
                      "följ-med-stop 15 % när du ligger på plus och stop −20 %",
              "groups": ("lrhr_pit", "gold_pit"), "entry": 20, "exit": 40, "trail": 0.15, "stop": 0.20, "turn": True},
    "lrhr_plus": {"name": "LRHR förbättrad: samma regler som Smart men bara LRHR",
                  "groups": ("lrhr_pit",), "entry": 20, "exit": 40, "trail": 0.15, "stop": 0.20, "turn": True},
    "lrhr_orig": {"name": "LRHR original: köp under Cheap, sälj vid Expensive",
                  "groups": ("lrhr_pit",), "entry": 20, "exit": 40, "trail": None, "stop": None, "turn": False},
    "diamant": {"name": "Diamant: LRHR, köp under 200W, sälj vid Expensive",
                "groups": ("lrhr_pit",), "entry": 0, "exit": 40, "trail": None, "stop": None, "turn": False},
    "gold_orig": {"name": "Guld original: köp under Cheap, sälj vid Expensive",
                  "groups": ("gold_pit",), "entry": 20, "exit": 40, "trail": None, "stop": None, "turn": False},
}


def _stock_frames(rows, data, masks):
    out = {}
    for r in rows:
        t = r["ticker"]
        if not any(t in masks.get(g, {}) for g in masks):
            continue
        pts = series(load(data / "t" / fname(t)))
        if len(pts) < MA + 10:
            continue
        closes = [c for _, c in pts]
        pre = [0.0]
        for c in closes:
            pre.append(pre[-1] + c)
        dist = [None] * len(pts)
        for i in range(MA, len(pts)):
            dist[i] = (closes[i] / ((pre[i + 1] - pre[i + 1 - MA]) / MA) - 1) * 100
        out[t] = {"day": {d: i for i, (d, _) in enumerate(pts)}, "c": closes, "dist": dist}
    return out


def slots(cfg, frames, masks, spy, K=SLOT_K):
    days = [d for d, _ in spy]
    n = len(days)
    start = max(MA + 1, next((i for i, d in enumerate(days) if d >= START), MA + 1))
    groups = cfg["groups"]
    pos, w_prev, wk, ser, trades, wins = {}, [], [], [], 0, 0
    for i in range(start, n):
        d, dp = days[i], days[i - 1]
        r = 0.0
        for t in w_prev:
            F = frames[t]
            a, b = F["day"].get(dp), F["day"].get(d)
            if a is not None and b is not None:
                r += (F["c"][b] / F["c"][a] - 1) / K
        cost = 0.0
        for t in list(pos):
            F, p = frames[t], pos[t]
            j = F["day"].get(d)
            if j is None or F["dist"][j] is None:
                continue
            c = F["c"][j]
            p["peak"] = max(p["peak"], c)
            ret = c / p["p"] - 1
            out = F["dist"][j] >= cfg["exit"]
            if cfg["trail"] is not None and ret > 0 and c <= p["peak"] * (1 - cfg["trail"]):
                out = True
            if cfg["stop"] is not None and ret <= -cfg["stop"]:
                out = True
            if out:
                trades += 1
                wins += ret > 0
                cost += COST / K
                del pos[t]
        if len(pos) < K:
            cand = []
            for t, F in frames.items():
                if t in pos:
                    continue
                j = F["day"].get(d)
                if j is None or j < 2 or F["dist"][j] is None or F["dist"][j] >= cfg["entry"]:
                    continue
                if not any(t in masks[g] and masks[g][t][j] for g in groups):
                    continue
                c = F["c"]
                if cfg["turn"] and not (c[j] > c[j - 1] and c[j - 1] < c[j - 2]):
                    continue
                cand.append((F["dist"][j], t, c[j]))
            cand.sort()
            for _, t, c in cand[:K - len(pos)]:
                pos[t] = {"p": c, "peak": c}
                cost += COST / K
        w_prev = list(pos)
        wk.append(r - cost)
        ser.append((d, r - cost))
    return _perf(ser, trades, wins)


def _perf(ser, trades=None, wins=None):
    def sh(a):
        if len(a) < 20:
            return None
        m = sum(a) / len(a)
        sd = (sum((x - m) ** 2 for x in a) / len(a)) ** 0.5
        return round((m * 52 - 0.03) / (sd * 52 ** 0.5), 2) if sd else None

    def run_eq(rs):
        e, pk, dd, curve = 1.0, 1.0, 0.0, []
        for k, x in enumerate(rs):
            e *= max(0.0, 1 + x)
            pk = max(pk, e)
            dd = min(dd, e / pk - 1)
            curve.append(e)
        return e, dd, curve
    rs = [x for _, x in ser]
    yrs = len(rs) / 52
    e, dd, curve = run_eq(rs)
    lev = [1.5 * x - 0.5 * FIN_COST / 52 for x in rs]
    e15, dd15, _ = run_eq(lev)
    by = {}
    for d, x in ser:
        y = (date(1970, 1, 1) + timedelta(days=d)).year
        by[y] = by.get(y, 1.0) * (1 + x)
    tr = [x for d, x in ser if d < TRAIN_END]
    te = [x for d, x in ser if d >= TRAIN_END]
    out = {"cagr": round((e ** (1 / yrs) - 1) * 100, 1), "maxdd": round(dd * 100, 1), "sharpe": sh(rs),
           "sharpeTrain": sh(tr), "sharpeTest": sh(te),
           "cagr15": round((e15 ** (1 / yrs) - 1) * 100, 1) if e15 > 0 else -100.0, "maxdd15": round(dd15 * 100, 1),
           "years": {str(y): round((v - 1) * 100, 1) for y, v in sorted(by.items())},
           "curve": [[ser[k][0], round(curve[k], 4)] for k in range(0, len(ser), 4)] + [[ser[-1][0], round(curve[-1], 4)]]}
    if trades is not None:
        out["trades"] = trades
        out["win"] = round(wins / trades * 100, 1) if trades else None
    return out


def run_slots(rows, data, masks, spy):
    frames = _stock_frames(rows, data, masks)
    res = {k: dict(name=v["name"], **slots(v, frames, masks, spy)) for k, v in SLOTS.items()}
    s0 = max(MA + 1, next((i for i, (d, _) in enumerate(spy) if d >= START), MA + 1))
    res["spy"] = dict(name="S&P 500 (köp och behåll)", **_perf([(spy[i][0], spy[i][1] / spy[i - 1][1] - 1) for i in range(s0, len(spy))]))
    return res


def run(data: Path):
    u = load(data / "universe.json", {})
    rows = [r for r in u.get("rows", []) if r.get("type", "EQUITY") == "EQUITY"]
    lead = leaders(rows)
    groups = {
        "all": ("Alla aktier", [r for r in rows if not r.get("excluded")]),
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
    spy_map = {d: c for d, c in spy}
    masks = {k: {} for k in PIT_GROUPS}
    for r in rows:
        if r.get("excluded"):
            continue
        det = load(data / "t" / fname(r["ticker"]))
        info = pit_info(r, det, spy_map) if det else None
        if not info:
            continue
        for gk, (_, fn) in PIT_GROUPS.items():
            m = [bool(fn(x)) for x in info]
            if any(m):
                masks[gk][r["ticker"]] = m
    for gk, (nm, _) in PIT_GROUPS.items():
        groups[gk] = (nm, [r for r in rows if r["ticker"] in masks[gk]])
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
                key = (r["ticker"], rk, gk if gk in PIT_GROUPS else "")
                if key not in cache:
                    cache[key] = simulate(series(load(data / "t" / fname(r["ticker"]))), rk, masks[gk].get(r["ticker"]) if gk in PIT_GROUPS else None)
                for t in cache[key]:
                    allt.append({**t, "t": r["ticker"]})
            out["res"][f"{gk}|{rk}"] = stats(allt, bench)
            if rk == "orig" and gk in ("gold", "lrhr", "gold_pit", "lrhr_pit"):
                allt.sort(key=lambda t: t["in"], reverse=True)
                out["trades"][gk] = [{"t": t["t"], "in": t["in"], "out": t["out"], "r": round(t["r"] * 100, 1),
                                      "dd": round(t["dd"] * 100, 1), "lr": round(t["lr"] * 100, 1), "done": t["done"]}
                                     for t in allt[:40]]
    out["port"] = {}
    for gk in ("lrhr", "gold", "mega", "lrhr_pit", "gold_pit", "mega_pit"):
        for rk in ("orig", "fire", "firefair", "fairstop"):
            out["port"][f"{gk}|{rk}"] = portfolio(groups[gk][1], rk, data, spy, masks.get(gk))
    try:
        out["slots"] = run_slots(rows, data, masks, spy)
    except Exception as e:  # noqa: BLE001
        print("platsportfölj fel", e)
    out["method"] = {"cost": COST, "mcRatio": MC_RATIO, "lev": LEV, "finCost": FIN_COST,
                     "pit": "Urval med det som var känt varje vecka: rapporterad vinst per kvartal, kurs, börsvärde och beta."}
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
    for k, v in (res.get("slots") or {}).items():
        print("SLOTS", k, {x: v[x] for x in ("cagr", "maxdd", "sharpe", "sharpeTrain", "sharpeTest", "cagr15", "maxdd15", "trades", "win") if x in v})


if __name__ == "__main__":
    math  # noqa: B018
    main()
