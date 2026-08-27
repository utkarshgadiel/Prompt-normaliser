"""
Build the canonical vocabulary from the real CRM data.

The vocabulary is generated from the source CSVs, not transcribed from
behavior.md -- the prompt's knowledge base was measured to disagree with the
data (phantom projects, misspelled products, missing sources).

Emits src/vocabulary.json:
  canonical  -> the exact stored value, used for filters
  aliases    -> user-typed variants that map to it
  column     -> where it lives, per table
  row_count  -> frequency, for disambiguation and for flagging long-tail noise

Run: python src/vocab_build.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data"
OUT = Path(__file__).parent / "vocabulary.json"

csv.field_size_limit(10_000_000)

# table -> (filename, {facet: column})
SOURCES = {
    "lead": ("lead_report_new (1).csv", {
        "project": "Project__c",
        "product": "Product_Category__c",
        "source": "Lead_Source__c",
        "subsource": "Lead_Source_Sub_Category__c",
        "feedback": "Customer_Feedback__c",
        "status": "Status",
        "rating": "Rating__c",
        "property_type": "Property_Type__c",
        "city": "City__c",
        "owner": "OwnerName__c",
        "disqualification_reason": "Disqualification_Reason__c",
        "junk_reason": "Junk_Reason__c",
    }),
    "opportunity": ("opportunities_report.csv", {
        "project": "Project__c",
        "product": "Project_Category__c",      # NB: differs from other tables
        "source": "Lead_Source__c",
        "subsource": "Lead_Source_Sub_Category__c",
        "property_type": "Property_Type__c",
        "owner": "Owner_Name__c",
        "disqualification_reason": "Disqualification_Reason__c",
    }),
    "event": ("Events_report.csv", {
        "project": "Project__c",
        "product": "Product_Category__c",
        "subject": "Subject__c",
        "appointment_status": "Appointment_Status__c",
        "owner": "OwnerName__c",
    }),
    "task": ("task_report.csv", {
        "project": "Project__c",
        "product": "Product_Category__c",
        "subject": "Subject__c",
        "status": "Status__c",
        "feedback": "Customer_Feedback__c",
        "sales_team_feedback": "Sales_Team_Feedback__c",
        "transfer_status": "Transfer_Status__c",
        "owner": "OwnerName__c",
    }),
    "case": ("service_request_report.csv", {
        "project": "Project__c",
        "product": "Product_Category__c",
        "service_category": "Service_Category__c",
        "service_subcategory": "Service_Sub_catogery__c",   # sic: stored misspelled
        "service_request_type": "Service_Request_Type__c",
        "feedback": "Feedback__c",
        "owner": "Owner_Name__c",
    }),
}

# Values that are data noise rather than real business entities.
NOISE = {"", "#n/a", "n/a", "null", "none", "-", "generic"}

# A facet with more distinct values than this is free text, not a controlled
# vocabulary. Measured: subject has 77,905 distinct values (task Subject__c is
# free text) and city has 4,762. Alias-matching against those produces constant
# false positives -- e.g. the subject 'We were unable to contact you_Wave City'
# occurs 12,291 times and contains a project name.
CONTROLLED_MAX_CARDINALITY = 500

# Ignore long-tail values below this row count. Drops one-off data corruption
# such as 'Digital' and '#N/A' appearing in Project__c.
MIN_ROWS = 5

# Free-text facets still need *some* entries (users do filter by city and by
# subject), so keep only the frequent, unambiguous ones.
FREE_TEXT_MIN_ROWS = 200


def _norm(s: str) -> str:
    """Aggressive fold used for alias matching: case, punctuation, spacing."""
    s = s.lower().strip()
    s = re.sub(r"[_\-\.]+", " ", s)
    s = re.sub(r"[^\w\s()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_forms(value: str) -> set[str]:
    """Plausible ways a user might type this value."""
    n = _norm(value)
    forms = {n, n.replace(" ", ""), n.replace(" ", "-")}
    # trailing parenthetical codes: "lig_001_(310)" -> "lig 001", "lig"
    stripped = re.sub(r"\s*\([^)]*\)", "", n).strip()
    if stripped:
        forms.add(stripped)
        forms.add(stripped.replace(" ", ""))
    # drop trailing digit-only tokens: "veridia 3" -> "veridia"
    head = re.sub(r"\s+\d+$", "", stripped).strip()
    if head and head != stripped:
        forms.add(head)
    return {f for f in forms if f}


def build() -> dict:
    facets: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    columns: dict[str, dict[str, str]] = defaultdict(dict)
    missing_files = []

    for table, (fname, colmap) in SOURCES.items():
        path = DATA / fname
        if not path.exists():
            missing_files.append(fname)
            continue
        # Indexed csv.reader rather than DictReader: an order of magnitude
        # faster across ~1.35M rows, which matters at 215MB of input.
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                continue
            idx = {f: header.index(c) for f, c in colmap.items() if c in header}
            for facet in idx:
                columns[facet][table] = colmap[facet]

            counters = {f: facets[f][table] for f in idx}
            pairs = list(idx.items())
            width = len(header)
            for row in reader:
                if len(row) != width:
                    continue                      # ragged line: skip, don't guess
                for facet, i in pairs:
                    raw = row[i].strip()
                    if raw:
                        counters[facet][raw] += 1

        # Drop noise once, after counting, rather than normalising every cell.
        for facet in list(facets):
            c = facets[facet].get(table)
            if c:
                for val in [v for v in c if _norm(v) in NOISE]:
                    del c[val]

    vocab: dict = {"facets": {}, "facet_meta": {},
                   "columns": {k: v for k, v in columns.items()},
                   "missing_files": missing_files}

    for facet, per_table in facets.items():
        # Merge case/spacing variants under the most frequent spelling.
        # Normalise each distinct value exactly once and record its tables as
        # we go -- recomputing per entry is quadratic and was the hot spot.
        merged: dict[str, Counter] = defaultdict(Counter)
        key_tables: dict[str, set[str]] = defaultdict(set)
        for table, counter in per_table.items():
            for value, n in counter.items():
                key = _norm(value)
                merged[key][value] += n
                key_tables[key].add(table)

        entries = []
        for key, spellings in merged.items():
            canonical, _ = spellings.most_common(1)[0]
            aliases: set[str] = set()
            for sp in spellings:
                aliases |= _alias_forms(sp)
            entries.append({
                "canonical": canonical,
                "aliases": sorted(aliases),
                "variants": sorted(spellings),          # every stored spelling
                "row_count": sum(spellings.values()),
                "tables": sorted(key_tables[key]),
            })
        entries.sort(key=lambda e: -e["row_count"])

        # Classify, then apply the appropriate frequency floor.
        free_text = len(entries) > CONTROLLED_MAX_CARDINALITY
        floor = FREE_TEXT_MIN_ROWS if free_text else MIN_ROWS
        kept = [e for e in entries if e["row_count"] >= floor]

        vocab["facets"][facet] = kept
        vocab["facet_meta"][facet] = {
            "kind": "free_text" if free_text else "controlled",
            "distinct_total": len(entries),
            "kept": len(kept),
            "min_rows": floor,
            # Free-text facets are unsafe for loose alias matching.
            "alias_matchable": not free_text,
        }

    # Head-token aliases: users type "WMCC" for "WMCC Sec 32". Added only when
    # the first token is globally unique across every alias-matchable entry
    # AND looks like an acronym (no vowels) -- "wave" (many entries) and
    # dictionary words like "social" or "media" (which matched unrelated
    # values when this was tried unrestricted) are never added, while "wmcc"
    # is. Found via the batch run of 27 Aug 2026, where every "for WMCC"
    # filter was silently dropped.
    head_count: Counter = Counter()
    matchable = [f for f, m in vocab["facet_meta"].items() if m["alias_matchable"]]
    for facet in matchable:
        for e in vocab["facets"][facet]:
            tok = _norm(e["canonical"]).split(" ")[0]
            head_count[tok] += 1
    for facet in matchable:
        for e in vocab["facets"][facet]:
            tok = _norm(e["canonical"]).split(" ")[0]
            if (len(tok) >= 4 and not tok.isdigit() and head_count[tok] == 1
                    and not any(v in tok for v in "aeiou")
                    and tok not in e["aliases"]):
                e["aliases"] = sorted(set(e["aliases"]) | {tok})

    return vocab


if __name__ == "__main__":
    v = build()
    if v["missing_files"]:
        print(f"WARNING missing data files: {v['missing_files']}", file=sys.stderr)
    OUT.write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}\n")
    for facet, entries in sorted(v["facets"].items()):
        meta = v["facet_meta"][facet]
        multi = sum(1 for e in entries if len(e["variants"]) > 1)
        note = f" {multi} w/variants" if multi else ""
        flag = "" if meta["alias_matchable"] else "  [FREE TEXT - exact match only]"
        print(f"  {facet:24s} {len(entries):4d} kept of {meta['distinct_total']:6d}{note}{flag}")
