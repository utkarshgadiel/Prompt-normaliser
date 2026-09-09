"""
Prompt Normaliser API — importable into watsonx Orchestrate as a tool.

Exposes the normaliser over HTTP with an OpenAPI spec Orchestrate can consume.

Run:
    uvicorn api:app --host 0.0.0.0 --port 8100 --app-dir src

OpenAPI spec for Orchestrate import:
    http://<host>:8100/openapi.json
"""
from __future__ import annotations

import sys
import json
import hashlib
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from funnel_format import render  # noqa: E402
from normaliser import normalise, vocab  # noqa: E402
from clock_utils import business_today

app = FastAPI(
    title="CRM Prompt Normaliser",
    version="1.1.0",
    description=(
        "Normalises a free-form CRM question into a validated execution plan: "
        "the metric, the tool that owns it, resolved dates, canonical entity "
        "filters, and the exact query text each backend tool parses correctly. "
        "Call this FIRST on every CRM data question, before any CRM tool."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class NormaliseRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1, max_length=8000,
        description="The standalone CRM question, preserving the user's intent and explicit changes.",
        json_schema_extra={"example":
                           "Total leads for wave city between 1 April 2026 and 30 June 2026"},
    )
    today: Optional[date] = Field(
        None,
        description="Reference date as YYYY-MM-DD. Omit to use the Asia/Kolkata business date. "
                    "Only set this for testing.",
    )
    decompose: bool = Field(
        True,
        description="Split multi-entity and multi-period questions into separate "
                    "tool calls. Leave true unless you specifically want one call.",
    )


class RankingOut(BaseModel):
    direction: Literal["top", "bottom"]
    count: Optional[int] = Field(None, ge=1, description="Null means ordering all rows without a limit.")


class ToolCallOut(BaseModel):
    call_id: str
    tool: str = Field(..., description="Backend tool to invoke.")
    agent: str = Field(..., description="Agent that owns this tool.")
    metric: str = Field(..., description="Canonical metric key.")
    metric_label: str = Field(..., description="Display label for the metric.")
    canonical_text: str = Field(
        ...,
        description="Send this EXACTLY as the tool's question or query parameter according to its schema. Do not reword, "
                    "reorder, re-punctuate or 'tidy' it — the wording is chosen "
                    "to match what that specific parser accepts.",
    )
    start_date: Optional[str] = Field(None, description="Resolved start, YYYY-MM-DD.")
    end_date: Optional[str] = Field(None, description="Resolved end, YYYY-MM-DD.")
    period_kind: str
    period_label: str = Field(
        "", description="Internal label. Do not show this to the user."
    )
    period_display: str = Field(
        "",
        description="Clean period text for the table heading, e.g. "
                    "'FY2025-26' or 'April 2026 to June 2026'. Use this in the "
                    "heading only after verifying the applied backend period. A different span is a validation failure.",
    )
    comparison: str = Field(..., description="none | month_on_month | quarter_on_quarter | year_on_year")
    groupings: list[str] = Field(default_factory=list)
    rank: Optional[RankingOut] = None
    defer_chart: bool = False
    collaborator_message: str = Field(
        ..., description="Ready-to-send JSON string containing this complete call. "
        "Copy as the collaborator message without rebuilding it or dropping fields.")
    filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Canonical entity values. Rows outside these values must not "
                    "be shown.",
    )


class NormaliseResponse(BaseModel):
    plan_id: str
    ok: bool = Field(..., description="False means execute no CRM calls; explain the limitation or ask the supplied clarification.")
    raw: str
    calls: list[ToolCallOut] = Field(default_factory=list)
    call_count: int = 0
    agents: list[str] = Field(default_factory=list)
    clarification: Optional[str] = Field(
        None,
        description="When ok is false, the reason. Rephrase it naturally for the "
                    "user. Do not disguise backend limitations as ambiguous user input.",
    )
    diagnostics: list[str] = Field(
        default_factory=list,
        description="Internal diagnostic evidence. Do not print raw engineering text. Explain material coverage or validation limitations in plain language.",
    )
    decomposed: bool = False
    blocked_reason: Optional[str] = Field(None, description="A known backend capability limitation; do not execute a replacement period or metric.")


