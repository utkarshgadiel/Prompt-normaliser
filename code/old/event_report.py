from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai import Credentials
from prestodb.auth import BasicAuthentication
import prestodb
import re
import os
from pathlib import Path
import json
import logging
from datetime import datetime, date, timedelta
from calendar import monthrange
from enum import Enum
from dotenv import load_dotenv
from typing import Any, Dict, List, Optional, Tuple, Union

load_dotenv(Path(__file__).with_name(".env.crm_reporting"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# ENVIRONMENT / CONNECTION CONFIG
# ============================================================================
PRESTO_HOST     = os.getenv("PRESTO_HOST")
PRESTO_PORT     = int(os.getenv("PRESTO_PORT", "31351"))
PRESTO_USER     = os.getenv("PRESTO_USERNAME")
PRESTO_PASSWORD = os.getenv("PRESTO_PASSWORD")
PRESTO_CATALOG  = os.getenv("PRESTO_CATALOG", "salesforcereport")
PRESTO_SCHEMA   = os.getenv("PRESTO_EVENT_SCHEMA", "event_sf_report")
PRESTO_TABLE    = os.getenv("TABLE_EVENT", "event_report")

WATSONX_API_KEY    = os.getenv("WATSONX_API_KEY")
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID")
WATSONX_URL        = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

credentials = Credentials(url=WATSONX_URL, api_key=WATSONX_API_KEY)
llm_model = ModelInference(
    model_id=os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct"),
    credentials=credentials,
    project_id=WATSONX_PROJECT_ID,
    params={"max_new_tokens": 500, "temperature": 0.1}
)


# ============================================================================
# QUERY TYPE ENUM
# ============================================================================
class QueryType(str, Enum):
    SINGLE_DATE            = "single_date"
    DATE_RANGE             = "date_range"
    LAST_N_DAYS            = "last_n_days"
    LAST_N_WEEKS           = "last_n_weeks"
    THIS_WEEK              = "this_week"
    LAST_N_MONTHS          = "last_n_months"
    LAST_N_YEARS           = "last_n_years"
    LAST_N_QUARTERS        = "last_n_quarters"
    THIS_MONTH             = "this_month"
    THIS_QUARTER           = "this_quarter"
    THIS_YEAR              = "this_year"
    LAST_MONTH             = "last_month"
    LAST_QUARTER           = "last_quarter"
    LAST_YEAR              = "last_year"
    QUARTER_WISE           = "quarter_wise"
    MONTH_WISE             = "month_wise"
    YEAR_WISE              = "year_wise"
    SPECIFIC_YEAR          = "specific_year"
    SPECIFIC_DATE          = "specific_date"
    CURRENT_FY             = "current_fy"
    MONTH_RANGE            = "month_range"
    MONTH_RANGE_MONTH_WISE = "month_range_month_wise"
    MULTI_DATE_RANGE       = "MULTI_DATE_RANGE"
    MONTH_MULTI_MONTH_WISE = "month_multi_month_wise"
    MULTI_MONTH            = "multi_month"
    STATUS_COMPARISON      = "status_comparison"
    SUBJECT_TIME_COMPARE   = "subject_time_comparison"


class AggregationType(str, Enum):
    COUNT = "count"
    SUM   = "sum"
    AVG   = "avg"
    MIN   = "min"
    MAX   = "max"


# ============================================================================
# API MODELS
# ============================================================================
class EventSQLRequest(BaseModel):
    question:  str  = Field(..., description="Natural language query about events/appointments")
    catalog:   str  = Field(default=PRESTO_CATALOG)
    db_schema: str  = Field(default=PRESTO_SCHEMA)
    table:     str  = Field(default=PRESTO_TABLE)

class DateRange(BaseModel):
    start_date: str
    end_date:   Optional[str] = None
    label:      Optional[str] = None

class EventSQLResponse(BaseModel):
    status:             str
    query_type:         str
    sql:                str
    schema_metadata:    list | None = Field(default=None, alias="schema")
    data:               list | None = None
    execution:          dict | None = None
    date_ranges:        List[DateRange]
    is_valid:           bool
    validation_message: Optional[str]           = None
    metadata:           Optional[Dict[str, Any]] = None
    intent_summary:     Optional[Dict[str, Any]] = None
    totals:             Optional[Dict[str, Any]] = None

    class Config:
        validate_by_name = True


# ============================================================================
# DATE PARSER  — YYYYMMDD integer format, Apr–Mar FY
# ============================================================================
class DateParser:
    MONTH_MAP = {
        "jan": 1,  "january": 1,  "Jan": 1,"January": 1, "February": 2,"Feb": 2, "february": 2,"feb": 2,
        "March":3 , "Mar":3, "mar": 3,  "march": 3,     "apr": 4,  "april": 4,"Apr": 4, "April": 4,
        "may": 5, "May": 5, "jun": 6,   "Jun": 6,    "june": 6, "June": 6,
        "jul": 7,  "july": 7,  "Jul": 7, "July": 7,    "aug": 8,  "august": 8, "Aug": 8, "August": 8,
        "sep": 9,  "sept": 9,  "Sep": 9, "September": 9, "september": 9,
        "oct": 10, "october": 10, "Oct": 10, "October": 10, "nov": 11, "november": 11, "Nov": 11, "November": 11,
        "dec": 12, "december": 12, "Dec": 12, "December": 12,
    }

    WORD_TO_NUM = {
        "one": 1,  "two": 2,   "three": 3, "four": 4,
        "five": 5, "six": 6,   "seven": 7, "eight": 8,
        "nine": 9, "ten": 10,  "eleven": 11, "twelve": 12,
    }

    @staticmethod
    def get_current_fy(today=None) -> int:
        if not today:
            today = datetime.today()
        return today.year if today.month >= 4 else today.year - 1

    @staticmethod
    def get_fy_quarter(month: int) -> int:
        if 4 <= month <= 6:   return 1
        if 7 <= month <= 9:   return 2
        if 10 <= month <= 12: return 3
        return 4

    @staticmethod
    def date_to_yyyymmdd(dt: date) -> str:
        return dt.strftime("%Y%m%d")

    @staticmethod
    def yyyymmdd_to_ddmmyyyy(s: str) -> str:
        """Convert YYYYMMDD to DD-MM-YYYY for date_parse usage."""
        if len(s) != 8 or not s.isdigit():
            return s
        return f"{s[6:8]}-{s[4:6]}-{s[:4]}"

    @staticmethod
    def get_fy_start_end(fy_year: int) -> Tuple[str, str]:
        return f"{fy_year}0401", f"{fy_year + 1}0331"

    @staticmethod
    def today_yyyymmdd() -> str:
        return datetime.today().strftime("%Y%m%d")

    @staticmethod
    def parse_flexible_date(text: str, default_year=None):
        if not text:
            return None
        text = text.lower().strip()
        # Normalize ordinal day tokens like "22nd", "15 th", "21st" to plain digits.
        text = re.sub(r'\b(\d{1,2})\s*(st|nd|rd|th)\b', r'\1', text)
        today = date.today()
        current_fy = DateParser.get_current_fy(today)

        def resolve_year(mth):
            if default_year:
                return default_year
            return current_fy if mth >= 4 else current_fy + 1

        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            pass
        try:
            return datetime.strptime(text.replace("-", "/"), "%d/%m/%Y").date()
        except ValueError:
            pass

        m = re.search(r'(\d{1,2})\s+([a-z]{3,9})(?:\s+(\d{4}))?', text)
        if m:
            try:
                d   = int(m.group(1))
                mth = DateParser.MONTH_MAP.get(m.group(2)) or DateParser.MONTH_MAP.get(m.group(2)[:3])
                y   = int(m.group(3)) if m.group(3) else resolve_year(mth)
                if mth:
                    return date(y, mth, d)
            except Exception:
                pass

        # month + day (e.g. "aug 14", "august 3, 2025")
        # (?!\d) prevents matching the first 2 digits of a 4-digit year as a day
        m = re.search(r'([a-z]{3,9})\s+(\d{1,2})(?!\d)(?:,?\s*(\d{4}))?', text)
        if m:
            try:
                mth = DateParser.MONTH_MAP.get(m.group(1)) or DateParser.MONTH_MAP.get(m.group(1)[:3])
                d   = int(m.group(2))
                y   = int(m.group(3)) if m.group(3) else resolve_year(mth)
                if mth:
                    return date(y, mth, d)
            except Exception:
                pass

        # month + 4-digit year only (e.g. "aug 2025") -> first day of month
        m = re.search(r'([a-z]{3,9})\s+(20\d{2})\b', text)
        if m:
            try:
                mth = DateParser.MONTH_MAP.get(m.group(1)) or DateParser.MONTH_MAP.get(m.group(1)[:3])
                y   = int(m.group(2))
                if mth:
                    return date(y, mth, 1)
            except Exception:
                pass

        return None

    @staticmethod
    def parse_from_date(query: str, today: date = None) -> Optional[Dict[str, Any]]:
        if not today:
            today = date.today()
        q = query.lower()
        pattern = re.search(
            r'\b(?:from|after|since)\s+((?:\d{1,2}\s+[a-z]{3,9}(?:\s+\d{4})?)|(?:[a-z]{3,9}\s+\d{1,2}(?:\s+\d{4})?)|(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|(?:\d{4}))',
            q
        )
        if not pattern:
            return None
        raw_date = pattern.group(1)
        start_dt = DateParser.parse_flexible_date(raw_date)
        if not start_dt and raw_date.isdigit() and len(raw_date) == 4:
            start_dt = date(int(raw_date), 4, 1)
        if not start_dt:
            return None
        try:
            return {
                "type":       QueryType.DATE_RANGE,
                "start_date": DateParser.date_to_yyyymmdd(start_dt),
                "end_date":   DateParser.today_yyyymmdd(),
                "label":      f"From {start_dt.strftime('%d %b %Y')}",
            }
        except Exception:
            return None

    @staticmethod
    def parse_specific_date_or_range(query: str) -> Optional[Dict[str, Any]]:
        q      = query.lower()
        q      = re.sub(r'\b(\d{1,2})\s*(st|nd|rd|th)\b', r'\1', q)

        def _extract_year(token: str) -> Optional[int]:
            m = re.search(r'\b(20\d{2})\b', token)
            return int(m.group(1)) if m else None

        tokens = re.findall(
            r'\d{4}-\d{2}-\d{2}'
            r'|\d{1,2}[/-]\d{1,2}[/-]\d{4}'
            r'|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec'
            r'|january|february|march|april|june|july|august|september|october|november|december)'
            r'(?:,?\s*\d{4})?\b'
            r'|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec'
            r'|january|february|march|april|june|july|august|september|october|november|december)'
            r'\s+\d{1,2}(?:,?\s*\d{4})?\b',
            q, re.IGNORECASE
        )
        if len(tokens) >= 2:
            year1 = _extract_year(tokens[0])
            year2 = _extract_year(tokens[1])
            d1 = DateParser.parse_flexible_date(tokens[0], default_year=year2 if year1 is None else None)
            d2 = DateParser.parse_flexible_date(tokens[1], default_year=year1 if year2 is None else None)
            if d1 and d2:
                if d1 > d2:
                    d1, d2 = d2, d1
                return {
                    "type":       QueryType.DATE_RANGE,
                    "start_date": DateParser.date_to_yyyymmdd(d1),
                    "end_date":   DateParser.date_to_yyyymmdd(d2),
                    "label":      f"{d1} to {d2}",
                }
        if len(tokens) == 1:
            d1 = DateParser.parse_flexible_date(tokens[0])
            if d1:
                return {
                    "type":  QueryType.SPECIFIC_DATE,
                    "date":  DateParser.date_to_yyyymmdd(d1),
                    "label": d1.strftime("%d %B %Y"),
                }
        return None

    @staticmethod
    def parse_fy_till_month(query: str) -> Optional[Dict[str, Any]]:
        q = query.lower()
        m = re.search(r'fy\s*(\d{4})\s+till\s+([a-z]{3,9})', q)
        if not m:
            return None
        fy_year  = int(m.group(1))
        mth_name = m.group(2)
        mth      = DateParser.MONTH_MAP.get(mth_name) or DateParser.MONTH_MAP.get(mth_name[:3])
        if not mth:
            return None
        y = fy_year if mth >= 4 else fy_year + 1
        _, ld    = monthrange(y, mth)
        start_dt, _ = DateParser.get_fy_start_end(fy_year)
        end_dt   = date(y, mth, ld)
        return {
            "type":       QueryType.DATE_RANGE,
            "start_date": start_dt,
            "end_date":   DateParser.date_to_yyyymmdd(end_dt),
            "label":      f"FY{fy_year} till {end_dt.strftime('%B %Y')}",
        }

    @staticmethod
    def parse_quarter_intent(q: str) -> Optional[Dict[str, Any]]:
        today      = date.today()
        current_fy = DateParser.get_current_fy(today)
        quarter_mentions = re.findall(r'\bq([1-4])\b', q)
        if not quarter_mentions:
            return None
        quarter_range_match = re.search(
            r'\bq([1-4])\b\s*(?:to|till|through|thru|-|–)\s*\bq([1-4])\b(?:\s*(20\d{2}))?',
            q
        )
        # year_match = re.search(r'\b(20\d{2})\b', q)
        # if quarter_range_match and quarter_range_match.group(3):
        #     target_fy = int(quarter_range_match.group(3))
        # else:
        #     target_fy = int(year_match.group(1)) if year_match else current_fy
        # is_mom = any(k in q for k in [
        #     "mom","month over month","month-on-month",
        #     "monthly","month wise","month on month","months wise"
        # ])
        year_match = re.search(r'\b(20\d{2})\b', q)
        if quarter_range_match and quarter_range_match.group(3):
            target_fy = int(quarter_range_match.group(3))
        elif year_match:
            target_fy = int(year_match.group(1))
        elif re.search(r'\b(last year|last fy|previous year|previous fy)\b', q):
            # Shift back one FY from current
            target_fy = current_fy - 1
        else:
            target_fy = current_fy
        is_mom = any(k in q for k in [
            "mom","month over month","month-on-month",
            "monthly","month wise","month on month","months wise", "month by month"
        ])    

        def qd(fy, qn):
            if qn == 1: return datetime(fy, 4, 1),    datetime(fy, 6, 30)
            elif qn == 2: return datetime(fy, 7, 1),  datetime(fy, 9, 30)
            elif qn == 3: return datetime(fy, 10, 1), datetime(fy, 12, 31)
            else:         return datetime(fy+1, 1, 1), datetime(fy+1, 3, 31)

        if quarter_range_match:
            start_q = int(quarter_range_match.group(1))
            end_q   = int(quarter_range_match.group(2))
            if start_q <= end_q:
                quarter_nums = list(range(start_q, end_q + 1))
            else:
                # Wrap-around support (e.g., q3 to q1 => q3,q4,q1)
                quarter_nums = list(range(start_q, 5)) + list(range(1, end_q + 1))
        else:
            quarter_nums = sorted(set(int(x) for x in quarter_mentions))

        if is_mom:
            periods = []
            for qn in quarter_nums:
                s, e = qd(target_fy, qn)
                m_iter, y_iter = s.month, s.year
                while (y_iter < e.year) or (y_iter == e.year and m_iter <= e.month):
                    _, ld = monthrange(y_iter, m_iter)
                    ms = datetime(y_iter, m_iter, 1)
                    me = datetime(y_iter, m_iter, ld)
                    periods.append({
                        "label":      ms.strftime("%b %Y"),
                        "start_date": DateParser.date_to_yyyymmdd(ms.date()),
                        "end_date":   DateParser.date_to_yyyymmdd(me.date()),
                    })
                    m_iter += 1
                    if m_iter == 13:
                        m_iter = 1; y_iter += 1
            return {"type": QueryType.MONTH_WISE, "periods": periods,
                    "label": f"Q{'/Q'.join(str(x) for x in quarter_nums)} FY{target_fy} (MoM)"}

        if len(quarter_nums) > 1:
            quarters = []
            for qn in quarter_nums:
                s, e = qd(target_fy, qn)
                quarters.append({
                    "quarter":    f"Q{qn} FY{target_fy}",
                    "start_date": DateParser.date_to_yyyymmdd(s.date()),
                    "end_date":   DateParser.date_to_yyyymmdd(e.date()),
                })
            return {"type": QueryType.QUARTER_WISE, "quarters": quarters,
                    "label": f"Q{'/Q'.join(str(x) for x in quarter_nums)} FY{target_fy}"}

        qn = quarter_nums[0]
        s, e = qd(target_fy, qn)
        return {
            "type":       QueryType.DATE_RANGE,
            "start_date": DateParser.date_to_yyyymmdd(s.date()),
            "end_date":   DateParser.date_to_yyyymmdd(e.date()),
            "label":      f"Q{qn} FY{target_fy}",
        }


# ============================================================================
# EVENT SCHEMA / COLUMN METADATA
# ============================================================================
class EventColumnMetadata:
    COLUMNS = {
        "created_date_c":       {"type": "VARCHAR", "description": "Event creation date stored as DD-MM-YYYY string — parse with TRY(date_parse(col,'%d-%m-%Y'))"},
        "subject_c":            {"type": "VARCHAR", "description": "Event subject — 'Personal Appointment Booked' for appointments, 'Call' for calls"},
        "appointment_status_c": {"type": "VARCHAR", "description": "Appointment status: completed, scheduled, cancelled, revisit, re-visit, rescheduled, re-schedule"},
        "ownername_c":         {"type": "VARCHAR", "description": "Name of the event owner (NOT created_by_c)"},
        "created_by_c":         {"type": "VARCHAR", "description": "System/record creation metadata — NOT the human owner"},
        "project_c":            {"type": "VARCHAR", "description": "Project name: wave city, wmcc sec 32, wave estate, wave executive floors"},
        "product_category_c":   {"type": "VARCHAR", "description": "Product/unit category (veridia, dream homes, eden, plots, etc.)"},
    }

    # The date column — stored as DD-MM-YYYY VARCHAR (NOT integer like lead/opp)
    DATE_COLUMN   = "created_date_c"
    DATE_COLUMNS  = ["created_date_c"]

    # Count column
    COUNT_COLUMN  = "*"  # events use COUNT(*)

    # Dimension columns
    DIMENSION_COLUMNS = [
        "subject_c", "appointment_status_c", "ownername_c",
        "project_c", "product_category_c",
    ]

    # If generated SQL uses a created_by filter but the table only supports ownername
    # fall back to ownername_c to avoid invalid SQL on the current event_report table.
    COLUMN_FALLBACKS = {
        "created_by_c": "ownername_c",
    }

    # ── Appointment status value map ──────────────────────────────────────────
    # Maps keyword → SQL condition tuple (sql_condition, label)
    APPOINTMENT_STATUS_MAP = {
        # meeting booked / appointment booked → subject_c only
        "meeting booked":       ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        "appointment booked":   ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        "appointments booked":  ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        "meetings booked":      ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        "booked meeting":       ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        "booked appointment":   ('"subject_c" = \'Personal Appointment Booked\'',                                           "meeting_booked"),
        # meeting done / completed
        "meeting done":         ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "meetings done":        ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "completed meeting":    ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "completed meetings":   ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "completed appointment":('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "meeting completed":    ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "total meetings":       ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        "total meeting":        ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
        # scheduled
        "scheduled appointment":('LOWER(TRIM("appointment_status_c")) = \'scheduled\'',                                     "scheduled"),
        "scheduled appointments":('LOWER(TRIM("appointment_status_c")) = \'scheduled\'',                                    "scheduled"),
        "scheduled meeting":    ('LOWER(TRIM("appointment_status_c")) = \'scheduled\'',                                     "scheduled"),
        "scheduled meetings":   ('LOWER(TRIM("appointment_status_c")) = \'scheduled\'',                                     "scheduled"),
        # cancelled
        "cancelled appointment":('LOWER(TRIM("appointment_status_c")) = \'cancelled\'',                                     "cancelled"),
        "cancelled appointments":('LOWER(TRIM("appointment_status_c")) = \'cancelled\'',                                    "cancelled"),
        "cancelled meeting":    ('LOWER(TRIM("appointment_status_c")) = \'cancelled\'',                                     "cancelled"),
        "cancelled meetings":   ('LOWER(TRIM("appointment_status_c")) = \'cancelled\'',                                     "cancelled"),
        # revisit
        "revisit":              ('LOWER(TRIM("appointment_status_c")) IN (\'revisit\', \'re-visit\')',                       "revisit"),
        "re-visit":             ('LOWER(TRIM("appointment_status_c")) IN (\'revisit\', \'re-visit\')',                       "revisit"),
        "revisit appointment":  ('LOWER(TRIM("appointment_status_c")) IN (\'revisit\', \'re-visit\')',                       "revisit"),
        "revisit meeting":      ('LOWER(TRIM("appointment_status_c")) IN (\'revisit\', \'re-visit\')',                       "revisit"),
        # rescheduled
        "rescheduled":          ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',                "rescheduled"),
        "re-schedule":          ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',                "rescheduled"),
        "re-scheduled":         ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',                "rescheduled"),
        "rescheduled appointment":('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',              "rescheduled"),
        "re-scheduled appointment":('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',            "rescheduled"),
        "rescheduled meeting":  ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',                "rescheduled"),
        "re-scheduled meeting": ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')',                "rescheduled"),
    }

    # Generic appointment/meeting trigger → adds subject_c filter (no status qualifier)
    GENERIC_APPOINTMENT_KEYWORDS = ["appointment", "appointments", "meeting", "meetings"]
    APPOINTMENT_STATUS_QUALIFIERS = [
        "booked", "done", "completed", "scheduled",
        "cancelled", "revisit", "rescheduled", "re-schedule", "re-scheduled", "re-visit"
    ]

    # ── Product categories ────────────────────────────────────────────────────
    PRODUCT_CATEGORIES = [
        "veridia","dream homes","eligo","wave floor","old plots","executive floors",
        "plots-res","wave garden","eden","new plots","wave galleria","wrc old plot",
        "swamanorath","amore","livork","wave floor 99","ews_p2","ews","prime floors",
        "wrc plots","mayfair park","silver","wave floor 85","ews_001_(410)","hssc",
        "metro mart","lig_001_(310)","wrc floors","wbt 1","wave garden gh2-ph-2",
        "lig","lig_p2","armonia villa","trucia","gold","elegantia","plots-res-if",
        "irenia","harmony greens","veridia-4","edenia","vasilia","plots-comm",
        "dream bazaar","veridia-5","sco","retail","wave residency","veridia-3",
        "wbt a","eminence","comm booth","veridia-7","courtyard",
        "wave business square","institutional","veridia tower 7","wrc fsi","fsi",
        "hubb","group housing 1","villas","plot-res-if","veridia-6","villa",
        "commercial plots","aranyam valley","wrc institutional","institutional_we",
        "dream homes_we","golf range","waved garden",
    ]

    # ── Project values ─────────────────────────────────────────────────────────
    PROJECT_VALUES = [
        "wave city", "wmcc sec 32", "wave estate", "wave executive floors",
        "wmcc", "wave city phase 1",
    ]

    # ── KEYWORD MAPPING ────────────────────────────────────────────────────────
    KEYWORD_MAPPING = {
        # ── Appointment status filters ────────────────────────────────────────
        "meeting booked":          {"column": "subject_c",            "value": "__meeting_booked__"},
        "appointment booked":      {"column": "subject_c",            "value": "__meeting_booked__"},
        "booked meeting":          {"column": "subject_c",            "value": "__meeting_booked__"},
        "booked appointment":      {"column": "subject_c",            "value": "__meeting_booked__"},
        "meeting done":            {"column": "appointment_status_c", "value": "__meeting_done__"},
        "meetings done":           {"column": "appointment_status_c", "value": "__meeting_done__"},
        "completed meeting":       {"column": "appointment_status_c", "value": "__meeting_done__"},
        "completed appointment":   {"column": "appointment_status_c", "value": "__meeting_done__"},
        "meeting completed":       {"column": "appointment_status_c", "value": "__meeting_done__"},
        "total meetings":          {"column": "appointment_status_c", "value": "__meeting_done__"},
        "total meeting":           {"column": "appointment_status_c", "value": "__meeting_done__"},
        "scheduled appointment":   {"column": "appointment_status_c", "value": "scheduled"},
        "scheduled appointments":  {"column": "appointment_status_c", "value": "scheduled"},
        "scheduled meeting":       {"column": "appointment_status_c", "value": "scheduled"},
        "cancelled appointment":   {"column": "appointment_status_c", "value": "cancelled"},
        "cancelled appointments":  {"column": "appointment_status_c", "value": "cancelled"},
        "cancelled meeting":       {"column": "appointment_status_c", "value": "cancelled"},
        "revisit":                 {"column": "appointment_status_c", "value": "__revisit__"},
        "re-visit":                {"column": "appointment_status_c", "value": "__revisit__"},
        "revisit appointment":     {"column": "appointment_status_c", "value": "__revisit__"},
        "rescheduled":             {"column": "appointment_status_c", "value": "__rescheduled__"},
        "re-schedule":             {"column": "appointment_status_c", "value": "__rescheduled__"},
        "re-scheduled":            {"column": "appointment_status_c", "value": "__rescheduled__"},
        "rescheduled appointment": {"column": "appointment_status_c", "value": "__rescheduled__"},
        "re-scheduled appointment": {"column": "appointment_status_c", "value": "__rescheduled__"},
        "rescheduled meeting":     {"column": "appointment_status_c", "value": "__rescheduled__"},
        "re-scheduled meeting":    {"column": "appointment_status_c", "value": "__rescheduled__"},

        # ── Subject type ──────────────────────────────────────────────────────
        "call":                    {"column": "subject_c",            "value": "Call"},
        "calls":                   {"column": "subject_c",            "value": "Call"},

        # ── Project keywords ──────────────────────────────────────────────────
        "wave city":               {"column": "project_c",            "value": "wave city"},
        "wmcc":                    {"column": "project_c",            "value": "wmcc sec 32"},
        "wmcc sec 32":             {"column": "project_c",            "value": "wmcc sec 32"},
        "wave estate":             {"column": "project_c",            "value": "wave estate"},
        "wave executive floors":   {"column": "project_c",            "value": "wave executive floors"},
        "wave city phase 1":       {"column": "project_c",            "value": "wave city phase 1"},

        # ── Grouping-only keywords ────────────────────────────────────────────
        "by project":              {"column": "project_c"},
        "project wise":            {"column": "project_c"},
        "by product":              {"column": "product_category_c"},
        "product wise":            {"column": "product_category_c"},
        "product bifurcation":     {"column": "product_category_c"},
        "product breakdown":       {"column": "product_category_c"},
        "product trend":           {"column": "product_category_c"},
        "category wise":           {"column": "product_category_c"},
        "by owner":                {"column": "ownername_c"},
        "owner wise":              {"column": "ownername_c"},
        "owner name wise":         {"column": "ownername_c"},
        "owner name":              {"column": "ownername_c"},
        "by status":               {"column": "appointment_status_c"},
        "status wise":             {"column": "appointment_status_c"},
        "by subject":              {"column": "subject_c"},
        "subject wise":            {"column": "subject_c"},

        # ── Aggregation keywords ──────────────────────────────────────────────
        "total events":            {"aggregation": "count", "column": "*"},
        "total event":             {"aggregation": "count", "column": "*"},
        "total appointments":      {"aggregation": "count", "column": "*"},
        "total appointment":       {"aggregation": "count", "column": "*"},
        "how many events":         {"aggregation": "count", "column": "*"},
        "how many appointments":   {"aggregation": "count", "column": "*"},
        "count events":            {"aggregation": "count", "column": "*"},
    }


# ============================================================================
# MULTI-PERIOD HELPER FUNCTIONS
# ============================================================================
def mom_logic(q: str):
    today      = datetime.today()
    current_fy = DateParser.get_current_fy()
    target_fy  = current_fy
    if "last year" in q or "previous year" in q:
        target_fy = current_fy - 1
    else:
        for word in q.split():
            if word.isdigit() and len(word) == 4:
                target_fy = int(word); break
    periods = []
    for month in range(4, 16):
        m  = month if month <= 12 else month - 12
        y  = target_fy if month <= 12 else target_fy + 1
        _, ld = monthrange(y, m)
        s  = datetime(y, m, 1)
        e  = datetime(y, m, ld)
        periods.append({
            "label":      s.strftime("%b %Y"),
            "start_date": DateParser.date_to_yyyymmdd(s.date()),
            "end_date":   DateParser.date_to_yyyymmdd(e.date()),
        })
    return {"type": QueryType.MONTH_WISE, "fy": target_fy, "periods": periods,
            "label": f"Month-wise FY{target_fy}"}


def last_n_mom_logic(q: str):
    last_n_month_match = re.search(
        r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+months?\b", q
    )
    is_mom = any(k in q for k in [
        "mom","month over month","month-on-month",
        "monthly","month wise","month on month","months wise"
    ])
    today = datetime.today()
    try:
        raw_n = last_n_month_match.group(1)
        n     = int(raw_n) if raw_n.isdigit() else DateParser.WORD_TO_NUM.get(raw_n, 1)
        first_of_month = today.date().replace(day=1)
        end_dt   = first_of_month - timedelta(days=1)
        start_dt = end_dt.replace(day=1)
        months   = []
        for _ in range(n):
            months.append(start_dt)
            start_dt = (start_dt - timedelta(days=1)).replace(day=1)
        months   = list(reversed(months))
        start_dt = months[0]
        if is_mom:
            periods = []
            for ms in months:
                yy, mm = ms.year, ms.month
                _, ld  = monthrange(yy, mm)
                s = datetime(yy, mm, 1)
                e = datetime(yy, mm, ld)
                periods.append({
                    "label":      s.strftime("%b %Y"),
                    "start_date": DateParser.date_to_yyyymmdd(s.date()),
                    "end_date":   DateParser.date_to_yyyymmdd(e.date()),
                })
            return {"type": QueryType.MONTH_WISE, "periods": periods,
                    "label": f"Last {n} Months (MoM)"}
        return {
            "type":       QueryType.LAST_N_MONTHS,
            "start_date": DateParser.date_to_yyyymmdd(start_dt),
            "end_date":   DateParser.date_to_yyyymmdd(end_dt),
            "label":      f"Last {n} Months",
        }
    except Exception:
        pass


def last_n_quarter_mom_qoq(q: str):
    last_n_quarter_match = re.search(
        r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+quarters?\b", q
    )
    is_mom = any(k in q for k in [
        "mom","month over month","month-on-month","monthly","month wise","month on month","months wise"
    ])
    is_qoq = any(k in q for k in [
        "qoq","quarter over quarter","quarter-on-quarter","quarterly","quarter wise","quarter on quarter", "quarterwise"
    ])
    today      = datetime.today()
    current_fy = DateParser.get_current_fy()
    try:
        raw_n = last_n_quarter_match.group(1)
        n = max(1, int(raw_n) if raw_n.isdigit() else DateParser.WORD_TO_NUM.get(raw_n, 1))
        year_match    = re.search(r"\b(20\d{2})\b", q)
        explicit_year = int(year_match.group(1)) if year_match else None
        base_fy       = explicit_year if explicit_year else current_fy
        cq            = DateParser.get_fy_quarter(today.month)
        eq            = cq - 1 if cq > 1 else 4
        efy           = base_fy if cq > 1 else base_fy - 1
        meta = []
        qu, fy2 = eq, efy
        for _ in range(n):
            meta.append((fy2, qu))
            qu -= 1
            if qu == 0:
                qu = 4; fy2 -= 1
        meta.reverse()

        def qd(fy3, qn):
            if qn == 1: return datetime(fy3, 4, 1),    datetime(fy3, 6, 30)
            elif qn == 2: return datetime(fy3, 7, 1),  datetime(fy3, 9, 30)
            elif qn == 3: return datetime(fy3, 10, 1), datetime(fy3, 12, 31)
            else:         return datetime(fy3+1, 1, 1), datetime(fy3+1, 3, 31)

        if is_qoq:
            quarters = []
            for fy3, qn in meta:
                s2, e2 = qd(fy3, qn)
                quarters.append({"quarter": f"Q{qn} FY{fy3}",
                                  "start_date": DateParser.date_to_yyyymmdd(s2.date()),
                                  "end_date":   DateParser.date_to_yyyymmdd(e2.date())})
            return {"type": QueryType.QUARTER_WISE, "quarters": quarters,
                    "label": f"Last {n} Quarters (QoQ)"}

        if is_mom:
            periods = []
            for fy3, qn in meta:
                s2, e2 = qd(fy3, qn)
                m_iter, y_iter = s2.month, s2.year
                while (y_iter < e2.year) or (y_iter == e2.year and m_iter <= e2.month):
                    _, ld = monthrange(y_iter, m_iter)
                    s = datetime(y_iter, m_iter, 1)
                    e = datetime(y_iter, m_iter, ld)
                    periods.append({"label": s.strftime("%b %Y"),
                                    "start_date": DateParser.date_to_yyyymmdd(s.date()),
                                    "end_date":   DateParser.date_to_yyyymmdd(e.date())})
                    m_iter += 1
                    if m_iter == 13:
                        m_iter = 1; y_iter += 1
            return {"type": QueryType.MONTH_WISE, "periods": periods,
                    "label": f"Last {n} Quarters (MoM)"}

        s2, _  = qd(*meta[0])
        _, e2  = qd(*meta[-1])
        return {"type": QueryType.LAST_N_QUARTERS,
                "start_date": DateParser.date_to_yyyymmdd(s2.date()),
                "end_date":   DateParser.date_to_yyyymmdd(e2.date()),
                "label":      f"Last {n} Quarters"}
    except Exception as ex:
        logger.error(f"LAST_N_QUARTERS error: {ex}")


def yoy_logic(q: str):
    current_fy = DateParser.get_current_fy()
    start_fy   = 2020
    end_fy     = current_fy
    years      = list(range(start_fy, end_fy + 1))
    periods    = []
    for fy in years:
        s, e = DateParser.get_fy_start_end(fy)
        periods.append({"year": f"FY{fy}", "start_date": s, "end_date": e})
    return {"type": QueryType.YEAR_WISE, "years": years, "periods": periods,
            "label": f"Year-on-Year (FY{start_fy}–FY{end_fy})"}


def last_n_year_mom_qoq_yoy(q: str):
    last_n_year_match = re.search(
        r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+years?\b", q
    )
    is_mom = any(k in q for k in ["mom","month over month","month-on-month","monthly","month wise","month on month","months wise"])
    is_qoq = any(k in q for k in ["qoq","quarter over quarter","quarter-on-quarter","quarterly","quarter wise","quarter on quarter", "quarterwise"])
    is_yoy = any(k in q for k in ["yoy","year on year","yearly","year wise","by year","annual trend","year over year"])
    current_fy = DateParser.get_current_fy()
    raw_n  = last_n_year_match.group(1)
    n      = int(raw_n) if raw_n.isdigit() else DateParser.WORD_TO_NUM.get(raw_n, 1)
    end_fy   = current_fy - 1
    start_fy = end_fy - n + 1

    if is_yoy:
        years   = list(range(start_fy, end_fy + 1))
        periods = []
        for fy in years:
            s, e = DateParser.get_fy_start_end(fy)
            periods.append({"year": f"FY{fy}", "start_date": s, "end_date": e})
        return {"type": QueryType.YEAR_WISE, "years": years, "periods": periods,
                "label": f"Last {n} Years (YoY)"}

    if is_qoq:
        quarters = []
        for fy in range(start_fy, end_fy + 1):
            for qn in range(1, 5):
                if qn == 1:   s, e = datetime(fy, 4, 1),    datetime(fy, 6, 30)
                elif qn == 2: s, e = datetime(fy, 7, 1),    datetime(fy, 9, 30)
                elif qn == 3: s, e = datetime(fy, 10, 1),   datetime(fy, 12, 31)
                else:         s, e = datetime(fy+1, 1, 1),  datetime(fy+1, 3, 31)
                quarters.append({"quarter": f"Q{qn} FY{fy}",
                                  "start_date": DateParser.date_to_yyyymmdd(s.date()),
                                  "end_date":   DateParser.date_to_yyyymmdd(e.date())})
        return {"type": QueryType.QUARTER_WISE, "quarters": quarters,
                "label": f"Last {n} Years (QoQ)"}

    if is_mom:
        periods = []
        for fy in range(start_fy, end_fy + 1):
            for month in range(4, 16):
                m = month if month <= 12 else month - 12
                y = fy if month <= 12 else fy + 1
                _, ld = monthrange(y, m)
                s = datetime(y, m, 1)
                e = datetime(y, m, ld)
                periods.append({"label": s.strftime("%b %Y"),
                                 "start_date": DateParser.date_to_yyyymmdd(s.date()),
                                 "end_date":   DateParser.date_to_yyyymmdd(e.date())})
        return {"type": QueryType.MONTH_WISE, "periods": periods,
                "label": f"Last {n} Years (MoM)"}

    s, _ = DateParser.get_fy_start_end(start_fy)
    _, e = DateParser.get_fy_start_end(end_fy)
    return {"type": QueryType.LAST_N_YEARS, "start_date": s, "end_date": e,
            "label": f"Last {n} Years"}


def year_range_logic(q: str):
    if re.search(r'\b\d{1,2}\s+[a-z]{3,9}\s+20\d{2}\b', q):
        return None
    year_range_match = re.search(r'\b(20\d{2})\s*(?:to|till|–|-)\s*(20\d{2})\b', q)
    if year_range_match:
        start_year = int(year_range_match.group(1))
        end_year   = int(year_range_match.group(2))
        periods    = []
        for fy in range(start_year, end_year + 1):
            s, e = DateParser.get_fy_start_end(fy)
            periods.append({"year": f"FY{fy}", "start_date": s, "end_date": e})
        return {"type": QueryType.YEAR_WISE,
                "years": list(range(start_year, end_year + 1)),
                "periods": periods,
                "label": f"FY{start_year} to FY{end_year}"}
    return None


def detect_year_and(q: str):
    matches = re.findall(r'\b(20\d{2})\b', q)
    if len(matches) >= 2 and re.search(r'\band\b', q):
        years   = sorted(set(int(y) for y in matches))
        periods = []
        for fy in years:
            s, e = DateParser.get_fy_start_end(fy)
            periods.append({"year": f"FY{fy}", "start_date": s, "end_date": e})
        return {"type": QueryType.YEAR_WISE, "years": years, "periods": periods,
                "label": " & ".join([f"FY{y}" for y in years])}
    return None


def parse_month_range_logic(q: str):
    # Supports explicit month-year ranges like:
    # "apr 2024 to nov 2025", "april 2024 - november 2025"
    explicit_month_year_range = re.search(
        r"(?P<m1>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(?P<y1>20\d{2})\s*"
        r"(?:to|till|through|thru|-|–)\s*"
        r"(?P<m2>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(?P<y2>20\d{2})",
        q
    )
    if explicit_month_year_range:
        m1_num = DateParser.MONTH_MAP.get(explicit_month_year_range.group("m1")[:3])
        m2_num = DateParser.MONTH_MAP.get(explicit_month_year_range.group("m2")[:3])
        y1 = int(explicit_month_year_range.group("y1"))
        y2 = int(explicit_month_year_range.group("y2"))
        if not m1_num or not m2_num:
            return None

        _, ld2 = monthrange(y2, m2_num)
        start_str = DateParser.date_to_yyyymmdd(date(y1, m1_num, 1))
        end_str   = DateParser.date_to_yyyymmdd(date(y2, m2_num, ld2))

        is_mom = any(k in q for k in [
            "mom","month over month","month-on-month","monthly","month wise","month on month","months wise"
        ])
        if is_mom:
            periods = []
            m_iter, y_iter = m1_num, y1
            while (y_iter < y2) or (y_iter == y2 and m_iter <= m2_num):
                _, ld = monthrange(y_iter, m_iter)
                ms = datetime(y_iter, m_iter, 1)
                me = datetime(y_iter, m_iter, ld)
                periods.append({
                    "label":      ms.strftime("%b %Y"),
                    "start_date": DateParser.date_to_yyyymmdd(ms.date()),
                    "end_date":   DateParser.date_to_yyyymmdd(me.date())
                })
                m_iter += 1
                if m_iter == 13:
                    m_iter = 1
                    y_iter += 1
            return {
                "type": QueryType.MONTH_RANGE_MONTH_WISE,
                "periods": periods,
                "label": f"{explicit_month_year_range.group('m1').title()} {y1} to {explicit_month_year_range.group('m2').title()} {y2} (MoM)"
            }

        return {
            "type": QueryType.MONTH_RANGE,
            "start_date": start_str,
            "end_date": end_str,
            "label": f"{explicit_month_year_range.group('m1').title()} {y1} to {explicit_month_year_range.group('m2').title()} {y2}"
        }

    month_range_match = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*(?:to|till|-|–)\s*"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*", q
    )
    if not month_range_match:
        return None
    is_mom = any(k in q for k in ["mom","month over month","month-on-month","monthly","month wise","month on month","months wise"])
    current_fy = DateParser.get_current_fy()
    year_match = re.search(r'\b(20\d{2})\b', q)
    target_year = int(year_match.group(1)) if year_match else None

    m1_name = month_range_match.group(1)[:3]
    m2_name = month_range_match.group(2)[:3]
    m1_num  = DateParser.MONTH_MAP.get(m1_name)
    m2_num  = DateParser.MONTH_MAP.get(m2_name)
    if not m1_num or not m2_num:
        return None

    def resolve_year(mth):
        if target_year:
            return target_year
        return current_fy if mth >= 4 else current_fy + 1

    y1 = resolve_year(m1_num)
    y2 = resolve_year(m2_num)
    if m2_num < m1_num and not target_year:
        y2 = y1 + 1 if m2_num < 4 else y1

    _, ld2    = monthrange(y2, m2_num)
    start_str = DateParser.date_to_yyyymmdd(date(y1, m1_num, 1))
    end_str   = DateParser.date_to_yyyymmdd(date(y2, m2_num, ld2))

    if is_mom:
        periods = []
        m_iter, y_iter = m1_num, y1
        while (y_iter < y2) or (y_iter == y2 and m_iter <= m2_num):
            _, ld = monthrange(y_iter, m_iter)
            ms = datetime(y_iter, m_iter, 1)
            me = datetime(y_iter, m_iter, ld)
            periods.append({"label":      ms.strftime("%b %Y"),
                             "start_date": DateParser.date_to_yyyymmdd(ms.date()),
                             "end_date":   DateParser.date_to_yyyymmdd(me.date())})
            m_iter += 1
            if m_iter == 13:
                m_iter = 1; y_iter += 1
        return {"type": QueryType.MONTH_RANGE_MONTH_WISE, "periods": periods,
                "label": f"{month_range_match.group(1).title()} to {month_range_match.group(2).title()} (MoM)"}

    return {"type": QueryType.MONTH_RANGE, "start_date": start_str, "end_date": end_str,
            "label": f"{month_range_match.group(1).title()} to {month_range_match.group(2).title()}"}


def discrete_month(q: str):
    # Do not run when "till date" / "to date" patterns are present — handled separately
    if re.search(r'\btill\s+date\b|\bto\s+date\b|\btill\s+today\b', q):
        return None
    month_pattern = r'\b(' + '|'.join(DateParser.MONTH_MAP.keys()) + r')\b'
    found_months  = re.findall(month_pattern, q, re.IGNORECASE)
    if len(found_months) < 2:
        return None
    # Allow "and" as a joining word but block range separators (to/till/–/-)
    if re.search(r'\bto\b|\btill\b|–', q):
        return None
    # Block bare hyphen only when it looks like a date-range separator (digit-digit)
    if re.search(r'\d\s*-\s*\d', q):
        return None
    current_fy  = DateParser.get_current_fy()
    year_match  = re.search(r'\b(20\d{2})\b', q)
    target_year = int(year_match.group(1)) if year_match else None
    periods     = []
    seen        = set()
    for mn in found_months:
        mnum = DateParser.MONTH_MAP[mn.lower()]
        if mnum in seen:
            continue
        seen.add(mnum)
        # If an explicit year is given, use it for all months;
        # otherwise use FY-aware logic per month
        y = target_year if target_year else (current_fy if mnum >= 4 else current_fy + 1)
        _, ld = monthrange(y, mnum)
        ms = datetime(y, mnum, 1)
        me = datetime(y, mnum, ld)
        periods.append({"label":      ms.strftime("%b %Y"),
                         "start_date": DateParser.date_to_yyyymmdd(ms.date()),
                         "end_date":   DateParser.date_to_yyyymmdd(me.date())})
    if len(periods) < 2:
        return None
    return {"type": QueryType.MULTI_MONTH, "periods": periods,
            "label": ", ".join(p["label"] for p in periods)}


# ============================================================================
# LLM INTENT PROMPT
# ============================================================================
def build_event_llm_prompt(question: str) -> str:
    schema_lines = "\n".join([
        f"  - {col} ({meta['type']}): {meta['description']}"
        for col, meta in EventColumnMetadata.COLUMNS.items()
    ])

    return f"""
You are a strict JSON extraction engine for an event/appointment tracking system.
Your ONLY job is to extract structured intent from a natural language query about events and appointments.
You must NEVER infer, guess, or hallucinate values. Only extract what is explicitly stated.
You must NEVER generate SQL. Only return JSON.

=============================================================
TABLE CONTEXT
=============================================================
Table: event_report
Columns:
{schema_lines}

IMPORTANT — Date column: created_date_c is stored as a DD-MM-YYYY VARCHAR string.
It is NOT an integer. SQL must always parse it using:
  date(TRY(date_parse("created_date_c", '%d-%m-%Y')))
Do NOT mention YYYYMMDD or integers for this column.

=============================================================
ABSOLUTE GROUND RULES
=============================================================
1. Return ONLY valid JSON wrapped in <JSON_RESPONSE> tags. No explanation, no markdown, no SQL.
2. NEVER include a field in "filters" unless the user explicitly mentioned a value for it.
3. NEVER add a column to "group_by" unless user explicitly asked to group/split/break down by it.
4. NEVER infer a date_hint unless the user explicitly mentioned a time period.
   - If no time reference → set date_hint to null.
5. NEVER put date columns in "filters". Dates go ONLY in date_hint + date_column.
6. aggregation MUST always be a LIST, even when only one aggregation is mentioned.
7. If a field has no value to extract, omit it from filters entirely.

=============================================================
OUTPUT SCHEMA
=============================================================
{{
  "aggregation":       [ <string> ],           // always a list; default ["event_count"]
  "group_by":          [ <column_name> ],      // only explicitly requested groupings
  "filters":           {{ <column>: <value> }},// only explicitly mentioned filter values
  "date_hint":         <string | null>,        // raw user phrase or null
  "date_column":       "created_date_c",       // always this for events
  "appointment_filter": <string | null>,       // raw appointment status phrase or null
  "is_yoy":            <bool>                  // true if query is year-on-year
}}

=============================================================
SECTION 1 — AGGREGATION MAPPING
=============================================================
| User says                                                  | Token          |
|------------------------------------------------------------|----------------|
| total events / how many events / event count / events      | "event_count"  |
| total appointments / appointments / meeting count          | "event_count"  |
| total meetings / meeting done count                        | "event_count"  |

- Default to ["event_count"]
- aggregation is ALWAYS a list.

=============================================================
SECTION 2 — APPOINTMENT FILTER EXTRACTION (appointment_filter field)
=============================================================
This field captures the RAW appointment status phrase for Python to handle.

Triggers and values:
- "meeting booked" / "appointment booked" / "booked meeting"     → "meeting_booked"
- "meeting done" / "meetings done" / "completed meeting"         → "meeting_done"
- "total meetings" / "meeting completed"                         → "meeting_done"
- "scheduled appointment" / "scheduled meeting"                  → "scheduled"
- "cancelled appointment" / "cancelled meeting"                  → "cancelled"
- "revisit" / "re-visit" / "revisit appointment"                 → "revisit"
- "rescheduled" / "re-schedule" / "rescheduled appointment"      → "rescheduled"
- Generic "appointment" or "meeting" (no status qualifier)       → "generic_appointment"
- No appointment/meeting mention                                 → null

NOTE: Do NOT put appointment status in "filters". Use "appointment_filter" only.

=============================================================
SECTION 3 — FILTER COLUMN MAPPING
=============================================================

3A. PROJECT (project_c)
Triggers: "wave city", "wmcc", "wmcc sec 32", "wave estate", "wave executive floors"
→ project_c: exact project name
"wmcc" ALWAYS maps to project_c as 'wmcc sec 32'. NEVER any other column.
Use LIKE matching (partial/LOWER). NEVER use = for project_c.

3B. PRODUCT CATEGORY (product_category_c)
Triggers: user mentions a product name from the known list: veridia, dream homes, eligo,
          eden, new plots, old plots, wave floor, prime floors, etc.
OR if user says: "product wise", "by product", "category wise", "product bifurcation"
→ product_category_c: the product name value

# In SECTION 3 — FILTER COLUMN MAPPING, 3C block, add:

3C. OWNER NAME (ownername_c)
- CRITICAL: Extract ONLY the person's name. STOP before any temporal word.
- "events by Ravi in last year" → ownername_c: "Ravi"  (NOT "Ravi in last year")
- "owned by Ravinder Kaur last month" → ownername_c: "Ravinder Kaur"  (NOT "Ravinder Kaur last month")
- "created by Ravinder Kaur in last year" → created_by_c: "Ravinder Kaur"
- Stop words: in, from, during, for, last, this, current, previous, next, jan, feb, ..., fy, q1-q4, any 4-digit year


3D. SUBJECT TYPE (subject_c)
Triggers: "call", "calls" → "Call"
(Appointment-type subject filtering is handled via appointment_filter field, not here)

=============================================================
SECTION 4 — YOY DETECTION (is_yoy field)
=============================================================
Set is_yoy = true if query contains ANY of:
"year on year", "year-on-year", "yoy", "yearly trend", "per year",
"by year", "year wise", "yearwise", "compare years", "each year",
"every year", "all years", "annual trend", "year over year"

When is_yoy = true: date_hint MUST be set to null (unless user gave explicit year range)

=============================================================
SECTION 5 — DATE EXTRACTION
=============================================================
5A. date_column: Always "created_date_c" for events.

5B. date_hint: Return the raw user phrase describing the time period.
Examples: "today", "this week", "this month", "last month", "last quarter",
  "this quarter", "last year", "last 3 months", "last 6 months",
  "q1", "q2", "q3", "q4", "quarter wise", "month wise", "mom", "year wise", "quarterwise",
  "yoy", "fy 2024", "april 2025", "jan to march", "2024 to 2025", null

=============================================================
SECTION 6 — GROUP_BY RULES
=============================================================
Only include a column in group_by if the user explicitly says:
"by <column>", "group by <column>", "break down by <column>",
"month wise", "quarter wise", "project wise", "product wise", "quarterwise",
"category wise", "owner wise","owner name wise", "status wise", "subject wise", etc.

NEVER infer group_by from filters.

=============================================================
SECTION 7 — WORKED EXAMPLES
=============================================================

Q: "Total appointments this month"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{}},"date_hint":"this month","date_column":"created_date_c","appointment_filter":"generic_appointment","is_yoy":false}}

Q: "Meeting booked this quarter by project"
A: {{"aggregation":["event_count"],"group_by":["project_c"],"filters":{{}},"date_hint":"this quarter","date_column":"created_date_c","appointment_filter":"meeting_booked","is_yoy":false}}

Q: "Meeting done from wave city last month"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{"project_c":"wave city"}},"date_hint":"last month","date_column":"created_date_c","appointment_filter":"meeting_done","is_yoy":false}}

Q: "Year on year appointments"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{}},"date_hint":null,"date_column":"created_date_c","appointment_filter":"generic_appointment","is_yoy":true}}

Q: "Meeting booked vs meeting done this year"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{}},"date_hint":"this year","date_column":"created_date_c","appointment_filter":"comparison:meeting_booked:meeting_done","is_yoy":false}}

Q: "Product wise meeting done last quarter"
A: {{"aggregation":["event_count"],"group_by":["product_category_c"],"filters":{{}},"date_hint":"last quarter","date_column":"created_date_c","appointment_filter":"meeting_done","is_yoy":false}}

Q: "Total events by owner month wise"
A: {{"aggregation":["event_count"],"group_by":["ownername_c"],"filters":{{}},"date_hint":"month wise","date_column":"created_date_c","appointment_filter":null,"is_yoy":false}}

Q: "Cancelled appointments from veridia"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{"product_category_c":"veridia"}},"date_hint":null,"date_column":"created_date_c","appointment_filter":"cancelled","is_yoy":false}}

Q: "Meeting done this year vs last year"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{}},"date_hint":"this year vs last year","date_column":"created_date_c","appointment_filter":"subject_time_comparison:meeting_done:year","is_yoy":false}}

Q: "Revisit appointments month wise last 3 months"
A: {{"aggregation":["event_count"],"group_by":[],"filters":{{}},"date_hint":"last 3 months","date_column":"created_date_c","appointment_filter":"revisit","is_yoy":false}}

=============================================================
NOW PROCESS THE FOLLOWING QUERY
=============================================================
User Query: "{question}"

Return ONLY the JSON object wrapped in <JSON_RESPONSE> tags. No other text.

<JSON_RESPONSE>
"""


# ============================================================================
# LLM INTENT DETECTOR
# ============================================================================
class LLMIntentDetector:
    def extract_intent(self, question: str) -> Dict[str, Any]:
        prompt = build_event_llm_prompt(question)
        try:
            response = llm_model.generate_text(prompt)
            logger.info(f"Raw LLM response: {response}")
            json_part = self._extract_first_json(response)
            intent    = json.loads(json_part)
            logger.info(f"Parsed LLM Intent: {intent}")
            return intent
        except Exception as e:
            logger.error(f"LLM intent parsing failed: {e}")
        return {
            "aggregation": ["event_count"],
            "group_by":    [],
            "filters":     {},
            "date_hint":   None,
            "date_column": "created_date_c",
            "appointment_filter": None,
            "is_yoy":      False,
        }

    @staticmethod
    def _extract_first_json(text: str) -> str:
        tag_match = re.search(r"<JSON_RESPONSE>(.*?)</JSON_RESPONSE>", text, re.DOTALL)
        if tag_match:
            content = tag_match.group(1).strip()
            content = re.sub(r"^\s*```json\s*", "", content)
            content = re.sub(r"```\s*$", "", content)
            return content
        start_idx = text.find('{')
        end_idx   = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            potential_json = text[start_idx:end_idx + 1]
            potential_json = potential_json.replace('\\{', '{').replace('\\}', '}')
            brace_count = 0
            for i, ch in enumerate(potential_json):
                if ch == "{":   brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        return potential_json[:i + 1]
        raise ValueError("No valid JSON object found in LLM response")

def parse_quarter_mom(q: str, today: datetime = None):
    if today is None:
        today = datetime.today()
    q = q.lower()
    if not any(k in q for k in ["mom", "month on month", "month wise", "month"]):
        return None

    fy = DateParser.get_current_fy(today)
    current_q = DateParser.get_fy_quarter(today.month)

    quarters = []
    if any(k for k in ["last quarter","previous quarter","last qtr","previous qtr"] if k in q):
        if current_q == 1:
            q_num = 4; year = fy - 1
        else:
            q_num = current_q - 1; year = fy
        quarters = [f"q{q_num}"]
    else:
        q_pattern = r"(?:q(?:uarter|tr)?\s*([1-4]))"
        found_qs  = re.findall(rf"\b{q_pattern}\b", q)
        quarters  = [f"q{num}" for num in found_qs]
        if not quarters:
            return None
        year_match = re.search(r"\b(20\d{2})\b", q)
        year = int(year_match.group(1)) if year_match else fy

    quarter_to_months = {1: [4,5,6], 2: [7,8,9], 3: [10,11,12], 4: [1,2,3]}
    periods = []
    for qtr in quarters:
        q_num  = int(qtr[1])
        months = quarter_to_months[q_num]
        for mm in months:
            y = year + 1 if q_num == 4 and mm in (1,2,3) else year
            _, ld = monthrange(y, mm)
            s = datetime(y, mm, 1)
            e = datetime(y, mm, ld)
            periods.append({
                "label":      s.strftime("%b %Y"),
                "start_date": s.strftime("%Y%m%d"),
                "end_date":   e.strftime("%Y%m%d"),
            })
    return {
        "type":    QueryType.MONTH_RANGE_MONTH_WISE,
        "periods": periods,
        "label":   f"Q{quarters} {year} MoM",
    }


# ============================================================================
# INTENT DETECTOR  — normalises LLM output + deterministic extraction
# ============================================================================
class EventIntentDetector:
    def __init__(self):
        self.keywords = EventColumnMetadata.KEYWORD_MAPPING

    # ── Appointment status condition resolver ─────────────────────────────────
    @staticmethod
    def get_status_sql_condition(status_key: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (sql_condition, label) for a given appointment status key."""
        MAP = {
            "meeting_booked":  ('"subject_c" = \'Personal Appointment Booked\'', "meeting_booked"),
            "meeting_done":    ('("subject_c" = \'Personal Appointment Booked\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "meeting_done"),
            "scheduled":       ('LOWER(TRIM("appointment_status_c")) = \'scheduled\'', "scheduled"),
            "cancelled":       ('LOWER(TRIM("appointment_status_c")) = \'cancelled\'', "cancelled"),
            "revisit":         ('LOWER(TRIM("appointment_status_c")) IN (\'revisit\', \'re-visit\')', "revisit"),
            "rescheduled":     ('LOWER(TRIM("appointment_status_c")) IN (\'rescheduled\', \'re-schedule\')', "rescheduled"),
            "generic_appointment": ('"subject_c" = \'Personal Appointment Booked\'', "appointment"),
            # Call-specific conditions (subject_c='Call' with various statuses)
            "call_generic":    ('"subject_c" = \'Call\'', "call"),
            "call_completed":  ('("subject_c" = \'Call\' AND LOWER(TRIM("appointment_status_c")) = \'completed\')', "call_completed"),
            "call_scheduled":  ('("subject_c" = \'Call\' AND LOWER(TRIM("appointment_status_c")) = \'scheduled\')', "call_scheduled"),
            # Generic completed status (useful for non-call subjects)
            "completed":       ("LOWER(TRIM(\"appointment_status_c\")) = 'completed'", "completed"),
        }
        return MAP.get(status_key, (None, None))

    # ── Extract appointment filter from question deterministically ────────────
    def extract_appointment_filter(self, question: str) -> Optional[str]:
        """Deterministically extract appointment_filter key from question text."""
        q = question.lower()

        # ─ CALL-SPECIFIC LOGIC (Priority 0 — before all other checks) ─────────────────
        if re.search(r'\bcalls?\b', q):  # has "call" or "calls"
            # Check for call + status combinations (most specific first)
            if re.search(r'\bcalls?\s+(?:done|completed)\b', q):
                return "call_completed"  # Call subject + completed status
            elif re.search(r'\bcalls?\s+scheduled\b', q):
                return "call_scheduled"  # Call subject + scheduled status
            elif re.search(r'\bcalls?\s+(?:booked|booking)\b', q):
                # Calls booked → just Call subject filter, no status
                return "call_generic"
            elif not re.search(r'\b(?:done|completed|scheduled|booked|booking|cancelled|revisit|rescheduled|re-schedule|re-scheduled|re-visit)\b', q):
                # Generic "calls" or "call" with no status qualifier
                return "call_generic"

        # Priority 1: explicit rescheduled/revisit terms before generic scheduled.
        if re.search(r'\b(?:re[-\s]?scheduled|rescheduled)\b', q):
            return 'rescheduled'
        if re.search(r'\b(?:re[-\s]?visit|revisit)\b', q):
            return 'revisit'

        # Priority 1: specific multi-status comparison (X vs Y)
        vs_match = re.search(r'([\w\s]+?)\s+(?:vs|versus)\s+([\w\s]+?)(?:\s+(?:in|for|during|by|from|this|last)|$)', q)
        if vs_match:
            e1 = vs_match.group(1).strip()
            e2 = vs_match.group(2).strip()
            # Subject-time comparison: "meeting done this year vs last year"
            stime = re.search(
                r'([\w\s]+?)\s+(this|current)\s+(fy|year|month|quarter)\s+(?:vs|versus)\s+(last|previous)\s+(fy|year|month|quarter)',
                q
            )
            if stime:
                subject    = stime.group(1).strip()
                period_type = stime.group(3)
                # Detect status of subject
                for key, (_, label) in {
                    "meeting done": "meeting_done", "meeting booked": "meeting_booked",
                    "scheduled": "scheduled", "cancelled": "cancelled",
                    "revisit": "revisit", "rescheduled": "rescheduled"
                }.items() if False else EventColumnMetadata.APPOINTMENT_STATUS_MAP.items():
                    if key in subject:
                        return f"subject_time_comparison:{label}:{period_type}"
                return f"subject_time_comparison:generic_appointment:{period_type}"

            # Status comparison
            def label_from_text(t):
                if "meeting done" in t or "completed" in t: return "meeting_done"
                if "meeting booked" in t or "appointment booked" in t or "booked" in t: return "meeting_booked"
                if "scheduled" in t: return "scheduled"
                if "cancelled" in t: return "cancelled"
                if "revisit" in t or "re-visit" in t: return "revisit"
                if "rescheduled" in t or "re-schedule" in t: return "rescheduled"
                return None

            l1 = label_from_text(e1)
            l2 = label_from_text(e2)
            if l1 and l2:
                return f"comparison:{l1}:{l2}"

        # Priority 1.5: multi-status comparison with connectors like
        # "both ... and ...", "bifurcate ... and ...", "split ... and ..."
        # NOTE: bare "and" between month/date tokens (e.g. "aug and sep 2024") must NOT
        # trigger a comparison — only fire when >= 2 distinct status keywords are present.
        def _extract_status_labels(text: str):
            status_hits = []
            for keyword, (_, label) in sorted(
                EventColumnMetadata.APPOINTMENT_STATUS_MAP.items(),
                key=lambda x: len(x[0]),
                reverse=True,
            ):
                for m in re.finditer(rf"\b{re.escape(keyword)}\b", text):
                    start, end = m.span()
                    if any(not (end <= prev_start or start >= prev_end) for prev_start, prev_end, _ in status_hits):
                        continue
                    status_hits.append((start, end, label))
            status_hits.sort(key=lambda x: x[0])
            ordered_labels = []
            for _, _, label in status_hits:
                if label not in ordered_labels:
                    ordered_labels.append(label)
            return ordered_labels

        comparison_connector = re.search(
            r"\b(both|bifurcate|split|breakdown|break down|compare|comparison)\b", q
        )
        if not comparison_connector:
            if re.search(r"\band\b", q):
                _status_count = len(_extract_status_labels(q))
                if _status_count >= 2:
                    comparison_connector = re.search(r"\band\b", q)

        if comparison_connector:
            ordered_labels = _extract_status_labels(q)
            if len(ordered_labels) >= 2:
                return f"comparison:{ordered_labels[0]}:{ordered_labels[1]}"

        # Priority 2: specific status keywords (longest match first)
        for keyword in sorted(EventColumnMetadata.APPOINTMENT_STATUS_MAP.keys(), key=len, reverse=True):
            if re.search(rf'\b{re.escape(keyword)}\b', q):
                _, label = EventColumnMetadata.APPOINTMENT_STATUS_MAP[keyword]
                return label

        # Priority 3: generic appointment/meeting (no qualifier)
        has_generic = any(kw in q for kw in EventColumnMetadata.GENERIC_APPOINTMENT_KEYWORDS)
        has_qualifier = any(kw in q for kw in EventColumnMetadata.APPOINTMENT_STATUS_QUALIFIERS)
        if has_generic and not has_qualifier:
            return "generic_appointment"

        return None
    

    @staticmethod
    def clean_name_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
        """Strip temporal suffixes from name-type columns."""
        TEMPORAL_STOP = re.compile(
            r'\s+(?:in\s+)?(?:last|this|current|previous|next)\s+'
            r'(?:year|month|quarter|week|day|fy|q[1-4])\b.*$'
            r'|\s+(?:in\s+)?(?:fy\s*)?\d{4}\b.*$'
            r'|\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|'
            r'july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|'
            r'dec(?:ember)?)\b.*$',
            re.IGNORECASE
        )
        generic_invalid = {
            "owner", "owner name", "created by", "owned by", "name",
            "by owner", "by owner name", "me", "my", "all", "fy", "year", "month", "week", "day",
            "last", "this", "current", "previous", "next", "FY"
        }
        for col in ("ownername_c", "created_by_c"):
            if col in filters:
                cleaned = []
                for v in filters[col]:
                    v_clean = TEMPORAL_STOP.sub('', str(v)).strip()
                    if not v_clean or v_clean.lower() in generic_invalid:
                        continue
                    cleaned.append(v_clean)
                if cleaned:
                    filters[col] = list(set(cleaned))
                else:
                    del filters[col]
        return filters

    # ── Filter normalisation ──────────────────────────────────────────────────
    def normalize_filters(self, raw_filters: Dict[str, Any]) -> Dict[str, Any]:
        normalized = {}
        col_map = {k.lower(): k for k in EventColumnMetadata.COLUMNS.keys()}

        for col, values in raw_filters.items():
            if not values:
                continue
            target_col = col_map.get(col.lower(), col)

            # product/project redirection
            if target_col != "product_category_c":
                v_str = " ".join([str(v) for v in (values if isinstance(values, list) else [values])]).lower()
                for cat in EventColumnMetadata.PRODUCT_CATEGORIES:
                    if cat in v_str:
                        target_col = "product_category_c"; break
            if target_col not in ("project_c", "product_category_c"):
                v_str = " ".join([str(v) for v in (values if isinstance(values, list) else [values])]).lower()
                for proj in EventColumnMetadata.PROJECT_VALUES:
                    if proj in v_str:
                        target_col = "project_c"; break

            normalized_values = []
            value_list = values if isinstance(values, list) else [values]

            expanded = []
            for v in value_list:
                if isinstance(v, str):
                    v_clean = re.sub(r'\s+and\s+', ',', v, flags=re.IGNORECASE)
                    if "," in v_clean:
                        expanded.extend([x.strip() for x in v_clean.split(",") if x.strip()])
                    else:
                        expanded.append(v)
                else:
                    expanded.append(v)

            for val in expanded:
                if isinstance(val, str):
                    val_clean = re.sub(r'^(owner(?:name)?|created by)\s+', '', val.strip(), flags=re.IGNORECASE)
                else:
                    val_clean = val
                val_str = str(val_clean).lower().strip()

                # After val_clean assignment, add for ownername_c:
                if target_col == "ownername_c":
                    # Strip trailing temporal phrases
                    val_clean = re.sub(
                        r'\s+(?:in\s+)?(?:last|this|current|previous|next)\s+(?:year|month|quarter|week|day|fy|q[1-4]).*$',
                        '', str(val_clean), flags=re.IGNORECASE
                    ).strip()
                    val_clean = re.sub(
                        r'\s+(?:in\s+)?(?:fy\s*)?\d{4}.*$', '', str(val_clean), flags=re.IGNORECASE
                    ).strip()
                    val_clean = re.sub(
                        r'\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b.*$',
                        '', str(val_clean), flags=re.IGNORECASE
                    ).strip()

                if target_col == "project_c":
                    project_matches = [proj for proj in EventColumnMetadata.PROJECT_VALUES if proj in val_str]
                    if project_matches:
                        normalized_values.extend(project_matches)
                        continue
                if target_col == "product_category_c":
                    category_matches = [cat for cat in EventColumnMetadata.PRODUCT_CATEGORIES if cat in val_str]
                    if category_matches:
                        normalized_values.extend(category_matches)
                        continue

                if target_col in ("ownername_c", "created_by_c"):
                    if re.search(r'\b(and|in|of|for|wave|wmcc|estate|city|march|april|may|june|july|aug|sept?|oct|nov|dec|month|year|owner|ownername|created|\d{4})\b', val_str):
                        continue
                    if len(val_str.split()) > 4:
                        continue
                    if not re.search(r'^[a-z][a-z\s\.\'-]+$', val_str):
                        continue

                # Skip appointment status tokens — handled separately
                if val_str in [k for k, v in self.keywords.items() if "value" not in v]:
                    continue
                # Skip appointment-status-like values
                if val_str in ["__meeting_booked__","__meeting_done__","__revisit__","__rescheduled__","__scheduled__","__cancelled__","appointment","meeting"]:
                    continue
                normalized_values.append(val_clean)

            if normalized_values:
                normalized.setdefault(target_col, [])
                normalized[target_col].extend(normalized_values)
                normalized[target_col] = list(set(normalized[target_col]))

            # At the end of normalize_filters, before returning:
        for col in list(normalized.keys()):
            if col in ("ownername_c", "created_by_c"):
                vals = sorted(normalized[col], key=len)
                deduped = []
                for v in vals:
                    if not any(v.lower() in existing.lower() and v != existing 
                            for existing in deduped):
                        deduped.append(v)
                normalized[col] = deduped    



        return normalized

    # ── Full intent normalisation ─────────────────────────────────────────────
    def normalize_intent(self, raw_intent: Dict[str, Any], question: str) -> Dict[str, Any]:
        q = question.lower()

        # 1. Aggregation
        raw_agg = raw_intent.get("aggregation", ["event_count"])
        if isinstance(raw_agg, str):
            raw_agg = [raw_agg]
        normalized_agg = raw_agg if raw_agg else ["event_count"]

        # 2. Filters — normalize what LLM found, then merge deterministic
        raw_filters        = raw_intent.get("filters", {})
        # Strip appointment columns from filters — handled via appointment_filter
        clean_raw = {k: v for k, v in raw_filters.items()
                     if k not in ("subject_c", "appointment_status_c")}
        normalized_filters = self.normalize_filters(clean_raw)
        deterministic_filters = self.extract_filters(question)
        for col, values in deterministic_filters.items():
            if col not in normalized_filters:
                normalized_filters[col] = values
            else:
                normalized_filters[col] = list(set(normalized_filters[col] + values))

        # Prefer explicit created-by over owner when query says "created by"
        if re.search(r'\bcreated by\b', question, re.IGNORECASE):
            normalized_filters.pop("ownername_c", None)
        elif re.search(r'\b(?:owner|owned by|handled by|owner name)\b', question, re.IGNORECASE):
            normalized_filters.pop("created_by_c", None)

        # 3. Group by — only when user explicitly asks to group/split
        normalized_groupby = self.extract_groupby(question)
        if not normalized_groupby:
            raw_group_by = raw_intent.get("group_by", [])
            if isinstance(raw_group_by, str):
                raw_group_by = [raw_group_by]
            normalized_groupby = [g for g in raw_group_by if g]

        if not normalized_groupby and re.search(r'\bby owner\b', question, re.IGNORECASE):
            if not re.search(r'\bby owner\b\s+(?!in\b|from\b|during\b|for\b|last\b|this\b|current\b|next\b|today\b|yesterday\b|jan\b|feb\b|mar\b|apr\b|may\b|jun\b|jul\b|aug\b|sep\b|oct\b|nov\b|dec\b|fy\b|q[1-4]\b)[A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+)*\b', question, re.IGNORECASE):
                normalized_groupby.append("ownername_c")

        if not normalized_groupby and len(normalized_filters.get("project_c", [])) > 1:
            if re.search(r'\band\b|,', question.lower()):
                normalized_groupby.append("project_c")

        # 4. Appointment filter — deterministic wins over LLM
        appointment_filter = self.extract_appointment_filter(question)
        if not appointment_filter:
            raw_appt_filter = raw_intent.get("appointment_filter")
            # Guardrail: only trust LLM appointment filter when query text has
            # explicit appointment/meeting language.
            has_appt_language = (
                any(kw in q for kw in EventColumnMetadata.GENERIC_APPOINTMENT_KEYWORDS)
                or any(kw in q for kw in EventColumnMetadata.APPOINTMENT_STATUS_QUALIFIERS)
                or "appointment" in q
                or "meeting" in q
            )
            if raw_appt_filter and has_appt_language:
                appointment_filter = raw_appt_filter

        # 5. YoY detection
        is_yoy_keywords = [
            "year on year","year-on-year","yoy","yearly trend","per year",
            "by year","year wise","yearwise","compare years","each year",
            "every year","all years","annual trend","year over year"
        ]
        has_explicit_yoy = any(kw in q for kw in is_yoy_keywords)
        has_mom = any(kw in q for kw in [
            "mom", "month on month", "month-over-month", "month over month",
            "month-on-month", "month wise", "monthly", "per month", "by month"
        ])
        has_qoq = any(kw in q for kw in [
            "qoq", "quarter on quarter", "quarter-over-quarter", "quarter over quarter",
            "quarter-on-quarter", "quarter wise", "quarterly", "per quarter", "by quarter", "quarterwise"
        ])

        # Guardrail: do not allow LLM-only YoY flag to override explicit MoM/QoQ queries.
        llm_is_yoy = bool(raw_intent.get("is_yoy", False))
        is_yoy = has_explicit_yoy or (llm_is_yoy and not (has_mom or has_qoq))
        

        
        # Note: Call subject is now handled via appointment_filter (call_generic, call_completed, call_scheduled)
        # Do NOT add subject_c filter here for calls — it's baked into the appointment condition

        print(f"DEBUG: Normalized filters before cleaning: {normalized_filters}")

        normalized_filters = EventIntentDetector.clean_name_filters(normalized_filters)        
        if "ownername_c" in raw_filters and "ownername_c" in normalized_filters:
            # Convert both values to lists if they are strings
            raw_owner = raw_filters["ownername_c"]
            norm_owner = normalized_filters["ownername_c"]

            if not isinstance(raw_owner, list):
                raw_owner = [raw_owner]

            if not isinstance(norm_owner, list):
                norm_owner = [norm_owner]

            # Merge and remove duplicates
            normalized_filters["ownername_c"] = list(set(norm_owner + raw_owner))
        else:
            # Remove ownername_c if it is present in only one of them
            normalized_filters.pop("ownername_c", None)
        
        return {
            "aggregation":        normalized_agg,
            "filters":            normalized_filters,
            "group_by":           list(set(normalized_groupby)),
            "date_hint":          raw_intent.get("date_hint"),
            "date_column":        "created_date_c",
            "appointment_filter": appointment_filter,
            "is_yoy":             is_yoy,
        }

    # ── Deterministic filter extraction ───────────────────────────────────────
    def extract_filters(self, question: str) -> Dict[str, Any]:
        filters        = {}
        question_lower = question.lower()

        # Project filters
        project_list = EventColumnMetadata.PROJECT_VALUES
        for project in project_list:
            if project in question_lower:
                filters.setdefault("project_c", []).append(project)
        if "project_c" in filters:
            filters["project_c"] = sorted(set(filters["project_c"]))

        # Product category filters
        for category in EventColumnMetadata.PRODUCT_CATEGORIES:
            if category.lower() in question_lower:
                filters.setdefault("product_category_c", []).append(category)
        if "product_category_c" in filters:
            filters["product_category_c"] = sorted(set(filters["product_category_c"]))

        # Created-by / owner name detection
        created_by_match = re.search(
        r'\bcreated by\s+([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+)*)',
        question, re.IGNORECASE
    )
        if created_by_match:
            filters.setdefault("created_by_c", []).append(created_by_match.group(1).strip())
            return filters

        # In extract_filters(), replace owner_match regex with:
        owner_match = re.search(
            r'\b(?:owner(?:name)?|owned by|handled by|event by|events by|for|by|of)\s+'
            r'([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+)*?)'
            r'(?=\s+(?:wise|in|from|during|for|last|this|current|next|today|yesterday|'
            r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|fy|q[1-4]|\d{4})|$)',
            question, re.IGNORECASE
        )

        def _split_owner_names(phrase: str):
            parts = [p.strip() for p in re.split(r'\s+(?:and|,)\s+', phrase) if p.strip()]
            clean_parts = []
            for part in parts:
                part = re.sub(
                    r'\s+(?:in|from|during|for|last|this|current|previous|next|today|yesterday|'
                    r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|fy|q[1-4]|\d{4})\b.*$',
                    '', part, flags=re.IGNORECASE
                ).strip()
                if part:
                    clean_parts.append(part)
            return clean_parts

        if owner_match:
            owner_name = owner_match.group(1).strip()
            owner_name_lower = owner_name.lower()
            project_phrases = [proj.lower() for proj in EventColumnMetadata.PROJECT_VALUES]
            product_phrases = [cat.lower() for cat in EventColumnMetadata.PRODUCT_CATEGORIES]
            generic_owner_names = {"owner", "owner name", "created by", "owned by", "by owner", "my owner", "all"}
            if any(proj in owner_name_lower for proj in project_phrases):
                owner_match = None
            elif any(cat in owner_name_lower for cat in product_phrases):
                owner_match = None
            elif any(month in owner_name_lower for month in DateParser.MONTH_MAP.keys()):
                owner_match = None
            elif owner_name_lower in generic_owner_names:
                owner_match = None
            elif any(word in owner_name_lower for word in ["product","project","appointment","meeting","event","group","wise"]):
                owner_match = None

        if owner_match:
            owner_name = owner_match.group(1).strip()
            owner_names = _split_owner_names(owner_name)
            for name in owner_names:
                filters.setdefault("ownername_c", []).append(name)

        return filters

    # ── Deterministic group-by extraction ────────────────────────────────────
    def extract_groupby(self, question: str) -> List[str]:
        question_lower = question.lower()
        group_by = []

        def followed_by_name(prefix: str) -> bool:
            return bool(re.search(
                rf'\b{prefix}\s+(?!in\b|from\b|during\b|for\b|last\b|this\b|current\b|next\b|today\b|yesterday\b|jan\b|feb\b|mar\b|apr\b|may\b|jun\b|jul\b|aug\b|sep\b|oct\b|nov\b|dec\b|fy\b|q[1-4]\b)[A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+)*\b',
                question_lower,
                re.IGNORECASE
            ))

        def followed_by_project(prefix: str) -> bool:
            return any(re.search(rf'\b{prefix}\s+{re.escape(proj)}\b', question_lower) for proj in EventColumnMetadata.PROJECT_VALUES)

        def followed_by_product(prefix: str) -> bool:
            return any(re.search(rf'\b{prefix}\s+{re.escape(cat)}\b', question_lower) for cat in EventColumnMetadata.PRODUCT_CATEGORIES)

        if re.search(r'\b(by project|project wise|per project|projectwise)\b', question_lower):
            if not followed_by_project('by project'):
                group_by.append("project_c")
        if re.search(r'\b(by product|product wise|category wise|per product|productwise|product bifurcation|product breakdown|product trend)\b', question_lower):
            if not followed_by_product('by product'):
                group_by.append("product_category_c")
        if re.search(r'\b(by owner|by owner name|owner wise|per owner|ownername|owner name|owner name wise)\b', question_lower):
            if not followed_by_name('by owner'):
                group_by.append("ownername_c")
        if re.search(r'\b(by status|status wise|per status)\b', question_lower):
            group_by.append("appointment_status_c")
        if re.search(r'\b(by subject|subject wise|per subject)\b', question_lower):
            group_by.append("subject_c")
        return group_by

    # ── Date intent detection — 28-step cascade ───────────────────────────────
    def detect_date_intent(self, question: str):
        q = question.lower()
        logger.info(f"Detecting date intent: {q}")

        NUMBER_WORDS = {
            "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,
            "seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,
            "thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
            "seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,
        }

        last_n_year_match    = re.search(r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+years?\b", q)
        last_n_month_match   = re.search(r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+months?\b", q)
        last_n_quarter_match = re.search(r"\blast\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+quarters?\b", q)
        is_mom  = any(k in q for k in ["mom","month over month","month-on-month","monthly","month wise","month on month","months wise"])
        is_yoy  = any(k in q for k in ["yoy","year on year","yearly","year wise","by year","annual trend","year over year",
                                        "yearwise","compare years","each year","every year","all years","year-on-year","per year"])
        has_year = bool(re.search(r'\b(19|20)\d{2}\b', q))
        has_date_keyword = (
            any(word in q for word in [
                "day","month","quarter","year","date","last","this","current",
                "fy","q1","q2","q3","q4","qtr","mom","qoq","yoy","today",
                "yesterday","week","fiscal",
            ])
            or any(m in q for m in DateParser.MONTH_MAP.keys())
            or has_year
        )

        if not has_date_keyword:
            return None

        today      = datetime.today()
        current_fy = DateParser.get_current_fy()

        # 1️⃣ YoY — return None so engine uses no date filter (all years returned)
        if is_yoy and not last_n_year_match:
            # Check if user gave explicit year range
            year_range = re.search(r'\b(20\d{2})\s*(?:to|till|–|-)\s*(20\d{2})\b', q)
            if year_range:
                return yoy_logic(q)   # builds YEAR_WISE periods with range
            return {"type": QueryType.YEAR_WISE, "no_date_filter": True,
                    "label": "Year-on-Year (All Years)"}

        # last quarter + mom
        mom_last_quarter = parse_quarter_mom(q)
        if mom_last_quarter:
            return mom_last_quarter

        # 2️⃣ QoQ full FY
        if is_qoq and not last_n_quarter_match and not last_n_year_match:
            target_fy = current_fy
            if "last year" in q or "previous year" in q or "last fy" in q or "previous fy" in q:
                target_fy = current_fy - 1
            else:
                for word in q.split():
                    if word.isdigit() and len(word) == 4:
                        target_fy = int(word); break
            quarters = []
            for q_num in range(1, 5):
                if q_num == 1:   s, e = datetime(target_fy,4,1),    datetime(target_fy,6,30)
                elif q_num == 2: s, e = datetime(target_fy,7,1),    datetime(target_fy,9,30)
                elif q_num == 3: s, e = datetime(target_fy,10,1),   datetime(target_fy,12,31)
                else:            s, e = datetime(target_fy+1,1,1),  datetime(target_fy+1,3,31)
                quarters.append({"quarter": f"Q{q_num} FY{target_fy}",
                                  "start_date": DateParser.date_to_yyyymmdd(s.date()),
                                  "end_date":   DateParser.date_to_yyyymmdd(e.date())})
            return {"type": QueryType.QUARTER_WISE, "fy": target_fy, "quarters": quarters,
                    "label": f"Quarter-wise FY{target_fy}"}

        # # 3️⃣ Specific Q1/Q2/Q3/Q4
        # quarter_intent = DateParser.parse_quarter_intent(q)
        # if quarter_intent:
        #     return quarter_intent

        # 3️⃣ Specific Q1/Q2/Q3/Q4
        # Guard: if "last year/fy" context is present alongside a quarter token,
        # parse_quarter_intent already handles it via the updated target_fy logic above.
        # Only skip here if it's a pure "last year" without a quarter token.
        _has_quarter_token = bool(re.search(r'\bq[1-4]\b', q))
        _has_last_year = bool(re.search(
            r'\b(last year|last fy|previous year|previous fy)\b', q
        ))

        if _has_last_year and not _has_quarter_token:
            # Pure "last year" — let Step 2️⃣3️⃣ handle it
            pass
        else:
            quarter_intent = DateParser.parse_quarter_intent(q)
            if quarter_intent:
                return quarter_intent

        # 3️⃣b  "X till date" / "X to date" / "X till today" — open-ended range ending today
        # Handles:  "april 2025 till date", "14 aug till date", "18 sep 2025 till date"
        if re.search(r'\btill\s+date\b|\bto\s+date\b|\btill\s+today\b', q):
            # Strip the trailing "till date / to date / till today" and parse the start
            stripped = re.sub(r'\s*\b(?:till|to)\s+(?:date|today)\b.*$', '', q).strip()
            start_dt = DateParser.parse_flexible_date(stripped)
            if start_dt is None:
                # Try to parse as "month year" or "month"
                m_yr = re.search(
                    r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*'
                    r'(?:\s+(20\d{2}))?', stripped
                )
                if m_yr:
                    mnum = DateParser.MONTH_MAP.get(m_yr.group(1)[:3])
                    yr   = int(m_yr.group(2)) if m_yr.group(2) else None
                    if mnum:
                        if yr is None:
                            yr = current_fy if mnum >= 4 else current_fy + 1
                        start_dt = date(yr, mnum, 1)
            if start_dt:
                return {
                    "type":       QueryType.DATE_RANGE,
                    "start_date": DateParser.date_to_yyyymmdd(start_dt),
                    "end_date":   DateParser.today_yyyymmdd(),
                    "label":      f"From {start_dt.strftime('%d %b %Y')} till date",
                }

        # 4️⃣ Month range (jan to march)
        month_logic = parse_month_range_logic(q)
        if month_logic:
            return month_logic

        # 5️⃣ This quarter vs last quarter comparison
        if "this quarter vs last quarter" in q or "this quarter compared to last quarter" in q:
            this_q    = DateParser.get_fy_quarter(today.month)
            target_fy = current_fy
            last_q    = this_q - 1 if this_q > 1 else 4
            last_q_fy = target_fy if this_q > 1 else target_fy - 1
            def gqd(q_num, fy):
                if q_num == 1:   return datetime(fy,4,1),    datetime(fy,6,30)
                elif q_num == 2: return datetime(fy,7,1),    datetime(fy,9,30)
                elif q_num == 3: return datetime(fy,10,1),   datetime(fy,12,31)
                else:            return datetime(fy+1,1,1),  datetime(fy+1,3,31)
            ts, te = gqd(this_q, target_fy)
            ls, le = gqd(last_q, last_q_fy)
            return {
                "type": QueryType.MULTI_DATE_RANGE,
                "ranges": [
                    {"start_date": DateParser.date_to_yyyymmdd(ts.date()), "end_date": DateParser.date_to_yyyymmdd(te.date()), "label": f"This Quarter FY{target_fy} Q{this_q}"},
                    {"start_date": DateParser.date_to_yyyymmdd(ls.date()), "end_date": DateParser.date_to_yyyymmdd(le.date()), "label": f"Last Quarter FY{last_q_fy} Q{last_q}"},
                ],
                "label": "This Quarter vs Last Quarter",
            }

        # 6️⃣ Discrete months
        discrete = discrete_month(q)
        if discrete:
            return discrete

        # 7️⃣ Today / yesterday
        if any(k in q for k in ["today","today's","todays"]):
            return {"type": QueryType.SPECIFIC_DATE, "date": DateParser.today_yyyymmdd(),
                    "label": today.strftime("%d %B %Y")}
        if "yesterday" in q or "last day" in q or "last date" in q or "previous day" in q:
            yd = today.date() - timedelta(days=1)
            return {"type": QueryType.SPECIFIC_DATE, "date": DateParser.date_to_yyyymmdd(yd),
                    "label": f"Yesterday {yd}"}

        # 8️⃣ This week
        if "this week" in q or "current week" in q:
            start_of_week = today.date() - timedelta(days=today.weekday())
            return {"type": QueryType.THIS_WEEK,
                    "start_date": DateParser.date_to_yyyymmdd(start_of_week),
                    "end_date":   DateParser.today_yyyymmdd(), "label": "This Week"}

        # 9️⃣ Last N days
        if ("last" in q or "previous" in q) and "day" in q:
            words = q.split(); n = None
            for i, word in enumerate(words):
                if word in ("last","previous") and i + 1 < len(words):
                    nxt = words[i+1]
                    if nxt == "day":    n = 1; break
                    if nxt.isdigit():   n = int(nxt); break
                    if nxt in NUMBER_WORDS: n = NUMBER_WORDS[nxt]; break
            if n:
                end   = today.date() - timedelta(days=1)
                start = end - timedelta(days=n - 1)
                return {"type": QueryType.LAST_N_DAYS,
                        "start_date": DateParser.date_to_yyyymmdd(start),
                        "end_date":   DateParser.date_to_yyyymmdd(end),
                        "label":      "Yesterday" if n == 1 else f"Last {n} Days"}

        # 🔟 Last N weeks
        if ("last" in q or "previous" in q) and "week" in q:
            words = q.split()
            for i, word in enumerate(words):
                if word in ("last","previous") and i + 1 < len(words):
                    try:
                        n = int(words[i+1]) if words[i+1].isdigit() else DateParser.WORD_TO_NUM.get(words[i+1], 1)
                        last_sunday = today.date() - timedelta(days=today.weekday() + 1)
                        start_dt    = last_sunday - timedelta(weeks=n - 1, days=6)
                        return {"type": QueryType.LAST_N_WEEKS,
                                "start_date": DateParser.date_to_yyyymmdd(start_dt),
                                "end_date":   DateParser.date_to_yyyymmdd(last_sunday),
                                "label":      f"Last {n} Week{'s' if n>1 else ''}"}
                    except Exception:
                        pass

        # 1️⃣1️⃣ This month
        if "this month" in q or "current month" in q:
            start = today.date().replace(day=1)
            return {"type": QueryType.THIS_MONTH,
                    "start_date": DateParser.date_to_yyyymmdd(start),
                    "end_date":   DateParser.today_yyyymmdd(),
                    "label":      f"This Month - {today.strftime('%B %Y')}"}

        # 1️⃣2️⃣ Last month
        if "last month" in q or "previous month" in q:
            first_of_this  = today.date().replace(day=1)
            last_day_prev  = first_of_this - timedelta(days=1)
            first_day_prev = last_day_prev.replace(day=1)
            return {"type": QueryType.LAST_MONTH,
                    "start_date": DateParser.date_to_yyyymmdd(first_day_prev),
                    "end_date":   DateParser.date_to_yyyymmdd(last_day_prev),
                    "label":      f"Last Month - {first_day_prev.strftime('%B %Y')}"}

        # 1️⃣3️⃣ MoM full FY
        if is_mom and not last_n_month_match and not last_n_quarter_match and not last_n_year_match:
            return mom_logic(q)

        # 1️⃣4️⃣ Last N months (with optional MoM)
        if last_n_month_match and not is_qoq and not is_yoy:
            return last_n_mom_logic(q)

        # 1️⃣5️⃣ This quarter
        if "this quarter" in q or "current quarter" in q:
            cq = DateParser.get_fy_quarter(today.month)
            if cq == 1:   s = datetime(current_fy, 4, 1)
            elif cq == 2: s = datetime(current_fy, 7, 1)
            elif cq == 3: s = datetime(current_fy, 10, 1)
            else:         s = datetime(current_fy+1, 1, 1)
            return {"type": QueryType.THIS_QUARTER,
                    "start_date": DateParser.date_to_yyyymmdd(s.date()),
                    "end_date":   DateParser.today_yyyymmdd(),
                    "label":      f"This Quarter FY{current_fy} Q{cq}"}

        # 1️⃣6️⃣ Last quarter
        if "last quarter" in q or "previous quarter" in q:
            cq   = DateParser.get_fy_quarter(today.month)
            lq   = cq - 1 if cq > 1 else 4
            lqfy = current_fy if cq > 1 else current_fy - 1
            if lq == 1:   s, e = datetime(lqfy,4,1),    datetime(lqfy,6,30)
            elif lq == 2: s, e = datetime(lqfy,7,1),    datetime(lqfy,9,30)
            elif lq == 3: s, e = datetime(lqfy,10,1),   datetime(lqfy,12,31)
            else:         s, e = datetime(lqfy+1,1,1),  datetime(lqfy+1,3,31)
            return {"type": QueryType.LAST_QUARTER,
                    "start_date": DateParser.date_to_yyyymmdd(s.date()),
                    "end_date":   DateParser.date_to_yyyymmdd(e.date()),
                    "label":      f"Last Quarter FY{lqfy} Q{lq}"}

        # 1️⃣7️⃣ Last N quarters
        if last_n_quarter_match and not is_yoy:
            return last_n_quarter_mom_qoq(q)

        # 1️⃣8️⃣ YoY / year wise (with last_n_year)
        if last_n_year_match:
            return last_n_year_mom_qoq_yoy(q)

        # 1️⃣9️⃣ Year range (2022 to 2025)
        yr = year_range_logic(q)
        if yr:
            return yr

        # 2️⃣0️⃣ Multiple years (2023 and 2025)
        ya = detect_year_and(q)
        if ya:
            return ya

        # 2️⃣2️⃣ This year / this FY
        if "this year" in q or "this fy" in q or "current fy" in q or "current year" in q:
            s, _ = DateParser.get_fy_start_end(current_fy)
            return {"type": QueryType.THIS_YEAR, "start_date": s,
                    "end_date": DateParser.today_yyyymmdd(), "label": f"FY{current_fy} (YTD)"}

        # 2️⃣3️⃣ Last year / previous year
        if "last year" in q or "previous year" in q or "previous fy" in q or "last fy" in q:
            s, e = DateParser.get_fy_start_end(current_fy - 1)
            return {"type": QueryType.LAST_YEAR, "start_date": s, "end_date": e,
                    "label": f"FY{current_fy - 1}"}

        # 2️⃣4️⃣ FY format (fy2024, fy 2025)
        # If a month name is ALSO present (e.g. "sep fy2024"), skip here and let
        # Step 28 handle it — it will use the FY year to resolve the correct calendar year.
        _month_pat = r'\b(' + '|'.join(DateParser.MONTH_MAP.keys()) + r')\b'
        _has_month_in_q = bool(re.search(_month_pat, q, re.IGNORECASE))
        if "fy" in q and not _has_month_in_q:
            for word in q.split():
                if word.startswith("fy") and word[2:].isdigit():
                    year = int(word[2:]) if len(word[2:]) == 4 else 2000 + int(word[2:])
                    s, e = DateParser.get_fy_start_end(year)
                    return {"type": QueryType.SPECIFIC_YEAR, "year": year,
                            "start_date": s, "end_date": e, "label": f"FY{year}"}

        # 2️⃣5️⃣ Specific date or date range
        date_intent = DateParser.parse_specific_date_or_range(q)
        if date_intent:
            return date_intent

        # 2️⃣6️⃣ From/after/since
        from_intent = DateParser.parse_from_date(q)
        if from_intent:
            return from_intent

        # 2️⃣7️⃣ FY till month
        till_month = DateParser.parse_fy_till_month(q)
        if till_month:
            return till_month

        # 2️⃣8️⃣ Specific month (april, may 2025)
        month_pattern = r'\b(' + '|'.join(DateParser.MONTH_MAP.keys()) + r')\b'
        month_match   = re.search(month_pattern, q, re.IGNORECASE)

        def _contains_day(text):
            return bool(re.search(r'\b\d{1,2}(st|nd|rd|th)?\b', text))

        found_month = None
        if month_match and not _contains_day(q):
            found_month = DateParser.MONTH_MAP[month_match.group(1).lower()]

        found_year  = None
        year_match  = re.search(r'\b(20\d{2})\b', q)
        if year_match:
            found_year = int(year_match.group(1))
        # Also extract year from "fy2024" / "fy 2025" attached format (no word boundary before digits)
        if not found_year:
            fy_yr_match = re.search(r'\bfy\s*(20\d{2})\b', q)
            if fy_yr_match:
                found_year = int(fy_yr_match.group(1))

        if found_month:
            fy_shift = -1 if re.search(r"\b(last|previous)\s+year\b", q) else 0
            if found_year:
                # If year was extracted from an "fyYYYY" token, apply FY calendar logic:
                # months Apr-Dec belong to FY start year; Jan-Mar belong to FY start year + 1
                _from_fy_token = bool(re.search(r'\bfy\s*' + str(found_year) + r'\b', q))
                if _from_fy_token and found_month < 4:
                    year = found_year + 1   # e.g. jan fy2024 -> Jan 2025
                else:
                    year = found_year       # e.g. sep fy2024 -> Sep 2024
            else:
                eff_fy = current_fy + fy_shift
                # FY logic: if month >= 4 use FY start year, else use FY end year
                year   = eff_fy if found_month >= 4 else eff_fy + 1
            start_dt = datetime(year, found_month, 1)
            _, ld    = monthrange(year, found_month)
            end_dt   = datetime(year, found_month, ld)
            return {"type": QueryType.DATE_RANGE,
                    "start_date": DateParser.date_to_yyyymmdd(start_dt.date()),
                    "end_date":   DateParser.date_to_yyyymmdd(end_dt.date()),
                    "label":      start_dt.strftime("%B %Y")}

        if found_year:
            s, e = DateParser.get_fy_start_end(found_year)
            return {"type": QueryType.SPECIFIC_YEAR, "year": found_year,
                    "start_date": s, "end_date": e, "label": f"FY{found_year}"}

        return None


# ============================================================================
# SQL GENERATOR  — event-specific, uses date_parse (NOT YYYYMMDD BETWEEN)
# NOTE: Event created_date_c is VARCHAR DD-MM-YYYY, not INTEGER like lead/opp.
# All date comparisons use: date(TRY(date_parse("created_date_c", '%d-%m-%Y')))
# ============================================================================
class EventSQLGenerator:

    # ── Convert YYYYMMDD internal format → SQL date literal ──────────────────
    @staticmethod
    def yyyymmdd_to_date_literal(s: str) -> str:
        """Convert YYYYMMDD string → DATE 'YYYY-MM-DD' literal."""
        if len(s) != 8 or not s.isdigit():
            return f"DATE '{s}'"
        return f"DATE '{s[:4]}-{s[4:6]}-{s[6:8]}'"

    # ── Build the date filter WHERE condition ─────────────────────────────────
    @staticmethod
    def build_date_filter(date_range: Optional[Tuple[str, str]], date_column: str = "created_date_c") -> Optional[str]:
        """
        Event date column is VARCHAR DD-MM-YYYY.
        Comparisons use: date(TRY(date_parse("col", '%d-%m-%Y')))
        BETWEEN DATE 'YYYY-MM-DD' AND DATE 'YYYY-MM-DD'
        """
        if not date_range:
            return None
        start, end = date_range
        date_expr  = f'date(TRY(date_parse("{date_column}", \'%d-%m-%Y\')))'
        if start and end:
            s_lit = EventSQLGenerator.yyyymmdd_to_date_literal(start)
            e_lit = EventSQLGenerator.yyyymmdd_to_date_literal(end)
            return f"{date_expr} BETWEEN {s_lit} AND {e_lit}"
        elif start:
            s_lit = EventSQLGenerator.yyyymmdd_to_date_literal(start)
            return f"{date_expr} >= {s_lit}"
        elif end:
            e_lit = EventSQLGenerator.yyyymmdd_to_date_literal(end)
            return f"{date_expr} <= {e_lit}"
        return None

    # ── Build appointment filter condition ────────────────────────────────────
    @staticmethod
    def build_appointment_condition(appointment_filter: Optional[str]) -> Optional[str]:
        if not appointment_filter:
            return None
        # Skip comparison types — handled by comparison generator
        if appointment_filter.startswith("comparison:") or appointment_filter.startswith("subject_time_comparison:"):
            return None
        cond, _ = EventIntentDetector.get_status_sql_condition(appointment_filter)
        return cond

    # ── Build WHERE clause ────────────────────────────────────────────────────
    @staticmethod
    def build_where_clause(
        filters:            Dict[str, Any],
        date_filter:        Optional[str]  = None,
        appointment_cond:   Optional[str]  = None,
    ) -> str:
        conditions = []

        # 1. Date filter first
        if date_filter:
            conditions.append(date_filter)

        # 2. Appointment/meeting status condition
        if appointment_cond:
            conditions.append(appointment_cond)

        # 3. Column filters
        for col, values in filters.items():
            effective_col = EventColumnMetadata.COLUMN_FALLBACKS.get(col, col)
            col_conditions = []
            value_list     = values if isinstance(values, list) else [values]

            for v in value_list:
                v_str = str(v).lower().strip()

                # Owner name — exact for full name, LIKE for partial
                if effective_col == "ownername_c":
                    if " " in v_str:
                        col_conditions.append(f'LOWER("{effective_col}") = \'{v_str}\'')
                    else:
                        col_conditions.append(f'LOWER("{effective_col}") LIKE \'%{v_str}%\'')
                    continue

                # project_c — always LIKE (never =)
                if effective_col == "project_c":
                    like_val = v_str.replace(" ", "%")
                    col_conditions.append(f'LOWER("{effective_col}") LIKE \'%{like_val}%\'')
                    continue

                # product_category_c — always LIKE
                if col == "product_category_c":
                    col_conditions.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')
                    continue

                # subject_c — LIKE
                if col == "subject_c":
                    col_conditions.append(f'"{col}" = \'{v}\'')
                    continue

                # Default: LIKE with wildcards
                col_conditions.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')

            if col_conditions:
                if len(col_conditions) == 1:
                    conditions.append(col_conditions[0])
                else:
                    conditions.append("(" + " OR ".join(col_conditions) + ")")

        return "WHERE " + " AND ".join(conditions) if conditions else ""

    # ── Period expressions for month/quarter/year grouping ────────────────────
    @staticmethod
    def build_period_select(period_type: Optional[str], date_column: str = "created_date_c") -> Tuple[Optional[str], Optional[str]]:
        """Return (select_expr_with_alias, group_by_expr)."""
        if not period_type:
            return None, None

        date_parse_expr = f'TRY(date_parse("{date_column}", \'%d-%m-%Y\'))'

        if period_type == "month":
            yr_expr   = f"CAST(year({date_parse_expr}) AS VARCHAR)"
            mo_expr   = f"LPAD(CAST(month({date_parse_expr}) AS VARCHAR), 2, '0')"
            time_expr = f"({yr_expr} || '-' || {mo_expr})"
            return f"{time_expr} AS period", time_expr

        elif period_type == "quarter":
            month_expr = f"MONTH({date_parse_expr})"
            year_expr  = f"YEAR({date_parse_expr})"
            q_expr = (
                f"CASE "
                f"WHEN {month_expr} IN (4,5,6) THEN 'Q1' "
                f"WHEN {month_expr} IN (7,8,9) THEN 'Q2' "
                f"WHEN {month_expr} IN (10,11,12) THEN 'Q3' "
                f"WHEN {month_expr} IN (1,2,3) THEN 'Q4' "
                f"END"
            )
            fy_expr = (
                f"CASE WHEN {month_expr} >= 4 THEN {year_expr} "
                f"ELSE {year_expr} - 1 END"
            )
            time_expr = f"CONCAT({q_expr}, ' FY', CAST({fy_expr} AS VARCHAR))"
            return f"{time_expr} AS period", time_expr

        elif period_type == "year":
            date_parse_expr2 = f'TRY(date_parse("{date_column}", \'%d-%m-%Y\'))'
            fy_expr = (
                f"CASE "
                f"WHEN month({date_parse_expr2}) >= 4 "
                f"THEN CAST(year({date_parse_expr2}) AS VARCHAR) || '-' || CAST(year({date_parse_expr2}) + 1 AS VARCHAR) "
                f"ELSE CAST(year({date_parse_expr2}) - 1 AS VARCHAR) || '-' || CAST(year({date_parse_expr2}) AS VARCHAR) "
                f"END"
            )
            return f"{fy_expr} AS period", fy_expr

        return None, None

    # ── Core SQL builder ──────────────────────────────────────────────────────
    @staticmethod
    def generate_sql(
        catalog:            str,
        schema:             str,
        table:              str,
        group_by:           List[str],
        filters:            Dict[str, Any],
        date_range:         Optional[Tuple[str, str]] = None,
        date_column:        str = "created_date_c",
        period_type:        Optional[str] = None,
        appointment_filter: Optional[str] = None,
    ) -> str:
        select_parts   = []
        group_by_parts = []

        # If caller filtered by owner but did not request grouping, include owner in GROUP BY
        if "ownername_c" in filters and "ownername_c" not in group_by:
            group_by = list(group_by) + ["ownername_c"]

        # Period column
        period_sel, period_grp = EventSQLGenerator.build_period_select(period_type, date_column)
        if period_sel:
            select_parts.append(period_sel)
            group_by_parts.append(period_grp)

        # User-requested group_by columns
        select_parts.extend([f'"{col}"' for col in group_by])
        group_by_parts.extend([f'"{col}"' for col in group_by])

        # Aggregation — events always use COUNT(*)
        select_parts.append('COUNT(*) AS "Event Count"')

        select_clause = "SELECT " + ", ".join(select_parts)
        from_clause   = f'FROM "{catalog}"."{schema}"."{table}"'

        date_filter       = EventSQLGenerator.build_date_filter(date_range, date_column)
        appointment_cond  = EventSQLGenerator.build_appointment_condition(appointment_filter)
        where_clause      = EventSQLGenerator.build_where_clause(
            filters=filters, date_filter=date_filter, appointment_cond=appointment_cond
        )
        group_by_clause   = ("GROUP BY " + ", ".join(group_by_parts)) if group_by_parts else ""

        sql = "\n".join(filter(None, [select_clause, from_clause, where_clause, group_by_clause]))
        return sql.strip()

    # ── Subject-time comparison SQL (e.g., meeting done this year vs last year) ─
    @staticmethod
    def generate_subject_time_comparison_sql(
        catalog: str, schema: str, table: str,
        status_key: str, period_type: str,
        filters: Dict[str, Any],
    ) -> str:
        status_cond, _ = EventIntentDetector.get_status_sql_condition(status_key)
        if not status_cond:
            status_cond = "1=1"

        today      = datetime.today()
        current_fy = DateParser.get_current_fy()
        date_parse = 'date(TRY(date_parse("created_date_c", \'%d-%m-%Y\')))'
        from_clause = f'FROM "{catalog}"."{schema}"."{table}"'

        # Extra filters
        extra_conds = []
        for col, values in filters.items():
            v_str = str(values[0] if isinstance(values, list) else values).lower()
            if col == "project_c":
                extra_conds.append(f'LOWER("{col}") LIKE \'%{v_str.replace(" ","%")}%\'')
            elif col == "product_category_c":
                extra_conds.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')
        extra_and = (" AND " + " AND ".join(extra_conds)) if extra_conds else ""

        if period_type == "year":
            fy_s = f"{current_fy}-04-01"
            fy_e_last = f"{current_fy}-03-31"
            fy_s_last = f"{current_fy-1}-04-01"
            sql = f"""SELECT
    CASE
        WHEN {date_parse} BETWEEN DATE '{fy_s}' AND current_date THEN 'this_year'
        WHEN {date_parse} BETWEEN DATE '{fy_s_last}' AND DATE '{fy_e_last}' THEN 'last_year'
    END AS period,
    COUNT(*) AS "Event Count"
{from_clause}
WHERE {status_cond}
    AND (
        {date_parse} BETWEEN DATE '{fy_s_last}' AND DATE '{fy_e_last}'
        OR {date_parse} BETWEEN DATE '{fy_s}' AND current_date
    ){extra_and}
GROUP BY 1
ORDER BY period DESC"""

        elif period_type == "month":
            sql = f"""SELECT
    CASE
        WHEN month(TRY(date_parse("created_date_c", '%d-%m-%Y'))) = month(current_date)
            AND year(TRY(date_parse("created_date_c", '%d-%m-%Y'))) = year(current_date)
        THEN 'this_month'
        WHEN month(TRY(date_parse("created_date_c", '%d-%m-%Y'))) = month(date_add('month', -1, current_date))
            AND year(TRY(date_parse("created_date_c", '%d-%m-%Y'))) = year(date_add('month', -1, current_date))
        THEN 'last_month'
    END AS period,
    COUNT(*) AS "Event Count"
{from_clause}
WHERE {status_cond}
    AND {date_parse} BETWEEN date_trunc('month', date_add('month', -1, current_date)) AND current_date{extra_and}
GROUP BY 1
ORDER BY period DESC"""

        elif period_type == "quarter":
            cq   = DateParser.get_fy_quarter(today.month)
            lq   = cq - 1 if cq > 1 else 4
            lqfy = current_fy if cq > 1 else current_fy - 1
            if cq == 1:   tqs, tqe = f"{current_fy}-04-01", f"{current_fy}-06-30"
            elif cq == 2: tqs, tqe = f"{current_fy}-07-01", f"{current_fy}-09-30"
            elif cq == 3: tqs, tqe = f"{current_fy}-10-01", f"{current_fy}-12-31"
            else:         tqs, tqe = f"{current_fy+1}-01-01", f"{current_fy+1}-03-31"
            if lq == 1:   lqs, lqe = f"{lqfy}-04-01", f"{lqfy}-06-30"
            elif lq == 2: lqs, lqe = f"{lqfy}-07-01", f"{lqfy}-09-30"
            elif lq == 3: lqs, lqe = f"{lqfy}-10-01", f"{lqfy}-12-31"
            else:         lqs, lqe = f"{lqfy+1}-01-01", f"{lqfy+1}-03-31"
            sql = f"""SELECT
    CASE
        WHEN {date_parse} BETWEEN DATE '{tqs}' AND current_date THEN 'this_quarter'
        WHEN {date_parse} BETWEEN DATE '{lqs}' AND DATE '{lqe}' THEN 'last_quarter'
    END AS period,
    COUNT(*) AS "Event Count"
{from_clause}
WHERE {status_cond}
    AND (
        {date_parse} BETWEEN DATE '{lqs}' AND DATE '{lqe}'
        OR {date_parse} BETWEEN DATE '{tqs}' AND current_date
    ){extra_and}
GROUP BY 1
ORDER BY period DESC"""
        else:
            sql = EventSQLGenerator.generate_sql(
                catalog=catalog, schema=schema, table=table,
                group_by=[], filters=filters, date_range=None,
                appointment_filter=status_key,
            )
        return sql.strip()

    # ── Status comparison UNION ALL SQL ───────────────────────────────────────
    @staticmethod
    def generate_status_comparison_sql(
        catalog: str, schema: str, table: str,
        status_keys: List[str],
        time_dimension: Optional[str],
        date_range: Optional[Tuple[str, str]],
        filters: Dict[str, Any],
    ) -> str:
        date_parse_str = 'date(TRY(date_parse("created_date_c", \'%d-%m-%Y\')))'
        from_clause    = f'FROM "{catalog}"."{schema}"."{table}"'

        # Date filter
        date_cond = None
        if date_range and date_range[0]:
            s_lit = EventSQLGenerator.yyyymmdd_to_date_literal(date_range[0])
            e_lit = EventSQLGenerator.yyyymmdd_to_date_literal(date_range[1]) if date_range[1] else "current_date"
            date_cond = f"{date_parse_str} BETWEEN {s_lit} AND {e_lit}"

        # Extra column filters
        extra_conds = []
        for col, values in filters.items():
            v_str = str(values[0] if isinstance(values, list) else values).lower()
            if col == "project_c":
                extra_conds.append(f'LOWER("{col}") LIKE \'%{v_str.replace(" ","%")}%\'')
            elif col == "product_category_c":
                extra_conds.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')
            elif col == "ownername_c":
                if " " in v_str:
                    extra_conds.append(f'LOWER("{col}") = \'{v_str}\'')
                else:
                    extra_conds.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')

        # Time dimension expressions
        dp_expr = 'TRY(date_parse("created_date_c", \'%d-%m-%Y\'))'
        if time_dimension == "month":
            time_sel  = f"CAST(year({dp_expr}) AS VARCHAR) || '-' || LPAD(CAST(month({dp_expr}) AS VARCHAR), 2, '0') AS year_month"
            time_grp  = f"CAST(year({dp_expr}) AS VARCHAR) || '-' || LPAD(CAST(month({dp_expr}) AS VARCHAR), 2, '0')"
            time_alias = "year_month"
            order_by   = "year_month ASC, category ASC"
        elif time_dimension == "year":
            fy_case   = f"CASE WHEN month({dp_expr}) >= 4 THEN CAST(year({dp_expr}) AS VARCHAR) || '-' || CAST(year({dp_expr}) + 1 AS VARCHAR) ELSE CAST(year({dp_expr}) - 1 AS VARCHAR) || '-' || CAST(year({dp_expr}) AS VARCHAR) END"
            time_sel  = f"{fy_case} AS fiscal_year"
            time_grp  = fy_case
            time_alias = "fiscal_year"
            order_by   = "fiscal_year ASC, category ASC"
        elif time_dimension == "quarter":
            q_case    = f"CASE WHEN month({dp_expr}) IN (4,5,6) THEN 'Q1' WHEN month({dp_expr}) IN (7,8,9) THEN 'Q2' WHEN month({dp_expr}) IN (10,11,12) THEN 'Q3' WHEN month({dp_expr}) IN (1,2,3) THEN 'Q4' END"
            time_sel  = f"{q_case} AS quarter"
            time_grp  = q_case
            time_alias = "quarter"
            order_by   = "quarter ASC, category ASC"
        else:
            time_sel  = None
            time_grp  = None
            time_alias = None
            order_by   = "category ASC"

        union_parts = []
        for sk in status_keys:
            sc, label = EventIntentDetector.get_status_sql_condition(sk)
            if not sc:
                continue
            conds = []
            if date_cond:
                conds.append(date_cond)
            conds.append(sc)
            conds.extend(extra_conds)
            where_str = "WHERE " + " AND ".join(conds) if conds else ""

            if time_sel and time_grp:
                union_parts.append(
                    f"SELECT {time_sel}, '{label}' AS category, COUNT(*) AS \"Event Count\"\n"
                    f"{from_clause}\n{where_str}\nGROUP BY {time_grp}"
                )
            else:
                union_parts.append(
                    f"SELECT '{label}' AS category, COUNT(*) AS \"Event Count\"\n"
                    f"{from_clause}\n{where_str}"
                )

        if not union_parts:
            return ""
        sql = "\n\nUNION ALL\n\n".join(union_parts)
        return f"(\n{sql}\n)\nORDER BY {order_by}"

    # ── YoY SQL builder (all years, no date filter) ───────────────────────────
    @staticmethod
    def generate_yoy_sql(
        catalog: str, schema: str, table: str,
        filters: Dict[str, Any],
        appointment_filter: Optional[str],
        date_range: Optional[Tuple[str, str]] = None,
    ) -> str:
        dp_expr   = 'TRY(date_parse("created_date_c", \'%d-%m-%Y\'))'
        fy_case   = (
            f"CASE WHEN month({dp_expr}) >= 4 "
            f"THEN CAST(year({dp_expr}) AS VARCHAR) || '-' || CAST(year({dp_expr}) + 1 AS VARCHAR) "
            f"ELSE CAST(year({dp_expr}) - 1 AS VARCHAR) || '-' || CAST(year({dp_expr}) AS VARCHAR) "
            f"END"
        )
        from_clause = f'FROM "{catalog}"."{schema}"."{table}"'

        conds = []
        # Year range only if explicitly given
        if date_range and date_range[0]:
            s_lit = EventSQLGenerator.yyyymmdd_to_date_literal(date_range[0])
            e_lit = EventSQLGenerator.yyyymmdd_to_date_literal(date_range[1]) if date_range[1] else "current_date"
            conds.append(f'date(TRY(date_parse("created_date_c", \'%d-%m-%Y\'))) BETWEEN {s_lit} AND {e_lit}')

        # Appointment filter
        if appointment_filter and not appointment_filter.startswith("comparison:"):
            appt_cond = EventSQLGenerator.build_appointment_condition(appointment_filter)
            if appt_cond:
                conds.append(appt_cond)

        # Column filters
        for col, values in filters.items():
            v_str = str(values[0] if isinstance(values, list) else values).lower()
            if col == "project_c":
                conds.append(f'LOWER("{col}") LIKE \'%{v_str.replace(" ","%")}%\'')
            elif col == "product_category_c":
                conds.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')
            elif col == "ownername_c":
                if " " in v_str:
                    conds.append(f'LOWER("{col}") = \'{v_str}\'')
                else:
                    conds.append(f'LOWER("{col}") LIKE \'%{v_str}%\'')

        where_clause = ("WHERE " + " AND ".join(conds)) if conds else ""
        sql = f'SELECT {fy_case} AS fiscal_year, COUNT(*) AS "Event Count"\n{from_clause}'
        if where_clause:
            sql += f'\n{where_clause}'
        sql += f'\nGROUP BY {fy_case}'
        sql += f'\nORDER BY fiscal_year ASC'
        return sql.strip()


# ============================================================================
# SQL VALIDATOR
# ============================================================================
class EventSQLValidator:
    @staticmethod
    def validate(sql: str) -> Tuple[bool, Optional[str]]:
        if not sql or not sql.strip():
            return False, "SQL query is empty"
        sql_upper = sql.upper()
        if "SELECT" not in sql_upper:
            return False, "Missing SELECT clause"
        if "FROM" not in sql_upper:
            return False, "Missing FROM clause"
        if sql.count("(") != sql.count(")"):
            return False, "Unbalanced parentheses"
        for kw in ["DROP","DELETE","TRUNCATE","ALTER","CREATE","INSERT","UPDATE"]:
            if f" {kw} " in sql_upper:
                return False, f"Dangerous keyword detected: {kw}"
        return True, "SQL is valid"


# ============================================================================
# PRESTO EXECUTION
# ============================================================================
def run_presto_query(sql: str) -> Tuple[List[Dict], Optional[List[str]], Optional[str]]:
    try:
        with prestodb.dbapi.connect(
            host=PRESTO_HOST,
            port=PRESTO_PORT,
            user=PRESTO_USER,
            catalog=PRESTO_CATALOG,
            schema=PRESTO_SCHEMA,
            http_scheme="https",
            auth=BasicAuthentication(PRESTO_USER, PRESTO_PASSWORD),
        ) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall() or []
            cols = [desc[0] for desc in cur.description] if cur.description else []
            result = [dict(zip(cols, row)) for row in rows]
            return result, cols, None
    except Exception as e:
        logger.error(f"Presto execution error: {e}", exc_info=True)
        return [], [], f"Presto execution failed: {str(e)}"


# ============================================================================
# POST-PROCESSING
# ============================================================================
def enforce_descending_order(sql: str) -> str:
    TEMPORAL_COLS = {"period","fy_year","month","quarter","year","fiscal_year","year_month"}
    sql = sql.rstrip(";").strip()
    if re.search(r'\bORDER BY\b', sql, re.IGNORECASE):
        def fix_order(m):
            parts     = [p.strip() for p in m.group(1).split(",")]
            new_parts = []
            for part in parts:
                tokens   = part.split()
                col_name = tokens[0].lower().strip('"').strip("'")
                if col_name in TEMPORAL_COLS:
                    new_parts.append(part)
                elif len(tokens) == 1:
                    new_parts.append(part + " DESC")
                elif tokens[-1].upper() == "ASC":
                    new_parts.append(" ".join(tokens[:-1]) + " DESC")
                else:
                    new_parts.append(part)
            return "ORDER BY " + ", ".join(new_parts)
        sql = re.sub(r"ORDER BY\s+(.+?)$", fix_order, sql, flags=re.IGNORECASE)
    else:
        alias_match = re.search(
            r'\bAS\s+"?(Event Count|event_count|total_count|count)\b"?',
            sql, re.IGNORECASE
        )
        if alias_match:
            sql += f' ORDER BY "{alias_match.group(1)}" DESC'
    return sql


NON_ADDITIVE_MARKERS  = ["%", ":"]
NON_ADDITIVE_KEYS     = {"fy_year","month","quarter","days","year","financial year","fiscal year","fiscal_year","year_month"}
_TIME_DIMENSION_WORDS = {"year","month","quarter","week","day","fiscal"}

def _is_additive_key(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in NON_ADDITIVE_KEYS:
        return False
    if any(w in key_lower for w in _TIME_DIMENSION_WORDS):
        return False
    return not any(m in key for m in NON_ADDITIVE_MARKERS)

def add_total_row(data: list) -> list:
    if not data or len(data) <= 1:
        return data
    total_row      = {}
    first_str_done = False
    for key in data[0].keys():
        col_values = [row.get(key) for row in data]
        non_null   = [v for v in col_values if v is not None]
        if not _is_additive_key(key):
            total_row[key] = "-" if first_str_done else "Total"
            if not first_str_done:
                first_str_done = True
            continue
        try:
            nums = [float(v) for v in non_null]
            total_row[key] = round(sum(nums), 2)
        except (ValueError, TypeError):
            if not first_str_done:
                total_row[key] = "Total"; first_str_done = True
            else:
                total_row[key] = "-"
    data.append(total_row)
    return data


# ============================================================================
# MAIN ENGINE
# ============================================================================
class EventNLToSQLEngine:
    def __init__(self):
        self.llm_detector    = LLMIntentDetector()
        self.intent_detector = EventIntentDetector()
        self.sql_validator   = EventSQLValidator()

    def process(self, request: EventSQLRequest) -> EventSQLResponse:
        question = request.question.strip()
        logger.info(f"Processing question: {question}")

        # ── Step 1: LLM extracts JSON intent ──────────────────────────────────
        llm_intent = self.llm_detector.extract_intent(question)

        # ── Step 2: Deterministic normalisation ───────────────────────────────
        llm_intent = self.intent_detector.normalize_intent(llm_intent, question)
        logger.info(f"Normalized Intent: {llm_intent}")

        # ── Step 3: Python deterministically parses date intent ───────────────
        date_intent = self.intent_detector.detect_date_intent(question)
        logger.info(f"Resolved date intent: {date_intent}")

        is_yoy = llm_intent.get("is_yoy", False)

        if date_intent is None and not is_yoy:
            fy    = DateParser.get_current_fy()
            start, end = DateParser.get_fy_start_end(fy)
            date_intent = {
                "type":       QueryType.CURRENT_FY,
                "start_date": start,
                "end_date":   end,
                "label":      f"FY{fy}",
            }

        query_type          = date_intent["type"] if date_intent else QueryType.YEAR_WISE
        appointment_filter  = llm_intent.get("appointment_filter")
        filters             = llm_intent.get("filters", {})
        group_by            = llm_intent.get("group_by", [])
        date_column         = "created_date_c"

        logger.info(f"Query type: {query_type} | Appointment filter: {appointment_filter}")
        logger.info(f"Filters: {filters} | Group by: {group_by}")

        # ── Step 4: Normalise SPECIFIC_DATE → DATE_RANGE ──────────────────────
        if date_intent and date_intent.get("type") == QueryType.SPECIFIC_DATE:
            date_intent = {
                "type":       QueryType.DATE_RANGE,
                "start_date": date_intent["date"],
                "end_date":   date_intent["date"],
                "label":      date_intent.get("label"),
            }
            query_type = QueryType.DATE_RANGE

        date_ranges = []
        final_sql   = ""

        # ── Step 5: YoY — no date filter, group by fiscal year ────────────────
        if is_yoy or (date_intent and date_intent.get("no_date_filter")):
            # Explicit year range if provided
            yoy_date_range = None
            if date_intent and not date_intent.get("no_date_filter") and date_intent.get("type") == QueryType.YEAR_WISE:
                periods = date_intent.get("periods", [])
                if periods:
                    yoy_date_range = (periods[0]["start_date"], periods[-1]["end_date"])

            # Handle comparison inside YoY
            if appointment_filter and appointment_filter.startswith("comparison:"):
                parts = appointment_filter.split(":")
                status_keys = parts[1:]
                final_sql = EventSQLGenerator.generate_status_comparison_sql(
                    catalog=request.catalog, schema=request.db_schema, table=request.table,
                    status_keys=status_keys, time_dimension="year",
                    date_range=yoy_date_range, filters=filters,
                )
            else:
                final_sql = EventSQLGenerator.generate_yoy_sql(
                    catalog=request.catalog, schema=request.db_schema, table=request.table,
                    filters=filters, appointment_filter=appointment_filter,
                    date_range=yoy_date_range,
                )
            date_ranges.append(("", "", "Year-on-Year (All Years)"))

        # ── Step 6: Subject-time comparison (meeting done this year vs last year) ─
        elif appointment_filter and appointment_filter.startswith("subject_time_comparison:"):
            parts       = appointment_filter.split(":")
            status_key  = parts[1] if len(parts) > 1 else "generic_appointment"
            period_type = parts[2] if len(parts) > 2 else "year"
            final_sql   = EventSQLGenerator.generate_subject_time_comparison_sql(
                catalog=request.catalog, schema=request.db_schema, table=request.table,
                status_key=status_key, period_type=period_type, filters=filters,
            )
            date_ranges.append(("", "", f"Subject-Time Comparison ({period_type})"))

        # ── Step 7: Status comparison (meeting booked vs meeting done) ─────────
        elif appointment_filter and appointment_filter.startswith("comparison:"):
            parts       = appointment_filter.split(":")
            status_keys = parts[1:]
            # Time dimension from comparison info
            q           = question.lower()
            time_dim    = None
            if any(k in q for k in [
                "mom", "month over month", "month-over-month", "month-on-month",
                "month wise", "month on month", "monthly", "per month", "by month"
            ]):
                time_dim = "month"
            elif any(k in q for k in [
                "qoq", "quarter over quarter", "quarter-over-quarter", "quarter-on-quarter",
                "quarter on quarter", "quarter wise", "quarterly", "per quarter", "by quarter"
            ]):
                time_dim = "quarter"
            elif any(k in q for k in ["yoy","year wise","yearly","per year","by year"]):
                time_dim = "year"

            date_range_val = None
            if date_intent:
                if date_intent.get("start_date"):
                    # Simple date range — start_date is top-level
                    date_range_val = (date_intent["start_date"], date_intent.get("end_date"))
                elif date_intent.get("periods"):
                    # Multi-period (MULTI_MONTH, MONTH_WISE, etc.) — collapse to overall span
                    periods_list = date_intent["periods"]
                    all_starts = [p["start_date"] for p in periods_list if p.get("start_date")]
                    all_ends   = [p["end_date"]   for p in periods_list if p.get("end_date")]
                    if all_starts and all_ends:
                        date_range_val = (min(all_starts), max(all_ends))
                elif date_intent.get("ranges"):
                    ranges_list = date_intent["ranges"]
                    all_starts = [r["start_date"] for r in ranges_list if r.get("start_date")]
                    all_ends   = [r["end_date"]   for r in ranges_list if r.get("end_date")]
                    if all_starts and all_ends:
                        date_range_val = (min(all_starts), max(all_ends))

            final_sql = EventSQLGenerator.generate_status_comparison_sql(
                catalog=request.catalog, schema=request.db_schema, table=request.table,
                status_keys=status_keys, time_dimension=time_dim,
                date_range=date_range_val, filters=filters,
            )
            date_ranges.append((
                date_range_val[0] if date_range_val else "",
                date_range_val[1] if date_range_val else "",
                "Status Comparison"
            ))

        # ── Step 8: Multi-period (MoM, QoQ, UNION ALL) ────────────────────────
        else:
            MULTI_PERIOD_TYPES = [
                QueryType.QUARTER_WISE, QueryType.MONTH_WISE, QueryType.YEAR_WISE,
                QueryType.MULTI_DATE_RANGE, QueryType.MONTH_RANGE_MONTH_WISE,
                QueryType.MONTH_MULTI_MONTH_WISE, QueryType.MULTI_MONTH,
            ]

            if query_type in MULTI_PERIOD_TYPES and date_intent and not date_intent.get("no_date_filter"):
                periods = (
                    date_intent.get("quarters") or
                    date_intent.get("periods")  or
                    date_intent.get("ranges")   or []
                )
                if not periods:
                    raise ValueError("No periods defined for multi-period query")

                sqls = []
                for period in periods:
                    period_start = period["start_date"]
                    period_end   = period["end_date"]
                    period_label = (
                        period.get("quarter") or
                        period.get("label")   or
                        period.get("year", f"{period_start}-{period_end}")
                    )
                    if query_type in [QueryType.MONTH_WISE, QueryType.MONTH_RANGE_MONTH_WISE,
                                       QueryType.MONTH_MULTI_MONTH_WISE, QueryType.MULTI_MONTH]:
                        period_type = "month"
                    elif query_type in [QueryType.QUARTER_WISE, QueryType.MULTI_DATE_RANGE]:
                        period_type = "quarter"
                    elif query_type == QueryType.YEAR_WISE:
                        period_type = "year"
                    else:
                        period_type = None

                    sql = EventSQLGenerator.generate_sql(
                        catalog=request.catalog, schema=request.db_schema, table=request.table,
                        group_by=group_by, filters=filters,
                        date_range=(period_start, period_end),
                        date_column=date_column,
                        period_type=period_type,
                        appointment_filter=appointment_filter,
                    )
                    sqls.append(sql)
                    date_ranges.append((period_start, period_end, period_label))

                final_sql = "\n\nUNION ALL\n\n".join(sqls)
                final_sql = f'(\n{final_sql}\n) ORDER BY "Event Count" DESC'
                if group_by:
                    final_sql += ", " + ", ".join(f'"{col}"' for col in group_by)

            else:
                # Single period
                date_range_val = None
                if date_intent and date_intent.get("start_date"):
                    date_range_val = (date_intent["start_date"], date_intent.get("end_date"))

                final_sql = EventSQLGenerator.generate_sql(
                    catalog=request.catalog, schema=request.db_schema, table=request.table,
                    group_by=group_by, filters=filters,
                    date_range=date_range_val,
                    date_column=date_column,
                    period_type=None,
                    appointment_filter=appointment_filter,
                )
                date_ranges.append((
                    date_intent.get("start_date", "") if date_intent else "",
                    date_intent.get("end_date", "")   if date_intent else "",
                    date_intent.get("label", "")       if date_intent else "",
                ))
                if group_by:
                    final_sql += f'\nORDER BY "Event Count" DESC'

        # ── Step 9: Validate → Execute → Post-process ─────────────────────────
        final_sql = enforce_descending_order(final_sql)
        is_valid, validation_msg = self.sql_validator.validate(final_sql)
        data, schema_cols, error_msg = (
            run_presto_query(final_sql) if is_valid else ([], [], validation_msg)
        )
        data   = add_total_row(data)
        totals = {k: v for row in data for k, v in row.items()
                  if isinstance(v, (int, float)) and _is_additive_key(k)}

        logger.info(f"Generated SQL:\n{final_sql}")

        return EventSQLResponse(
            status             = "success" if is_valid and not error_msg else "error",
            query_type         = query_type,
            sql                = final_sql,
            schema_metadata    = schema_cols,
            data               = data,
            execution          = {"executed": is_valid, "row_count": len(data), "error": error_msg},
            date_ranges        = [DateRange(start_date=s, end_date=e, label=l) for s, e, l in date_ranges],
            is_valid           = is_valid,
            validation_message = validation_msg,
            metadata           = {"llm_intent": llm_intent, "date_intent": date_intent},
            intent_summary     = {
                "aggregation":          ["Event Count"],
                "filters_detected":     filters,
                "group_by_columns":     group_by,
                "appointment_filter":   appointment_filter,
                "is_yoy":               is_yoy,
                "date_range":           date_intent.get("label") if date_intent else "Multiple Periods",
            },
            totals = totals,
        )


# ============================================================================
# FASTAPI APP
# ============================================================================
app    = FastAPI(title="Event NL-to-SQL Engine", version="1.0.0")
engine = EventNLToSQLEngine()


@app.post("/generate-sql", response_model=EventSQLResponse)
async def generate_sql(request: EventSQLRequest):
    try:
        return engine.process(request)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {
        "status":      "healthy",
        "version":     "1.0.0",
        "date_format": "DD-MM-YYYY VARCHAR — parsed with date(TRY(date_parse(col,'%d-%m-%Y')))",
        "note":        "Event dates are NOT YYYYMMDD integers — raw string comparison with date literals",
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("=" * 70)
    logger.info("EVENT NL-TO-SQL ENGINE")
    logger.info("LLM: intent JSON only | Python: date parsing + SQL generation")
    logger.info("Date format: DD-MM-YYYY VARCHAR → date(TRY(date_parse(...,'%d-%m-%Y')))")
    logger.info("Special: appointment_filter, YoY no-date-filter, status comparisons")
    logger.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8003)
