"""
Deterministic funnel table rendering.

The funnel backends return ONE flat record per row holding the stage counts and
the conversion ratios interleaved in alphabetical order:

    {"Junk %": "31.2%", "Junk Leads": 11514, "MB:MD": 2.02, ..., "Valid Leads": 25390}

Production incidents included omitted ratio tables and lost columns. This module
makes the table split, column order and number formatting deterministic. Agents
must still validate the source response and copy the successful output without
omitting rows; rendering alone cannot guarantee end-to-end accuracy.

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
from decimal import Decimal, InvalidOperation
import html

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
        raise ValueError("A boolean is not a funnel metric.")
    try:
        text = str(value).strip()
        number = Decimal(text[:-1] if kind == "percent" and text.endswith("%") else text)
    except InvalidOperation:
        raise ValueError("A funnel metric must be numeric or null.")
    if not number.is_finite():
        raise ValueError("A non-finite funnel metric cannot be displayed.")
    if kind == "ratio":
        return f"{number:.2f}"
    if kind == "percent":
        return f"{number:.2f}%"
    if number != number.to_integral_value():
        raise ValueError("A count must be an integer; refusing to round it.")
    return indian_group(int(number))


def _kind_of(key: str) -> str:
    if _is_ratio(key):
        return "ratio"
    return "percent" if key.strip().endswith("%") else "count"


def _is_ratio(key: str) -> bool:
    return ":" in key


def _row_of(record: dict[str, Any]) -> dict[str, Any]:
    """A funnel record, whatever wrapper it arrived in."""
    return {k: v for k, v in record.items() if not isinstance(v, (dict, list))}


# The scope column each funnel tool breaks down by. Keyed on the tool name from
# the plan, because the payload cannot be trusted to say: project_funnel groups
# by project_c but emits its rows under the key "product_wise_metrics", so
# inferring "Product" from that key would mislabel every project funnel.
TOOL_SCOPE: dict[str, str] = {
    "lead_funnel":        "",            # overall funnel, no breakdown
    "product_funnel":     "Product",
    "project_funnel":     "Project",
    "source_funnel":      "Source",
    "subsource_funnel":   "Sub-Source",
    "lead_user_funnel":   "User",
    "sales_user_funnel":  "User",
}

# Wrapper keys that hold the rows, across the seven services. Some hold a dict
# keyed by scope name, others a list of {"name": ..., **metrics} records.
_ROW_KEYS = (
    "lead_funnel", "product_wise_metrics", "source_wise_metrics",
    "sub_source_wise_metrics", "subsource_wise_metrics",
    "lead_wise_user_metrics", "sales_wise_user_metrics",
    "funnel", "sources", "data",
)

# Keys that never hold funnel rows, whatever their shape.
_NOT_ROWS = {"totals", "metadata", "intent_summary", "execution", "date_ranges",
             "llm_intent", "date_intent", "schema", "periods_data"}


def _label_of(rec: dict[str, Any]) -> str:
    """The scope name in a list-style record.

    The services build these as {"name": <scope>, **metrics}, so `name` is
    checked first; the fallback covers any service that labels it differently.
    """
    for key in ("name", "scope", "label", "product", "project", "source",
                "sub_source", "subsource", "user", "user_name"):
        if isinstance(rec.get(key), str):
            return rec[key]
    for key, value in rec.items():
        if isinstance(value, str) and not _is_ratio(key) and not key.endswith("%"):
            return value
    return ""


# The count keys every funnel record carries at least one of. A dict without
# any of these, and without a ratio, is not a funnel row whatever else it is.
_COUNT_KEYS = {k for k, _ in METRIC_COLUMNS}


def _is_funnel_record(rec: Any) -> bool:
    """True only for a dict that actually holds funnel figures.

    Without this the extractor accepted any dict-of-dicts as a breakdown. A
    product funnel filtered to a product with no leads returns
    {"responses": {"funnel for EDEN fy 2021": {"result": {"status":
    "no_data"}}}}, and that was rendered as a one-row table whose Product was
    the literal question string, reported ok true. A wrong table that claims
    success is worse than an error, so a row now has to prove it is one.
    """
    if not isinstance(rec, dict):
        return False
    return any(k in RATIO_COLUMNS or k in _COUNT_KEYS for k in rec)


def _rows_from(value: Any) -> list[tuple[str, dict]] | None:
    """Rows out of one wrapper value, or None if it holds no funnel rows."""
    if isinstance(value, dict):
        if _is_funnel_record(value):                   # a single flat record
            return [("", _row_of(value))]
        inner = list(value.values())
        if inner and all(_is_funnel_record(v) for v in inner):
            return [(str(k), _row_of(v)) for k, v in value.items()
                    if str(k).strip().lower() != "total"]
        return None
    if isinstance(value, list) and value:
        rows = []
        for rec in value:
            if not _is_funnel_record(rec):
                raise ValueError("A funnel row is malformed; refusing to silently drop it.")
            label = _label_of(rec)
            if label.strip().lower() == "total":
                continue                               # a summary, not a row
            rows.append((label, _row_of(rec)))
        return rows or None
    return None


def _unwrap(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Peel the per-question wrapper product_funnel uses when it is filtered.

    Filtered to one product it answers with
    {"responses": {"<the question>": {"result": {...}}}} rather than the flat
    shape, and `result` is where the funnel actually lives -- or where a
    no_data status says there is none. Returns the payload to read, plus an
    explanatory message when the service reported no data.
    """
    if payload.get("error") or str(payload.get("status", "")).lower() in ("error", "failed"):
        raise ValueError(str(payload.get("error") or payload.get("message") or "Funnel service failed."))
    responses = payload.get("responses")
    if isinstance(responses, dict) and responses:
        if len(responses) != 1:
            raise ValueError("Multiple wrapped results must be formatted separately; merging would lose periods or rows.")
        entry = next(iter(responses.values()))
        if not isinstance(entry, dict):
            raise ValueError("Malformed wrapped funnel result.")
        result = entry.get("result", entry)
        if not isinstance(result, dict):
            raise ValueError("Malformed wrapped funnel result.")
        return _unwrap(result)

    if payload.get("error") or str(payload.get("status", "")).lower() in ("error", "failed"):
        raise ValueError(str(payload.get("error") or payload.get("message") or "Funnel service failed."))

    # A bare no_data / message payload with no funnel block at all.
    if str(payload.get("status", "")).lower() in ("no_data", "empty"):
        return payload, str(payload.get("message") or "No data.")
    return payload, None


