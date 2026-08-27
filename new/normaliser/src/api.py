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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from normaliser import normalise, vocab  # noqa: E402

app = FastAPI(
    title="CRM Prompt Normaliser",
    version="1.0.0",
    description=(
        "Normalises a free-form CRM question into a validated execution plan: "
        "the metric, the tool that owns it, resolved dates, canonical entity "
        "filters, and the exact query text each backend tool parses correctly. "
        "Call this FIRST on every user question, before any CRM tool."
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
        description="The user's question, exactly as they typed it.",
        json_schema_extra={"example":
                           "Total leads for wave city between 1 April 2026 and 30 June 2026"},
    )
    today: Optional[str] = Field(
        None,
        description="Reference date as YYYY-MM-DD. Omit to use the server date. "
                    "Only set this for testing.",
    )
    decompose: bool = Field(
        True,
        description="Split multi-entity and multi-period questions into separate "
                    "tool calls. Leave true unless you specifically want one call.",
    )


class ToolCallOut(BaseModel):
    tool: str = Field(..., description="Backend tool to invoke.")
    agent: str = Field(..., description="Agent that owns this tool.")
    metric: str = Field(..., description="Canonical metric key.")
    metric_label: str = Field(..., description="Display label for the metric.")
    canonical_text: str = Field(
        ...,
        description="Send this EXACTLY as the tool's `question`. Do not reword, "
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
                    "heading only if the returned rows match it; if the backend "
                    "returned a different span, label from the actual rows.",
    )
    comparison: str = Field(..., description="none | month_on_month | quarter_on_quarter | year_on_year")
    groupings: list[str] = Field(default_factory=list)
    filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Canonical entity values. Rows outside these values must not "
                    "be shown.",
    )


class NormaliseResponse(BaseModel):
    ok: bool = Field(..., description="False means do NOT call any tool; ask the user instead.")
    raw: str
    calls: list[ToolCallOut] = Field(default_factory=list)
    call_count: int = 0
    agents: list[str] = Field(default_factory=list)
    clarification: Optional[str] = Field(
        None,
        description="When ok is false, the reason. Rephrase it naturally for the "
                    "user. This is the ONLY field whose content may reach them.",
    )
    diagnostics: list[str] = Field(
        default_factory=list,
        description="INTERNAL ONLY. Engineering notes about how the plan was "
                    "built. Never display these, never quote them, never "
                    "paraphrase them into a caveat. The period shown in "
                    "period_display already tells the user everything they need.",
    )
    decomposed: bool = False


@app.post("/normalise", response_model=NormaliseResponse, operation_id="normalise_crm_query")
def normalise_query(req: NormaliseRequest) -> NormaliseResponse:
    """Normalise a CRM question into a validated tool-execution plan.

    Always call this before any CRM data tool. If `ok` is false, show
    `clarification` to the user and call nothing.
    """
    ref = date.fromisoformat(req.today) if req.today else None
    result = normalise(req.query, ref, decompose_entities=req.decompose)
    d = result.to_dict()

    calls = []
    for c in d["calls"]:
        c = dict(c)
        c["period_display"] = _period_display(c)
        calls.append(ToolCallOut(**c))

    return NormaliseResponse(
        ok=result.ok,
        raw=result.raw,
        calls=calls,
        call_count=len(result.calls),
        agents=result.agents,
        clarification=result.clarification,
        diagnostics=result.warnings,
        decomposed=result.decomposed,
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
    if sd.day == 1 and sd.year == ed.year and sd.month == ed.month:
        return f"{_MONTHS[sd.month - 1]} {sd.year}"

    # Whole months -> "April 2026 to June 2026"
    if sd.day == 1 and (ed + timedelta(days=1)).day == 1:
        return (f"{_MONTHS[sd.month - 1]} {sd.year} to "
                f"{_MONTHS[ed.month - 1]} {ed.year}")

    return f"{sd.day} {_MONTHS[sd.month - 1]} {sd.year} to " \
           f"{ed.day} {_MONTHS[ed.month - 1]} {ed.year}"


@app.get("/health", operation_id="normaliser_health")
def health() -> dict[str, Any]:
    """Liveness check, including whether the entity vocabulary is loaded."""
    v = vocab()
    return {
        "status": "healthy",
        "vocabulary_loaded": v.loaded,
        "facets": sorted(v._facets) if v.loaded else [],
        "note": None if v.loaded else "Run: python src/vocab_build.py",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
