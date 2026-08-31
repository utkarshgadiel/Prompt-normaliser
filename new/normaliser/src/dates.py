"""
Single date resolver for all CRM tools.

Replaces the twelve independently-written parsers in code/old/, which were
measured to disagree with each other (see grammar/DATE_GRAMMAR.md).

Design rules, each traceable to a measured defect:

1. Resolve ONCE, here. Downstream tools receive resolved dates, never a phrase
   they must re-parse.
2. NEVER return a silent default. An unrecognised expression yields
   Period(kind=UNRESOLVED) so the caller can ask instead of guessing. The old
   services fell back to the current FY without telling anyone.
3. A comparison qualifier combined with an explicit sub-year range is a
   CONFLICT, surfaced as a warning rather than silently resolved one way.

Fiscal year runs 1 April -> 31 March. FY<n> means the year beginning April <n>.
Fiscal quarters follow behavior.md:428 -- Q1 = Apr-Jun, Q2 = Jul-Sep,
Q3 = Oct-Dec, Q4 = Jan-Mar.
"""
from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_NAME = {v: k.capitalize() for k, v in MONTHS.items() if len(k) > 3}
MONTH_NAME.update({1: "January", 2: "February", 3: "March", 4: "April",
                   5: "May", 6: "June", 7: "July", 8: "August",
                   9: "September", 10: "October", 11: "November", 12: "December"})

# Earliest financial year the system holds for the broadest reports. Per-tool
# floors live in intents.DATA_START_FY and are applied after routing.
DEFAULT_START_FY = 2020

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}

_M = "|".join(sorted(MONTHS, key=len, reverse=True))


class Kind(str, Enum):
    RANGE = "range"              # one contiguous window
    MONTH_LIST = "month_list"    # discrete months, each reported separately
    QUARTER_LIST = "quarter_list"
    YEAR_LIST = "year_list"
    UNRESOLVED = "unresolved"


class Comparison(str, Enum):
    NONE = "none"
    MOM = "month_on_month"
    QOQ = "quarter_on_quarter"
    YOY = "year_on_year"


@dataclass
class Span:
    start: date
    end: date
    label: str = ""


@dataclass
class Period:
    kind: Kind
    spans: list[Span] = field(default_factory=list)
    comparison: Comparison = Comparison.NONE
    label: str = ""
    warnings: list[str] = field(default_factory=list)
    source_text: str = ""

    @property
    def resolved(self) -> bool:
        return self.kind is not Kind.UNRESOLVED and bool(self.spans)

    @property
    def start(self) -> date | None:
        return min(s.start for s in self.spans) if self.spans else None

    @property
    def end(self) -> date | None:
        return max(s.end for s in self.spans) if self.spans else None


# --------------------------------------------------------------------------
# fiscal helpers
# --------------------------------------------------------------------------