def extract_rows(payload: dict[str, Any],
                 tool: str = "") -> tuple[list[tuple[str, dict]], str]:
    """Pull (scope_label, record) pairs out of any funnel response shape.

    Covers every shape the seven services return:
      * overall        -> {"lead_funnel": {...17 keys...}}
      * dict breakdown -> {"product_wise_metrics": {"EDEN": {...}, ...}}
      * list breakdown -> {"source_wise_metrics": [{"name": "Digital", ...}]}
      * the bare record, unwrapped

    `tool` comes from the plan and decides the scope column. Without it the
    column falls back to "Scope", which is honest rather than wrong.
    """
    scope = TOOL_SCOPE.get(tool.strip().lower(), "") if tool else ""

    # Named wrappers first, in the order the services use them.
    for key in _ROW_KEYS:
        if key in payload:
            rows = _rows_from(payload[key])
            if rows:
                return rows, _resolve_scope(scope, tool, rows)

    # Then any other wrapper that looks like funnel rows.
    for key, value in payload.items():
        if key in _NOT_ROWS:
            continue
        rows = _rows_from(value)
        if rows:
            return rows, _resolve_scope(scope, tool, rows)

    # The record itself, unwrapped.
    if _is_funnel_record(payload):
        return [("", _row_of(payload))], ""
    return [], ""


