"""Institutionellt ägande från Nasdaqs öppna API (bygger på 13F-anmälningar till SEC).

För varje amerikansk aktie: hur många institutioner som ökat, minskat, öppnat nya
positioner och sålt allt senaste kvartalet, nettoflödet i aktier, och de största
ägarnas innehav och förändring. Ingen nyckel krävs.
"""
from __future__ import annotations

import re
import threading
import time

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
       "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
URL = "https://api.nasdaq.com/api/company/{t}/institutional-holdings?limit={n}&type={ty}&sortColumn=marketValue&sortOrder=DESC"
_gate = threading.Lock()
_next = [0.0]
_state = {"ok": 0, "fail": 0, "blocked": False}


def _num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    neg = s.startswith("(") or s.startswith("-")
    s = re.sub(r"[^0-9.]", "", s)
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _get(t, ty, n):
    import requests
    with _gate:
        w = _next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _next[0] = time.time() + 0.6
    r = requests.get(URL.format(t=t, ty=ty, n=n), headers=HDR, timeout=20)
    if r.status_code in (403, 429):
        _state["blocked"] = r.status_code == 403
        raise RuntimeError(f"HTTP {r.status_code}")
    r.raise_for_status()
    return (r.json() or {}).get("data") or {}


def _rows(block):
    if not isinstance(block, dict):
        return []
    rows = block.get("rows")
    if rows is None and isinstance(block.get("table"), dict):
        rows = block["table"].get("rows")
    return rows or []


def _pos(rows, *keys):
    for r in rows:
        lab = str(r.get("positions") or r.get("label") or "").lower()
        if any(k in lab for k in keys):
            return _num(r.get("holders")), _num(r.get("shares"))
    return None, None


def _holders(data, n):
    out = []
    for r in _rows((data or {}).get("holdingsTransactions") or {})[:n]:
        out.append({"n": str(r.get("ownerName") or "")[:50].title(), "d": _date(r.get("date")),
                    "sh": _num(r.get("sharesHeld")), "ch": _num(r.get("sharesChange")),
                    "cp": _num(r.get("sharesChangePCT")), "v": _num(r.get("marketValue"))})
    return out


def _date(s):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(s or ""))
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else str(s or "")[:10]


def fetch(t, full=True):
    """Returnerar ett dict med sammanfattning, flöde och ägare, eller None."""
    if "." in t or t.startswith("^") or _state["blocked"]:
        return None
    try:
        d = _get(t, "TOTAL", 15)
        summ = d.get("ownershipSummary") or {}
        act, ns = _rows(d.get("activePositions")), _rows(d.get("newSoldOutPositions"))
        inc_h, inc_s = _pos(act, "increased")
        dec_h, dec_s = _pos(act, "decreased")
        held_h, held_s = _pos(act, "held")
        tot_h, tot_s = _pos(act, "total")
        new_h, new_s = _pos(ns, "new")
        out_h, out_s = _pos(ns, "sold")
        top = _holders(d, 15)
        pct = None
        for k, v in summ.items():
            if "pct" in k.lower() or "ownership" in str((v or {}).get("label", "")).lower():
                pct = _num((v or {}).get("value"))
                break
        res = {"pct": pct, "n": tot_h, "sh": tot_s, "inc": [inc_h, inc_s], "dec": [dec_h, dec_s], "held": [held_h, held_s],
               "new": [new_h, new_s], "out": [out_h, out_s], "top": top}
        dates = [h["d"] for h in top if h.get("d")]
        res["q"] = max(set(dates), key=dates.count) if dates else None
        if full:
            for ty, key in (("NEW", "topNew"), ("SOLDOUT", "topOut"), ("INCREASED", "topInc"), ("DECREASED", "topDec")):
                try:
                    res[key] = _holders(_get(t, ty, 8), 8)
                except Exception:  # noqa: BLE001
                    res[key] = None
        res.update(flow(res))
        _state["ok"] += 1
        return res if (tot_h or top) else None
    except Exception:  # noqa: BLE001
        _state["fail"] += 1
        return None


def flow(r):
    """Nettoflöde och omdöme: köper eller säljer institutionerna?"""
    inc_s, dec_s = (r.get("inc") or [None, None])[1] or 0, (r.get("dec") or [None, None])[1] or 0
    new_s, out_s = (r.get("new") or [None, None])[1] or 0, (r.get("out") or [None, None])[1] or 0
    inc_h, dec_h = (r.get("inc") or [0, 0])[0] or 0, (r.get("dec") or [0, 0])[0] or 0
    new_h, out_h = (r.get("new") or [0, 0])[0] or 0, (r.get("out") or [0, 0])[0] or 0
    tot = r.get("sh") or 0
    net = (inc_s + new_s - dec_s - out_s)
    netp = round(net / tot * 100, 2) if tot else None
    buyers, sellers = inc_h + new_h, dec_h + out_h
    breadth = round(buyers / sellers, 2) if sellers else None
    score = 0.0
    if netp is not None:
        score += max(-1, min(1, netp / 3))
    if breadth is not None:
        score += max(-1, min(1, (breadth - 1) / 0.4))
    score /= 2 if (netp is not None and breadth is not None) else 1
    verdict = "bull" if score >= 0.25 else "bear" if score <= -0.25 else "neutral"
    return {"net": net, "netp": netp, "breadth": breadth, "score": round(score, 2), "verdict": verdict}


def merge_hist(old, cur):
    """Kvartalshistorik: en rad per rapportkvartal, byggs på över tid."""
    h = [x for x in (old or []) if isinstance(x, dict) and x.get("q")]
    if cur and cur.get("q"):
        pt = {"q": cur["q"], "pct": cur.get("pct"), "n": cur.get("n"), "netp": cur.get("netp"), "breadth": cur.get("breadth"),
              "inc": (cur.get("inc") or [None])[0], "dec": (cur.get("dec") or [None])[0],
              "new": (cur.get("new") or [None])[0], "out": (cur.get("out") or [None])[0], "verdict": cur.get("verdict")}
        h = [x for x in h if x["q"] != pt["q"]] + [pt]
        h.sort(key=lambda x: x["q"])
    return h[-20:] or None


def status():
    return dict(_state)
