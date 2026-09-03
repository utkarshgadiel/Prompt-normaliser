"""
Grammar contract tests.

The critical guard. Asserts that every canonical form the normaliser emits is
still parsed correctly by the real backend it targets.

Without this, a well-meaning tidy-up -- "April, May and June 2026" shortened to
"April, May, June 2026", or "1 April 2026 to 30 June 2026" rewritten as the
more natural "between 1 April 2026 and 30 June 2026" -- silently reintroduces
the bugs measured in grammar/DATE_GRAMMAR.md. Those forms return the wrong
period with no error, so no other test would catch it.

Run: python -m pytest tests/ -v
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "grammar"))

import harness  # noqa: E402
from dates import resolve  # noqa: E402
from intents import Tool  # noqa: E402
from normaliser import normalise  # noqa: E402
from probe import extract_range  # noqa: E402

TODAY = date(2026, 8, 26)

# Tools whose date parser is verified. `event` is excluded because it raises
# NameError('is_qoq') on every dated query -- see DATE_GRAMMAR.md s1.
# `targets` is excluded because 20 of 21 forms resolve wrongly -- s4.
VERIFIED = {Tool.LEAD: "lead", Tool.OPPORTUNITY: "opp",
            Tool.TASK: "task", Tool.CASE: "case"}


def _parse(service: str, text: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result, meta = harness.parse_dates(service, text)
    if meta["error"]:
        return None, meta["error"]
    return extract_range(result), None


@pytest.mark.parametrize("query,expected", [
    ("Total leads between 1 April 2026 and 30 June 2026", ("2026-04-01", "2026-06-30")),
    ("Total leads for April, May and June 2026", ("2026-04-01", "2026-06-30")),
    ("Total leads for April 2026, May 2026 and June 2026", ("2026-04-01", "2026-06-30")),
    ("Total leads from 1 April 2026 to 30 June 2026", ("2026-04-01", "2026-06-30")),
    ("Total leads for last fy", ("2025-04-01", "2026-03-31")),
    ("Total leads for June 2026", ("2026-06-01", "2026-06-30")),
])
@pytest.mark.parametrize("tool", list(VERIFIED))
def test_emitted_form_round_trips(query, expected, tool):
    """What the normaliser emits must parse back to the period it resolved.

    This is the whole contract: normalisation is only safe if the canonical
    form survives the backend's own parser.
    """
    norm = normalise(query, TODAY)
    assert norm.ok, f"normaliser rejected: {query}"

    service = VERIFIED[tool]
    spans = []
    for call in norm.calls:
        got, err = _parse(service, call.canonical_text)
        assert err is None, f"{service} errored on {call.canonical_text!r}: {err}"
        assert got and got[0], (
            f"{service} failed to parse emitted form {call.canonical_text!r} "
            f"(from {query!r}) -- it would fall back to a silent default")
        spans.append(got)

    lo = min(s[0] for s in spans)
    hi = max(s[1] for s in spans if s[1])
    assert (lo, hi) == expected, (
        f"{service}: emitted {[c.canonical_text for c in norm.calls]} "
        f"-> {lo}~{hi}, expected {expected[0]}~{expected[1]}")


@pytest.mark.parametrize("service", list(VERIFIED.values()))
def test_forbidden_forms_still_broken(service):
    """Guard the *reasons* for the rules, not just the rules.

    If a backend is fixed so these forms work, this test fails and the rule in
    render.py can be relaxed deliberately -- rather than someone assuming it was
    always safe.
    """
    target = ("2026-04-01", "2026-06-30")
    # opp is absent deliberately: it parses bare-'to' numeric dates correctly.
    # The blanket ban on dd-mm-yyyy in render.py is kept because lead, task and
    # case all widen it to the full FY -- one tool coping is not a reason to
    # emit a form the other three mishandle.
    forbidden = {
        "case": ["total leads between 1 April 2026 and 30 June 2026",
                 "total leads 1 April 2025 to 31 March 2026"],
        "lead": ["total leads 01-04-2026 to 30-06-2026"],
        "task": ["total leads 01-04-2026 to 30-06-2026"],
    }.get(service, [])
    for text in forbidden:
        got, err = _parse(service, text)
        assert err is not None or got != target, (
            f"{service} now parses {text!r} correctly. The restriction in "
            f"render.py may be relaxed -- update DATE_GRAMMAR.md and this test.")


def test_event_service_is_broken():
    """Pin the is_qoq bug so the fix is noticed when it lands.

    When event_report.py:1780 gains its missing `is_qoq` assignment, this test
    fails -- the signal to add Tool.EVENT to render._RANGE_OK and re-probe.
    """
    _, err = _parse("event", "total events for April, May and June 2026")
    assert err is not None and "is_qoq" in err, (
        "event_report no longer raises NameError('is_qoq'). Re-run "
        "grammar/probe.py and move Tool.EVENT into the verified sets.")


# --------------------------------------------------------------------------
# Per-year series ("yearly breakdown"). Measured 26 Aug 2026 -- the failure
# this pins: "total leads FY2019-20 to FY2026-27 yearly" collapsed to a single
# FY2019 call in production, and the master then hand-looped 16 steps and
# invented results for the years that returned nothing.
# --------------------------------------------------------------------------

def _raw_parse(service: str, text: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result, meta = harness.parse_dates(service, text)
    return result, meta["error"]


@pytest.mark.parametrize("query", [
    "total leads FY2019-20 to FY2026-27 yearly",
    "show me yearly leads",
    "leads year wise",
    "total leads year on year",
])
def test_year_series_is_one_call_lead(query):
    """A yearly breakdown of leads is ONE call whose form the backend returns
    as YEAR_WISE rows FY2020..current -- never a per-year loop, never a lone
    FY2019 call."""
    norm = normalise(query, TODAY)
    assert norm.ok and len(norm.calls) == 1, (query, norm.clarification)
    call = norm.calls[0]
    assert call.canonical_text == "total leads 2020 to 2026", call.canonical_text
    res, err = _raw_parse("lead", call.canonical_text)
    assert err is None
    assert str(getattr(res["type"], "value", res["type"])) == "year_wise"
    assert len(res["periods"]) == 7
    assert res["periods"][0]["start_date"] == "20200401"
    assert res["periods"][-1]["end_date"] == "20270331"


def test_year_series_event_uses_yoy_range_form():
    """event_report crashes on every dated form except yoy; its yoy branch --
    uniquely -- honours an explicit year range."""
    norm = normalise("meetings yearly", TODAY)
    assert norm.ok and len(norm.calls) == 1
    call = norm.calls[0]
    assert call.canonical_text.endswith("yoy 2020 to 2026"), call.canonical_text
    res, err = _raw_parse("event", call.canonical_text)
    assert err is None, err
    assert str(getattr(res["type"], "value", res["type"])) == "year_wise"
    assert len(res["periods"]) == 7


def test_year_series_case_decomposes_per_year():
    """case_report has no year-series support (bare ranges fall to the current
    FY; yoy returns one wrong contiguous span), so it gets one verified
    "fy <year>" call per year."""
    norm = normalise("cases year on year", TODAY)
    assert norm.ok and len(norm.calls) == 7
    for call, fy in zip(norm.calls, range(2020, 2027)):
        assert call.canonical_text == f"cases fy {fy}", call.canonical_text
        got, err = _parse("case", call.canonical_text)
        assert err is None
        assert got == (f"{fy}-04-01", f"{fy + 1}-03-31")


def test_fy_pair_labels_resolve():
    """FY labels written as year pairs must not lose the range."""
    p = resolve("total leads fy 2019-20 to fy 2026-27", TODAY)
    assert p.resolved and len(p.spans) == 8
    assert p.spans[0].start.isoformat() == "2019-04-01"
    assert p.spans[-1].end.isoformat() == "2027-03-31"
    single = resolve("total leads for FY2024-25", TODAY)
    assert single.resolved
    assert (single.start.isoformat(), single.end.isoformat()) == \
        ("2024-04-01", "2025-03-31")


def test_month_grain_over_window_round_trips():
    """"month wise June to Dec 2025" must keep BOTH the window and the grain.

    The only measured form that does is the month-name range with the year
    once at the end; the day form plus a comparison token makes the backend
    discard the window and answer for the whole FY.
    """
    norm = normalise("total leads month wise from June to Dec 2025", TODAY)
    assert norm.ok and len(norm.calls) == 1
    call = norm.calls[0]
    assert call.canonical_text == "total leads mom June to December 2025", \
        call.canonical_text
    res, err = _raw_parse("lead", call.canonical_text)
    assert err is None
    assert len(res["periods"]) == 7
    assert res["periods"][0]["start_date"] == "20250601"
    assert res["periods"][-1]["end_date"] == "20251231"


def test_case_range_uses_month_name_form():
    """case_report substitutes the current FY into any day-form range naming
    2+ months; the same window as a month-name range parses correctly."""
    norm = normalise("total cases 1 April 2024 to 30 June 2024", TODAY)
    assert norm.ok and len(norm.calls) == 1
    call = norm.calls[0]
    assert call.canonical_text == "cases April to June 2024", call.canonical_text
    got, err = _parse("case", call.canonical_text)
    assert err is None
    assert got == ("2024-04-01", "2024-06-30")


def test_comparison_token_dropped_on_day_form():
    """yoy/qoq next to an unexpressible window: the window wins, the token is
    dropped, and a warning says so. With the token the backend would discard
    the window for its hardcoded floor."""
    norm = normalise("total leads yoy between 1 April 2026 and 15 June 2026", TODAY)
    assert norm.ok and len(norm.calls) == 1
    call = norm.calls[0]
    assert "yoy" not in call.canonical_text, call.canonical_text
    assert call.canonical_text.endswith("1 April 2026 to 15 June 2026")
    assert any("breakdown dropped" in w or "grain" in w for w in norm.warnings)


def test_single_period_funnel_always_runs():
    """A funnel narrowed to ONE period runs whatever its breakdown breadth.

    Pins the production dead-end of 26 Aug 2026: "sales user funnel April
    2024" was refused three times in a row by the row-estimate guard even
    though a single-month user funnel is exactly what was asked for.
    """
    for q in ("sales user funnel for April 2024",
              "sales user funnel Q3 2024",
              "sub source wise funnel for Q1 2026",
              "product wise funnel for fy 2025",
              "top 5 sales user funnel April 2024"):
        norm = normalise(q, TODAY)
        assert norm.ok and len(norm.calls) == 1, (q, norm.clarification)


def test_last_n_excludes_the_current_period():
    """"Last N <unit>" means N COMPLETED units; the current one is excluded.

    Client-stated semantics, 31 Aug 2026, and it matches the backends' own
    arithmetic (lead_report.py:2961). Days and weeks were counting back from
    today and including it, shifting every such window one day forward.
    """
    aug31 = date(2026, 8, 31)          # a Monday
    cases = {
        "total leads last 30 days": ("2026-08-01", "2026-08-30"),
        "total leads last 7 days": ("2026-08-24", "2026-08-30"),
        "total leads last 1 day": ("2026-08-30", "2026-08-30"),
        "total leads last 2 weeks": ("2026-08-17", "2026-08-30"),
        "total leads last 3 months": ("2026-05-01", "2026-07-31"),
        "total leads last 2 quarters": ("2026-01-01", "2026-06-30"),
        "total leads last 2 years": ("2024-04-01", "2026-03-31"),
    }
    for q, expected in cases.items():
        p = resolve(q, aug31)
        assert p.resolved, q
        assert (p.start.isoformat(), p.end.isoformat()) == expected, \
            (q, p.start.isoformat(), p.end.isoformat())
        assert p.end < aug31, f"{q} must not include today"


def test_week_and_day_pairs_resolve_and_split():
    """this/last week and today/yesterday are real periods and split as pairs.

    "this week" was absent from the resolver entirely and fell through to the
    current-FY default -- a whole financial year answering a seven-day
    question, with no warning (found 31 Aug 2026).
    """
    aug31 = date(2026, 8, 31)
    p = resolve("total leads this week", aug31)
    assert (p.start.isoformat(), p.end.isoformat()) == ("2026-08-31", "2026-08-31")
    p = resolve("total leads last week", aug31)
    assert (p.start.isoformat(), p.end.isoformat()) == ("2026-08-24", "2026-08-30")

    for q in ("lead funnel for this week vs last week",
              "total leads today vs yesterday",
              "total leads this month vs last month",
              "total leads this quarter vs last quarter"):
        norm = normalise(q, aug31)
        assert norm.ok and len(norm.calls) == 2, (q, len(norm.calls))
        assert norm.calls[0].start_date != norm.calls[1].start_date, q


def test_case_week_forms_round_trip():
    """case cannot express a sub-month day range at all, and its "this week"
    runs into the future, so weeks use the phrase or the rolling form."""
    aug31 = date(2026, 8, 31)
    norm = normalise("total cases last week", aug31)
    assert norm.calls[0].canonical_text == "cases last week"
    got, err = _parse("case", "cases last week")
    assert err is None and got == ("2026-08-24", "2026-08-30")

    # "this week" must never be emitted for case: it resolves Mon->Sun.
    got, err = _parse("case", "cases this week")
    assert err is None and got[1] > "2026-08-31", (
        "case_report no longer runs 'this week' into the future; the "
        "exclusion in render.py can be revisited.")


def test_range_connectors_do_not_drop_the_middle():
    """"April 2025 through June 2025" is one range, not the list [Apr, Jun].

    Found in production 27 Aug 2026: only "to", "till" and "-" were accepted,
    so "through" fell past the month-range branch into the discrete-month-list
    branch and May vanished with no error.
    """
    for word in ("to", "till", "thru", "through", "until", "upto", "up to", "-"):
        p = resolve(f"total leads april 2025 {word} june 2025", TODAY)
        assert p.resolved, word
        assert (p.start.isoformat(), p.end.isoformat()) == \
            ("2025-04-01", "2025-06-30"), (word, p.start, p.end)

    # "and" must still split two named periods rather than joining them.
    norm = normalise("total leads for last fy and current fy", TODAY)
    assert len(norm.calls) == 2, [c.canonical_text for c in norm.calls]


def test_targets_metric_wins_over_events_and_cases():
    """"appointment completion rate" and "SR resolved" are targetvsactuals
    metrics, not event/case row counts.

    Mirrors the fallback routing priority in the data agent's behavior file
    (target / cre / gre / ql / sr tested BEFORE case and event). Pins the
    batch finding of 27 Aug 2026, where these produced spurious extra calls
    and "QL achievement percentage" was refused outright.
    """
    for q in ("Show QL achievement percentage for Abhishek Verma",
              "Show appointment completion rate for Abhishek Verma",
              "Show service request resolution rate for Abhishek Verma",
              "Show top 5 users by QL actual",
              "Show the user with the highest QL target surplus"):
        norm = normalise(q, TODAY)
        assert norm.ok, (q, norm.clarification)
        assert all(c.tool == "targetvsactuals" for c in norm.calls), \
            (q, [c.tool for c in norm.calls])


def test_sale_value_is_refused_not_counted():
    """No tool holds a rupee amount. Answering "sales value" with a sales
    COUNT is a silent wrong answer, so it must refuse."""
    norm = normalise("Show me total sales value for last FY", TODAY)
    assert not norm.ok and "value" in norm.clarification


def test_unknown_named_entity_refuses():
    """A named value the data does not hold must refuse, not silently run
    org-wide (batch 27 Aug 2026: "source Social Media" ran unscoped)."""
    norm = normalise("Show lead funnel for source Social Media in current FY", TODAY)
    assert not norm.ok
    assert "Social Media" in norm.clarification
    assert norm.unknown_entities == ["Social Media"]


def test_acronym_head_alias_resolves():
    """"WMCC" must resolve to the project "WMCC Sec 32"; every "for WMCC"
    filter was silently dropped before (batch 27 Aug 2026)."""
    norm = normalise("total leads for WMCC last fy", TODAY)
    assert norm.ok and norm.calls[0].filters.get("project") == ["WMCC Sec 32"]


def test_funnel_grouping_outranks_named_entity():
    """"source-wise funnel for Eden" is a SOURCE breakdown filtered to Eden,
    not a product funnel."""
    norm = normalise("Show me source-wise lead funnel for Eden in 2025", TODAY)
    assert norm.ok and norm.calls[0].tool == "source_funnel"
    assert norm.calls[0].filters.get("product") == ["EDEN"]


def test_source_preferred_over_subsource_for_mirrored_values():
    """"Channel Partner" exists in both columns with near-identical counts;
    source is the primary reporting home."""
    norm = normalise("Show Channel Partner lead funnel for this year", TODAY)
    assert norm.ok and norm.calls[0].tool == "source_funnel"


def test_funnel_quarter_form_is_service_split():
    """product_funnel needs the bare year; every other funnel needs the fy
    form. Only a PAST-year probe reveals this (grammar/DATE_GRAMMAR.md s8)."""
    norm = normalise("Show me product-wise lead funnel for Q1 2024", TODAY)
    assert norm.ok and norm.calls[0].tool == "product_funnel"
    assert norm.calls[0].canonical_text.endswith("q1 2024"), \
        norm.calls[0].canonical_text

    norm = normalise("Show me source-wise lead funnel for Q1 2024", TODAY)
    assert norm.ok and norm.calls[0].tool == "source_funnel"
    assert norm.calls[0].canonical_text.endswith("q1 fy 2024"), \
        norm.calls[0].canonical_text


def test_product_funnel_grain_decomposes():
    """product_funnel cannot express a mom series over an FY in any phrasing,
    so it becomes one verified single-month call per month.

    Scoped to one product: the unscoped version is 66 products x 12 months
    and is correctly stopped by the volume guard instead.
    """
    norm = normalise("Show month-on-month lead funnel for Eden in FY 2024", TODAY)
    assert norm.ok and len(norm.calls) == 12, \
        (len(norm.calls), norm.clarification)
    assert all(c.tool == "product_funnel" for c in norm.calls)
    assert norm.calls[0].canonical_text.endswith("April 2024")
    assert norm.calls[-1].canonical_text.endswith("March 2025")


def test_grain_kept_in_one_call_where_supported():
    """source_funnel handles mom at the endpoint, so it stays ONE call."""
    norm = normalise("Show month-on-month lead funnel for Digital source in FY 2024",
                     TODAY)
    assert norm.ok and len(norm.calls) == 1, [c.canonical_text for c in norm.calls]
    assert norm.calls[0].tool == "source_funnel"
    assert norm.calls[0].canonical_text.endswith("mom fy 2024")


def test_target_wording_is_preserved():
    """targetvsactuals picks its report from literal keywords in the question.

    DATA_DICTIONARY (targetvsactuals.py:2788) keys "qualified"/"ql"/"lead" to
    report 1, "appointment"/"booked"/"completion" to report 2 and "service
    request"/"sr"/"resolved" to report 3, and KPI_COLUMN_MAP then splits
    booked from completion. Flattening every target question to the generic
    label "targets vs actuals" stripped all of those words, so the backend
    could not tell which report or columns were wanted (3 Sep 2026).
    """
    cases = {
        "show me achievement qualified target for Abhishek Verma": "qualified",
        "show me QL target and QL actual for Admin": "ql",
        "show me SR target and resolved actual for Admin": "sr",
        "show me appointment booked target for Admin": "booked",
        "show me appointment completion target for Admin": "completion",
    }
    for q, keyword in cases.items():
        norm = normalise(q, TODAY)
        assert norm.ok, (q, norm.clarification)
        text = norm.calls[0].canonical_text.lower()
        assert keyword in text, (q, text)
        assert not text.startswith("targets vs actuals"), (q, text)
        # the heading follows the question too
        assert norm.calls[0].metric_label.lower() != "targets vs actuals", q

    # targetvsactuals has no lead-feedback dimension, so "qualified" must not
    # also become a filter the tool cannot honour.
    norm = normalise("show me qualified target for Abhishek Verma", TODAY)
    assert set(norm.calls[0].filters) <= {"owner"}, norm.calls[0].filters


def test_hyphenated_groupings_survive():
    """"service-request-type-wise" is the same breakdown as the spaced form.

    The grouping patterns are written with spaces, so a hyphen between words
    matched nothing and the breakdown vanished from the plan (2 Sep 2026).
    """
    norm = normalise(
        "Show service-request-type-wise total satisfied cases for Wave Estate "
        "in Q1 2025.", TODAY)
    assert norm.ok, norm.clarification
    text = norm.calls[0].canonical_text
    assert "service request type wise" in text, text
    assert "Wave Estate" in text, text
    # The metric already carries Satisfied; it must not be repeated as a filter.
    assert text.count("Satisfied") + text.count("satisfied") == 1, text


def test_shared_noun_splits_into_one_metric_each():
    """"cold and hot leads in Delhi" is two metrics, both scoped to Delhi.

    Only the qualifier next to the noun matched, so "cold" was lost and its
    rating became a filter on the hot-leads call instead (2 Sep 2026).
    """
    norm = normalise("how many cold and hot leads are generated in delhi ncr "
                     "for last 3 months", TODAY)
    assert norm.ok, norm.clarification
    keys = {c.metric for c in norm.calls}
    assert keys == {"cold_leads", "hot_leads"}, keys
    for c in norm.calls:
        assert c.filters.get("city") == ["Delhi"], (c.canonical_text, c.filters)
        # the metric's own value must not be restated as a filter
        assert "rating" not in c.filters, c.filters

    norm = normalise("show me open and completed tasks for wave city last month",
                     TODAY)
    assert {c.metric for c in norm.calls} == {"open_tasks", "completed_tasks"}
    assert all(c.filters.get("project") == ["Wave City"] for c in norm.calls)


def test_trailing_scope_does_not_override_an_attached_entity():
    """A trailing entity reaches only metrics that named none of their own.

    "sales of Veridia and leads of Wave City" must not put Wave City on sales.
    """
    norm = normalise("sales of veridia and leads of wave city last fy", TODAY)
    by_metric = {c.metric: c.filters for c in norm.calls}
    assert by_metric["sales_done"].get("product") == ["Veridia"]
    assert "project" not in by_metric["sales_done"], by_metric["sales_done"]
    assert by_metric["total_leads"].get("project") == ["Wave City"]


def test_funnel_grain_breakdowns_ask_to_narrow():
    """A wise-breakdown repeated across periods is always too wide to show."""
    for q in ("show me month on month product wise funnel",
              "quarter on quarter source wise funnel",
              "year on year sub source wise funnel"):
        norm = normalise(q, TODAY)
        assert not norm.ok, (q, [c.canonical_text for c in norm.calls])
        assert "funnel rows" in norm.clarification, q


def test_funnel_noun_form_keeps_the_breakdown_in_the_text():
    """"product funnel" must emit "funnel product wise", not a bare "funnel".

    Pins the production failure of 2 Sep 2026: routing was right but the
    canonical text lost the breakdown, so it read as the overall lead funnel
    and the collaborator executed it as one.
    """
    expect = {
        "product funnel for June 2026": ("product_funnel", "product wise"),
        "source funnel for June 2026": ("source_funnel", "source wise"),
        "sub source funnel for June 2026": ("subsource_funnel", "subsource wise"),
        "project funnel for June 2026": ("project_funnel", "project wise"),
        "funnel by source June 2026": ("source_funnel", "source wise"),
    }
    for q, (tool, phrase) in expect.items():
        norm = normalise(q, TODAY)
        assert norm.ok, (q, norm.clarification)
        assert norm.calls[0].tool == tool, (q, norm.calls[0].tool)
        assert phrase in norm.calls[0].canonical_text, \
            (q, norm.calls[0].canonical_text)

    # A named value is a filter, not a breakdown: no grouping is implied and
    # the filter must survive.
    norm = normalise("funnel for Eden June 2026", TODAY)
    assert norm.calls[0].tool == "product_funnel"
    assert "product wise" not in norm.calls[0].canonical_text
    assert norm.calls[0].filters.get("product") == ["EDEN"]

    # A month after the facet noun is a period, not a missing entity.
    assert normalise("funnel by product April 2025", TODAY).ok


def test_funnel_noun_forms_route_to_their_dimension():
    """"source funnel" must reach source_funnel, not the overall lead funnel.

    Pins the production mis-route of 27 Aug 2026: only "X wise" phrasings and
    named entity values routed; every bare noun form ("source funnel",
    "product funnel", "funnel by source") fell through to lead_funnel.
    """
    expect = {
        "source funnel": "source_funnel",
        "funnel by source": "source_funnel",
        "sub source funnel": "subsource_funnel",
        "product funnel": "product_funnel",
        "project funnel": "project_funnel",
        "lead source funnel": "source_funnel",
        "lead funnel": "lead_funnel",
    }
    for q, tool in expect.items():
        norm = normalise(q, TODAY)
        assert norm.ok and norm.calls[0].tool == tool, (
            q, norm.calls[0].tool if norm.calls else norm.clarification)


def test_multi_period_scoped_funnel_asks_with_grain():
    """A breakdown repeated across periods asks, offering one period of the
    grain the user named -- month for mom, financial year for a year span."""
    norm = normalise("product wise funnel month on month for last fy", TODAY)
    assert not norm.ok and "single month" in norm.clarification
    norm = normalise("sales user funnel for last 2 years", TODAY)
    assert not norm.ok and "single financial year" in norm.clarification


def test_grain_series_across_years_decomposes():
    """"qoq last 3 years" collapses to ONE year when phrased "qoq 2023 to
    2025"; one "qoq fy <year>" call per year is the verified form."""
    norm = normalise("total tasks quarter on quarter for last 3 years", TODAY)
    assert norm.ok and len(norm.calls) == 3
    for call, fy in zip(norm.calls, range(2023, 2026)):
        assert call.canonical_text == f"tasks qoq fy {fy}", call.canonical_text
        res, err = _raw_parse("task", call.canonical_text)
        assert err is None
        assert len(res["quarters"]) == 4
        assert res["quarters"][0]["start_date"] == f"{fy}0401"
