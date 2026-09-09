"""Block measured unsafe legacy forms without modifying the reference services.

This is a capability gate, not a second date parser or a guarantee about live
data. Collaborators must still validate actual backend responses.
"""
import re
from datetime import date


def execution_blocker(call, warnings, today):
    if call.tool == "event_report":
        # The unchanged parser's YoY branch expands even explicit year ranges
        # to its served floor through current FY. Only that exact window is safe.
        fy = today.year if today.month >= 4 else today.year - 1
        full_series = (call.start_date == "2020-04-01" and
                       call.end_date == f"{fy + 1}-03-31" and
                       call.comparison == "year_on_year" and
                       re.search(r"\byoy\b", call.canonical_text))
        quarter_months = re.search(r"\bmom\s+q[1-4]\s+fy\s+\d{4}\b", call.canonical_text)
        if not full_series and not quarter_months:
            return ("backend_period_unavailable", "The event service cannot reliably execute this period and breakdown. "
                    "A year-by-year series from FY2020-21 through the current FY is supported.")

    unsafe = ("cannot parse date ranges", "cannot be expressed safely",
              "widened by one day", "one extra day", "breakdown dropped",
              "explicit-range form not verified", "sum the rows for the span total")
    if any(any(marker in warning.lower() for marker in unsafe) for warning in warnings):
        return ("backend_period_unavailable", "The report cannot reliably preserve both this date window and breakdown. "
                "Choose a supported whole month, quarter or financial year; the requested period has not been changed.")

    if call.tool == "case_report":
        if (call.start_date != call.end_date and
                re.search(r"\b\d{1,2} [A-Za-z]+ \d{4} to \d{1,2} [A-Za-z]+ \d{4}\b", call.canonical_text)):
            return ("backend_period_unavailable", "The case service collapses this explicit day range. Choose a whole month, financial year or a supported relative period.")
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        for values in call.filters.values():
            for value in values:
                collisions = [num for token, num in months.items()
                              if token in value.lower() and not re.search(rf"\b{token}\b", value.lower())]
                if collisions:
                    start, end = date.fromisoformat(call.start_date), date.fromisoformat(call.end_date)
                    if start.month != end.month or any(m != start.month for m in collisions):
                        return ("backend_filter_unavailable", "The case service confuses part of this filter value with a month. "
                                "It cannot reliably apply this filter to the requested period.")
    return None
