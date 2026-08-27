"""
Canonical surface rendering.

Turns a resolved Period into the exact English form the target tool's parser
was measured to accept. Every rule here is backed by an executed probe result
in grammar/DATE_GRAMMAR.md -- none of it is inferred from reading regexes.

The cleaner-looking form is often the broken one, so do not "tidy" these
strings. tests/test_grammar_contract.py exists to catch exactly that.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from dates import Comparison, Kind, MONTH_NAME, Period, Span
from intents import (FUNNEL_DAYRANGE_OK, FUNNEL_Q4_OK,
                     FUNNEL_QUARTER_BARE_YEAR, FUNNEL_TOOLS, Tool)

# Tools whose date parser was verified against each form.
# See grammar/DATE_GRAMMAR.md section 2.
_RANGE_OK = {Tool.LEAD, Tool.OPPORTUNITY, Tool.TASK, Tool.CASE}
_MONTHLIST_OK = {Tool.LEAD, Tool.OPPORTUNITY, Tool.TASK, Tool.CASE}

COMPARISON_TOKEN = {
    Comparison.MOM: "mom",
    Comparison.QOQ: "qoq",
    Comparison.YOY: "yoy",
}


def _day(d) -> str:
    """'1 April 2026' -- no ordinal suffix, no zero padding, month spelled out."""
    return f"{d.day} {MONTH_NAME[d.month]} {d.year}"


def _is_whole_month(s: Span) -> bool:
    return (s.start.day == 1
            and s.end.day == monthrange(s.end.year, s.end.month)[1]
            and s.start.year == s.end.year
            and s.start.month == s.end.month)


def _whole_fy(s: Span) -> int | None:
    """Return the FY year if this span is exactly one financial year."""
    if (s.start.month, s.start.day) == (4, 1) and (s.end.month, s.end.day) == (3, 31) \
            and s.end.year == s.start.year + 1:
        return s.start.year
    return None


def _whole_quarter(s: Span) -> tuple[int, int] | None:
    """Return (fy, quarter) if this span is exactly one fiscal quarter."""
    ends = {4: (6, 30), 7: (9, 30), 10: (12, 31), 1: (3, 31)}
    if s.start.day != 1 or s.start.month not in ends:
        return None
    em, ed = ends[s.start.month]
    ey = s.end.year if s.start.month != 1 else s.start.year
    if (s.end.year, s.end.month, s.end.day) != (ey, em, ed) \
            or (s.start.month != 1 and s.end.year != s.start.year):
        return None
    if s.start.month == 4:
        return s.start.year, 1
    if s.start.month == 7:
        return s.start.year, 2
    if s.start.month == 10:
        return s.start.year, 3
    return s.start.year - 1, 4


def render_period(period: Period, tool: Tool,
                  today: date | None = None) -> tuple[str, list[str]]:
    """Return (canonical date phrase, warnings) for this tool's grammar."""
    warnings: list[str] = []
    today = today or date.today()

    if not period.resolved:
        return "", ["Period unresolved; no date phrase emitted."]

    if tool is Tool.TARGETS:
        # Measured: 1 of 21 forms parse correctly. Only a single whole month works.
        if len(period.spans) == 1 and _is_whole_month(period.spans[0]):
            s = period.spans[0]
            return f"{MONTH_NAME[s.start.month]} {s.start.year}", warnings
        # Measured 26 Aug 2026: "fy 2024" resolves to the exact FY span in
        # targetvsactuals, so a whole financial year is also safe.
        if len(period.spans) == 1 and _whole_fy(period.spans[0]) is not None:
            return f"fy {_whole_fy(period.spans[0])}", warnings
        warnings.append(
            "targetvsactuals cannot parse date ranges (see DATE_GRAMMAR.md s4): "
            "20 of 21 forms resolve incorrectly. Pass resolved dates as explicit "
            "parameters, or treat this result as unverified."
        )
        s = period.spans[0]
        return f"{MONTH_NAME[s.start.month]} {s.start.year}", warnings

    # A whole financial year is emitted as "fy <year>" for every tool.
    # Measured: case_report mangles "1 April 2025 to 31 March 2026" into
    # 2027-03-01 -> 2026-04-30 -- an inverted range, which returns zero rows.
    # "fy 2025" resolves correctly everywhere. (DATE_GRAMMAR.md s6)
    # Check the whole span, not just a single-span period: a month-on-month
    # period is 12 month spans that together cover exactly one FY, and
    # "fy 2026" parses identically while avoiding the cross-calendar-year
    # range form.
    overall = Span(min(s.start for s in period.spans),
                   max(s.end for s in period.spans))
    fy = _whole_fy(overall)
    if fy is not None:
        return f"fy {fy}", warnings

    # Multi-year periods. Measured 26 Aug 2026, probe re-run:
    #   A year SERIES (the user asked yearly/yoy) for lead/opp/task is bare
    #     "2020 to 2026" -> YEAR_WISE, one row per FY, honouring exactly the
    #     requested span. "fy A to fy B" keeps only the two endpoint years and
    #     "FY2019-20 to FY2026-27" keeps only the first, so ONLY the bare-year
    #     form may be emitted. No yoy token: with it the backends' yoy branch
    #     fires first and replaces the span with its own hardcoded floor
    #     (FY2020 lead/task, FY2018 opp).
    #   A multi-year SPAN with no series asked (comparison none) falls through
    #     to the day-form range below, which returns one total.
    #   event is the exception both ways: every dated form except yoy crashes
    #     (NameError: is_qoq, event_report.py:1829), and its yoy branch --
    #     uniquely -- honours an explicit range. So event always gets
    #     "yoy A to B" and can only ever answer year-wise.
    #   case/targets have no per-year series support at all -- the normaliser
    #     decomposes those into one "fy <year>" call per year upstream, as it
    #     does for mom/qoq series over multiple years ("qoq fy 2023" is the
    #     verified per-year form; "qoq 2023 to 2025" collapses to one FY).
    if period.kind is Kind.YEAR_LIST and len(period.spans) > 1:
        a = period.spans[0].start.year
        b = period.spans[-1].start.year
        if tool is Tool.EVENT:
            if period.comparison is not Comparison.YOY:
                warnings.append(
                    "event_report cannot return a single total for a multi-year "
                    "span (every dated form except yoy crashes); emitted the "
                    "year-wise form instead -- sum the rows for the span total.")
            return f"yoy {a} to {b}", warnings
        if period.comparison is Comparison.YOY \
                and tool in (Tool.LEAD, Tool.OPPORTUNITY, Tool.TASK):
            return f"{a} to {b}", warnings

    # Month-grain over an explicit window, and case's multi-month windows.
    # The ONLY form measured to keep both the window and the grain is the
    # month-name range with the year stated once at the end: "mom June to
    # December 2025" -> month-wise rows June..December in lead/opp/task, and
    # the correct span in case (which ignores the grain token in its date
    # parser). The day form plus a comparison token DISCARDS the window
    # (full-FY series, DATE_GRAMMAR.md s3), and for case even the bare
    # day form substitutes the current FY. Cross-calendar-year name ranges
    # invert in case ("November 2024 to February 2025" -> 2027-02..2026-11),
    # so this form is limited to one calendar year.
    if period.kind is Kind.RANGE and len(period.spans) == 1:
        s = period.spans[0]
        whole_months = (s.start.day == 1
                        and s.end.day == monthrange(s.end.year, s.end.month)[1])
        if whole_months and s.start.year == s.end.year \
                and s.start.month != s.end.month \
                and (tool is Tool.CASE or period.comparison is Comparison.MOM):
            return (f"{MONTH_NAME[s.start.month]} to "
                    f"{MONTH_NAME[s.end.month]} {s.end.year}"), warnings

        # case-only relative forms, measured 27 Aug 2026. The day form of
        # these windows is mangled by case_report ("1 April 2025 to 27 August
        # 2026" -> current FY substituted; "9 July to 27 August 2026" ->
        # snapped to whole months), but case's own relative branches resolve
        # them exactly: "from April 2025 till date" -> 2025-04-01..today, and
        # "last 50 days" -> today-49..today.
        if tool is Tool.CASE and s.end == today:
            if s.start.day == 1:
                return (f"from {MONTH_NAME[s.start.month]} {s.start.year} "
                        f"till date"), warnings
            n = (s.end - s.start).days + 1
            return f"last {n} days", warnings

    if tool is Tool.CASE:
        # case_report's extract_specific_months_from_query fires on 2+ month
        # names, keeps only the month numbers and substitutes the CURRENT FY --
        # silently discarding the year the user asked for. Multi-month spans
        # must therefore be decomposed upstream into single-month calls.
        multi_month = (period.spans[0].start.year != period.spans[0].end.year
                       or period.spans[0].start.month != period.spans[0].end.month) \
            if len(period.spans) == 1 else len(period.spans) > 1
        if multi_month and not all(_is_whole_month(s) for s in period.spans):
            warnings.append(
                "case_report discards the year on any range spanning 2+ months "
                "and substitutes the current FY (DATE_GRAMMAR.md s6). This range "
                "cannot be expressed safely -- pass resolved dates as parameters "
                "or treat the result as unverified.")

    # Discrete months -> "April, May and June 2026".
    # Year stated ONCE at the end: repeating it per month silently drops the
    # first month in four of five services.
    if period.kind is Kind.MONTH_LIST and all(_is_whole_month(s) for s in period.spans):
        # A SINGLE whole month ("August 2026") is verified in all thirteen
        # services, funnels included (measured 27 Aug 2026). Only a multi-month
        # list is service-dependent -- and for funnels it never reaches here,
        # because those decompose to one call per month upstream.
        if tool not in _MONTHLIST_OK and len(period.spans) > 1:
            warnings.append(f"{tool.value}: month-list form not verified.")
        years = {s.start.year for s in period.spans}
        names = [MONTH_NAME[s.start.month] for s in period.spans]
        if len(years) == 1:
            year = years.pop()
            if len(names) == 1:
                return f"{names[0]} {year}", warnings
            return f"{', '.join(names[:-1])} and {names[-1]} {year}", warnings
        # Months straddle a fiscal-year boundary; a single list would be
        # ambiguous, so fall through to an explicit range.
        warnings.append("Month list spans multiple calendar years; "
                        "emitted as an explicit range instead.")

    # Whole fiscal quarter on a funnel tool. The form is service-split, and
    # only a PAST-year probe shows it (27 Aug 2026): product_funnel needs the
    # bare year, every other service needs "q<n> fy <year>" -- the bare year
    # there returns the CURRENT FY's quarter. Q4 is honoured only in
    # project/product/subsource; source and lead fall through to the day form,
    # and the user funnels have Q4 split into month calls upstream.
    if tool in FUNNEL_TOOLS and len(period.spans) == 1:
        wq = _whole_quarter(period.spans[0])
        if wq:
            fy_q, qn = wq
            if qn < 4 or tool in FUNNEL_Q4_OK:
                if tool in FUNNEL_QUARTER_BARE_YEAR:
                    return f"q{qn} {fy_q}", warnings
                return f"q{qn} fy {fy_q}", warnings

    # Everything else -> "1 April 2026 to 30 June 2026".
    # Bare 'to'. NOT 'between X and Y' (collapses to one day in case/targets)
    # and NOT 'from X to Y' (end becomes today in case).
    if tool in FUNNEL_TOOLS:
        if tool not in FUNNEL_DAYRANGE_OK:
            # A day-form range collapses to the last days of the end month in
            # these services, which silently answers a 2-day question. A
            # rolling window ending today has a native form that resolves to a
            # sane window of the right length, so prefer it and let the agent
            # report the span actually returned (their day convention differs
            # by one or two days from ours).
            if period.end == today and period.start is not None:
                n = (period.end - period.start).days + 1
                warnings.append(
                    f"{tool.value}: emitted the relative form 'last {n} days' "
                    f"because day-form ranges collapse in this service. Its "
                    f"own day convention may differ by a day or two -- label "
                    f"the table from the period actually returned.")
                return f"last {n} days", warnings
            warnings.append(
                f"{tool.value}: day-form ranges collapse to the tail of the "
                f"end month in this service (measured); this window cannot be "
                f"expressed safely -- treat the result as unverified.")
    elif tool not in _RANGE_OK:
        warnings.append(f"{tool.value}: explicit-range form not verified.")
    return f"{_day(period.start)} to {_day(period.end)}", warnings