def fy_of(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_span(fy: int) -> Span:
    return Span(date(fy, 4, 1), date(fy + 1, 3, 31), f"FY{fy}-{str(fy + 1)[2:]}")


def fq_span(fy: int, q: int) -> Span:
    starts = {1: (fy, 4), 2: (fy, 7), 3: (fy, 10), 4: (fy + 1, 1)}
    y, m = starts[q]
    end_y, end_m = (y, m + 2) if m + 2 <= 12 else (y + 1, m - 10)
    return Span(date(y, m, 1), date(end_y, end_m, monthrange(end_y, end_m)[1]),
                f"Q{q} FY{fy}-{str(fy + 1)[2:]}")


def fq_of(d: date) -> int:
    return {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2,
            10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]


def month_span(y: int, m: int) -> Span:
    return Span(date(y, m, 1), date(y, m, monthrange(y, m)[1]), f"{MONTH_NAME[m]} {y}")


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = (y * 12 + (m - 1)) + delta
    return idx // 12, idx % 12 + 1


# --------------------------------------------------------------------------
# normalisation of the raw text
# --------------------------------------------------------------------------

def _fy_pair(m: re.Match) -> str:
    """'fy 2019-20' -> 'fy 2019' when the suffix really is the next year."""
    a, b = m.group(1), m.group(2)
    nxt = int(a) + 1
    if b == str(nxt) or (len(b) == 2 and int(b) == nxt % 100):
        return f"fy {a}"
    return m.group(0)


def _bare_pair(m: re.Match) -> str:
    """'2019-20' -> '2019' when the suffix is the next year (an FY label)."""
    a, b = m.group(1), m.group(2)
    if int(b) == (int(a) + 1) % 100:
        return a
    return m.group(0)


def _clean(text: str) -> str:
    t = text.lower().strip()
    t = t.replace("–", "-").replace("—", "-").replace("’", "'")
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)          # 1st -> 1
    for w, n in WORD_NUM.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    t = re.sub(r"\bqtrs?\b", "quarter", t)
    t = re.sub(r"\byrs?\b", "year", t)
    t = re.sub(r"\bmnths?\b", "month", t)
    t = re.sub(r"\bfinancial\s+year\b|\bfiscal\s+year\b", "fy", t)
    # FY labels written as a year pair -- "FY2019-20", "fy 2019/2020",
    # "fy 19-20" -- collapse to the single anchor year every parser
    # understands. Only when the suffix really is the following year;
    # anything else is left alone rather than guessed at. Without this,
    # "FY2019-20 to FY2026-27" matched the single-FY branch as "fy 2019"
    # and the rest of the range was silently discarded (the exact failure
    # seen in production on 26 Aug 2026).
    t = re.sub(r"\bfy\s*(\d{4})\s*[-/]\s*(\d{2}|\d{4})\b", _fy_pair, t)
    t = re.sub(r"\bfy\s*(\d{2})\s*[-/]\s*(\d{2})\b",
               lambda m: f"fy {m.group(1)}"
               if int(m.group(2)) == (int(m.group(1)) + 1) % 100 else m.group(0), t)
    # Bare "2019-20" outside a dd-mm-yyyy context is also an FY label.
    t = re.sub(r"(?<![\d/-])\b(20\d{2})\s*-\s*(\d{2})\b(?![-/]?\d)", _bare_pair, t)
    t = re.sub(r"\s+", " ", t)
    return t


# Grain-series phrasings. The backends' own is_mom / is_qoq / is_yoy checks
# treat "yearly", "year wise", "monthly", "quarter wise" etc. exactly like the
# on/over forms (lead_report.py:2845, event_report.py:1795, task_report.py:2017),
# so the normaliser must read them the same way -- otherwise "yearly leads"
# loses its grain here and resolves to a single default-FY total. The 'half-' /
# 'bi-' lookbehinds stop "half-yearly" and "bi-monthly" being read as series.
_CMP_PATTERNS = [
    (Comparison.MOM, r"\bmom\b|\bmonth[\s\-]?on[\s\-]?month\b|\bmonth[\s\-]?over[\s\-]?month\b"
                     r"|(?<!bi\s)(?<!bi-)\bmonthly\b|\bmonths?[\s\-]?wise\b|\bmonthwise\b"
                     r"|\bmonth\s+by\s+month\b|\bper\s+month\b|\bby\s+month\b"
                     r"|\beach\s+month\b|\bevery\s+month\b"),
    (Comparison.QOQ, r"\bqoq\b|\bquarter[\s\-]?on[\s\-]?quarter\b|\bquarter[\s\-]?over[\s\-]?quarter\b"
                     r"|\bquarterly\b|\bquarters?[\s\-]?wise\b|\bquarterwise\b"
                     r"|\bquarter\s+by\s+quarter\b|\bper\s+quarter\b|\bby\s+quarter\b"
                     r"|\beach\s+quarter\b|\bevery\s+quarter\b"),
    (Comparison.YOY, r"\byoy\b|\byear[\s\-]?on[\s\-]?year\b|\byear[\s\-]?over[\s\-]?year\b"
                     r"|(?<!half\s)(?<!half-)\byearly\b|\byears?[\s\-]?wise\b|\byearwise\b"
                     r"|\byear\s+by\s+year\b|\bper\s+year\b|\bby\s+year\b"
                     r"|\beach\s+year\b|\bevery\s+year\b|\ball\s+years\b"
                     r"|\bannual\s+trend\b|\bannually\b|\bcompare\s+years\b"),
]


