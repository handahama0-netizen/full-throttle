"""Swing-idéer: tekniska indikatorer på dagliga kurser och backtest av korta affärer.

Upplägget som fungerat bäst i testerna (ca 600 aktier, dagliga kurser sedan 2023):
köp när RSI 14 går under 30 (översåld), köp på öppningen dagen efter, stopp 3 ATR under köpet.
  k1 (max en månad):  sälj när RSI går över 50 eller efter 20 handelsdagar.
  k3 (1–3 månader):   sälj när RSI går över 65 eller efter 60 handelsdagar.
Andra upplägg som testades (RSI 2-dipp i upptrend, rekyl till 50-dagars, utbrott på volym) slog inte
slumpmässiga köp tillräckligt och används inte.
Skriver data/swing.json: indikatorer per aktie, kursnivåer för köp och sälj, och historiken per upplägg.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np

COST = 0.002  # courtage och spread, köp + sälj
BUY_RSI = 30
STOP_ATR = 3.0
HORIZONS = {
    "k1": {"name": "Max en månad", "exit": 50, "max": 20},
    "k3": {"name": "1–3 månader", "exit": 65, "max": 60},
}
BIG = 50  # md $: storbolag (gav bäst resultat i testerna)


def fname(t):
    return t.replace("^", "_").replace("/", "_") + ".json"


def _roll_mean(a, k):
    out = np.full(a.shape, np.nan)
    if len(a) >= k:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[k - 1:] = (c[k:] - c[:-k]) / k
    return out


def _roll_std(a, k):
    m = _roll_mean(a, k)
    return np.sqrt(np.maximum(_roll_mean(a * a, k) - m * m, 0))


def _rsi(c, k=14):
    """Wilders RSI. Returnerar serien och snittuppgång/-nedgång efter sista dagen (för kursnivåer)."""
    out = np.full(c.shape, np.nan)
    if len(c) <= k:
        return out, None, None
    d = np.diff(c)
    up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    g, ls = up[:k].mean(), dn[:k].mean()
    for i in range(k, len(c)):
        if i > k:
            g = (g * (k - 1) + up[i - 1]) / k
            ls = (ls * (k - 1) + dn[i - 1]) / k
        out[i] = 100.0 if ls == 0 else 100 - 100 / (1 + g / ls)
    return out, g, ls


def rsi_price(c, g, ls, target, k=14):
    """Kursen i morgon som ger RSI = target (en dags rörelse). Ungefärlig nivå att hålla koll på."""
    if g is None or ls is None:
        return None
    q = target / (100 - target)  # g'/l'
    # uppgång u: g' = ((k-1)g + u)/k, l' = (k-1)l/k  ->  u = q(k-1)l - (k-1)g
    u = q * (k - 1) * ls - (k - 1) * g
    if u >= 0:
        return c + u
    # nedgång d: g' = (k-1)g/k, l' = ((k-1)l + d)/k  ->  d = (k-1)g/q - (k-1)l
    dn = (k - 1) * g / q - (k - 1) * ls
    return c - dn if dn > 0 else c


def indicators(d):
    c = np.array(d["c"], float)
    h = np.array(d["h"], float)
    lw = np.array(d["l"], float)
    o = np.array(d["o"], float)
    v = np.array(d.get("v") or [0] * len(c), float)
    pc = np.insert(c[:-1], 0, c[0])
    tr = np.maximum(h - lw, np.maximum(np.abs(h - pc), np.abs(lw - pc)))
    r14, g, lo = _rsi(c, 14)
    return {"t": d["t"], "c": c, "h": h, "l": lw, "o": o, "v": v, "r14": r14, "g": g, "lo": lo,
            "s20": _roll_mean(c, 20), "s50": _roll_mean(c, 50), "s200": _roll_mean(c, 200),
            "sd20": _roll_std(c, 20), "atr": _roll_mean(tr, 14),
            "vavg": np.insert(_roll_mean(v, 50)[:-1], 0, np.nan)}


def simulate(x, hz):
    """Alla affärer i en aktie för en tidshorisont: köp på öppningen efter RSI < 30, stopp 3 ATR."""
    H = HORIZONS[hz]
    c, lw, o, atr, r = x["c"], x["l"], x["o"], x["atr"], x["r14"]
    n, out, i = len(c), [], 200
    while i < n - 1:
        if not (r[i] < BUY_RSI) or np.isnan(atr[i]) or not o[i + 1]:
            i += 1
            continue
        e = o[i + 1]
        stop, sell, j = e - STOP_ATR * atr[i], None, i + 1
        while j < n:
            if lw[j] <= stop:
                sell = min(o[j], stop)
            elif r[j] > H["exit"] or j - i >= H["max"]:
                sell = c[j]
            if sell is not None:
                break
            j += 1
        if sell is None:  # affären pågår fortfarande
            break
        out.append((sell / e - 1 - COST, j - i, int(x["t"][i])))
        i = j + 1
    return out


def stats(trades):
    if not trades:
        return None
    r = np.array([t[0] for t in trades])
    days = np.array([t[1] for t in trades])
    win, loss = r[r > 0], r[r <= 0]
    return {"n": int(len(r)), "win": round(float((r > 0).mean() * 100), 1), "avg": round(float(r.mean() * 100), 2),
            "med": round(float(np.median(r) * 100), 2), "days": round(float(days.mean()), 1),
            "avgWin": round(float(win.mean() * 100), 1) if len(win) else None,
            "avgLoss": round(float(loss.mean() * 100), 1) if len(loss) else None,
            "worst": round(float(r.min() * 100), 1),
            "pf": round(float(win.sum() / -loss.sum()), 2) if len(loss) and loss.sum() < 0 else None}


def _f(v, dp=2):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), dp)


def _with_today(d, r):
    """Lägg in dagens kurs från universumet om den är nyare än sista dagsstapeln (körningar under dagen)."""
    price, asof = r.get("price"), r.get("asOf")
    if not price or not asof:
        return d
    try:
        dc = (date.fromisoformat(asof) - date(1970, 1, 1)).days
    except ValueError:
        return d
    last = d["c"][-1]
    if abs(price / last - 1) < 1e-6:  # samma kurs (helg eller före öppning): inget nytt
        return d
    d = {k: list(v) for k, v in d.items()}
    if dc > d["t"][-1]:
        d["t"].append(dc)
        d["o"].append(last)
        d["h"].append(max(price, last))
        d["l"].append(min(price, last))
        d["c"].append(price)
        if "v" in d:
            d["v"].append(d["v"][-1])
    elif dc == d["t"][-1]:
        d["c"][-1] = price
        d["h"][-1] = max(d["h"][-1], price)
        d["l"][-1] = min(d["l"][-1], price)
    return d


def quality(r):
    """Tjänar pengar och växer (dagens siffror)."""
    return (r.get("netMargin") or -1) > 0 and (r.get("revGrowth1y") or -1) > 0


def run(data_dir: Path, with_backtest=True, prev=None, log=print):
    data_dir = Path(data_dir)
    u = json.loads((data_dir / "universe.json").read_text())
    rows = [r for r in u.get("rows", []) if r.get("type", "EQUITY") == "EQUITY" and not r.get("excluded")]
    out_rows, per_stock = {}, {}
    groups = {hz: {"all": [], "big": [], "bigq": []} for hz in HORIZONS}
    for r in rows:
        p = data_dir / "t" / fname(r["ticker"])
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text()).get("d")
        except (ValueError, OSError):
            continue
        if not d or len(d.get("c") or []) < 220 or None in d["c"] or None in d["o"]:
            continue
        x = indicators(_with_today(d, r))
        c, i = x["c"], len(x["c"]) - 1
        r14 = x["r14"]
        # dagar sedan RSI senast var under 30 (inom 10 dagar), och kursen då
        ago = next((k for k in range(0, 11) if i - k >= 0 and r14[i - k] < BUY_RSI), None)
        rec = {"rsi": _f(r14[i], 1), "rsiY": _f(r14[i - 1], 1), "atr": _f(x["atr"][i], 4),
               "s20": _f(x["s20"][i], 4), "s50": _f(x["s50"][i], 4), "s200": _f(x["s200"][i], 4),
               "bbl": _f(x["s20"][i] - 2 * x["sd20"][i], 4),
               "vr": _f(x["v"][i] / x["vavg"][i], 2) if x["vavg"][i] else None,
               "chg5": _f((c[i] / c[i - 5] - 1) * 100, 1), "chg20": _f((c[i] / c[i - 20] - 1) * 100, 1),
               "p30": _f(rsi_price(c[i], x["g"], x["lo"], 30), 4), "p50": _f(rsi_price(c[i], x["g"], x["lo"], 50), 4),
               "p65": _f(rsi_price(c[i], x["g"], x["lo"], 65), 4),
               "ago": ago, "agoP": _f(c[i - ago], 4) if ago is not None else None}
        if r14[i] < 45 or ago is not None:  # kursen för grafen bara för kandidater (håller filen liten)
            rec["c"] = [_f(v, 4) for v in c[-90:]]
            rec["t0"] = int(x["t"][-90]) if len(x["t"]) >= 90 else int(x["t"][0])
        out_rows[r["ticker"]] = rec
        if with_backtest:
            big, q = (r.get("mcapB") or 0) >= BIG, quality(r)
            per = {}
            for hz in HORIZONS:
                tr = simulate(x, hz)
                groups[hz]["all"] += tr
                if big:
                    groups[hz]["big"] += tr
                    if q:
                        groups[hz]["bigq"] += tr
                if tr:
                    per[hz] = [len(tr), sum(1 for t in tr if t[0] > 0), round(sum(t[0] for t in tr) / len(tr) * 100, 2)]
            if per:
                per_stock[r["ticker"]] = per
    res = {"rows": out_rows, "rule": {"buy": BUY_RSI, "stop": STOP_ATR, "big": BIG,
                                      "hz": {k: dict(v) for k, v in HORIZONS.items()}}}
    if with_backtest:
        res["stats"] = {hz: {g: stats(v) for g, v in gs.items()} for hz, gs in groups.items()}
        # jämförelse: köp vilken dag som helst och håll lika länge
        res["perStock"] = per_stock
        days = [t[2] for gs in groups.values() for t in gs["all"]]
        res["btFrom"] = min(days) if days else None
        res["btAt"] = date.today().isoformat()
    elif prev:
        for k in ("stats", "perStock", "btFrom", "btAt"):
            if k in prev:
                res[k] = prev[k]
    log("swing:", len(out_rows), "aktier,", sum(1 for v in out_rows.values() if v["rsi"] is not None and v["rsi"] < BUY_RSI),
        "översålda just nu")
    return res


if __name__ == "__main__":
    import sys
    res = run(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
    for hz, gs in res["stats"].items():
        for g, s in gs.items():
            print(hz, g, s)
