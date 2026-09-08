"""
Tests for the deterministic funnel table renderer.

These pin the behaviour the master agent kept getting wrong by hand: dropping
the ratios table entirely, showing all nine ratios instead of five, summing the
Total row, and putting a Total row under a single row of data.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from funnel_format import RATIO_COLUMNS, indian_group, render  # noqa: E402

# The real response behind "show me lead funnel from last FY" on 8 Sep 2026.
LEAD_FUNNEL = {
    "analysis_type": "single_period",
    "filter": "2025-04-01 to 2026-03-31",
    "status": "success",
    "lead_funnel": {
        "Junk %": "31.2%", "Junk Leads": 11514, "MB:MD": 2.02, "MB:SD": 4.09,
        "MD:SD": 2.02, "Meeting Booked": 6391, "Meeting Done": 3158,
        "SOL Leads (Interested)": 7820, "SOL:MB": 1.22, "SOL:SD": 5.01,
        "Sales Done": 1561, "TL:SD": 23.64, "TL:VL": 1.45, "Total Leads": 36904,
        "VL:SD": 16.27, "VL:SOL": 3.25, "Valid Leads": 25390,
    },
    "totals": {
        "Junk Leads": 11514, "Meeting Booked": 6391, "Meeting Done": 3158,
        "SOL Leads (Interested)": 7820, "Sales Done": 1561,
        "Total Leads": 36904, "Valid Leads": 25390,
    },
}


def _has_total_row(table: str) -> bool:
    """True when a BODY row's first cell is Total.

    Written as a helper because the obvious substring check matches the
    "Total Leads (TL)" column header and passes on tables that have no Total
    row at all.
    """
    for line in table.splitlines()[2:]:
        if line.strip("|").split("|")[0].strip() == "Total":
            return True
    return False


def test_both_tables_are_always_rendered():
    """The failure this module exists to prevent: a funnel with one table.

    The same question produced both tables at 2:33pm and only the metrics
    table at 3:14pm on 8 Sep 2026, from an identical response.
    """
    out = render(LEAD_FUNNEL, heading="FY2025-26")
    assert out["ok"] and out["row_count"] == 1
    assert "Funnel Metrics" in out["markdown"]
    assert "Funnel Conversion Ratios" in out["markdown"]
    assert out["metrics_table"] and out["ratios_table"]


def test_ratios_table_has_exactly_five_columns():
    """TL:SD, VL:SD, SOL:SD and MB:SD skip stages and are never displayed."""
    ratios = render(LEAD_FUNNEL)["ratios_table"]
    header = ratios.splitlines()[0]
    assert [c.strip() for c in header.strip("|").split("|")] == RATIO_COLUMNS
    for skipped in ("TL:SD", "VL:SD", "SOL:SD", "MB:SD"):
        assert skipped not in ratios, skipped


def test_counts_and_ratios_land_in_the_right_table():
    out = render(LEAD_FUNNEL)
    assert "36,904" in out["metrics_table"]      # Total Leads, grouped
    assert "3.25" in out["ratios_table"]         # VL:SOL
    assert "3.25" not in out["metrics_table"]
    assert "36,904" not in out["ratios_table"]


def test_single_row_gets_no_serial_and_no_total_row():
    """A Total row under one row is a line of em dashes that sums nothing."""
    out = render(LEAD_FUNNEL)
    assert "S.No" not in out["metrics_table"]
    # a Total ROW, not the "Total Leads (TL)" header: check the first cell
    assert not _has_total_row(out["metrics_table"])
    # the ratios table never carries a Total row at any row count
    assert not _has_total_row(out["ratios_table"])


def test_total_row_is_copied_not_summed():
    """The backend's total wins even when it disagrees with the row sum.

    A lead counted under two sub-sources appears in two rows but is still one
    lead, so a breakdown that can double-count legitimately over-sums. Here the
    rows sum to 274,306 and the backend says 4,830; 4,830 is what shows.
    """
    payload = {
        "status": "success",
        "product_wise_metrics": {
            "EDEN":    {"Total Leads": 272488, "Sales Done": 1561, "TL:VL": 1.04},
            "VERIDIA": {"Total Leads": 1818, "Sales Done": 30, "TL:VL": 1.12},
        },
        "totals": {"Total Leads": 4830, "Sales Done": 1591},
    }
    metrics = render(payload)["metrics_table"]
    total_line = [l for l in metrics.splitlines() if l.startswith("| Total")][0]
    assert "4,830" in total_line
    assert "2,74,306" not in total_line and "274,306" not in total_line


def test_breakdown_gets_serial_scope_and_total():
    payload = {
        "status": "success",
        "product_wise_metrics": {
            "EDEN":    {"Total Leads": 100, "TL:VL": 1.04},
            "VERIDIA": {"Total Leads": 200, "TL:VL": 1.12},
        },
        "totals": {"Total Leads": 300},
    }
    out = render(payload)
    assert out["row_count"] == 2 and out["scope_column"] == "Product"
    assert "S.No" in out["metrics_table"] and "Product" in out["metrics_table"]
    assert _has_total_row(out["metrics_table"])
    # the ratios table keeps the scope column but never gains S.No or a Total
    assert "Product" in out["ratios_table"]
    assert "S.No" not in out["ratios_table"]
    assert not _has_total_row(out["ratios_table"])


def test_ratios_keep_two_decimals():
    """A ratio of exactly 2.0 prints as 2.00, not as a bare 2."""
    payload = {"f": {"Total Leads": 10, "TL:VL": 2.0, "VL:SOL": 3.5}}
    ratios = render(payload)["ratios_table"]
    assert "2.00" in ratios and "| 2 |" not in ratios


def test_columns_the_tool_never_reports_are_dropped():
    """User funnels have no lead stages; an em dash would imply missing data."""
    payload = {"sales_user_metrics": {
        "A": {"Meeting Booked": 10, "Meeting Done": 5, "Sales Done": 2, "MB:MD": 2.0},
        "B": {"Meeting Booked": 20, "Meeting Done": 8, "Sales Done": 4, "MB:MD": 2.5}}}
    metrics = render(payload)["metrics_table"]
    assert "Total Leads" not in metrics
    assert "Junk" not in metrics
    assert "Meeting Booked (MB)" in metrics


def test_show_narrows_to_one_table_only_when_asked():
    both = render(LEAD_FUNNEL)["markdown"]
    assert "Funnel Metrics" in both and "Funnel Conversion Ratios" in both

    ratios_only = render(LEAD_FUNNEL, show="ratios")["markdown"]
    assert "Funnel Conversion Ratios" in ratios_only
    assert "Funnel Metrics" not in ratios_only

    metrics_only = render(LEAD_FUNNEL, show="metrics")["markdown"]
    assert "Funnel Metrics" in metrics_only
    assert "Funnel Conversion Ratios" not in metrics_only


def test_indian_grouping_is_digit_preserving():
    """Grouping inserts commas and never changes a digit.

    The failure being pinned: 1818 rendered as "1,81,8 0" -- a different
    number, with a space in it, that no reader could detect.
    """
    cases = {1818: "1,818", 4338: "4,338", 22104: "22,104", 272488: "2,72,488",
             504210: "5,04,210", 2750000: "27,50,000", 10000000: "1,00,00,000",
             0: "0", 7: "7", 100: "100", 1000: "1,000", -272488: "-2,72,488"}
    for n, expected in cases.items():
        got = indian_group(n)
        assert got == expected, (n, got, expected)
        assert got.replace(",", "").replace("-", "") == str(abs(n))
        assert " " not in got


def test_unrecognised_payload_reports_rather_than_guessing():
    out = render({"status": "error", "message": "boom"})
    assert out["ok"] is False and out["markdown"] == ""