def detect_comparisons(text: str) -> list[Comparison]:
    """Every comparison named in the query, in the order the user wrote them.

    A question can legitimately ask for more than one -- "year on year sales and
    also the month on month sales" is two requests. Returning only the first
    silently drops half the question.
    """
    t = _clean(text)
    hits: list[tuple[int, Comparison]] = []
    for cmp_, pat in _CMP_PATTERNS:
        m = re.search(pat, t)
        if m:
            hits.append((m.start(), cmp_))
    return [c for _, c in sorted(hits)]


def detect_comparison(text: str) -> Comparison:
    """First comparison only. Kept for callers that want a single value."""
    found = detect_comparisons(text)
    return found[0] if found else Comparison.NONE


# Self-contained period phrases. A query naming two or more of these is asking
# for two or more result sets ("last fy and current fy", "this quarter vs last
# quarter"), not one range.
_PERIOD_PHRASES = [
    r"\b(?:last|previous|prev)\s+(?:fy|financial\s+year|fiscal\s+year)\b",
    r"\b(?:this|current)\s+(?:fy|financial\s+year|fiscal\s+year)\b",
    r"\b(?:last|previous|prev)\s+year\b",
    r"\b(?:this|current)\s+year\b",
    r"\b(?:last|previous|prev)\s+quarter\b",
    r"\b(?:this|current)\s+quarter\b",
    r"\b(?:last|previous|prev)\s+month\b",
    r"\b(?:this|current)\s+month\b",
    # Weeks and days complete the ladder. Without them "this week vs last
    # week" named no recognised phrase, fell past every branch, and silently
    # became the current financial year (found 31 Aug 2026).
    r"\b(?:last|previous|prev)\s+week\b",
    r"\b(?:this|current)\s+week\b",
    r"\byesterday\b",
    r"\btoday\b",
    r"\bfy\s*\d{4}\b",
    r"\bfy\s*\d{2}\b",
]

# Words that join the two ends of ONE range. Kept in one place so every
# branch accepts the same vocabulary: "April 2025 through June 2025" was
# falling past the month-range branch and being read as the discrete list
# [April, June], silently dropping May (found in production 27 Aug 2026).
# "and" is deliberately absent -- it only joins a range after "between", and
# treating it as a connector everywhere would break "last fy and current fy".
_TO = r"(?:to|till|thru|through|until|untill|upto|up\s+to|-)"

# If any of these appear, the query is one contiguous range and must not be
# split even though it contains "and".
_RANGE_MARKERS = re.compile(
    rf"\bbetween\b|\bfrom\b|\d{{1,2}}\s+(?:{_M})\b"
    rf"|\b(?:{_M})\s+\d{{4}}\s*{_TO}\s*",
)