@app.post("/normalise", response_model=NormaliseResponse, operation_id="normalise_crm_query")
def normalise_query(req: NormaliseRequest) -> NormaliseResponse:
    """Call this FIRST for every CRM data question, before selecting or calling any other tool.

    Send the user's question exactly as they typed it. The response tells you
    which tool to call, with which query text, over which resolved dates.

    If "ok" is false, do not call any tool. Rephrase "clarification" naturally
    for the user, as a short question with numbered options they can answer
    with a single keystroke.

    If "ok" is true, execute every entry in "calls". Pass each "canonical_text"
    to its tool as the question parameter WITHOUT ANY MODIFICATION. Do not
    reword, reorder, re-punctuate or shorten it. The wording is chosen to match
    what that specific backend parser accepts, and altering it causes the tool
    to return data for the wrong period without raising an error.

    Send the whole call to the collaborator, not just the text: write "tool",
    "question", "start_date", "end_date" and "period_display" as labelled lines
    in the message. A collaborator with no tool name has to guess which of its
    tools you meant, and it guesses wrong silently.

    "call_count" is a checksum. Execute that many calls and confirm you have
    that many sets of rows before writing anything. Never display a table for a
    call that was not issued or did not return.

    Never show the user anything from "diagnostics"; it is internal engineering
    output. Put the period in the table heading using "period_display", and add
    no caveat, warning or note about it.
    """
    ref = req.today or business_today()
    result = normalise(req.query, ref, decompose_entities=req.decompose)
    d = result.to_dict()
    # Content identifiers provide traceability, not authorisation or a security signature.
    plan_id = hashlib.sha256(json.dumps(
        {"reference_date": ref.isoformat(), "plan": d},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]

    calls = []
    for index, c in enumerate(d["calls"]):
        c = dict(c)
        c["period_display"] = _period_display(c)
        c["call_id"] = f"{plan_id}:{index + 1}"
        c["collaborator_message"] = json.dumps(
            {"request_type": "data", "plan_id": plan_id, **c},
            ensure_ascii=False, separators=(",", ":"))
        calls.append(ToolCallOut(**c))

    return NormaliseResponse(
        plan_id=plan_id,
        ok=result.ok,
        raw=result.raw,
        calls=calls,
        call_count=len(result.calls),
        agents=result.agents,
        clarification=result.clarification,
        diagnostics=result.warnings,
        decomposed=result.decomposed,
        blocked_reason=result.blocked_reason,
    )


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _period_display(call: dict) -> str:
    """Readable period for a table heading, built from the resolved dates."""
    s, e = call.get("start_date"), call.get("end_date")
    if not s or not e:
        return ""
    sd, ed = date.fromisoformat(s), date.fromisoformat(e)

    # Whole financial year -> "FY2025-26"
    if (sd.month, sd.day) == (4, 1) and (ed.month, ed.day) == (3, 31) \
            and ed.year == sd.year + 1:
        return f"FY{sd.year}-{str(sd.year + 1)[2:]}"

    # Span of whole financial years -> "FY2020-21 to FY2026-27"
    if (sd.month, sd.day) == (4, 1) and (ed.month, ed.day) == (3, 31):
        return (f"FY{sd.year}-{str(sd.year + 1)[2:]} to "
                f"FY{ed.year - 1}-{str(ed.year)[2:]}")

    # Whole single month -> "June 2026"
    if (sd.day == 1 and sd.year == ed.year and sd.month == ed.month
            and (ed + timedelta(days=1)).day == 1):
        return f"{_MONTHS[sd.month - 1]} {sd.year}"

    # Whole months -> "April 2026 to June 2026"
    if sd.day == 1 and (ed + timedelta(days=1)).day == 1:
        return (f"{_MONTHS[sd.month - 1]} {sd.year} to "
                f"{_MONTHS[ed.month - 1]} {ed.year}")

    return f"{sd.day} {_MONTHS[sd.month - 1]} {sd.year} to " \
           f"{ed.day} {_MONTHS[ed.month - 1]} {ed.year}"


class FunnelFormatRequest(BaseModel):
    include_totals: bool = Field(True, description="Set false for ranked, filtered or assembled results when the original aggregate no longer applies.")
    period_column: Literal["", "Month", "Quarter", "Financial Year", "Period"] = Field(
        "", description="Period column for explicitly assembled, labelled time-series rows.")
    response: dict[str, Any] = Field(
        ...,
        description=(
            "The funnel tool's response, pasted through unchanged. Any of the "
            "shapes the seven funnel services return is accepted."),
    )
    heading: str = Field(
        "",
        description=(
            "Period for the table headings, taken from period_display, "
            "for example 'FY2025-26'. Optional."),
    )
    show: Literal["both", "metrics", "ratios"] = Field(
        "both",
        description=(
            "'both' (the default and almost always correct), 'ratios' only "
            "when the user said funnel ratios or conversion ratios, or "
            "'metrics' only when they said funnel metrics or stage counts."),
    )
    tool: Literal["", "lead_funnel", "product_funnel", "project_funnel",
                  "source_funnel", "subsource_funnel", "lead_user_funnel",
                  "sales_user_funnel"] = Field(
        "",
        description=(
            "The tool field from the plan, copied exactly: lead_funnel, "
            "product_funnel, project_funnel, source_funnel, subsource_funnel, "
            "lead_user_funnel or sales_user_funnel. It names the breakdown "
            "column. Pass it: project_funnel returns its rows under the key "
            "'product_wise_metrics', so without the tool name a project "
            "breakdown would be headed Product."),
    )


class FunnelFormatResponse(BaseModel):
    ok: bool
    row_count: int = 0
    scope_column: str = ""
    metrics_table: str = ""
    ratios_table: str = ""
    missing_ratio_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Required ratio columns absent from one or more rows. Compare against the original response to determine whether the backend or transmission omitted them."),
    )
    empty: bool = Field(
        False,
        description=(
            "True when the funnel service reported no rows for this scope and "
            "period. That is a real answer, not a failure: say there were no "
            "records, and show no table and no graph."),
    )
    missing_metric_columns: list[str] = Field(default_factory=list)
    warning: Optional[str] = None
    markdown: str = Field(
        "",
        description=(
            "Both tables, ready to display. Copy this verbatim into the "
            "response; do not rebuild, reorder or re-format it."),
    )
    error: Optional[str] = None


