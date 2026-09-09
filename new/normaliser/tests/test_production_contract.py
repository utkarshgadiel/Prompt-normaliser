"""Regressions at the boundaries where production data was lost or mislabeled."""
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "grammar"))

import api
import normaliser
import harness
from funnel_format import render, _fmt
from test_funnel_format import LEAD_FUNNEL, _FULL

client = TestClient(api.app)


def test_api_preserves_rank_and_exact_collaborator_message():
    out = client.post("/normalise", json={
        "query": "top 5 products by total sales last fy", "today": "2026-09-09"})
    assert out.status_code == 200
    plan = out.json()
    assert plan["ok"]
    assert plan["call_count"] == len(plan["calls"])
    for call in plan["calls"]:
        assert call["rank"] == {"direction": "top", "count": 5}
        message = json.loads(call["collaborator_message"])
        assert message["plan_id"] == plan["plan_id"]
        for key in ("call_id", "tool", "canonical_text", "filters", "groupings", "rank",
                    "start_date", "end_date", "metric", "comparison"):
            assert message[key] == call[key]


def test_multiperiod_calls_have_distinct_ids():
    payload = {"query": "product wise sales fy 2023 and fy 2024 and fy 2025",
               "today": "2026-09-09"}
    plan = client.post("/normalise", json=payload).json()
    assert plan["ok"] and plan["call_count"] == 3
    assert len({c["call_id"] for c in plan["calls"]}) == 3
    assert plan == client.post("/normalise", json=payload).json()


@pytest.mark.parametrize("payload", [
    {"query": "leads", "today": "not-a-date"}, {"query": ""},
    {"query": "x" * 8001},
])
def test_bad_api_requests_are_422_not_500(payload):
    assert client.post("/normalise", json=payload).status_code == 422


def test_partial_month_heading_does_not_claim_whole_month():
    assert api._period_display({"start_date": "2026-09-01", "end_date": "2026-09-09"}) == "1 September 2026 to 9 September 2026"
    assert api._period_display({"start_date": "2026-09-01", "end_date": "2026-09-30"}) == "September 2026"


def test_missing_vocabulary_blocks_execution_and_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(normaliser, "_VOCAB", normaliser.Vocabulary(tmp_path / "absent.json"))
    out = client.post("/normalise", json={"query": "leads for Eden last fy"}).json()
    assert not out["ok"] and out["calls"] == []
    assert client.get("/health").status_code == 503


@pytest.mark.parametrize("text", [
    "wave city wave estate eden", "new plots and new  plots", "owner arjun kumar",
    "wAVE garden gh2-ph-2", "channel partner referral facebook", "(eden) unknown_eden",
])
def test_indexed_alias_matching_preserves_original_regex_semantics(text):
    v = normaliser.vocab()
    text = v._norm(text)
    expected = []
    # Filter literals cheaply before compiling the original expressions.
    for alias in v.longest_aliases():
        if alias in text:
            expected.extend((alias, *m.span()) for m in re.finditer(
                rf"(?<!\w){re.escape(alias)}(?!\w)", text))
    assert v.matches(text) == expected


def test_multi_result_formatter_does_not_overwrite_earlier_result():
    out = render({"responses": {
        "first": {"result": {"lead_funnel": dict(_FULL)}},
        "second": {"result": {"lead_funnel": {**_FULL, "Total Leads": 22}}},
    }}, tool="lead_funnel")
    assert not out["ok"] and not out["markdown"]
    assert "separately" in out["error"]


def test_malformed_list_row_is_not_silently_dropped():
    out = render({"data": [{"name": "Eden", **_FULL}, {"name": "Lost row"}]})
    assert not out["ok"] and not out["markdown"]


def test_missing_ratio_in_one_row_fails_even_if_other_row_has_it():
    incomplete = dict(_FULL)
    incomplete.pop("MD:SD")
    out = render({"data": [{"name": "A", **_FULL}, {"name": "B", **incomplete}]})
    assert not out["ok"] and not out["markdown"]
    assert "MD:SD" in out["missing_ratio_columns"]


def test_error_with_plausible_rows_is_never_a_success():
    out = render({**LEAD_FUNNEL, "status": "error", "message": "query failed"})
    assert not out["ok"] and not out["markdown"]