def render_query(
    metric_label: str,
    tool: Tool,
    period: Period,
    groupings: list[str] | None = None,
    filters: dict[str, list[str]] | None = None,
    today: date | None = None,
) -> tuple[str, list[str]]:
    """Assemble the full canonical query string for one tool call."""
    groupings = groupings or []
    filters = filters or {}
    parts: list[str] = [metric_label.lower()]
    warnings: list[str] = []

    for facet in groupings:
        if facet in ("month", "quarter", "year"):
            continue                      # carried by the comparison token
        parts.append(f"{facet.replace('_', ' ')} wise")

    for facet, values in filters.items():
        if not values:
            continue
        joined = values[0] if len(values) == 1 else \
            f"{', '.join(values[:-1])} and {values[-1]}"
        parts.append(f"for {joined}")

    phrase, w = render_period(period, tool, today)
    warnings.extend(w)

    if period.comparison is not Comparison.NONE:
        # Verified: 'mom' / 'qoq' / 'yoy' behave identically to the spelled-out
        # forms once a safe phrase is used, and are shorter. Exceptions, all
        # measured:
        #   - A YEAR_LIST's own phrase already carries the year grain ("2020 to
        #     2026" -> year-wise rows; event's phrase embeds "yoy" itself).
        #     Appending yoy here would trip the backends' yoy branch, which
        #     discards the requested span for its hardcoded floor.
        #   - YOY over a single whole FY ("year wise for fy 2025") is that
        #     year's total; "yoy fy 2025" would return every year instead.
        #   - A comparison token next to a DAY-FORM range makes the backends
        #     discard the range and answer for the full FY series
        #     (DATE_GRAMMAR.md s3). When the phrase could not carry the grain
        #     (mid-month or cross-calendar-year windows), the window wins:
        #     drop the token and say so.
        skip_token = (period.kind is Kind.YEAR_LIST or (
            period.comparison is Comparison.YOY
            and len(period.spans) == 1
            and _whole_fy(period.spans[0]) is not None))
        day_form = bool(re.match(r"\d{1,2}\s+[A-Z]", phrase or ""))
        if day_form and not skip_token:
            skip_token = True
            warnings.append(
                f"The requested window cannot carry a "
                f"'{period.comparison.value}' breakdown in {tool.value} (the "
                f"token would make the backend discard the window). The window "
                f"was kept and the breakdown dropped -- state the period "
                f"actually returned.")
        if not skip_token:
            parts.append(COMPARISON_TOKEN[period.comparison])

    if phrase:
        parts.append(phrase)

    return " ".join(parts), warnings
