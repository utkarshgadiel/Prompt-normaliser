"""
Metric registry and deterministic tool routing for the CRM-Data agent.

Routing is a lookup, not an LLM judgement. Each metric declares the single tool
that owns it, so the same business intent always lands on the same tool
regardless of phrasing.

Metric definitions are transcribed from logics.md and are NOT changed here --
they are the client's locked business semantics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Tool(str, Enum):
    # CRM-Data
    LEAD = "lead_report"
    OPPORTUNITY = "opportunity_report"
    EVENT = "event_report"
    TASK = "task_report"
    CASE = "case_report"
    TARGETS = "targetvsactuals"
    # CRM-Funnel
    LEAD_FUNNEL = "lead_funnel"
    PROJECT_FUNNEL = "project_funnel"
    PRODUCT_FUNNEL = "product_funnel"
    SOURCE_FUNNEL = "source_funnel"
    SUBSOURCE_FUNNEL = "subsource_funnel"
    LEAD_USER_FUNNEL = "lead_user_funnel"
    SALES_USER_FUNNEL = "sales_user_funnel"


FUNNEL_TOOLS = {
    Tool.LEAD_FUNNEL, Tool.PROJECT_FUNNEL, Tool.PRODUCT_FUNNEL,
    Tool.SOURCE_FUNNEL, Tool.SUBSOURCE_FUNNEL,
    Tool.LEAD_USER_FUNNEL, Tool.SALES_USER_FUNNEL,
}

AGENT = {t: ("CRM-Funnel" if t in FUNNEL_TOOLS else "CRM-Data") for t in Tool}

# Funnel date-grammar capability classes, measured 27 Aug 2026 by probing
# every funnel service offline -- at the ENDPOINT level, not just its
# DateResolver, because source/lead/lead-user/sales-user handle the grain in
# the endpoint before the resolver ever runs (grammar/DATE_GRAMMAR.md s8).
# "fy X" and "<Month> <Year>" parse exactly in all seven.
#
#   - GRAIN_OK: "mom fy X" / "qoq fy X" produce a true month/quarter series
#     for exactly the year asked. Only product_funnel cannot: it returns the
#     whole FY as one period, and no rephrasing fixes it ("mom April to March
#     2025" resolves to FY2025-26). Product grain therefore decomposes into
#     one verified single-period call per month or quarter.
#   - DAYRANGE_OK: "1 July 2026 to 30 September 2026" parses exactly. In
#     subsource and the two user funnels it collapses to the last days of the
#     end month.
#   - Quarters are service-split, and only a PAST-year probe reveals it:
#     product_funnel needs the bare year ("q1 2024"; "q1 fy 2024" widens to
#     the whole FY), while the DateResolver family needs the fy form ("q1 fy
#     2024"; "q1 2024" returns the CURRENT FY's quarter). Project and
#     subsource accept both. Q4_OK additionally covers "q4": in the
#     DateResolver family Q4 is wrong in BOTH forms, so those decompose into
#     three month calls.
FUNNEL_GRAIN_OK = {Tool.PROJECT_FUNNEL, Tool.SUBSOURCE_FUNNEL,
                   Tool.SOURCE_FUNNEL, Tool.LEAD_FUNNEL,
                   Tool.LEAD_USER_FUNNEL, Tool.SALES_USER_FUNNEL}
FUNNEL_DAYRANGE_OK = {Tool.PROJECT_FUNNEL, Tool.PRODUCT_FUNNEL,
                      Tool.SOURCE_FUNNEL, Tool.LEAD_FUNNEL}
FUNNEL_QUARTER_BARE_YEAR = {Tool.PRODUCT_FUNNEL}

# Rolling "last n days" conventions, measured 31 Aug 2026. Every service
# counts a different span from the same words, so a window of n days ending
# yesterday (the completed-period rule) needs a different n per service:
#   subsource_funnel  "last 15 days" -> 15 Aug..30 Aug  (n+1 days, ends
#                     yesterday), so emit n-1 and the span matches exactly.
#   lead/sales user   "last 15 days" -> 17 Aug..31 Aug  (n days, ends TODAY).
#                     No form ends yesterday, so emit n+1 to COVER the span
#                     and warn: the result carries one extra day.
# The value is added to the requested day count.
FUNNEL_ROLLING_DAY_OFFSET = {
    Tool.SUBSOURCE_FUNNEL: -1,
    Tool.LEAD_USER_FUNNEL: +1,
    Tool.SALES_USER_FUNNEL: +1,
}
FUNNEL_Q4_OK = {Tool.PROJECT_FUNNEL, Tool.PRODUCT_FUNNEL, Tool.SUBSOURCE_FUNNEL}

# Earliest financial year each report holds, as defined by the system.
# Nothing before this is ever requested: the backends will not return it, so
# asking produces an empty table or a period mismatch rather than an answer.
# "Year on year" means this start year through the current one.
DATA_START_FY = {
    Tool.LEAD: 2020,
    Tool.TASK: 2020,
    Tool.CASE: 2020,
    Tool.EVENT: 2020,
    Tool.TARGETS: 2020,
    Tool.OPPORTUNITY: 2018,
    # Funnels join leads, events and opportunities, so they are bounded by the
    # latest-starting input, which is the lead side.
    Tool.LEAD_FUNNEL: 2020,
    Tool.PROJECT_FUNNEL: 2020,
    Tool.PRODUCT_FUNNEL: 2020,
    Tool.SOURCE_FUNNEL: 2020,
    Tool.SUBSOURCE_FUNNEL: 2020,
    Tool.LEAD_USER_FUNNEL: 2020,
    Tool.SALES_USER_FUNNEL: 2020,
}

DEFAULT_START_FY = 2020


@dataclass(frozen=True)
class Metric:
    key: str
    tool: Tool
    label: str
    #: Ordered patterns. First match wins, so specific forms precede general.
    patterns: tuple[str, ...]
    #: Filter this metric implies, as (facet, value). None = plain row count.
    implies: tuple[str, str] | None = None
    note: str = ""


# Ordered most-specific first. `_find_metric` respects this order, which is why
# "not interested leads" cannot be captured by the generic "leads" pattern.
METRICS: list[Metric] = [
    # ---------------- funnel (must precede leads/sales) ----------------
    # A funnel question is about the whole conversion sequence, not a single
    # count. Placed first so "lead funnel" is not captured as "leads" and
    # "sales user funnel" is not captured as "sales". The specific funnel tool
    # is chosen afterwards by resolve_funnel_tool().
    Metric("funnel", Tool.LEAD_FUNNEL, "Funnel",
           (r"\bfunnels?\b", r"\bconversion\s+ratios?\b", r"\bconversions?\b"),
           None, "full lead-to-sale conversion sequence"),

    # ---------------- targets vs actuals (must precede events and cases) ----
    # Placed early, mirroring the fallback routing priority in the data
    # agent's behavior file: "target, cre, gre, ql target, sr target, sr
    # resolved" route to targetvsactuals FIRST. Without this, "appointment
    # completion rate" was claimed by the events metric and "SR target" by
    # the cases metric, producing spurious extra calls (batch run 27 Aug 2026,
    # prompts #798-801, #920-927, #940-974).
    Metric("targets_vs_actuals", Tool.TARGETS, "Targets vs Actuals",
           (r"targets?\s+(?:vs|versus|and)\s+actuals?",
            r"actuals?\s+(?:vs|versus)\s+targets?",
            r"\bql\s+(?:targets?|actuals?|achievements?)\b",
            r"\bsr\s+(?:targets?|resolved)\b", r"\bcre\b", r"\bgre\b",
            r"(?:service\s+requests?|srs?)\s+resolution\s+rate",
            r"appointment\s+(?:booked|booking|completion)\s+"
            r"(?:targets?|actuals?|rates?|achievements?)",
            r"\bachievements?\s+percentage\b",
            r"qualified\s+target",
            r"targets?\s+(?:variance|surplus|shortfall)",
            r"\btargets?\b"),
           None),

    # ---------------- leads ----------------
    Metric("junk_leads", Tool.LEAD, "Junk Leads",
           (r"junk\s+leads?", r"\bjunk\b(?=.*\blead)"),
           ("feedback", "Junk"), "customer_feedback_c = 'Junk'"),
    Metric("valid_leads", Tool.LEAD, "Valid Leads",
           (r"valid\s+leads?",),
           ("feedback", "__not_junk__"), "customer_feedback_c != 'Junk'"),
    # (?<!un) stops "unqualified leads" being captured as "qualified leads".
    Metric("qualified_leads", Tool.LEAD, "Qualified Leads (SOL)",
           (r"(?<!un)\bqualified\s+leads?", r"\bsol\s+leads?\b", r"\bsol\b"),
           ("feedback", "Interested"), "customer_feedback_c = 'Interested'"),
    Metric("not_interested_leads", Tool.LEAD, "Not Interested Leads",
           (r"not[\s\-]?interested\s+leads?", r"not[\s\-]?interested"),
           ("feedback", "Not Interested"), ""),
    Metric("open_leads", Tool.LEAD, "Open Leads",
           (r"open\s+leads?",),
           ("feedback", "Discussion Pending"),
           "customer_feedback_c = 'Discussion Pending'"),
    Metric("unqualified_leads", Tool.LEAD, "Unqualified Leads",
           (r"unqualified\s+leads?",), ("status", "Unqualified"), "status = 'Unqualified'"),
    Metric("new_leads", Tool.LEAD, "New Leads",
           (r"new\s+leads?",), ("status", "New"), "status = 'New'"),
    Metric("nurturing_leads", Tool.LEAD, "Nurturing Leads",
           (r"nurturing\s+leads?",), ("status", "Nurturing"), "status = 'Nurturing'"),
    Metric("hot_leads", Tool.LEAD, "Hot Leads", (r"hot\s+leads?",), ("rating", "Hot")),
    Metric("cold_leads", Tool.LEAD, "Cold Leads", (r"cold\s+leads?",), ("rating", "Cold")),
    Metric("warm_leads", Tool.LEAD, "Warm Leads", (r"warm\s+leads?",), ("rating", "Warm")),
    # The negative lookahead stops "lead source", "lead source sub category"
    # and "lead id" being read as the *leads* metric -- they are grouping
    # phrases and column names, not a request for a lead count.
    Metric("total_leads", Tool.LEAD, "Total Leads",
           (r"total\s+leads?(?!\s+(?:source|sub))",
            r"\bleads?\b(?!\s+(?:source|sub|id|owner))",
            r"\blead\s+count\b"),
           None, "row count on lead table"),

    # ---------------- opportunities / sales ----------------
    Metric("sales_done", Tool.OPPORTUNITY, "Sales Done",
           (r"sales?\s+done", r"\bsale\s+done\b", r"\bsd\b",
            r"total\s+sales?", r"\bsales?\b", r"\bbookings?\b", r"\brevenue\b"),
           ("__sales__", "non_blank"),
           "sales_order_number_c non-blank, counted on created_date_c (client-locked)"),
    Metric("opportunities", Tool.OPPORTUNITY, "Opportunities",
           (r"opportunit(?:y|ies)", r"\bdeals?\b"),
           None, "row count on opportunity table"),

    # ---------------- events / appointments ----------------
    Metric("meeting_done", Tool.EVENT, "Meeting Done",
           (r"meetings?\s+done", r"completed\s+meetings?", r"meetings?\s+completed",
            r"completed\s+appointments?", r"appointments?\s+completed", r"\bmd\b"),
           ("__event__", "done"),
           "subject_c='Personal Appointment Booked' AND appointment_status_c='completed'"),
    Metric("meeting_booked", Tool.EVENT, "Meeting Booked",
           (r"meetings?\s+booked", r"appointments?\s+booked", r"\bmb\b",
            r"personal\s+appointment\s+booked"),
           ("__event__", "booked"), "subject_c = 'Personal Appointment Booked'"),
    Metric("appointment_scheduled", Tool.EVENT, "Scheduled Appointments",
           (r"scheduled\s+(?:appointments?|meetings?)",
            r"appointments?\s+scheduled", r"meetings?\s+scheduled"),
           ("appointment_status", "Scheduled")),
    Metric("appointment_cancelled", Tool.EVENT, "Cancelled Appointments",
           (r"cancell?ed\s+(?:appointments?|meetings?)",
            r"appointments?\s+cancell?ed", r"meetings?\s+cancell?ed"),
           ("appointment_status", "Cancelled")),
    Metric("appointment_rescheduled", Tool.EVENT, "Rescheduled Appointments",
           (r"re[\s\-]?scheduled\s+(?:appointments?|meetings?)",
            r"appointments?\s+re[\s\-]?scheduled"),
           ("appointment_status", "Rescheduled")),
    Metric("appointment_revisit", Tool.EVENT, "Revisit Appointments",
           (r"re[\s\-]?visits?\b",), ("appointment_status", "Revisit")),
    Metric("events", Tool.EVENT, "Events",
           (r"\bevents?\b", r"\bappointments?\b", r"\bmeetings?\b", r"site\s+visits?"),
           None, "row count on event table"),

    # ---------------- tasks ----------------
    Metric("follow_up_tasks", Tool.TASK, "Follow Up Tasks",
           (r"follow[\s\-]?ups?\b", r"followups?\b"),
           ("subject", "__follow_up_set__"),
           "subject_c IN (Follow Up, Sales Follow Up, Experience Calling Follow Up, "
           "Welcome Calling Follow Up)"),
    # Both word orders: users write "cancelled task" and "task cancelled".
    Metric("open_tasks", Tool.TASK, "Open Tasks",
           (r"open\s+tasks?", r"tasks?\s+open\b"), ("status", "Open")),
    Metric("completed_tasks", Tool.TASK, "Completed Tasks",
           (r"completed\s+tasks?", r"tasks?\s+completed\b"), ("status", "Completed")),
    Metric("cancelled_tasks", Tool.TASK, "Cancelled Tasks",
           (r"cancell?ed\s+tasks?", r"tasks?\s+cancell?ed\b"),
           ("status", "__cancelled_set__"),
           "status_c IN (Cancelled, Canceled, Cancel)"),
    Metric("in_progress_tasks", Tool.TASK, "In Progress Tasks",
           (r"in[\s\-]?progress\s+tasks?", r"tasks?\s+in[\s\-]?progress\b"),
           ("status", "In Progress")),
    Metric("tasks", Tool.TASK, "Tasks",
           (r"\btasks?\b", r"\bto[\s\-]?dos?\b", r"\bactivit(?:y|ies)\b"),
           None, "row count on task table"),

    # ---------------- cases / service requests ----------------
    Metric("satisfied_cases", Tool.CASE, "Satisfied Cases",
           (r"satisfied\s+(?:cases?|service\s+requests?)",), ("feedback", "Satisfied")),
    Metric("complaint_cases", Tool.CASE, "Complaint Cases",
           (r"complaints?\b",), ("service_request_type", "Complaint")),
    # The lookahead keeps "SR target" / "SR resolved" with the targets metric.
    Metric("cases", Tool.CASE, "Cases",
           (r"\bcases?\b", r"service\s+requests?(?!\s+resolution)",
            r"\bsrs?\b(?!\s+(?:targets?|resolved|resolution))",
            r"\btickets?\b", r"\bissues?\b"),
           None, "row count on service request table"),

]

# Grouping phrase -> canonical facet.
GROUPINGS: list[tuple[str, str]] = [
    (r"sub[\s\-]?source\s+(?:sub\s+)?categor(?:y|ies)\s*wise", "subsource"),
    (r"source\s+sub[\s\-]?categor(?:y|ies)\s*wise", "subsource"),
    (r"sub[\s\-]?sources?\s*[\-\s]?wise", "subsource"),
    (r"grouped\s+by\s+sub[\s\-]?source", "subsource"),
    (r"(?:lead\s+)?sources?\s*[\-\s]?wise", "source"),
    (r"grouped\s+by\s+(?:lead\s+)?source", "source"),
    (r"on\s+the\s+basis\s+of\s+sources?", "source"),
    (r"projects?\s*[\-\s]?wise", "project"),
    (r"grouped\s+by\s+project\b", "project"),
    (r"project\s+categor(?:y|ies)\s*wise", "product"),
    (r"products?\s*[\-\s]?wise", "product"),
    (r"grouped\s+by\s+product", "product"),
    (r"on\s+the\s+basis\s+of\s+products?", "product"),
    (r"owners?\s*[\-\s]?wise", "owner"),
    (r"users?\s*[\-\s]?wise", "owner"),
    (r"grouped\s+by\s+owner", "owner"),
    (r"\bby\s+owner\b", "owner"),
    (r"cit(?:y|ies)\s*[\-\s]?wise", "city"),
    (r"in\s+each\s+city", "city"),
    (r"status\s*[\-\s]?wise", "status"),
    (r"\bby\s+status\b", "status"),
    (r"subjects?\s*[\-\s]?wise", "subject"),
    (r"appointment\s+status\s*[\-\s]?wise", "appointment_status"),
    (r"propert(?:y|ies)\s+types?\s*[\-\s]?wise", "property_type"),
    (r"\bby\s+property\s+type\b", "property_type"),
    (r"budget\s+ranges?\s*[\-\s]?wise", "budget_range"),
    (r"service\s+request\s+types?\s*[\-\s]?wise", "service_request_type"),
    (r"(?:as\s+per|by)\s+request\s+type", "service_request_type"),
    (r"sub[\s\-]?categor(?:y|ies)\s*wise", "service_subcategory"),
    (r"follow[\s\-]?up\s+status\s*[\-\s]?wise", "follow_up_status"),
    (r"(?:as\s+per|by)\s+action\s+taken", "action_taken"),
    (r"disqualification\s+reasons?\s*[\-\s]?wise", "disqualification_reason"),
    (r"months?\s*[\-\s]?wise", "month"),
    (r"quarters?\s*[\-\s]?wise", "quarter"),
    (r"years?\s*[\-\s]?wise", "year"),
]


# Ranking / superlative requests. Each yields a direction, a count, and often
# implies the grouping ("top 5 products" is a product breakdown).
RANKING = [
    (r"\btop\s+(\d+)\s+(\w+?)s?\b", "top"),
    (r"\bbottom\s+(\d+)\s+(\w+?)s?\b", "bottom"),
    (r"\btop\s+(\d+)\b", "top"),
    (r"\bbottom\s+(\d+)\b", "bottom"),
]

SUPERLATIVE = [
    (r"\b(?:most|highest|maximum|max|best)\b", "top"),
    (r"\b(?:least|lowest|minimum|min|worst|fewest)\b", "bottom"),
]

# Nouns that name a breakdown dimension when used bare, e.g. "top 5 products",
# "which source has generated the most leads".
FACET_NOUNS = {
    "product": "product", "products": "product",
    "project": "project", "projects": "project",
    "source": "source", "sources": "source",
    "subsource": "subsource", "sub-source": "subsource",
    "owner": "owner", "owners": "owner",
    "user": "owner", "users": "owner",
    "city": "city", "cities": "city",
    "status": "status", "statuses": "status",
    "subject": "subject", "subjects": "subject",
}


@dataclass
class Ranking:
    direction: str          # "top" | "bottom"
    count: int | None       # None means unbounded ordering
    facet: str | None       # dimension being ranked, when stated


def find_ranking(text: str) -> Ranking | None:
    """Detect 'top 5 products', 'which source has the most leads', etc."""
    t = text.lower()

    for pat, direction in RANKING:
        m = re.search(pat, t)
        if m:
            groups = m.groups()
            n = int(groups[0])
            facet = FACET_NOUNS.get(groups[1]) if len(groups) > 1 else None
            return Ranking(direction, n, facet)

    for pat, direction in SUPERLATIVE:
        if re.search(pat, t):
            # "which source has generated the most leads" -> rank sources, take 1
            m = re.search(r"\bwhich\s+(\w+?)s?\b", t)
            facet = FACET_NOUNS.get(m.group(1)) if m else None
            return Ranking(direction, 1 if m else None, facet)
    return None


@dataclass
class MetricMatch:
    metric: Metric
    span: tuple[int, int]
    matched_text: str


def find_metrics(text: str) -> list[MetricMatch]:
    """All metrics named in the query, most-specific first, without overlap.

    Overlap suppression is what stops "not interested leads" from also matching
    the generic "leads" pattern, and "meeting done" from also matching "events".
    """
    t = text.lower()
    out: list[MetricMatch] = []
    claimed: list[tuple[int, int]] = []

    for metric in METRICS:
        for pat in metric.patterns:
            for m in re.finditer(pat, t):
                s, e = m.span()
                if any(s < ce and e > cs for cs, ce in claimed):
                    continue
                out.append(MetricMatch(metric, (s, e), m.group(0)))
                claimed.append((s, e))
                break
            else:
                continue
            break
    out.sort(key=lambda x: x.span[0])
    return out


def find_groupings(text: str) -> list[str]:
    t = text.lower()
    found: list[str] = []
    claimed: list[tuple[int, int]] = []
    for pat, facet in GROUPINGS:
        for m in re.finditer(pat, t):
            s, e = m.span()
            if any(s < ce and e > cs for cs, ce in claimed):
                continue
            claimed.append((s, e))
            if facet not in found:
                found.append(facet)
            break
    return found


# Which funnel tool serves a funnel question. Evaluated in order; first match
# wins. Mirrors the priority tree in the original behavior.md, corrected so a
# product name never routes to the project funnel.
def resolve_funnel_tool(text: str, groupings: list[str],
                        entities: dict[str, list[str]]) -> tuple[Tool | None, str | None]:
    """Return (tool, clarification). A clarification means the query is ambiguous."""
    t = text.lower()
    ents = entities or {}

    # Hyphen-tolerant: users write "sales-user-wise" and "lead-user-wise".
    if re.search(r"\bsales?[\s\-]+(?:user|team|person|people|wise)s?\b"
                 r"|\buser[\s\-]+wise\s+sales?\b", t):
        return Tool.SALES_USER_FUNNEL, None
    if re.search(r"\bleads?[\s\-]+(?:user|team)s?\b|\buser[\s\-]+wise\s+leads?\b", t):
        return Tool.LEAD_USER_FUNNEL, None

    # "user funnel" with no sales/lead qualifier is genuinely ambiguous.
    if re.search(r"\buser\b", t) or "owner" in groupings:
        return None, ("Did you want the sales user funnel or the lead user funnel? "
                      "Sales user shows conversion per salesperson; lead user shows "
                      "it per lead owner.")

    # Bare dimension nouns. "source wise funnel" is caught through groupings,
    # but "source funnel" / "funnel by source" carries no "wise" and names no
    # entity, and fell through to the overall lead funnel (measured failure,
    # 27 Aug 2026). Sub-source before source, since one contains the other.
    _joins = r"(?:by|of|per|across|on|for\s+each|for\s+every|for\s+all)"
    if re.search(rf"\bsub[\s\-]?sources?\s+funnels?\b"
                 rf"|\bfunnels?\s+{_joins}\s+sub[\s\-]?sources?\b", t):
        return Tool.SUBSOURCE_FUNNEL, None
    if re.search(rf"\bsources?\s+funnels?\b|\bfunnels?\s+{_joins}\s+sources?\b", t):
        return Tool.SOURCE_FUNNEL, None
    if re.search(rf"\bproducts?\s+funnels?\b|\bfunnels?\s+{_joins}\s+products?\b", t):
        return Tool.PRODUCT_FUNNEL, None
    if re.search(rf"\bprojects?\s+funnels?\b|\bfunnels?\s+{_joins}\s+projects?\b", t):
        return Tool.PROJECT_FUNNEL, None

    # An explicit breakdown request ("source-wise funnel for Eden") outranks a
    # named entity: the grouping names the dimension the user wants rows BY,
    # the entity is a filter within it. Checking entities first sent
    # "source-wise funnel for Eden" to the product funnel (batch 27 Aug 2026).
    if "subsource" in groupings:
        return Tool.SUBSOURCE_FUNNEL, None
    if "product" in groupings:
        return Tool.PRODUCT_FUNNEL, None
    if "project" in groupings:
        return Tool.PROJECT_FUNNEL, None
    if "source" in groupings:
        return Tool.SOURCE_FUNNEL, None

    if ents.get("subsource"):
        return Tool.SUBSOURCE_FUNNEL, None
    if ents.get("product"):
        return Tool.PRODUCT_FUNNEL, None
    if ents.get("project"):
        return Tool.PROJECT_FUNNEL, None
    if ents.get("source"):
        return Tool.SOURCE_FUNNEL, None

    return Tool.LEAD_FUNNEL, None


# Approximate distinct values per breakdown, used to predict output size before
# running a funnel. A funnel row is 8 metrics plus 10 ratios, so a scoped funnel
# repeated across many periods becomes unreadable long before it becomes slow.
FACET_CARDINALITY = {
    "project": 3, "product": 66, "source": 20, "subsource": 81,
    "owner": 127, "city": 23, "status": 9,
}

FUNNEL_SCOPE_FACET = {
    Tool.PROJECT_FUNNEL: "project",
    Tool.PRODUCT_FUNNEL: "product",
    Tool.SOURCE_FUNNEL: "source",
    Tool.SUBSOURCE_FUNNEL: "subsource",
    Tool.LEAD_USER_FUNNEL: "owner",
    Tool.SALES_USER_FUNNEL: "owner",
    Tool.LEAD_FUNNEL: None,
}

# Above this many funnel rows the answer stops being readable in chat.
FUNNEL_ROW_LIMIT = 40


def estimate_funnel_rows(tool: Tool, period_count: int,
                         entities: dict[str, list[str]] | None) -> int:
    """Rows a funnel request would produce: scope values x periods."""
    facet = FUNNEL_SCOPE_FACET.get(tool)
    if facet is None:
        breadth = 1
    else:
        named = (entities or {}).get(facet) or []
        breadth = len(named) if named else FACET_CARDINALITY.get(facet, 20)
    return breadth * max(period_count, 1)


def route(metrics: list[MetricMatch]) -> tuple[Tool | None, list[Tool]]:
    """Return (primary tool, all tools needed).

    Multiple tools means the query spans agents and must be decomposed --
    e.g. "opportunity vs leads", "sales and opportunity".
    """
    tools: list[Tool] = []
    for mm in metrics:
        if mm.metric.tool not in tools:
            tools.append(mm.metric.tool)
    return (tools[0] if tools else None), tools
