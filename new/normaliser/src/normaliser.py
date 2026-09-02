"""
Prompt normaliser for the CRM-Data agent.

Turns a free-form user query into a structured, validated NormalisedQuery plus
the canonical text each target tool was measured to parse correctly.

Design constraints, each earned from a measured defect (grammar/DATE_GRAMMAR.md):

* Deterministic. Lookup tables and a single date resolver do the rewriting.
  No LLM rewrites free text -- a rewriter that turns "not interested" into
  "interested", or drops the second project, produces a confident wrong number
  with no error.
* Never silently default. Unresolved dates and unknown entities are reported,
  not guessed.
* Decomposition is structural. Multiple entities, periods or metrics split into
  separate calls without the user typing "separately".
* Output is per-tool. There is no single canonical form that all tools accept.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dates import (Comparison, Kind, Period, Span, fq_span, fy_of, fy_span,  # noqa: E402
                   month_span, detect_comparisons, detect_multi_periods, resolve)
from intents import (AGENT, DATA_START_FY, DEFAULT_START_FY,  # noqa: E402
                     FUNNEL_GRAIN_OK, FUNNEL_ROW_LIMIT, FUNNEL_SCOPE_FACET,
                     FUNNEL_TOOLS, Ranking, Tool, estimate_funnel_rows,
                     find_groupings, find_metrics, find_ranking,
                     resolve_funnel_tool, route)
from render import render_query  # noqa: E402

VOCAB_PATH = Path(__file__).parent / "vocabulary.json"

# Facets the user can filter on, in the order we PREFER them when one alias
# exists in several. Source precedes subsource deliberately: values like
# "Channel Partner" and "Outdoor" are mirrored into both columns with nearly
# identical row counts, and the source column is the primary reporting home
# (measured 27 Aug 2026 -- subsource-first sent "Channel Partner" funnels to
# the wrong tool). Values that exist only as sub-sources (Facebook, Google)
# are unaffected.
FILTERABLE = ["source", "subsource", "product", "project", "city", "owner",
              "property_type", "status", "feedback", "appointment_status",
              "subject", "service_request_type", "service_subcategory",
              "service_category", "rating", "disqualification_reason"]

# Words that must never be treated as entity names even if they collide.
STOPWORDS = {
    "total", "show", "me", "give", "list", "display", "all", "the", "for", "of",
    "in", "and", "or", "by", "with", "wise", "count", "between", "from", "to",
    "till", "vs", "versus", "compare", "each", "per", "on", "basis", "how",
    "many", "what", "which", "who", "top", "get", "fetch", "see", "view",
    "generic",
}


@dataclass
class ToolCall:
    tool: str
    agent: str
    metric: str
    metric_label: str
    canonical_text: str
    start_date: str | None
    end_date: str | None
    period_kind: str
    comparison: str
    groupings: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    period_label: str = ""
    rank: dict | None = None


@dataclass
class NormalisedQuery:
    raw: str
    ok: bool
    calls: list[ToolCall] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification: str | None = None
    unknown_entities: list[str] = field(default_factory=list)
    decomposed: bool = False
    agents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Vocabulary:
    """Canonical entity lookup, generated from the real data by vocab_build.py."""

    def __init__(self, path: Path = VOCAB_PATH):
        self.loaded = path.exists()
        self._alias: dict[str, list[tuple[str, str, int]]] = {}
        self._facets: dict[str, list[dict]] = {}
        self._meta: dict[str, dict] = {}
        if not self.loaded:
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self._facets = data.get("facets", {})
        self._meta = data.get("facet_meta", {})
        for facet, entries in self._facets.items():
            matchable = self._meta.get(facet, {}).get("alias_matchable", True)
            for e in entries:
                # Free-text facets (subject, city) are exact-match only. Loose
                # alias forms there collide constantly -- the subject
                # 'We were unable to contact you_Wave City' contains a project.
                forms = e["aliases"] if matchable else [self._norm(e["canonical"])]
                for a in forms:
                    self._alias.setdefault(a, []).append(
                        (facet, e["canonical"], e["row_count"]))

    @staticmethod
    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[_\-\.]+", " ", s)
        s = re.sub(r"[^\w\s()]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def longest_aliases(self) -> list[str]:
        return sorted(self._alias, key=len, reverse=True)

    def lookup(self, token: str, prefer: list[str] | None = None):
        """Return (facet, canonical) or None. `prefer` biases facet choice."""
        hits = self._alias.get(self._norm(token))
        if not hits:
            return None
        if prefer:
            for facet in prefer:
                for f, c, _ in hits:
                    if f == facet:
                        return f, c
        # Otherwise the most frequent value wins -- with real row counts this
        # resolves 'amore' to the product, not a rare owner surname.
        f, c, _ = max(hits, key=lambda h: h[2])
        return f, c

    def variants_of(self, facet: str, canonical: str) -> list[str]:
        for e in self._facets.get(facet, []):
            if e["canonical"] == canonical:
                return e["variants"]
        return [canonical]


_VOCAB: Vocabulary | None = None


def vocab() -> Vocabulary:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = Vocabulary()
    return _VOCAB


def extract_entities(text: str, tool: Tool):
    """Find named entities, longest-match-first, without overlapping.

    Returns (grouped, located) where `located` carries character offsets so a
    multi-metric query can bind each entity to the metric it sits nearest to.
    """
    v = vocab()
    if not v.loaded:
        return {}, []

    t = v._norm(text)
    found: dict[str, list[str]] = {}
    located: list[tuple[str, str, int, int]] = []
    claimed: list[tuple[int, int]] = []

    prefer = {
        Tool.OPPORTUNITY: ["project", "product", "source", "subsource",
                           "property_type", "owner"],
        Tool.CASE: ["project", "product", "service_request_type",
                    "service_subcategory", "service_category", "owner"],
        Tool.EVENT: ["project", "product", "appointment_status", "subject", "owner"],
        Tool.TASK: ["project", "product", "status", "subject", "owner"],
    }.get(tool, FILTERABLE)

    for alias in v.longest_aliases():
        if len(alias) < 3 or alias in STOPWORDS:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", t):
            s, e = m.span()
            if any(s < ce and e > cs for cs, ce in claimed):
                continue
            hit = v.lookup(alias, prefer=prefer)
            if not hit:
                continue
            facet, canonical = hit
            claimed.append((s, e))
            found.setdefault(facet, [])
            if canonical not in found[facet]:
                found[facet].append(canonical)
            located.append((facet, canonical, s, e))
    return found, located


def _split_multi_metric(text: str) -> bool:
    """True when the query explicitly contrasts two metrics."""
    return bool(re.search(r"\b(vs|versus)\b", text.lower())) or bool(
        re.search(r"\b\w+\s+and\s+(total\s+)?\w+\s+(for|between|in)\b", text.lower()))


def normalise(query: str, today: date | None = None,
              decompose_entities: bool = True) -> NormalisedQuery:
    """Normalise one user query into validated, per-tool canonical calls."""
    raw = query.strip()
    today = today or date.today()
    result = NormalisedQuery(raw=raw, ok=False)

    if not raw:
        result.clarification = "Empty query."
        return result

    # 0a. Concepts no tool computes. Answering these with a row count would be
    # a confident wrong answer, so refuse explicitly instead.
    unsupported = _detect_unsupported(raw)
    if unsupported:
        result.clarification = (
            f"'{unsupported}' is not something the current tools compute. "
            f"They return counts and breakdowns of leads, opportunities, sales, "
            f"events, tasks and cases -- not durations, averages or elapsed time. "
            f"Rephrase as a count, or this needs a new tool.")
        result.warnings.append(f"unsupported_metric: {unsupported}")
        return result

    # 0b. Conversational fragments depend on the previous turn. Fabricating a
    # complete query from them invents scope the user never asked for.
    if _is_context_fragment(raw):
        result.clarification = (
            "That refers back to an earlier question. Please restate it with the "
            "metric and period, or the orchestrator must supply the prior context.")
        result.warnings.append("context_fragment: requires conversation state")
        return result

    # 1. metric + routing -------------------------------------------------
    metrics = find_metrics(raw)
    if not metrics:
        result.clarification = (
            "I could not identify which metric you want. Please specify one of: "
            "leads, opportunities, sales, events, meetings, tasks, follow-ups, "
            "cases, or targets vs actuals.")
        return result

    # A funnel already reports every stage (total, valid, junk, qualified,
    # meeting booked, meeting done, sales done). "lead funnel" is one request,
    # not a lead count plus a funnel, so the funnel metric wins outright.
    if any(m.metric.key == "funnel" for m in metrics):
        metrics = [m for m in metrics if m.metric.key == "funnel"]

    primary, tools = route(metrics)

    # 2. period -----------------------------------------------------------
    # A query may name more than one comparison ("year on year sales and also
    # the month on month sales"). Resolve one period per comparison so none is
    # dropped; each becomes its own set of calls.
    comparisons = detect_comparisons(raw) or [Comparison.NONE]

    # "last fy and current fy", "this quarter vs last quarter" name two periods
    # and want two result sets. Resolving the whole string would keep only one.
    multi = detect_multi_periods(raw, today)
    if multi and len(comparisons) == 1:
        for p in multi:
            p.comparison = comparisons[0]
        periods = multi
        result.decomposed = True
        result.warnings.append(
            "Query names " + str(len(multi)) + " periods (" +
            ", ".join(p.label for p in multi) +
            "); each is returned as a separate result set.")
    else:
        periods = [resolve(raw, today, force_comparison=c) for c in comparisons]

    for p in periods:
        result.warnings.extend(w for w in p.warnings if w not in result.warnings)
    if len(comparisons) > 1:
        result.decomposed = True
        result.warnings.append(
            "Query asks for " + " and ".join(c.value for c in comparisons) +
            "; each is returned as a separate result set.")

    period = periods[0]
    if not period.resolved:
        result.clarification = (
            "I could not determine a date range from that. Please give one, "
            "for example 'last FY', 'Q1 2025', 'April to June 2026', or "
            "'between 1 April 2026 and 30 June 2026'.")
        return result

    # 3. grouping + ranking -----------------------------------------------
    groupings = find_groupings(raw)
    ranking = find_ranking(raw)
    # "top 5 products" / "which source has the most leads" name the breakdown
    # dimension implicitly; without this the ranking has nothing to rank.
    if ranking and ranking.facet and ranking.facet not in groupings:
        groupings.append(ranking.facet)

    # 4. entities ---------------------------------------------------------
    entities, located = extract_entities(raw, primary)
    if not vocab().loaded:
        result.warnings.append(
            "vocabulary.json not built -- entity filters were not resolved. "
            "Run: python src/vocab_build.py")

    # An explicitly NAMED entity that the data does not hold must refuse, not
    # silently run unscoped: "funnel for source Social Media" ran org-wide
    # when Social Media matched nothing (batch 27 Aug 2026). Conservative on
    # purpose -- only fires on "source/sub source/project/product <Proper
    # Noun>" where no vocabulary entry resolves any prefix of the name.
    gap = _named_entity_gap(raw)
    if gap:
        facet_word, candidate = gap
        known = ", ".join(
            e["canonical"] for e in vocab()._facets.get(facet_word, [])[:4])
        result.clarification = (
            f"'{candidate}' is not a {facet_word.replace('subsource', 'sub-source')} "
            f"in the data." + (f" Known values include: {known}." if known else ""))
        result.unknown_entities = [candidate]
        result.warnings.append(f"unknown_entity: {facet_word}={candidate}")
        return result

    # Do not filter on a facet the user is grouping by; that would collapse
    # the very breakdown they asked for.
    def _strip_grouped(d: dict[str, list[str]]) -> dict[str, list[str]]:
        return {f: v for f, v in d.items() if f not in groupings}

    all_filters = _strip_grouped(entities)

    # With several metrics, bind each entity to the metric it sits nearest to.
    # "sales of veridia and leads of wave city" means Veridia scopes the sales
    # and Wave City scopes the leads -- not both filters on both.
    def _filters_for(mm) -> dict[str, list[str]]:
        if len(metrics) < 2 or not located:
            return all_filters
        own: dict[str, list[str]] = {}
        for facet, canonical, s, _e in located:
            nearest = min(metrics, key=lambda m: abs(s - m.span[0]))
            if nearest is mm:
                own.setdefault(facet, []).append(canonical)
        # An entity written before any metric usually scopes the whole query
        # ("for wave city show me leads and tasks"), so fall back to shared.
        return _strip_grouped(own) if own else all_filters

    # 4b. funnel routing --------------------------------------------------
    # A funnel question carries one metric; which funnel tool serves it depends
    # on the breakdown asked for and the entities named.
    funnel_tool: Tool | None = None
    if any(m.metric.key == "funnel" for m in metrics):
        funnel_tool, ask, implied = resolve_funnel_tool(raw, groupings, entities)
        if ask:
            result.clarification = ask
            return result

        # "product funnel" is a breakdown request in the same way "product wise
        # funnel" is, so it must emit "funnel product wise". Without this the
        # canonical text was a bare "funnel June 2026", which reads as the
        # overall lead funnel and was executed as one (production, 2 Sep 2026).
        # Skipped when the user named a value on that facet: "funnel for Eden"
        # wants Eden's row, not one row per product, and adding the grouping
        # would strip the filter below.
        if implied and implied not in groupings and not entities.get(implied):
            groupings.append(implied)
            all_filters = _strip_grouped(entities)

        # A funnel narrowed to ONE period always runs, whatever the breadth of
        # its breakdown -- a single-month user funnel is one row per user, and
        # that is exactly what was asked for. Only a breakdown REPEATED across
        # periods (month on month, quarter on quarter, year on year, or a
        # multi-period span) multiplies into something unreadable in chat;
        # that case asks the user to pick one period of the grain they named,
        # instead of running it.
        period_count = sum(_effective_period_count(p) for p in periods)
        est = estimate_funnel_rows(funnel_tool, period_count, entities)
        if period_count > 1 and est > FUNNEL_ROW_LIMIT:
            scope = FUNNEL_SCOPE_FACET.get(funnel_tool)
            result.clarification = _funnel_too_broad(
                scope, period_count, est, periods[0])
            result.warnings.append(
                f"funnel_too_broad: ~{est} rows ({funnel_tool.value}, "
                f"{period_count} periods)")
            return result

    # 5. build calls ------------------------------------------------------
    # One call per METRIC, not per tool: "total opportunities and sales done"
    # are two metrics on the same backend and both must be requested.
    calls: list[ToolCall] = []
    for mm in metrics:
        metric = mm.metric
        tool = funnel_tool if metric.key == "funnel" else metric.tool
        base = _filters_for(mm)

        # Split a multi-entity query into one call per entity when the tool's
        # own extractor is known to keep only the first match.
        entity_sets: list[dict[str, list[str]]] = [base]
        if decompose_entities:
            for facet in ("project", "product", "source", "subsource"):
                if len(base.get(facet, [])) > 1:
                    entity_sets = [{**base, facet: [v]} for v in base[facet]]
                    result.decomposed = True
                    break

        # One period set per comparison the user asked for. Within each, split
        # discrete month lists into one call per month so each period is
        # reported separately -- this is what 'separately' used to do manually.
        period_sets: list[Period] = []
        is_funnel = tool in FUNNEL_TOOLS
        for per in periods:
            whole_fy = (per.start is not None and per.end is not None
                        and (per.start.month, per.start.day) == (4, 1)
                        and (per.end.month, per.end.day) == (3, 31)
                        and per.end.year == per.start.year + 1)
            # A funnel keeps a mom/qoq grain in ONE call only where measured
            # to work: project and subsource over a whole FY. Everywhere else
            # the grain is silently dropped by the backend, so the series is
            # decomposed into verified single-period calls instead.
            funnel_keeps_grain = (
                is_funnel and whole_fy and tool in FUNNEL_GRAIN_OK
                and per.comparison in (Comparison.MOM, Comparison.QOQ))
            if per.kind is Kind.MONTH_LIST and len(per.spans) > 1 \
                    and per.comparison is Comparison.NONE:
                period_sets.extend(
                    Period(Kind.MONTH_LIST, [s], per.comparison, s.label)
                    for s in per.spans)
                result.decomposed = True
            elif per.kind is Kind.MONTH_LIST and len(per.spans) > 1 \
                    and not funnel_keeps_grain \
                    and (is_funnel or (tool is Tool.CASE and not whole_fy)):
                # case_report substitutes the CURRENT financial year into any
                # text naming 2+ months, and most funnel services keep only
                # the first month of a list, so these go one month per call
                # whatever grain word came with them.
                period_sets.extend(
                    Period(Kind.MONTH_LIST, [s], Comparison.NONE, s.label)
                    for s in per.spans)
                result.decomposed = True
            elif per.kind is Kind.YEAR_LIST and len(per.spans) > 1 and (
                    is_funnel or tool in (Tool.CASE, Tool.TARGETS)
                    or per.comparison in (Comparison.MOM, Comparison.QOQ)):
                # Measured 26-27 Aug 2026: case_report ignores bare year
                # ranges, targetvsactuals parses only single months and FYs,
                # no funnel service has a year-series form, and "qoq 2023 to
                # 2025" collapses to ONE year in the report tools -- while
                # "qoq fy 2023" / "fy 2023" are verified. One call per year
                # keeps both the years and (where supported) the grain. YOY
                # is always cleared on the split calls.
                keep = per.comparison \
                    if (per.comparison in (Comparison.MOM, Comparison.QOQ)
                        and (not is_funnel or tool in FUNNEL_GRAIN_OK)) \
                    else Comparison.NONE
                period_sets.extend(
                    Period(Kind.RANGE, [s], keep, s.label)
                    for s in per.spans)
                result.decomposed = True
            elif is_funnel and per.kind is Kind.QUARTER_LIST \
                    and len(per.spans) > 1:
                # Multi-quarter funnel lists go one verified quarter call each.
                period_sets.extend(
                    Period(Kind.RANGE, [s], Comparison.NONE, s.label)
                    for s in per.spans)
                result.decomposed = True
            elif is_funnel and len(per.spans) == 1 and not funnel_keeps_grain \
                    and per.comparison in (Comparison.MOM, Comparison.QOQ):
                spans = (_month_spans_of(per.spans[0])
                         if per.comparison is Comparison.MOM
                         else _quarter_spans_of(per.spans[0]))
                if spans:
                    period_sets.extend(
                        Period(Kind.MONTH_LIST
                               if per.comparison is Comparison.MOM
                               else Kind.RANGE, [s], Comparison.NONE, s.label)
                        for s in spans)
                    result.decomposed = True
                else:
                    period_sets.append(per)
            elif tool in (Tool.LEAD_USER_FUNNEL, Tool.SALES_USER_FUNNEL) \
                    and len(per.spans) == 1 and _is_q4_span(per.spans[0]):
                # Q4 has no single-call form in the user funnels: "q4 <year>"
                # ignores the year and day-form ranges collapse (measured).
                # Three verified month calls instead.
                q4 = per.spans[0]
                period_sets.extend(
                    Period(Kind.MONTH_LIST, [month_span(q4.start.year, m)],
                           Comparison.NONE)
                    for m in (1, 2, 3))
                result.decomposed = True
            else:
                period_sets.append(per)

        # A period entirely below the tool's first year has no answer. Say so
        # rather than sending a request that can only come back empty.
        floor_fy = DATA_START_FY.get(tool, DEFAULT_START_FY)
        if all(p.end is not None and p.end < date(floor_fy, 4, 1) for p in periods):
            asked = periods[0]
            result.clarification = _out_of_coverage(
                metric.label, tool, floor_fy, asked, today)
            result.warnings.append(
                f"out_of_coverage: {tool.value} starts FY{floor_fy}")
            return result

        for eset in entity_sets:
            for per in period_sets:
                # Never ask a backend for a year it does not hold.
                per, clamp_note = clamp_to_coverage(per, tool)
                if clamp_note and clamp_note not in result.warnings:
                    result.warnings.append(clamp_note)

                text, warns = render_query(
                    metric.label, tool, per, groupings, eset, today)
                result.warnings.extend(w for w in warns if w not in result.warnings)
                calls.append(ToolCall(
                    tool=tool.value,
                    agent=AGENT[tool],
                    metric=metric.key,
                    metric_label=metric.label,
                    canonical_text=text,
                    start_date=per.start.isoformat() if per.start else None,
                    end_date=per.end.isoformat() if per.end else None,
                    period_kind=per.kind.value,
                    comparison=per.comparison.value,
                    groupings=list(groupings),
                    filters={k: list(v) for k, v in eset.items()},
                    period_label=per.label or period.label,
                    rank=({"direction": ranking.direction, "count": ranking.count}
                          if ranking else None),
                ))

    # 6. validation gate --------------------------------------------------
    # Nothing may be invented or lost between input and output.
    for c in calls:
        for facet, values in c.filters.items():
            for val in values:
                if not _appears_in(raw, val, facet):
                    result.warnings.append(
                        f"Filter {facet}='{val}' is not clearly present in the "
                        f"query; verify before executing.")
    if len(tools) > 1:
        result.warnings.append(
            f"Query spans {len(tools)} tools ({', '.join(t.value for t in tools)}); "
            f"results must be merged by the orchestrator.")

    result.calls = calls
    result.agents = sorted({c.agent for c in calls})
    result.ok = bool(calls)
    return result


# Concepts the CRM tools cannot compute. All of them are durations, averages or
# rates; every tool returns row counts and groupings only.
UNSUPPORTED = [
    (r"\bturn[\s\-]?around\s+time\b|\btat\b", "turnaround time"),
    # The tools count rows; none holds a rupee amount. Answering "sales value"
    # with a sales COUNT is a silent wrong answer (logics.md defines no value
    # or amount metric), so refuse and let the master offer the count.
    (r"\b(?:sales?|bookings?|deals?|revenue)\s+(?:value|amount)s?\b",
     "sale value / amount"),
    (r"\baverage\s+time\b|\bavg\s+time\b|\bmean\s+time\b", "average time"),
    (r"\btime\s+taken\b|\bhow\s+long\s+(?:does|did|it)\b", "elapsed time"),
    (r"\bduration\b|\bageing\b|\baging\b", "duration / ageing"),
    (r"\bvelocity\b|\bthroughput\s+rate\b", "velocity"),
    (r"\bforecast\b|\bpredict\b|\bprojection\b", "forecasting"),
]

# Phrases that only make sense against a previous turn.
CONTEXT_FRAGMENT = [
    r"^\s*(it|this|that|these|those|they)\b",
    r"\b(bifurcate|split|break|combine|compare)\s+(this|that|these|those|them|all)\b",
    r"^\s*now\s+\w+",
    r"\bsame\s+(as\s+)?(above|before|earlier)\b",
    r"^\s*what\s+about\b",
]


def _month_spans_of(span: Span) -> list[Span] | None:
    """Whole-month spans covering `span`, or None if its edges are ragged."""
    from calendar import monthrange
    if span.start.day != 1 \
            or span.end.day != monthrange(span.end.year, span.end.month)[1]:
        return None
    out, y, m = [], span.start.year, span.start.month
    while (y, m) <= (span.end.year, span.end.month):
        out.append(month_span(y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _quarter_spans_of(span: Span) -> list[Span] | None:
    """The four quarters of `span` when it is exactly one whole FY."""
    if (span.start.month, span.start.day) == (4, 1) \
            and (span.end.month, span.end.day) == (3, 31) \
            and span.end.year == span.start.year + 1:
        return [fq_span(span.start.year, q) for q in (1, 2, 3, 4)]
    return None


def _is_q4_span(span: Span) -> bool:
    return (span.start.month, span.start.day) == (1, 1) and \
        (span.end.year, span.end.month, span.end.day) == (span.start.year, 3, 31)


def clamp_to_coverage(period: Period, tool: Tool) -> tuple[Period, str | None]:
    """Trim a period so it never starts before the tool's first year of data.

    The backends hold nothing earlier and will not return it, so requesting it
    yields an empty table or a period mismatch instead of an answer. Spans that
    fall entirely below the floor are left alone -- the caller reports those as
    out of range rather than silently answering a different question.
    """
    floor_fy = DATA_START_FY.get(tool, DEFAULT_START_FY)
    floor = date(floor_fy, 4, 1)

    # "Year on year" with no stated period means every year this report holds,
    # so rebuild it from the tool's own floor. Opportunities reach back further
    # than the other reports, and trimming alone would never extend to it.
    if period.label.startswith("all years held"):
        cur_fy = date.today().year if date.today().month >= 4 else date.today().year - 1
        end_fy = max(fy_of(period.end) if period.end else cur_fy, floor_fy)
        spans = [fy_span(y) for y in range(floor_fy, end_fy + 1)]
        return Period(Kind.YEAR_LIST, spans, period.comparison, period.label,
                      list(period.warnings), period.source_text), None

    if not period.spans or period.start is None or period.start >= floor:
        return period, None

    kept = [s for s in period.spans if s.end >= floor]
    if not kept:
        return period, (
            f"requested period ends before {tool.value} data begins "
            f"(FY{floor_fy}-{str(floor_fy + 1)[2:]})")

    trimmed = [Span(max(s.start, floor), s.end, s.label) for s in kept]
    out = Period(period.kind, trimmed, period.comparison, period.label,
                 list(period.warnings), period.source_text)
    return out, (
        f"start trimmed to FY{floor_fy}-{str(floor_fy + 1)[2:]}, "
        f"the first year of {tool.value} data")


def _out_of_coverage(metric_label: str, tool: Tool, floor_fy: int,
                     asked: Period, today: date) -> str:
    """Explain that the requested period is earlier than any data held."""
    cur_fy = today.year if today.month >= 4 else today.year - 1
    fy = f"FY{floor_fy}-{str(floor_fy + 1)[2:]}"
    cur = f"FY{cur_fy}-{str(cur_fy + 1)[2:]}"
    asked_txt = ""
    if asked.start and asked.end:
        asked_txt = f" You asked for {asked.start:%b %Y} to {asked.end:%b %Y}."

    return (
        f"{metric_label} data starts in {fy}.{asked_txt}\n\n"
        f"1. {metric_label} from {fy} to {cur}\n"
        f"2. {metric_label} for {cur} only\n"
        f"3. A different period from {fy} onwards")


def _effective_period_count(p: Period) -> int:
    """Output periods a funnel request produces, including grain expansion.

    "product funnel month on month for last fy" is ONE span (the FY) carrying
    a MOM grain, but produces 12 output periods. Counting spans alone would
    let 66 products x 12 months straight through the volume guard.
    """
    n = max(len(p.spans), 1)
    if len(p.spans) == 1 and p.start and p.end:
        days = (p.end - p.start).days + 1
        if p.comparison is Comparison.MOM:
            n = max(1, round(days / 30.4))
        elif p.comparison is Comparison.QOQ:
            n = max(1, round(days / 91.3))
        elif p.comparison is Comparison.YOY:
            n = max(1, round(days / 365.25))
    return n


def _funnel_too_broad(scope: str | None, period_count: int,
                      est: int, period) -> str:
    """Ask the user to pick ONE period for a breakdown repeated across many.

    Only reached when period_count > 1: a funnel for a single month, quarter
    or financial year always runs, however wide its breakdown. The options
    offered match the grain the user named -- month for month on month,
    quarter for quarter on quarter, financial year for year on year.
    """
    label = {
        "project": "project", "product": "product", "source": "source",
        "subsource": "sub-source", "owner": "user",
    }.get(scope or "", "")

    unit = {Comparison.MOM: "month", Comparison.QOQ: "quarter",
            Comparison.YOY: "financial year"}.get(period.comparison)
    if unit is None:
        unit = {Kind.MONTH_LIST: "month", Kind.QUARTER_LIST: "quarter",
                Kind.YEAR_LIST: "financial year"}.get(
            period.kind, "month, quarter or financial year")

    span = ""
    if period and period.start and period.end:
        span = f" from {period.start:%b %Y} to {period.end:%b %Y}"

    if scope:
        return (
            f"That would return about {est} funnel rows — every {label} across "
            f"{period_count} periods{span} — which is too much to read in one "
            f"table.\n\n"
            f"1. The {label} funnel for a single {unit} you name\n"
            f"2. The trend for one named {label} across those periods\n"
            f"3. The overall lead funnel across those periods, without the "
            f"{label} breakdown")

    return (
        f"That would return about {est} funnel rows across {period_count} "
        f"periods{span}, which is too much to read in one table.\n\n"
        f"1. The funnel for a single {unit} you name\n"
        f"2. A shorter span of {unit}s")


# "source <Name>" / "project <Name>" with a capitalised name. Lowercase names
# are left alone (too many false positives); a capitalised one is the user
# naming a specific value, and if no prefix of it resolves, the value is not
# in the data.
_NAMED_FACET = re.compile(
    r"\b(sub[\s\-]?source|source|project|product)\s+(?:is\s+)?"
    r"((?:[A-Z][\w&.-]*)(?:\s+[A-Z][\w&.-]*){0,3})")

_FACET_KEY = {"source": "source", "project": "project", "product": "product"}


# Words that follow a facet noun without naming a value: "funnel by source
# June 2026" is a source breakdown for June, not a source called June.
_NOT_A_VALUE = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "wise", "funnel", "report", "count", "total", "last", "this", "current",
    "previous", "next", "fy", "q1", "q2", "q3", "q4", "and", "or", "for",
}


def _named_entity_gap(raw: str) -> tuple[str, str] | None:
    v = vocab()
    if not v.loaded:
        return None
    for m in _NAMED_FACET.finditer(raw):
        first = m.group(2).split()[0].lower().rstrip(",.")
        if first in _NOT_A_VALUE or first.isdigit():
            continue
        facet_word = re.sub(r"[\s\-]", "", m.group(1).lower())
        facet = "subsource" if facet_word == "subsource" else _FACET_KEY[facet_word]
        tokens = m.group(2).split()
        resolved = False
        for k in range(len(tokens), 0, -1):
            if v.lookup(" ".join(tokens[:k])) is not None:
                resolved = True
                break
        if not resolved:
            return facet, m.group(2)
    return None


def _detect_unsupported(text: str) -> str | None:
    t = text.lower()
    for pat, label in UNSUPPORTED:
        if re.search(pat, t):
            return label
    return None


def _is_context_fragment(text: str) -> bool:
    t = text.lower().strip()
    return any(re.search(p, t) for p in CONTEXT_FRAGMENT)


def _appears_in(raw: str, value: str, facet: str) -> bool:
    """Loose containment check: did the user actually mention this entity?"""
    v = vocab()
    norm_raw = v._norm(raw)
    for variant in v.variants_of(facet, value):
        nv = v._norm(variant)
        if nv and nv in norm_raw:
            return True
        head = re.sub(r"\s*\([^)]*\)", "", nv).strip()
        if head and head in norm_raw:
            return True
    return False


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Normalise a CRM query.")
    ap.add_argument("query", nargs="*", help="query text")
    ap.add_argument("--today", default=None, help="reference date YYYY-MM-DD")
    args = ap.parse_args()

    ref = date.fromisoformat(args.today) if args.today else None
    q = " ".join(args.query) or input("query> ")
    print(json.dumps(normalise(q, ref).to_dict(), indent=2))