def detect_multi_periods(text: str, today: date | None = None) -> list[Period]:
    """Periods for a query naming two or more distinct period phrases.

    Returns [] when the query names fewer than two, or is a single explicit
    range. "last fy and current fy separately" is two result sets;
    "between 1 April 2026 and 30 June 2026" is one.
    """
    today = today or date.today()
    t = _clean(text)
    if _RANGE_MARKERS.search(t):
        return []

    hits: list[tuple[int, int, str]] = []
    for pat in _PERIOD_PHRASES:
        for m in re.finditer(pat, t):
            s, e = m.span()
            if any(s < he and e > hs for hs, he, _ in hits):
                continue
            hits.append((s, e, m.group(0)))

    if len(hits) < 2:
        return []

    # Only split when the phrases are actually joined as alternatives.
    joiners = re.compile(r"\b(and|vs|versus|or)\b|,|&")
    hits.sort()
    for (_, e1, _), (s2, _, _) in zip(hits, hits[1:]):
        if not joiners.search(t[e1:s2]):
            return []

    out: list[Period] = []
    for _, _, phrase in hits:
        p = resolve(phrase, today)
        if p.resolved:
            p.label = p.label or phrase
            out.append(p)
    return out if len(out) >= 2 else []


def _year_for_month(m: int, anchor_year: int) -> int:
    """Within a fiscal year labelled `anchor_year`, Jan-Mar belong to year+1."""
    return anchor_year if m >= 4 else anchor_year + 1


# --------------------------------------------------------------------------
# resolver
# --------------------------------------------------------------------------

