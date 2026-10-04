"""Analytikerbrev skrivna av Claude (valfritt).

Körs bara om repot har en hemlighet som heter ANTHROPIC_API_KEY.
Varje brev bygger på aktiens data från Yahoo Finance plus Claudes egen webbsökning efter färska nyheter.
Kostnad ungefär 1 krona per brev (Claude Sonnet 5.5 + upp till 4 webbsökningar).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

MODEL = "claude-sonnet-5-5"
MAX_SEARCHES = 4

SYSTEM = """Du är en erfaren aktieanalytiker på en global investmentbank (som Goldman Sachs eller J.P. Morgan) och skriver ett kort analysbrev på svenska till en privatinvesterare.

Regler:
- Använd bara siffror från den bifogade datan eller från källor du hittar med webbsökning. Hitta aldrig på siffror.
- Sök efter de viktigaste nyheterna om bolaget från de senaste 30 dagarna och relevant makro (räntor, sektor). Max fyra sökningar.
- Förklara varför aktien rört sig. Datan innehåller en uppdelning i marknad (beta × index), sektor och bolagsspecifikt, samt hur analytikernas vinstprognoser ändrats. Använd den.
- Bedöm om rörelsen är en överreaktion, fundamentalt motiverad eller främst makro/sektor som sannolikt går över. Motivera med prognosrevideringar, rapportutfall och nyheter.
- Kommentera värderingen: DCF, omvänd DCF (vilken tillväxt kursen förutsätter), Graham och analytikernas riktkurser.
- Skriv sakligt och koncist, som en professionell analytiker. Ge inga direkta köp- eller säljråd.

Svara ENBART med ett JSON-objekt, utan annan text, med exakt dessa nycklar:
{"rubrik": "max 90 tecken", "slutsats": "2–3 meningar", "rorelse": "varför aktien rört sig senaste månaden, 3–5 meningar med siffror",
 "bedomning": "Överreaktion" | "Fundamentalt motiverat" | "Makro/sektor" | "Blandat" | "Ingen tydlig rörelse",
 "bedomning_motiv": "2–3 meningar", "vardering": "3–4 meningar", "risker": ["..."], "katalysatorer": ["..."],
 "nyheter": [{"datum": "YYYY-MM-DD", "rubrik": "...", "kalla": "...", "url": "https://...", "paverkan": "positiv" | "negativ" | "neutral"}]}"""


def _payload(row: dict, det: dict) -> dict:
    keep = ["ticker", "name", "longName", "sector", "industry", "currency", "price", "chg", "mcapB", "revCagr",
            "revCagrYears", "revGrowth1y", "earnGrowth1y", "roe", "roic", "grossMargin", "opMargin", "netMargin",
            "fcfMargin", "de", "peg", "pe", "fwdPe", "ps", "evEbitda", "ma200w", "dist200w", "zone", "targetMean",
            "targetHigh", "targetLow", "recKey", "nAnalysts", "nextEarnings", "epsRev30", "fairValue", "impliedG"]
    return {
        "aktie": {k: row.get(k) for k in keep},
        "kursattribution": det.get("att"),
        "prognoser": det.get("est"),
        "vardering": det.get("val"),
        "analytiker_andringar": (det.get("ud") or [])[:8],
        "rekommendationer": det.get("recs"),
        "nyheter_yahoo": det.get("news"),
        "datum": datetime.now(timezone.utc).date().isoformat(),
    }


def write_note(client, row: dict, det: dict) -> dict | None:
    msgs = [{"role": "user", "content": "Skriv analysbrevet för denna aktie. Data (JSON):\n" +
             json.dumps(_payload(row, det), ensure_ascii=False, default=str)}]
    tools = [{"type": "web_search_20260318", "name": "web_search", "max_uses": MAX_SEARCHES}]
    content = []
    for _ in range(4):  # servern kan pausa långa tur med webbsök – fortsätt då
        resp = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM, tools=tools, messages=msgs)
        content += list(resp.content)
        if resp.stop_reason != "pause_turn":
            break
        msgs = msgs + [{"role": "assistant", "content": resp.content}]
    text, cites = "", {}
    for b in content:
        if getattr(b, "type", "") == "text":
            text += b.text
            for c in getattr(b, "citations", None) or []:
                url = getattr(c, "url", None)
                if url:
                    cites[url] = getattr(c, "title", None) or url
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        note = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    note["kallor"] = [{"url": u, "titel": t} for u, t in cites.items()]
    note["date"] = datetime.now(timezone.utc).date().isoformat()
    note["model"] = MODEL
    return note


def pick(rows: list[dict], wanted: list[str], prev_dates: dict, max_n: int) -> list[str]:
    """Välj aktier: din lista + aktier med signal. Skriv om brevet om det är äldre än 7 dagar,
    kursen rört sig mer än 4 % eller rapporten nyss kommit."""
    today = datetime.now(timezone.utc).date()
    by = {r["ticker"]: r for r in rows}
    cands = [t for t in wanted if t in by] + [r["ticker"] for r in rows if r.get("signals") and r["ticker"] not in wanted]
    out = []
    for t in cands:
        r, d = by[t], prev_dates.get(t)
        age = (today - datetime.fromisoformat(d).date()).days if d else 999
        moved = abs(r.get("chg") or 0) >= 4
        if age >= 7 or (moved and age >= 1):
            out.append(t)
        if len(out) >= max_n:
            break
    return out


def run(site_dir: Path, tickers: list[str] | None, wanted: list[str], max_n: int = 12, log=print) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log("Ingen ANTHROPIC_API_KEY – hoppar över analysbrev.")
        return 0
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    upath = site_dir / "data" / "universe.json"
    u = json.loads(upath.read_text(encoding="utf-8"))
    rows = u["rows"]
    by = {r["ticker"]: r for r in rows}
    fn = lambda t: site_dir / "data" / "t" / (t.replace("^", "_").replace("/", "_") + ".json")  # noqa: E731
    if tickers:
        todo = [t for t in tickers if t in by][:max_n]
    else:
        prev_dates = {r["ticker"]: r.get("aiDate") for r in rows}
        todo = pick(rows, wanted, prev_dates, max_n)
    n = 0
    for t in todo:
        p = fn(t)
        if not p.exists():
            continue
        det = json.loads(p.read_text(encoding="utf-8"))
        try:
            note = write_note(client, by[t], det)
        except Exception as e:  # noqa: BLE001
            log("AI-fel", t, str(e)[:200])
            continue
        if not note:
            log("AI: kunde inte tolka svaret för", t)
            continue
        det["ai"] = note
        by[t]["aiDate"] = note["date"]
        p.write_text(json.dumps(det, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        n += 1
        log(f"AI-analys klar: {t}")
    upath.write_text(json.dumps(u, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return n
