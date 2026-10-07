"""Sektorrotation: veckoindex per sektor och var i rotationscykeln varje sektor ligger.

Varje sektor får ett likaviktat index av sina aktiers veckoavkastning (senaste ~70 veckorna), som jämförs
med S&P 500 (SPY). Två mått, som i ett Relative Rotation Graph:
  styrka  = sektorns avkastning mot SPY senaste 13 veckorna (i procentenheter)
  fart    = hur styrkan har ändrats senaste 4 veckorna (positivt = pengar strömmar in)
Fyra lägen: Hett (stark och ökar), Svalnar (stark men tappar), Kallt (svag och tappar),
På väg upp (svag men ökar = pengar börjar rotera in).

  python scripts/sectors.py --data _site/data
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WEEKS = 70
LOOK = 13    # styrka: 13 veckor (ett kvartal)
MOM = 4      # fart: förändring på 4 veckor
TAIL = 10    # så många veckors historik i rotationsdiagrammet


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def fname(tk):
    return tk.replace("^", "_").replace("/", "_") + ".json"


def weekly(det):
    w = (det or {}).get("w") or {}
    return {t: c for t, c in zip(w.get("t") or [], w.get("c") or []) if c}


def phase(s, m):
    if s >= 0 and m >= 0:
        return "hot"
    if s >= 0:
        return "cooling"
    if m >= 0:
        return "rising"
    return "cold"


def run(data: Path):
    u = load(data / "universe.json", {})
    spy = weekly(load(data / "t" / "SPY.json"))
    if len(spy) < WEEKS:
        return None
    days = sorted(spy)[-WEEKS:]
    groups = {}
    for r in u.get("rows", []):
        if r.get("type", "EQUITY") != "EQUITY" or not r.get("sector") or r.get("sector") == "Övrigt":
            continue
        groups.setdefault(r["sector"], []).append(r["ticker"])
        if r.get("industry"):
            groups.setdefault("ind:" + r["industry"], []).append(r["ticker"])
    series = {}
    for tks in groups.values():
        for t in tks:
            if t not in series:
                series[t] = weekly(load(data / "t" / fname(t)))

    def index(tks):
        """Likaviktat veckoindex; varje veckas avkastning kapas till ±25 % så att en enskild aktie inte styr."""
        idx, v = [], 100.0
        for i, d in enumerate(days):
            if i:
                rs = []
                for t in tks:
                    s = series.get(t) or {}
                    a, b = s.get(days[i - 1]), s.get(d)
                    if a and b:
                        rs.append(max(-0.25, min(0.25, b / a - 1)))
                if rs:
                    v *= 1 + sum(rs) / len(rs)
            idx.append(round(v, 3))
        return idx

    sidx = [round(spy[d] / spy[days[0]] * 100, 3) for d in days]

    def rrg(idx):
        pts = []
        for i in range(LOOK + MOM, len(idx)):
            s = ((idx[i] / idx[i - LOOK]) / (sidx[i] / sidx[i - LOOK]) - 1) * 100
            s0 = ((idx[i - MOM] / idx[i - MOM - LOOK]) / (sidx[i - MOM] / sidx[i - MOM - LOOK]) - 1) * 100
            pts.append([round(s, 2), round(s - s0, 2)])
        return pts[-TAIL:]

    out = {"asOf": u.get("asOf"), "weeks": days, "spy": sidx, "look": LOOK, "mom": MOM, "sectors": {}, "industries": {}}
    for name, tks in groups.items():
        if len(tks) < (3 if name.startswith("ind:") else 4):
            continue
        idx = index(tks)
        tail = rrg(idx)
        if not tail:
            continue
        s, m = tail[-1]
        # hur länge har sektorn legat i nuvarande läge (veckor)
        ph, k = phase(s, m), 0
        for p in reversed(tail):
            if phase(*p) != ph:
                break
            k += 1
        rec = {"n": len(tks), "idx": idx, "rrg": tail, "phase": ph, "weeks": k, "prev": phase(*tail[-5]) if len(tail) >= 5 else None}
        (out["industries"] if name.startswith("ind:") else out["sectors"])[name[4:] if name.startswith("ind:") else name] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="_site/data")
    a = ap.parse_args()
    res = run(Path(a.data))
    if res:
        (Path(a.data) / "sectors.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for k, v in sorted(res["sectors"].items(), key=lambda x: -x[1]["rrg"][-1][0]):
            print(f"{k:14s} n={v['n']:3d} styrka={v['rrg'][-1][0]:6.1f} fart={v['rrg'][-1][1]:6.1f} {v['phase']:8s} {v['weeks']}v (förut {v['prev']})")
        print(len(res["industries"]), "branscher")


if __name__ == "__main__":
    main()
