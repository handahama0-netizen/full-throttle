"""Gratis nyckeltal från SEC EDGAR (amerikanska bolags årsredovisningar, 10-K).

Använder SEC:s frames-API: ett anrop per nyckeltal och år ger alla bolag på en gång,
så hela universumet kostar runt 80 anrop. Ingen nyckel krävs, bara en User-Agent
med kontaktuppgift (SEC:s regel). Max 10 anrop per sekund.

Källa: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
from __future__ import annotations

import time
from datetime import date, datetime

# SEC:s format: "Företagsnamn kontakt@domän"
UA = {"User-Agent": "FullThrottle Research handahama0-netizen@users.noreply.github.com",
      "Accept-Encoding": "gzip, deflate"}

# Första begreppet som finns för bolaget används (blandas inte mellan begrepp).
CONCEPTS = {
    "rev": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense"],
    "ni": ["NetIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
}


STATUS = {"calls": 0, "ok": 0, "codes": {}, "errors": []}


def _note(code, url, msg=None):
    STATUS["codes"][str(code)] = STATUS["codes"].get(str(code), 0) + 1
    if msg and len(STATUS["errors"]) < 8:
        STATUS["errors"].append(f"{url.split('.gov', 1)[-1][:80]}: {msg[:160]}")


def _get(url, log):
    import requests
    STATUS["calls"] += 1
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            _note(r.status_code, url, None if r.status_code in (200, 404) else r.text[:160])
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            time.sleep(0.15)
            js = r.json()
            STATUS["ok"] += 1
            return js
        except Exception as e:  # noqa: BLE001
            _note(type(e).__name__, url, str(e))
            if i == 2:
                log("SEC fel", url.rsplit("/", 3)[-3:], str(e)[:100])
            time.sleep(2 * (i + 1))
    return None


def _d(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _ticker_map(log, cache):
    """{TICKER: cik} från SEC, med reservfil och förra nattens kopia."""
    m = _get("https://www.sec.gov/files/company_tickers.json", log)
    if m:
        STATUS["map"] = "company_tickers.json"
        return {str(v.get("ticker", "")).upper(): int(v["cik_str"]) for v in m.values() if v.get("cik_str")}
    import requests
    try:
        r = requests.get("https://www.sec.gov/include/ticker.txt", headers=UA, timeout=60)
        _note(r.status_code, "https://www.sec.gov/include/ticker.txt", None if r.ok else r.text[:160])
        if r.ok:
            out = {}
            for line in r.text.splitlines():
                p = line.split()
                if len(p) == 2 and p[1].isdigit():
                    out[p[0].upper()] = int(p[1])
            if out:
                STATUS["map"] = "ticker.txt"
                return out
    except Exception as e:  # noqa: BLE001
        _note(type(e).__name__, "ticker.txt", str(e))
    if cache:
        STATUS["map"] = "cache"
        return {k.upper(): int(v) for k, v in cache.items()}
    return {}


def load(tickers, log=print, cache=None):
    """Returnerar {ticker: {...}} för amerikanska bolag i listan. Tomt vid fel.
    cache = förra nattens {ticker: cik} om SEC:s tickerlista inte går att hämta."""
    want = {t.upper() for t in tickers if "." not in t and not t.startswith("^")}
    tmap = _ticker_map(log, cache)
    if not tmap:
        log("SEC: kunde inte hämta tickerlistan")
        return {}
    STATUS["cikMap"] = {tk: tmap[tk] for tk in sorted(want) if tk in tmap}
    cik_tk = {}
    for tk in sorted(want):
        cik = tmap.get(tk) or tmap.get(tk.replace("-", "."))
        if cik and cik not in cik_tk:
            cik_tk[cik] = tk
    log(f"SEC: {len(cik_tk)} av {len(want)} amerikanska symboler hittade")
    STATUS["matched"] = len(cik_tk)

    this_year = date.today().year
    years = range(this_year - 7, this_year + 1)
    # series[kind][concept][cik] = {end: val}
    series = {k: {c: {} for c in cs} for k, cs in CONCEPTS.items()}
    calls = 0
    for kind, concepts in CONCEPTS.items():
        for c in concepts:
            for y in years:
                js = _get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{c}/USD/CY{y}.json", log)
                calls += 1
                for p in (js or {}).get("data", []):
                    cik = p.get("cik")
                    if cik in cik_tk and p.get("val") is not None and p.get("end"):
                        series[kind][c].setdefault(cik, {})[p["end"]] = float(p["val"])
    log(f"SEC: {calls} anrop klara")

    def pick(kind, cik):
        best = None
        for c in CONCEPTS[kind]:
            s = series[kind][c].get(cik)
            if s:
                last = max(s)
                if best is None or last > max(best):  # begreppet med senaste året vinner
                    best = s
        return best or {}

    def at(s, end):
        """Värdet med samma bokslutsdatum (±20 dagar)."""
        if not s:
            return None
        e = _d(end)
        for k, v in s.items():
            if abs((_d(k) - e).days) <= 20:
                return v
        return None

    out = {}
    stale = date.today().replace(year=date.today().year - 2)
    for cik, tk in cik_tk.items():
        rev = pick("rev", cik)
        if not rev:
            continue
        ends = sorted(rev)
        if _d(ends[-1]) < stale:
            continue
        ni, ocf, capex, sbc = (pick(k, cik) for k in ("ni", "ocf", "capex", "sbc"))
        rows = []
        for e in ends[-7:]:
            o, cx = at(ocf, e), at(capex, e)
            rows.append({"end": e, "rev": rev[e], "ni": at(ni, e),
                         "fcf": (o - cx) if o is not None and cx is not None else None, "sbc": at(sbc, e)})
        last = rows[-1]
        rec = {"cik": cik, "years": rows}
        # Omsättningstillväxt per år (CAGR) över upp till 5 år
        le = _d(last["end"])
        base = next((r for r in rows if 2.5 <= (le - _d(r["end"])).days / 365.25 <= 5.5 and r["rev"] > 0), None)
        if base and last["rev"] > 0:
            n = round((le - _d(base["end"])).days / 365.25)
            rec["revCagr"] = round(((last["rev"] / base["rev"]) ** (1 / n) - 1) * 100, 2)
            rec["revCagrYears"] = n
        if len(rows) >= 2 and rows[-2]["rev"] and rows[-2]["rev"] > 0:
            rec["revGrowth1y"] = round((last["rev"] / rows[-2]["rev"] - 1) * 100, 2)
        if last["fcf"] is not None and last["rev"] > 0:
            rec["fcfMargin"] = round(last["fcf"] / last["rev"] * 100, 2)
        rec["fcf"], rec["sbc"] = last["fcf"], last["sbc"]
        out[tk] = rec
    log(f"SEC: nyckeltal för {len(out)} bolag")
    STATUS["companies"] = len(out)
    STATUS["withData"] = {k: sum(1 for c in series[k].values() for _ in c) for k in series}
    return out