def _resolve_scope(scope: str, tool: str, rows: list) -> str:
    """A breakdown needs a scope column; a single unlabelled row does not."""
    if len(rows) == 1 and not rows[0][0]:
        return ""
    if scope:
        return scope
    return "Scope" if not tool else TOOL_SCOPE.get(tool.strip().lower(), "Scope")


def render(payload: dict[str, Any], heading: str = "",
           show: str = "both", tool: str = "", include_totals: bool = True,
           period_column: str = "") -> dict[str, Any]:
    """Render or return an explicit failure; malformed data is never skipped."""
    try:
        if show not in ("both", "metrics", "ratios"):
            raise ValueError("show must be both, metrics or ratios.")
        if tool and tool not in TOOL_SCOPE:
            raise ValueError("Unrecognised funnel tool.")
        if period_column not in ("", "Month", "Quarter", "Financial Year", "Period"):
            raise ValueError("Unrecognised period column.")
        return _render(payload, heading, show, tool, include_totals, period_column)
    except (ValueError, TypeError, AttributeError) as exc:
        return {"ok": False, "error": str(exc), "row_count": 0,
                "metrics_table": "", "ratios_table": "", "markdown": "",
                "scope_column": ""}


def _render(payload: dict[str, Any], heading: str,
            show: str, tool: str, include_totals: bool,
            period_column: str) -> dict[str, Any]:
    """Render a funnel response as the two markdown tables.

    `show` is "both" (the default), "metrics" or "ratios", and reflects only
    what the user asked for in words -- never a judgement about the data.
    """
    payload, no_data = _unwrap(payload)
    rows, scope = extract_rows(payload, tool)
    if period_column:
        scope = period_column
    if no_data and rows:
        raise ValueError("The response says no data but also carries funnel rows.")
    if no_data and not rows:
        # A real, honest empty result. Say so plainly rather than rendering
        # an empty table, which would read as "we measured zero" instead of
        # "there is nothing here for this scope and period".
        return {"ok": False, "error": no_data, "empty": True,
                "row_count": 0, "scope_column": "",
                "metrics_table": "", "ratios_table": "", "markdown": ""}
    if not rows:
        return {"ok": False,
                "error": ("No funnel rows found in the payload. Send the "
                          "funnel tool's response exactly as it came back."),
                "row_count": 0, "scope_column": "",
                "metrics_table": "", "ratios_table": "", "markdown": ""}

    totals = dict(payload.get("totals") or {})
    for key in _ROW_KEYS:
        block = payload.get(key)
        candidates = ([(str(k), v) for k, v in block.items()] if isinstance(block, dict)
                      else [(_label_of(v), v) for v in block if isinstance(v, dict)]
                      if isinstance(block, list) else [])
        for label, record in candidates:
            if label.strip().lower() != "total" or not isinstance(record, dict):
                continue
            for metric, value in record.items():
                if metric not in _COUNT_KEYS:
                    continue
                if metric in totals and totals[metric] != value:
                    raise ValueError("The totals block and Total row disagree.")
                totals[metric] = value
    if not include_totals:
        totals = {}
    # Two independent questions. `labelled` decides whether a breakdown
    # column appears -- a funnel filtered to ONE source is still a source
    # breakdown and must say which source. `multi` decides S.No and the
    # Total row, which only make sense across two or more rows.
    labelled = any(label for label, _ in rows)
    multi = len(rows) > 1

    metrics_md = _metrics_table(rows, scope, totals, multi, labelled)
    ratios_md = _ratios_table(rows, scope, labelled)

    blocks = []
    if show in ("both", "metrics"):
        blocks.append(f"📊 Funnel Metrics{heading and ' — ' + heading}\n\n{metrics_md}")
    if show in ("both", "ratios"):
        blocks.append(
            f"📊 Funnel Conversion Ratios{heading and ' — ' + heading}\n\n{ratios_md}")

    # A dropped key must never be silent. A lead or breakdown funnel reports
    # all five stage-to-stage ratios. Absence is an error; compare with the
    # original response before attributing it to the backend or transmission. On 8 Sep 2026
    # MD:SD was present in the tool response and missing from what reached this
    # function, and the ratios table quietly came out with four columns.
    # The user funnels are the honest exception: they carry no lead stages, so
    # TL:VL, VL:SOL and SOL:MB genuinely do not exist for them.
    expected = ([c for c in RATIO_COLUMNS if c in ("MB:MD", "MD:SD")]
                if _user_funnel(tool, rows) else RATIO_COLUMNS)
    missing = [c for c in expected if any(c not in rec for _, rec in rows)]

    out = {
        "ok": True,
        "row_count": len(rows),
        "scope_column": scope,
        "metrics_table": metrics_md,
        "ratios_table": ratios_md,
        "markdown": "\n\n".join(blocks),
    }
    expected_metrics = (["Meeting Booked", "Meeting Done", "Sales Done"]
                        if _user_funnel(tool, rows) else [k for k, _ in METRIC_COLUMNS])
    missing_metrics = [k for k in expected_metrics if any(k not in rec for _, rec in rows)]
    if missing_metrics:
        out["missing_metric_columns"] = missing_metrics
    if missing or missing_metrics:
        out["ok"] = False
        out["missing_ratio_columns"] = missing
        out["warning"] = (
            f"Required fields are absent from one or more rows: {', '.join(missing + missing_metrics)}. "
            "Compare with the original response; the origin of the missing fields is unverified."
        )
        out["error"] = out["warning"]
        # Partial tables are diagnostic only. No finished answer on failure.
        out["markdown"] = ""
    return out