def test_total_row_is_preserved_without_totals_block():
    out = render({"data": [{"name": "A", **_FULL}, {"name": "B", **_FULL},
                           {"name": "Total", "Total Leads": 4321}]})
    assert out["ok"] and out["row_count"] == 2
    assert "| Total |  | 4,321 |" in out["metrics_table"]


def test_conflicting_total_sources_are_rejected():
    out = render({"data": [{"name": "A", **_FULL}, {"name": "B", **_FULL},
                           {"name": "Total", "Total Leads": 4321}],
                  "totals": {"Total Leads": 9999}})
    assert not out["ok"] and "disagree" in out["error"]


def test_duplicate_zero_rows_and_markdown_labels_survive():
    payload = {"data": [{"name": "A | B", **_FULL}, {"name": "A | B", **_FULL},
                         {"name": "Zero", **{k: 0 for k in _FULL}}]}
    before = copy.deepcopy(payload)
    out = render(payload)
    assert out["ok"] and out["row_count"] == 3
    assert out["metrics_table"].count("A &#124; B") == 2
    assert "Zero" in out["metrics_table"]
    assert payload == before


def test_large_integer_digits_are_not_converted_through_float():
    value = 10 ** 350 + 12345
    assert _fmt(value).replace(",", "") == str(value)
    assert _fmt("272488") == "2,72,488"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_numeric_metrics_rejected(value):
    out = render({"lead_funnel": {**_FULL, "Total Leads": value}})
    assert not out["ok"]


@pytest.mark.parametrize("query", [
    "meetings booked last month", "events yoy 2024 to 2025",
    "total cases last 30 days", "cre targets last 30 days",
])
def test_unsafe_backend_forms_do_not_leave_executable_calls(query):
    out = client.post("/normalise", json={"query": query, "today": "2026-09-09"}).json()
    assert not out["ok"] and out["calls"] == []
    assert out["blocked_reason"] == "backend_period_unavailable"
    assert out["clarification"]


@pytest.mark.parametrize("value", ["Kumar", "Arjun", "Rajan"])
def test_case_month_collision_is_blocked_at_normaliser_boundary(value):
    from types import SimpleNamespace
    from execution_guard import execution_blocker
    call = SimpleNamespace(tool="case_report", filters={"owner": [value]},
        start_date="2024-04-01", end_date="2025-03-31", canonical_text="cases fy 2024")
    reason, _ = execution_blocker(call, [], date(2026, 9, 9))
    assert reason == "backend_filter_unavailable"


def test_revenue_is_not_silently_converted_to_sales_count():
    out = normaliser.normalise("revenue last fy", date(2026, 9, 9))
    assert not out.ok and not out.calls


def test_independently_dated_metrics_remain_paired():
    n = normaliser.normalise("total leads last fy and sales this month", date(2026, 9, 9))
    assert n.ok
    assert [(c.metric, c.start_date, c.end_date) for c in n.calls] == [
        ("total_leads", "2025-04-01", "2026-03-31"),
        ("sales_done", "2026-09-01", "2026-09-30")]


def test_grouping_does_not_remove_named_scope():
    n = normaliser.normalise("project wise leads for Wave City and Wave Estate last fy", date(2026, 9, 9))
    assert n.ok and len(n.calls) == 2
    assert {c.filters["project"][0] for c in n.calls} == {"Wave City", "Wave Estate"}
    assert all("project" in c.groupings for c in n.calls)


def test_every_multivalued_facet_is_decomposed():
    n = normaliser.normalise("leads for Wave City and Wave Estate for Eden and Veridia last fy", date(2026, 9, 9))
    assert n.ok and len(n.calls) == 4
    assert {(c.filters["project"][0], c.filters["product"][0]) for c in n.calls} == {
        (p, v) for p in ("Wave City", "Wave Estate") for v in ("EDEN", "Veridia")}


@pytest.mark.parametrize("query", ["leads excluding junk last fy", "leads for xyz last fy",
    "leads for source xyz last fy", "cases 12 June 2024 to 19 June 2024",
    "top 0 products by sales last fy", "QL targets for Wave City last fy"])
def test_unsupported_scope_or_predicate_is_not_lost(query):
    out = client.post("/normalise", json={"query": query, "today": "2026-09-09"})
    assert out.status_code == 200
    assert not out.json()["ok"] and out.json()["calls"] == []


