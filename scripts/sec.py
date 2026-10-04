"""Gratis nyckeltal från SEC EDGAR (amerikanska bolags årsredovisningar, 10-K).

Använder SEC:s frames-API: ett anrop per nyckeltal och år ger alla bolag på en gång,
så hela universumet kostar runt 80 anrop. Ingen nyckel krävs, bara en User-Agent
med kontaktuppgift (SEC:s regel). Max 10 anrop per sekund.

Källa: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
from __future__ import annotations

import time
from datetime import date, datetime

UA = {"User-Agent": "FullThrottle aktiesida github.com/handahama0-netizen/full-throttle "
                    "handahama0-netizen@users.noreply.github.com",
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


def _get(url, log):
    import requests
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            time.sleep(0.15)
            return r.json()
        except Exception as e:  # noqa: BLE001
            if i == 2:
                log("SEC fel", url.rsplit("/", 3)[-3:], str(e)[:100])
            time.sleep(2 * (i + 1))
    return None


def _d(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def load(tickers, log=print):
    """Returnerar {ticker: {...}} för amerikanska bolag i listan. Tomt vid fel."""
    want = {t.upper() for t in tickers if "." not in t and not t.startswith("^")}
    m = _get("https://www.sec.gov/files/company_tickers.json", log)
    if not m:
        log("SEC: kunde inte hämta tickerlistan")
        return {}
    cik_tk = {}
    for v in m.values():
        tk = str(v.get("ticker", "")).upper()
        if tk in want and v.get("cik_str") not in cik_tk:
            cik_tk[int(v["cik_str"])] = tk
    log(f"SEC: {len(cik_tk)} av {len(want)} amerikanska symboler hittade")

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
    return out
