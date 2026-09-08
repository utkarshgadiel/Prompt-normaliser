"""
Deterministic funnel table rendering.

The funnel backends return ONE flat record per row holding the stage counts and
the conversion ratios interleaved in alphabetical order:

    {"Junk %": "31.2%", "Junk Leads": 11514, "MB:MD": 2.02, ..., "Valid Leads": 25390}

The master agent is supposed to split that into two tables. In practice it does
so unreliably -- the same question produced both tables at 2:33pm on 8 Sep 2026
and only the metrics table at 3:14pm, from an identical response. Prose cannot
fix that, because there is no instruction strong enough to make an LLM split a
record it has already decided looks like one table.

So the split happens here instead, in code, and the agent transcribes the
markdown it is handed. That is the one thing agents do reliably: the graph
tool's `markdown` field has been copied through correctly every single time,
because copying is not a judgement call.

Everything this module decides is fixed:
  * which keys are counts and which are ratios (a colon in the key)
  * the column order of both tables
  * that the ratios table shows exactly five stage-to-stage columns
  * Indian digit grouping
  * that a single-row table gets no S.No and no Total row
  * that the Total row is copied from `totals`, never summed
"""
from __future__ import annotations

from typing import Any

# Table 1, in display order. Keys are what the backends emit; values are the
# column headers the behaviour spec requires.
METRIC_COLUMNS: list[tuple[str, str]] = [
    ("Total Leads",            "Total Leads (TL)"),
    ("Junk Leads",             "Junk Leads"),
    ("Junk %",                 "Junk %"),
    ("Valid Leads",            "Valid Leads (VL)"),
    ("SOL Leads (Interested)", "Qualified Leads (SOL)"),
    ("Meeting Booked",         "Meeting Booked (MB)"),
    ("Meeting Done",           "Meeting Done (MD)"),
    ("Sales Done",             "Sale Done (SD)"),
]

# Table 2. Exactly these five, in this order. TL:SD, VL:SD, SOL:SD and MB:SD
# are returned by the backends but skip stages, so they are never displayed --
# nine ratio columns cannot be read across as a sequence.
RATIO_COLUMNS: list[str] = ["TL:VL", "VL:SOL", "SOL:MB", "MB:MD", "MD:SD"]

# Columns that cannot be summed, so they carry an em dash in the Total row.
_NOT_SUMMABLE = {"Junk %"}

EM_DASH = "—"


def indian_group(n: int) -> str:
    """Group digits the Indian way: last three, then twos.

    1818 -> "1,818"; 272488 -> "2,72,488"; 10000000 -> "1,00,00,000".

    Digit-preserving by construction: the output is the input's digits with
    commas inserted, so a number can never gain, lose or reorder a digit.
    """
    sign = "-" if n < 0 else ""
    digits = str(abs(int(n)))
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail])


def _fmt(value: Any, kind: str = "count") -> str:
    """Render one cell.

    `kind` is "count", "ratio" or "percent", and it is decided by which column
    the cell sits in -- never by inspecting the value. A ratio of exactly 2.0
    must print as 2.00, not 2: guessing from the value alone rendered MB:MD
    as a bare 2 in a column where every other cell had two decimals.
    """
    if value is None or value == "":
        return EM_DASH
    if isinstance(value, bool):
        return str(value)
    if kind == "ratio":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
    if kind == "percent":
        if isinstance(value, str):
            return value if value.strip().endswith("%") else f"{value}%"
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (int, float)) and float(value).is_integer():
        return indian_group(int(value))
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _kind_of(key: str) -> str:
    if _is_ratio(key):
        return "ratio"
    return "percent" if key.strip().endswith("%") else "count"


def _is_ratio(key: str) -> bool:
    return ":" in key


def _row_of(record: dict[str, Any]) -> dict[str, Any]:
    """A funnel record, whatever wrapper it arrived in."""
    return {k: v for k, v in record.items() if not isinstance(v, (dict, list))}