def _user_funnel(tool: str, rows: list[tuple[str, dict]]) -> bool:
    """A user funnel has no lead stages, so it reports only MB:MD and MD:SD."""
    if tool:
        return tool.strip().lower() in ("lead_user_funnel", "sales_user_funnel")
    return not any("Total Leads" in rec for _, rec in rows)


def _present(rows: list[tuple[str, dict]], key: str) -> bool:
    """Only show a column some row actually reported.

    The user funnels report meetings and sales but no lead stages, so their
    metrics table has no TL/Junk/VL/SOL columns at all. An em dash would say
    the backend returned nothing for a column it has; the truth is it has no
    such column.
    """
    return any(key in rec for _, rec in rows)


def _metrics_table(rows, scope, totals, multi, labelled) -> str:
    cols = [(k, h) for k, h in METRIC_COLUMNS if _present(rows, k)]
    header = ((["S.No"] if multi else [])
              + ([scope or "Scope"] if labelled else [])
              + [h for _, h in cols])

    body = []
    for i, (label, rec) in enumerate(rows, 1):
        cells = ([str(i)] if multi else []) + ([label] if labelled else []) + [
            _fmt(rec.get(k), _kind_of(k)) for k, _ in cols]
        body.append(cells)

    if multi and totals:
        # The Total row is COPIED from the backend's totals block, never summed.
        # A breakdown that can double-count (a lead under two sub-sources) makes
        # the row sum legitimately disagree with the backend's figure.
        total_cells = ["Total"] + ([""] if labelled else [])
        for k, _ in cols:
            total_cells.append(
                EM_DASH if k in _NOT_SUMMABLE or k not in totals
                else _fmt(totals[k], _kind_of(k)))
        body.append(total_cells)

    return _markdown(header, body)


def _ratios_table(rows, scope, labelled) -> str:
    cols = [c for c in RATIO_COLUMNS if _present(rows, c)]
    # No S.No on the ratios table, and no Total row at all: ratios do not sum,
    # and an empty Total row only invites the reader to look for one.
    header = ([scope or "Scope"] if labelled else []) + cols
    body = []
    for label, rec in rows:
        body.append(([label] if labelled else [])
                    + [_fmt(rec.get(c), "ratio") for c in cols])
    return _markdown(header, body)


def _markdown(header: list[str], body: list[list[str]]) -> str:
    def cell(value):
        return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\r\n", "\n").replace("\n", "<br>")
    lines = ["| " + " | ".join(map(cell, header)) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in body:
        lines.append("| " + " | ".join(map(cell, row)) + " |")
    return "\n".join(lines)