def test_no_chart_preference_survives_serialization():
    plan = client.post("/normalise", json={"query": "leads last month without charts"}).json()
    assert plan["ok"]
    assert all(json.loads(c["collaborator_message"])["defer_chart"] for c in plan["calls"])


def test_unbounded_ranking_does_not_fail_response_validation():
    plan = client.post("/normalise", json={"query": "Show users with the highest QL target surplus"}).json()
    assert plan["ok"] and plan["calls"][0]["rank"]["count"] is None


@pytest.mark.parametrize("key,value", [("MD:SD", "NaN"), ("Junk %", "Infinity%"),
                                      ("Total Leads", 1.4), ("Total Leads", "many")])
def test_numeric_corruption_is_not_rendered(key, value):
    out = render({"lead_funnel": {**_FULL, key: value}}, tool="lead_funnel")
    assert not out["ok"] and not out["markdown"]


def test_full_funnel_cannot_be_misidentified_as_user_funnel_after_losing_columns():
    out = render({"lead_funnel": {"Meeting Booked": 2, "Meeting Done": 1,
        "Sales Done": 1, "MB:MD": 2, "MD:SD": 1}}, tool="lead_funnel")
    assert not out["ok"] and "Total Leads" in out["missing_metric_columns"]


def test_outer_error_cannot_be_hidden_by_successful_wrapper():
    out = render({"status": "error", "responses": {"q": {"result": LEAD_FUNNEL}}})
    assert not out["ok"] and not out["markdown"]


def test_period_assembly_labels_and_ranked_total_suppression():
    payload = {"data": [{"name": "FY2024-25", **_FULL}, {"name": "FY2025-26", **_FULL}],
               "totals": {"Total Leads": 9999}}
    out = render(payload, tool="lead_funnel", period_column="Financial Year", include_totals=False)
    assert out["ok"] and out["row_count"] == 2
    assert "Financial Year" in out["metrics_table"] and "Financial Year" in out["ratios_table"]
    assert "9,999" not in out["markdown"]


def test_import_spec_matches_api_contract():
    sys.path.insert(0, str(ROOT))
    from export_openapi import build_spec
    spec = json.loads((ROOT / "openapi_orchestrate.json").read_text(encoding="utf-8"))
    assert spec == build_spec(spec["servers"])
    assert spec["openapi"] == "3.0.3"
    assert spec["paths"]["/normalise"]["post"]["operationId"] == "normalise_crm_query"
    assert spec["paths"]["/format_funnel"]["post"]["operationId"] == "format_funnel_tables"


@pytest.mark.parametrize("query", ["leads 31 September 2025", "leads 29 February 2025",
    "leads 2025-02-30", "leads last 0 days", "leads fy 9999", "leads fy 2025-27",
    "leads between 20 June 2025 and 1 June 2025", "leads for owner xyz last fy"])
def test_invalid_dates_and_unknown_owners_never_become_broad_queries(query):
    response = client.post("/normalise", json={"query": query, "today": "2026-09-09"})
    assert response.status_code == 200
    assert not response.json()["ok"] and response.json()["calls"] == []


@pytest.mark.parametrize("phrase,expected", [("15 September 2025", "2025-09-15"),
    ("2025-09-15", "2025-09-15"), ("15/09/2025", "2025-09-15"),
    ("29 February 2024", "2024-02-29")])
def test_single_day_is_not_broadened_to_month(phrase, expected):
    n = normaliser.normalise(f"leads on {phrase}", date(2026, 9, 9))
    assert n.ok and len(n.calls) == 1
    call = n.calls[0]
    assert call.start_date == call.end_date == expected
    got, metadata = harness.parse_dates("lead", call.canonical_text)
    from probe import extract_range
    assert metadata["error"] is None and extract_range(got) == (expected, expected)


@pytest.mark.parametrize("query", ["Show all open opportunities for Wave City month on month",
    "Show total leads for Wave City product wise",
    "Show total leads for Wave City where lead source is Digital for current FY",
    "Show the best performer in QL based on achievement percentage"])
def test_facet_words_inside_known_names_do_not_create_unknown_filters(query):
    n = normaliser.normalise(query, date(2026, 9, 9))
    assert n.ok, n.clarification