@app.post("/format_funnel", response_model=FunnelFormatResponse,
          operation_id="format_funnel_tables")
def format_funnel(req: FunnelFormatRequest) -> FunnelFormatResponse:
    """Call this after ANY funnel tool returns, from within the funnel agent that just called it.

    A funnel tool returns ONE flat record per row holding the stage counts and
    the conversion ratios interleaved in alphabetical order, so TL:VL and MB:MD
    sit among Total Leads and Junk Leads. It has to be shown as TWO tables:
    Funnel Metrics, then Funnel Conversion Ratios. Deriving that split by hand
    proved unreliable -- the same question produced two tables on one turn and
    one on the next -- so this endpoint does it deterministically.

    Send the funnel tool's response in "response" EXACTLY as it came back --
    every key, unreordered, nothing dropped. Send the tool you called in "tool"
    and the period in "heading". Return the resulting "markdown",
    "metrics_table" and "ratios_table" to the master unchanged.

    It applies the fixed column order, exactly five ratio columns (TL:VL,
    VL:SOL, SOL:MB, MB:MD, MD:SD), Indian digit grouping, and a Total row
    copied from the response's own totals block rather than summed. A
    single-row funnel gets no S.No and no Total row.

    If "missing_ratio_columns" comes back non-empty, the payload lost keys in
    transit -- resend the untouched response rather than displaying a short
    table. If "empty" comes back true the service found no records for that
    scope and period: say so in a sentence, and show no table and no graph.
    """
    out = render(req.response, heading=req.heading, show=req.show, tool=req.tool,
                 include_totals=req.include_totals, period_column=req.period_column)
    return FunnelFormatResponse(**out)


@app.get("/health", operation_id="normaliser_health")
def health() -> dict[str, Any]:
    """Liveness check, including whether the entity vocabulary is loaded."""
    v = vocab()
    out = {
        "status": "healthy" if v.loaded else "unavailable",
        "vocabulary_loaded": v.loaded,
        "facets": sorted(v._facets) if v.loaded else [],
        "note": None if v.loaded else "Run: python src/vocab_build.py",
    }
    return out if v.loaded else JSONResponse(status_code=503, content=out)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")))