def extract_rows(payload: dict[str, Any]) -> tuple[list[tuple[str, dict]], str]:
    """Pull (scope_label, record) pairs out of any funnel response shape.

    Handles the three shapes the seven services actually return:
      * single period   -> {"lead_funnel": {...17 keys...}}
      * breakdown dict  -> {"product_wise_metrics": {"EDEN": {...}, ...}}
      * breakdown list  -> {"data": [{"product": "EDEN", ...}, ...]}

    Returns the rows and the scope column name ("" when there is no breakdown).
    """
    # A breakdown keyed by scope name.
    for key, value in payload.items():
        if key == "totals" or not isinstance(value, dict):
            continue
        inner = list(value.values())
        if inner and all(isinstance(v, dict) for v in inner):
            return ([(str(k), _row_of(v)) for k, v in value.items()],
                    _scope_name(key))

    # A breakdown as a list of records.
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            rows = []
            for rec in value:
                label = next((str(v) for k, v in rec.items()
                              if isinstance(v, str) and not _is_ratio(k)), "")
                if label.lower() == "total":
                    continue          # a totals summary, not a data row
                rows.append((label, _row_of(rec)))
            return rows, _scope_name(key)

    # A single funnel record.
    for key, value in payload.items():
        if isinstance(value, dict) and any(_is_ratio(k) for k in value):
            return [("", _row_of(value))], ""

    # The record itself, unwrapped.
    if any(_is_ratio(k) for k in payload):
        return [("", _row_of(payload))], ""
    return [], ""


def _scope_name(key: str) -> str:
    k = key.lower()
    for token, label in (("subsource", "Sub-Source"), ("sub_source", "Sub-Source"),
                         ("product", "Product"), ("project", "Project"),
                         ("source", "Source"), ("user", "User"),
                         ("city", "City"), ("month", "Month")):
        if token in k:
            return label
    return "Scope"


def render(payload: dict[str, Any], heading: str = "",
           show: str = "both") -> dict[str, Any]:
    """Render a funnel response as the two markdown tables.

    `show` is "both" (the default), "metrics" or "ratios", and reflects only
    what the user asked for in words -- never a judgement about the data.
    """
    rows, scope = extract_rows(payload)
    if not rows:
        return {"ok": False, "error": "No funnel rows found in the payload.",
                "metrics_table": "", "ratios_table": "", "markdown": ""}

    totals = payload.get("totals") or {}
    multi = len(rows) > 1

    metrics_md = _metrics_table(rows, scope, totals, multi)
    ratios_md = _ratios_table(rows, scope, multi)

    blocks = []
    if show in ("both", "metrics"):
        blocks.append(f"📊 Funnel Metrics{heading and ' — ' + heading}\n\n{metrics_md}")
    if show in ("both", "ratios"):
        blocks.append(
            f"📊 Funnel Conversion Ratios{heading and ' — ' + heading}\n\n{ratios_md}")

    return {
        "ok": True,
        "row_count": len(rows),
        "scope_column": scope,
        "metrics_table": metrics_md,
        "ratios_table": ratios_md,
        "markdown": "\n\n".join(blocks),
    }


def _present(rows: list[tuple[str, dict]], key: str) -> bool:
    """Only show a column some row actually reported.

    The user funnels report meetings and sales but no lead stages, so their
    metrics table has no TL/Junk/VL/SOL columns at all. An em dash would say
    the backend returned nothing for a column it has; the truth is it has no
    such column.
    """
    return any(key in rec for _, rec in rows)


def _metrics_table(rows, scope, totals, multi) -> str:
    cols = [(k, h) for k, h in METRIC_COLUMNS if _present(rows, k)]
    header = ([("S.No"), (scope or "Scope")] if multi else []) + [h for _, h in cols]

    body = []
    for i, (label, rec) in enumerate(rows, 1):
        cells = ([str(i), label] if multi else []) + [
            _fmt(rec.get(k), _kind_of(k)) for k, _ in cols]
        body.append(cells)

    if multi:
        # The Total row is COPIED from the backend's totals block, never summed.
        # A breakdown that can double-count (a lead under two sub-sources) makes
        # the row sum legitimately disagree with the backend's figure.
        total_cells = ["Total", ""]
        for k, _ in cols:
            total_cells.append(
                EM_DASH if k in _NOT_SUMMABLE or k not in totals
                else _fmt(totals[k], _kind_of(k)))
        body.append(total_cells)

    return _markdown(header, body)


def _ratios_table(rows, scope, multi) -> str:
    cols = [c for c in RATIO_COLUMNS if _present(rows, c)]
    # No S.No on the ratios table, and no Total row at all: ratios do not sum,
    # and an empty Total row only invites the reader to look for one.
    header = ([scope or "Scope"] if multi else []) + cols
    body = []
    for label, rec in rows:
        body.append(([label] if multi else [])
                    + [_fmt(rec.get(c), "ratio") for c in cols])
    return _markdown(header, body)


def _markdown(header: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
