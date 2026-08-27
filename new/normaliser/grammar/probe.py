"""
Grammar probe: discover which phrasings each CRM service's date parser accepts.

The normaliser's canonical output form must be a form these parsers actually
handle. This sweeps controlled variants -- changing exactly one dimension at a
time -- so every failure has a single identifiable cause.

Run:  python grammar/probe.py
Out:  grammar/date_grammar.json, grammar/DATE_GRAMMAR.md
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness  # noqa: E402

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Variant corpus. Each entry: (dimension, variant_label, question, expected)
# `expected` is the (start, end) the phrase MUST resolve to, as ISO dates.
# ---------------------------------------------------------------------------

Q2_2026 = ("2026-04-01", "2026-06-30")
APR26 = ("2026-04-01", "2026-04-30")
JUN26 = ("2026-06-01", "2026-06-30")

# The two date shapes that dominate the UAT set (new.md).
CORPUS: list[tuple[str, str, str, tuple | None]] = [
    # --- explicit range: separator and preposition variants -----------------
    ("explicit_range", "between X and Y",
     "total leads between 1 April 2026 and 30 June 2026", Q2_2026),
    ("explicit_range", "from X to Y",
     "total leads from 1 April 2026 to 30 June 2026", Q2_2026),
    ("explicit_range", "X to Y (bare)",
     "total leads 1 April 2026 to 30 June 2026", Q2_2026),
    ("explicit_range", "between X to Y",
     "total leads between 1 April 2026 to 30 June 2026", Q2_2026),
    ("explicit_range", "dd-mm-yyyy",
     "total leads between 01-04-2026 and 30-06-2026", Q2_2026),
    ("explicit_range", "ordinal suffix",
     "total leads between 1st April 2026 and 30th June 2026", Q2_2026),

    # --- discrete month list: the ' and ' dependency ------------------------
    ("month_list", "A, B and C + year",
     "total leads for April, May and June 2026", Q2_2026),
    ("month_list", "A, B, C + year (no 'and')",
     "total leads for April, May, June 2026", Q2_2026),
    ("month_list", "A B and C (no commas)",
     "total leads for April May and June 2026", Q2_2026),
    ("month_list", "year on each month",
     "total leads for April 2026, May 2026 and June 2026", Q2_2026),

    # --- comparison-qualifier hyphenation ----------------------------------
    ("hyphenation", "month on month (spaces)",
     "total leads month on month for April, May and June 2026", Q2_2026),
    ("hyphenation", "month-on-month (hyphens)",
     "total leads month-on-month for April, May and June 2026", Q2_2026),
    ("hyphenation", "MoM (abbrev)",
     "total leads mom for April, May and June 2026", Q2_2026),

    # --- preposition -------------------------------------------------------
    ("preposition", "for",
     "total leads for April, May and June 2026", Q2_2026),
    ("preposition", "of",
     "total leads of April, May and June 2026", Q2_2026),
    ("preposition", "in",
     "total leads in April, May and June 2026", Q2_2026),
    ("preposition", "none",
     "total leads April, May and June 2026", Q2_2026),

    # --- single month ------------------------------------------------------
    ("single_month", "month + year",
     "total leads for June 2026", JUN26),
    ("single_month", "month only",
     "total leads for April", None),  # year inferred; recorded, not asserted

    # --- quarter -----------------------------------------------------------
    ("quarter", "Q2 2026",
     "total leads for Q2 2026", Q2_2026),
    ("quarter", "quarter 2 2026",
     "total leads for quarter 2 2026", Q2_2026),
    ("quarter", "Q2 FY2026",
     "total leads for Q2 FY2026", Q2_2026),

    # --- relative ----------------------------------------------------------
    ("relative", "current fy", "total leads for current fy", None),
    ("relative", "last fy", "total leads for last fy", None),
    ("relative", "last quarter", "total leads for last quarter", None),
    ("relative", "last qtr (abbrev)", "total leads for last qtr", None),
    ("relative", "this month", "total leads this month", None),
    ("relative", "last 40 days", "total leads for last 40 days", None),
    ("relative", "till date", "total leads from Jan 2026 till date", None),
]


def _iso(v) -> str | None:
    """Coerce a parser's varied return shapes into an ISO date string."""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y%m%d", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def extract_range(result):
    """Pull (start, end) out of whatever shape a service returned."""
    if result is None:
        return None, None
    if isinstance(result, tuple) and len(result) == 2:
        return _iso(result[0]), _iso(result[1])
    if isinstance(result, dict):
        for sk, ek in (("start_date", "end_date"), ("start", "end"), ("from", "to")):
            if sk in result:
                return _iso(result.get(sk)), _iso(result.get(ek))
        # multi-period shapes: take the span across all periods
        for key in ("periods", "months", "quarters", "years", "date_ranges", "ranges"):
            seq = result.get(key)
            if isinstance(seq, list) and seq:
                starts, ends = [], []
                for item in seq:
                    s, e = extract_range(item)
                    if s:
                        starts.append(s)
                    if e:
                        ends.append(e)
                if starts:
                    return min(starts), max(ends) if ends else None
    if isinstance(result, list) and result:
        starts, ends = [], []
        for item in result:
            s, e = extract_range(item)
            if s:
                starts.append(s)
            if e:
                ends.append(e)
        if starts:
            return min(starts), max(ends) if ends else None
    return None, None


def probe_all() -> dict:
    out: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "results": {}}

    for svc in harness.SERVICES:
        try:
            harness.load(harness.SERVICES[svc][0])
        except Exception as e:  # noqa: BLE001
            out["results"][svc] = {"status": "NOT_COVERED", "reason": str(e)[:200]}
            print(f"  {svc}: NOT COVERED -- {str(e)[:80]}")
            continue

        rows = []
        for dim, label, q, expected in CORPUS:
            result, meta = harness.parse_dates(svc, q)
            start, end = extract_range(result)

            if expected is None:
                verdict = "RECORDED"          # no ground truth asserted
            elif meta["error"]:
                verdict = "ERROR"
            elif start is None and end is None:
                verdict = "NO_PARSE"          # fell through -> silent default
            elif (start, end) == expected:
                verdict = "OK"
            else:
                verdict = "WRONG"             # parsed, but to the wrong range

            rows.append({
                "dimension": dim, "variant": label, "question": q,
                "expected": list(expected) if expected else None,
                "got": [start, end],
                "verdict": verdict,
                "error": meta["error"],
                "used_llm": bool(meta["llm_calls"]),
                "used_db": bool(meta["db_calls"]),
            })
        out["results"][svc] = {"status": "COVERED", "rows": rows}

        counts: dict[str, int] = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"  {svc:8s} {counts}")

    return out


if __name__ == "__main__":
    print("Probing date grammar across CRM services...\n")
    data = probe_all()
    (HERE / "date_grammar.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nWrote {HERE / 'date_grammar.json'}")