def resolve(text: str, today: date | None = None,
            force_comparison: Comparison | None = None) -> Period:
    """Resolve the period in `text`.

    `force_comparison` pins which comparison applies, so a query naming several
    ("year on year ... and also month on month") can be resolved once per
    comparison instead of losing all but the first.
    """
    today = today or date.today()
    t = _clean(text)
    cmp_ = force_comparison if force_comparison is not None else detect_comparison(t)
    cur_fy = fy_of(today)

    def done(p: Period) -> Period:
        p.comparison = cmp_
        p.source_text = text
        # A comparison qualifier plus an explicit sub-year window is
        # contradictory. The old backends silently discarded the window.
        if cmp_ is not Comparison.NONE and p.kind is Kind.RANGE and p.spans:
            days = (p.spans[0].end - p.spans[0].start).days
            if days < 350:
                p.warnings.append(
                    f"Query combines '{cmp_.value}' with an explicit "
                    f"{days + 1}-day window. These conflict: the comparison implies a "
                    f"multi-period series, the window implies one period. The window "
                    f"is honoured; the grain is kept only where a verified form can "
                    f"express both (whole months within one calendar year)."
                )
        return p

    # -- explicit range: "1 April 2026 to 30 June 2026" / "between X and Y" --
    m = re.search(
        rf"(?:between\s+)?(\d{{1,2}})\s+({_M})\.?\s*(\d{{4}})?\s*(?:{_TO}|and)\s*"
        rf"(\d{{1,2}})\s+({_M})\.?\s*(\d{{4}})", t)
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        y2 = int(y2)
        y1 = int(y1) if y1 else (y2 if MONTHS[m1] <= MONTHS[m2] else y2 - 1)
        return done(Period(Kind.RANGE, [Span(
            date(y1, MONTHS[m1], int(d1)), date(y2, MONTHS[m2], int(d2)))]))

    # -- numeric range: "01-04-2026 to 30-06-2026" --
    m = re.search(rf"(?:between\s+)?(\d{{1,2}})[-/](\d{{1,2}})[-/](\d{{4}})\s*(?:{_TO}|and)\s*"
                  r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", t)
    if m:
        a, b, c, d, e, f = (int(x) for x in m.groups())
        return done(Period(Kind.RANGE, [Span(date(c, b, a), date(f, e, d))]))

    # -- "<month> <year> to <month> <year>", and the year-less "April to June".
    # The trailing year is optional: "Q2 2026 (April to June)" must resolve to
    # the whole span, not to the two named months with May silently dropped.
    m = re.search(rf"({_M})\.?\s*(\d{{4}})?\s*{_TO}\s*({_M})\.?\s*(\d{{4}})?", t)
    if m and "between" not in t[:m.start()][-12:]:
        m1, y1, m2, y2 = m.groups()
        if not y2:
            years_in_q = re.findall(r"\b(\d{4})\b", t)
            y2 = years_in_q[-1] if years_in_q else str(_year_for_month(MONTHS[m2], cur_fy))
        y2 = int(y2)
        y1 = int(y1) if y1 else (y2 if MONTHS[m1] <= MONTHS[m2] else y2 - 1)
        e_y, e_m = y2, MONTHS[m2]
        return done(Period(Kind.RANGE, [Span(
            date(y1, MONTHS[m1], 1), date(e_y, e_m, monthrange(e_y, e_m)[1]))]))

    # -- "from <year> till now" / "since 2021" --
    m = re.search(r"\b(?:from|since|starting)\s+(?:fy\s*)?(20\d{2})\b", t)
    if m and re.search(r"\btill\s+(?:date|now|today)\b|\bto\s+(?:date|now|today)\b"
                       r"|\bonwards?\b|\buntil\s+now\b|\bsince\b", t):
        start_fy = int(m.group(1))
        return done(Period(Kind.RANGE, [Span(date(start_fy, 4, 1), today,
                                             f"FY{start_fy} to date")]))

    # -- "till date" / "from X till date" --
    if re.search(r"\btill\s+date\b|\bto\s+date\b|\btilldate\b", t):
        m = re.search(rf"(?:from\s+)?(?:(\d{{1,2}})\s+)?({_M})\.?\s*(\d{{4}})?", t)
        if m:
            d1, m1, y1 = m.groups()
            mm = MONTHS[m1]
            yy = int(y1) if y1 else _year_for_month(mm, cur_fy)
            start = date(yy, mm, int(d1) if d1 else 1)
            if start <= today:
                return done(Period(Kind.RANGE, [Span(start, today, "till date")]))

    # -- discrete month list: "April, May and June 2026" --
    found = re.findall(rf"\b({_M})\b", t)
    if len(found) >= 2:
        years = re.findall(r"\b(\d{4})\b", t)
        anchor = int(years[-1]) if years else cur_fy
        seen, ordered = set(), []
        for name in found:
            n = MONTHS[name]
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        # Per-month years, when the user wrote "April 2026, May 2026, June 2026"
        per = {MONTHS[name]: int(yr)
               for name, yr in re.findall(rf"\b({_M})\.?\s+(\d{{4}})\b", t)}
        spans = []
        for n in ordered:
            y = per.get(n) or (int(years[0]) if len(years) == 1
                               else _year_for_month(n, anchor))
            spans.append(month_span(y, n))
        return done(Period(Kind.MONTH_LIST, spans, label="month list"))

    # -- single month: "June 2026" / "in May" --
    m = re.search(rf"\b({_M})\.?\s*(?:fy\s*)?(\d{{4}})?\b", t)
    if m and not re.search(r"\b(last|this|current|previous|next)\b\s*$", t[:m.start()]):
        name, yr = m.groups()
        n = MONTHS[name]
        y = int(yr) if yr else _year_for_month(n, cur_fy)
        # Guard: a bare month with no year, in a query that also names a
        # relative period, is likelier part of that phrase -- fall through.
        if yr or not re.search(r"\b(last|this|current)\s+(month|quarter|year|fy)\b", t):
            return done(Period(Kind.MONTH_LIST, [month_span(y, n)]))

    # -- quarters: "Q1 and Q2 2023", "Q2 2026", "qtr 1 in last fy" --
    qs = re.findall(r"\bq(?:uarter)?\s*([1-4])\b", t)
    if qs:
        yr = re.search(r"\b(?:fy\s*)?(\d{4})\b", t)
        if yr:
            fy = int(yr.group(1))
        elif re.search(r"\blast\s+(?:fy|year)\b", t):
            fy = cur_fy - 1
        else:
            fy = cur_fy
        spans = [fq_span(fy, int(q)) for q in dict.fromkeys(qs)]
        kind = Kind.QUARTER_LIST if len(spans) > 1 else Kind.RANGE
        return done(Period(kind, spans))

    # -- last N <unit> --
    m = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month|quarter|year)s?\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        # "Last N <unit>" always means N COMPLETED units ending before the
        # current one. The current, part-finished unit is excluded: on
        # 31 August, "last 30 days" is 1-30 August, not 2-31 August. This is
        # the client's stated semantics and it matches the backends'
        # own arithmetic (lead_report.py:2961 and :2975). The month, quarter
        # and year branches below already worked this way; days and weeks
        # were counting back from today and silently included it, so every
        # such window was shifted one day forward (fixed 31 Aug 2026).
        if unit == "day":
            end = today - timedelta(days=1)
            return done(Period(Kind.RANGE, [Span(end - timedelta(days=n - 1), end,
                                                 f"last {n} days")]))
        if unit == "week":
            end = today - timedelta(days=today.weekday() + 1)   # last Sunday
            return done(Period(Kind.RANGE, [
                Span(end - timedelta(weeks=n - 1, days=6), end, f"last {n} weeks")]))
        if unit == "month":
            spans = []
            for i in range(n, 0, -1):
                y, mm = _shift_month(today.year, today.month, -i)
                spans.append(month_span(y, mm))
            return done(Period(Kind.MONTH_LIST, spans, label=f"last {n} months"))
        if unit == "quarter":
            spans, fy, q = [], cur_fy, fq_of(today)
            for _ in range(n):
                q -= 1
                if q == 0:
                    q, fy = 4, fy - 1
                spans.append(fq_span(fy, q))
            return done(Period(Kind.QUARTER_LIST, list(reversed(spans)),
                               label=f"last {n} quarters"))
        spans = [fy_span(cur_fy - i) for i in range(n, 0, -1)]
        return done(Period(Kind.YEAR_LIST, spans, label=f"last {n} years"))

    # -- financial-year range: "fy 2019 to fy 2026" (also what
    # "FY2019-20 to FY2026-27" becomes after _clean). Must be checked BEFORE
    # the single-FY branch, which would otherwise match "fy 2019" alone and
    # silently discard the rest of the range.
    m = re.search(rf"\b(?:from\s+|between\s+)?fy\s*(\d{{4}})\s*"
                  rf"(?:{_TO}|and)\s*(?:fy\s*)?(\d{{4}})\b", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b:
            return done(Period(Kind.YEAR_LIST, [fy_span(y) for y in range(a, b + 1)],
                               label=f"FY{a} to FY{b}"))

    # -- explicit FY --
    m = re.search(r"\bfy\s*(\d{4})\b|\bfy\s*(\d{2})\b", t)
    if m:
        raw = m.group(1) or m.group(2)
        fy = int(raw) if len(raw) == 4 else 2000 + int(raw)
        return done(Period(Kind.RANGE, [fy_span(fy)]))

    # -- relative named periods --
    if re.search(r"\b(current|this)\s+fy\b", t) or re.fullmatch(r"for current fy", t):
        return done(Period(Kind.RANGE, [fy_span(cur_fy)]))
    if re.search(r"\b(last|previous)\s+fy\b", t):
        return done(Period(Kind.RANGE, [fy_span(cur_fy - 1)]))
    if re.search(r"\btoday\b", t):
        return done(Period(Kind.RANGE, [Span(today, today, "today")]))
    if re.search(r"\byesterday\b", t):
        y = today - timedelta(days=1)
        return done(Period(Kind.RANGE, [Span(y, y, "yesterday")]))
    # Weeks, matching the backends' own definitions (lead_report.py:2944 and
    # :2969): this week runs Monday to today, last week is the previous whole
    # Monday-to-Sunday. Missing entirely before 31 Aug 2026, so "this week"
    # fell through to the current-FY default without a word of warning.
    if re.search(r"\b(this|current)\s+week\b", t):
        return done(Period(Kind.RANGE, [Span(
            today - timedelta(days=today.weekday()), today, "this week")]))
    if re.search(r"\b(last|previous|prev)\s+week\b", t):
        end = today - timedelta(days=today.weekday() + 1)
        return done(Period(Kind.RANGE, [Span(end - timedelta(days=6), end,
                                             "last week")]))
    if re.search(r"\bthis\s+month\b", t):
        return done(Period(Kind.MONTH_LIST, [month_span(today.year, today.month)]))
    if re.search(r"\blast\s+month\b", t):
        y, mm = _shift_month(today.year, today.month, -1)
        return done(Period(Kind.MONTH_LIST, [month_span(y, mm)]))
    if re.search(r"\bthis\s+quarter\b", t):
        return done(Period(Kind.RANGE, [fq_span(cur_fy, fq_of(today))]))
    if re.search(r"\blast\s+quarter\b", t):
        q, fy = fq_of(today) - 1, cur_fy
        if q == 0:
            q, fy = 4, fy - 1
        return done(Period(Kind.RANGE, [fq_span(fy, q)]))
    if re.search(r"\b(this|current)\s+year\b", t):
        return done(Period(Kind.RANGE, [fy_span(cur_fy)]))
    if re.search(r"\b(last|previous|prev)\s+year\b", t):
        return done(Period(Kind.RANGE, [fy_span(cur_fy - 1)]))

    # -- year range "2022 to 2026" --
    m = re.search(rf"\b(\d{{4}})\s*{_TO}\s*(\d{{4}})\b", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return done(Period(Kind.YEAR_LIST, [fy_span(y) for y in range(a, b + 1)]))

    # -- bare year --
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        return done(Period(Kind.RANGE, [fy_span(int(m.group(1)))]))

    # -- a comparison qualifier with no stated period implies its own series --
    # "year on year total cases" is a complete request: the qualifier supplies
    # the period. This mirrors what the backends do, but declares the span
    # instead of leaving it to a per-service hardcoded floor (which differs:
    # FY2020 in lead/task, FY2018 in opportunity -- see DATE_GRAMMAR.md s3).
    if cmp_ is Comparison.YOY:
        # Year on year means every year the system holds, from the first year of
        # data to the current one. The per-tool floor is applied later by
        # clamp_to_coverage, which trims this to that report's real start.
        p = Period(Kind.YEAR_LIST,
                   [fy_span(y) for y in range(DEFAULT_START_FY, cur_fy + 1)],
                   label="all years held (implied by year-on-year)")
        return done(p)
    if cmp_ is Comparison.QOQ:
        p = Period(Kind.QUARTER_LIST, [fq_span(cur_fy, q) for q in (1, 2, 3, 4)],
                   label=f"FY{cur_fy} quarters (implied by quarter-on-quarter)")
        p.warnings.append(
            f"No period given; 'quarter on quarter' defaulted to FY{cur_fy} quarters. "
            f"State this in the response.")
        return done(p)
    if cmp_ is Comparison.MOM:
        spans = [month_span(*_shift_month(cur_fy, 4, i)) for i in range(12)]
        p = Period(Kind.MONTH_LIST, spans,
                   label=f"FY{cur_fy} months (implied by month-on-month)")
        p.warnings.append(
            f"No period given; 'month on month' defaulted to FY{cur_fy} months. "
            f"State this in the response.")
        return done(p)

    # -- nothing recognised --
    # behavior.md:433 documents a current-FY default. Honour it, but DECLARE it:
    # the failure mode being avoided is a silent default the user never sees.
    p = Period(Kind.RANGE, [fy_span(cur_fy)], label=f"FY{cur_fy} (default)")
    p.warnings.append(
        f"No date expression found; defaulted to the current financial year "
        f"(1 April {cur_fy} - 31 March {cur_fy + 1}) per behavior.md:433. "
        f"This MUST be stated in the response.")
    return done(p)
