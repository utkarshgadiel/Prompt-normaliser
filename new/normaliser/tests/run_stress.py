"""Phrasing stress test.

Generates paraphrase variants of the real query families and checks mechanical
invariants on the plan, rather than eyeballing output. Every failure printed is
a concrete defect: something the user wrote that the plan silently lost.

Invariants:
  GROUPING  a "<facet> wise" / "<facet>-wise" / "by <facet>" phrase must appear
            in the plan's groupings
  FILTER    a named entity value must survive as a filter (unless grouped)
  METRICS   coordinated qualifiers must yield one metric each
  PERIOD    a stated period must not fall back to the declared default
  OK        the plan must run, or refuse for a stated reason
"""
import sys, itertools
from datetime import date
from pathlib import Path
sys.path.insert(0, r"d:\CRM\new\normaliser\src")
from normaliser import normalise

TODAY = date(2026, 9, 2)
fails, total = [], 0

def check(prompt, *, grouping=None, filters=(), metrics=None, period=True):
    global total
    total += 1
    try:
        n = normalise(prompt, TODAY)
    except Exception as e:
        fails.append((prompt, f"RAISED {type(e).__name__}: {e}")); return
    if not n.ok:
        fails.append((prompt, f"REFUSED: {(n.clarification or '')[:70]}")); return
    if grouping and not any(grouping in c.groupings for c in n.calls):
        fails.append((prompt, f"GROUPING lost '{grouping}' -> {n.calls[0].groupings} "
                              f"| {n.calls[0].canonical_text!r}")); return
    for facet, val in filters:
        if not any(val in c.filters.get(facet, []) for c in n.calls):
            fails.append((prompt, f"FILTER lost {facet}={val} -> "
                                  f"{n.calls[0].filters} | {n.calls[0].canonical_text!r}")); return
    if metrics and {c.metric for c in n.calls} != set(metrics):
        fails.append((prompt, f"METRICS {sorted({c.metric for c in n.calls})} "
                              f"!= {sorted(metrics)}")); return
    if period and any("defaulted to the current financial year" in w for w in n.warnings):
        fails.append((prompt, "PERIOD silently defaulted")); return

# ---------------------------------------------------------------- groupings
FACETS = {
    "project": "project", "product": "product", "source": "source",
    "sub source": "subsource", "sub-source": "subsource",
    "city": "city", "status": "status", "owner": "owner",
    "service request type": "service_request_type",
    "property type": "property_type",
}
METRIC_NOUNS = ["total leads", "total sales", "total tasks", "total cases",
                "meetings booked", "total opportunities"]
for (phrase, facet), noun in itertools.product(FACETS.items(), METRIC_NOUNS):
    h = phrase.replace(" ", "-")
    for form in (f"{phrase} wise {noun} for last month",
                 f"{h}-wise {noun} for last month",
                 f"{noun} {phrase} wise for last month",
                 f"{noun} by {phrase} for last month",
                 f"show me {h}-wise {noun} in Q1 2025"):
        check(form, grouping=facet)

# ---------------------------------------------------------------- shared noun
SHARED = [
    ("cold and hot leads last month", {"cold_leads", "hot_leads"}),
    ("hot, cold and warm leads last month", {"hot_leads", "cold_leads", "warm_leads"}),
    ("junk and valid leads last month", {"junk_leads", "valid_leads"}),
    ("new and nurturing leads last month", {"new_leads", "nurturing_leads"}),
    ("open and completed tasks last month", {"open_tasks", "completed_tasks"}),
    ("open, completed and cancelled tasks last month",
     {"open_tasks", "completed_tasks", "cancelled_tasks"}),
    ("satisfied and complaint cases last month", {"satisfied_cases", "complaint_cases"}),
]
for p, m in SHARED:
    check(p, metrics=m)
    check(p.replace("last month", "in delhi last month"), metrics=m,
          filters=[("city", "Delhi")])

# ---------------------------------------------------------------- entities
ENTS = [("project", "Wave City", "wave city"), ("project", "Wave Estate", "wave estate"),
        ("project", "WMCC Sec 32", "wmcc"), ("product", "EDEN", "eden"),
        ("product", "Veridia", "veridia"), ("source", "Digital", "digital"),
        ("source", "Channel Partner", "channel partner"),
        ("subsource", "Facebook", "facebook"), ("city", "Delhi", "delhi")]
for facet, canon, typed in ENTS:
    for form in (f"total leads for {typed} last month",
                 f"total leads in {typed} last month",
                 f"how many leads were generated in {typed} last month",
                 f"give me the lead count of {typed} for last month",
                 f"{typed} total leads last month"):
        check(form, filters=[(facet, canon)])

# ---------------------------------------------------------------- periods
PERIODS = ["last month", "this month", "last quarter", "this quarter", "last fy",
           "current fy", "Q1 2025", "q3 2024", "fy 2024", "April 2025",
           "April to June 2025", "1 April 2025 to 30 June 2025",
           "last 30 days", "last 3 months", "last 2 quarters", "last 2 years",
           "last week", "2022 to 2024", "April 2025 through June 2025"]
for p in PERIODS:
    check(f"total leads for {p}")
    check(f"show me total sales for {p}")

# ---------------------------------------------------------------- phrasing
for form in ["how many leads do we have for last month",
             "what is the total lead count for last month",
             "give me leads for last month",
             "leads last month",
             "I want to see total leads for last month",
             "can you show total leads for last month",
             "pull up total leads for last month",
             "total no of leads last month",
             "lead count last month"]:
    check(form)

print(f"variants checked: {total}   failures: {len(fails)}")
seen = set()
for p, why in fails:
    key = why.split()[0]
    seen.add(key)
    print(f"  [{key}] {p}\n        {why}")
print("\nfailure classes:", sorted(seen) or "none")
