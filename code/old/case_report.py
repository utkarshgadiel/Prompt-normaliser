import os
from pathlib import Path
import re
import json
import logging
from string import Template
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from sqlalchemy import create_engine, MetaData, Table, Column, String, select, func, and_, case, or_
from sqlalchemy.sql import Select
from sqlalchemy.engine import URL
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference
from dateutil.relativedelta import relativedelta
import prestodb
from dotenv import load_dotenv
from typing import Any, Dict, List, Union

# Fields containing these markers will NOT be summed
NON_ADDITIVE_MARKERS = ["%", ":"]
# Known numeric label/group keys that should not be totaled
NON_ADDITIVE_KEYS = {"year","fy", "quarter", "quarter_num", "month", "financial_year", "project", "project_c", "product_category_c", "owner_name_c", "service_request_type_c"}


def _is_additive_key(key: str) -> bool:
    """
    Decide whether a column/metric should be summed.
    """
    key_lower = key.lower()
    if key_lower in NON_ADDITIVE_KEYS:
        return False
    return not any(marker in key for marker in NON_ADDITIVE_MARKERS)


def _normalize_to_rows(data: Any) -> List[Dict[str, Any]]:
    """
    Normalize different response shapes into a list of rows.
    Supported:
    - List[Dict]  -> SQL-style output
    - Dict[str, Dict] -> Funnel-style output
    """
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]

    if isinstance(data, dict):
        # Funnel-style: { "Wave City": { ...metrics... }, ... }
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())

    return []


def calculate_master_totals(data: Any) -> Dict[str, Union[int, float]]:
    """
    MASTER aggregation logic:
    - Column-wise totals
    - Sums ALL additive numeric fields
    - Ignores % and ratio fields
    - Works for ALL result shapes you showed
    """
    rows = _normalize_to_rows(data)
    totals: Dict[str, float] = {}

    for row in rows:
        for key, value in row.items():
            if not _is_additive_key(key):
                continue

            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value

    # Clean numeric formatting
    final_totals = {}
    for k, v in totals.items():
        if float(v).is_integer():
            final_totals[k] = int(v)
        else:
            final_totals[k] = round(v, 2)

    return final_totals


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("case-agent")


DATE_FORMAT = "%d-%m-%Y"

# --------------------
# Configuration
# --------------------
load_dotenv(Path(__file__).with_name(".env.crm_reporting"))
CATALOG = os.getenv("PRESTO_CATALOG")
SCHEMA = os.getenv("PRESTO_CASE_SCHEMA")
TABLE_NAME = os.getenv("TABLE_CASE")
PRESTO_USER = os.getenv("PRESTO_USERNAME")
PRESTO_PWD = os.getenv("PRESTO_PASSWORD")
PRESTO_HOST = os.getenv("PRESTO_HOST")
PRESTO_PORT = int(os.getenv("PRESTO_PORT", "31351"))
WATSONX_MODEL_ID = os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")
WATSONX_APIKEY = os.getenv("WATSONX_API_KEY")

WATSONX_URL = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID")


def make_watsonx_model() -> Optional[ModelInference]:
    """Initialize WatsonX model with proper error handling"""
    try:
        if not WATSONX_APIKEY:
            logger.error("WATSONX_APIKEY not found in environment variables")
            return None
        
        if not WATSONX_PROJECT_ID:
            logger.error("WATSONX_PROJECT_ID not found in environment variables")
            return None
        
        logger.info(f"Initializing WatsonX model: {WATSONX_MODEL_ID}")
        logger.info(f"Using URL: {WATSONX_URL}")
        logger.info(f"Using Project ID: {WATSONX_PROJECT_ID}")
        
        creds = Credentials(url=WATSONX_URL, api_key=WATSONX_APIKEY)
        model_instance = ModelInference(
            model_id=WATSONX_MODEL_ID,
            credentials=creds,
            project_id=WATSONX_PROJECT_ID,
            params={"temperature": 0.0, "max_new_tokens": 512}
        )
        
        logger.info("WatsonX model initialized successfully")
        return model_instance
        
    except Exception as e:
        logger.error(f"Failed to create WatsonX model: {e}", exc_info=True)
        return None


# Initialize the model once
model = make_watsonx_model()

if model is None:
    logger.warning("⚠️ WatsonX model is not available. Check your credentials.")
else:
    logger.info("✅ WatsonX model is ready")

# Project categories (for project_c column)
PROJECT_CATEGORIES_LIST = [
    "wave city", "wmcc sec 32", "wave estate", "wave excutive floors", "wave executive floors"
]

# Product categories (for product_category_c column)
PRODUCT_CATEGORIES_LIST = [
    "veridia", "dream homes", "eligo", "wave floor", "old plots", "executive floors",
    "plots-res", "wave garden", "eden", "new plots", "wave galleria", "wrc old plot",
    "swamanorath", "amore", "livork", "wave floor 99", "ews_p2", "ews", "prime floors",
    "wrc plots", "mayfair park", "silver", "wave floor 85", "ews_001_(410)", "hssc",
    "metro mart", "lig_001_(310)", "wrc floors", "wbt 1", "wave garden gh2-ph-2", "lig",
    "lig_p2", "armonia villa", "trucia", "gold", "elegantia", "plots-res-if", "irenia",
    "harmony greens", "veridia-4", "edenia", "vasilia", "plots-comm", "dream bazaar",
    "veridia-5", "sco", "retail", "wave residency", "veridia-3", "wbt a", "eminence",
    "comm booth", "veridia-7", "courtyard", "wave business square", "institutional",
    "veridia tower 7", "wrc fsi", "fsi", "hubb", "group housing 1", "villas",
    "plot-res-if", "veridia-6", "villa", "commercial plots", "aranyam valley",
    "wrc institutional", "institutional_we", "dream homes_we", "facebook", "golf range",
    "waved garden", "samriddhi homes"
]


engine_url = URL.create(
    "trino",
    username=PRESTO_USER,
    password=PRESTO_PWD,
    host=PRESTO_HOST,
    port=PRESTO_PORT,
    database=f"{CATALOG}/{SCHEMA}",
)
engine = create_engine(engine_url, connect_args={"http_scheme": "https"}, future=True)
logger.info("SQLAlchemy engine created for SQL generation only.")


metadata = MetaData()
case_table = Table(
    TABLE_NAME,
    metadata,
    Column("service_request_id_c", String),
    Column("lead_id_c", String),
    Column("action_taken_c", String),
    Column("description_c", String),
    Column("feedback_c", String),
    Column("opportunity_c", String),
    Column("re_assigned_by_c", String),
    Column("service_request_number_c", String),
    Column("service_request_type_c", String),
    Column("service_sub_catogery_c", String),
    Column("subject_c", String),
    Column("opened_date_c", String),
    Column("service_request_last_modified_date_c", String),
    Column("service_request_owner_c", String),
    Column("project_c", String),
    Column("product_category_c", String),
    Column("owner_name_c", String),
    Column("opportunity_name_c", String),
)


class TimeRange(BaseModel):
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = Field(default=None, alias="to")

    class Config:
        populate_by_name = True
        extra = "ignore"


class QueryIntent(BaseModel):
    action: str = Field("filter")
    metrics: List[str] = Field(default_factory=list)
    select: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    time_range: Optional[TimeRange] = None
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    comparison_groups: List[str] = Field(default_factory=list)
    specific_months: List[int] = Field(default_factory=list)

    @validator("action")
    def action_must_be_known(cls, v):
        if v not in ("filter", "aggregate", "trend"):
            raise ValueError("action must be 'filter', 'aggregate' or 'trend'")
        return v


def now_kolkata() -> datetime:
    return datetime.now(tz=ZoneInfo("Asia/Kolkata"))


def get_quarter_from_month(month: int) -> int:
    """Get financial quarter (1-4) from month (1-12)"""
    if 4 <= month <= 6:
        return 1
    elif 7 <= month <= 9:
        return 2
    elif 10 <= month <= 12:
        return 3
    else:  # 1-3
        return 4


def get_financial_year(ref_date: date) -> str:
    """Get financial year string (e.g., '2026-2027')"""
    if ref_date.month >= 4:
        return f"{ref_date.year}-{ref_date.year + 1}"
    else:
        return f"{ref_date.year - 1}-{ref_date.year}"


def get_current_fy_for_months(ref_date: date) -> int:
    """
    Return the start year of the current financial year as an integer.
    e.g. for FY 2024-25, returns 2024.
    April onwards belongs to the new FY; Jan-Mar still belongs to the previous FY.
    """
    if ref_date.month >= 4:
        return ref_date.year
    else:
        return ref_date.year - 1

def extract_specific_months_from_query(query: str) -> List[int]:
    """
    Extract specific month numbers when query mentions multiple months like 'april and july'
    Returns list of month numbers (1-12) or empty list
    
    Examples:
    - "april and july" → [4, 7]
    - "count in april and july" → [4, 7]
    - "from april to july" → [] (handled as date range, not specific months)
    """
    if not query:
        return []
        
    query_lower = query.lower()
    logger.info(f"extract_specific_months_from_query: Processing query: '{query_lower}'")
    
    # Month mapping - all variations
    month_names = {
        'january': 1, 'jan': 1, 'January': 1, 'Jan': 1,
        'february': 2, 'feb': 2, 'February': 2, 'Feb': 2,
        'march': 3, 'mar': 3, 'March': 3, 'Mar': 3,
        'april': 4, 'apr': 4, 'April': 4, 'Apr': 4,
        'may': 5, 'May': 5,
        'june': 6, 'jun': 6, 'June': 6, 'Jun': 6,
        'july': 7, 'jul': 7, 'July': 7, 'Jul': 7,
        'august': 8, 'aug': 8, 'August': 8, 'Aug': 8,
        'september': 9, 'sep': 9, 'sept': 9, 'September': 9, 'Sep': 9,
        'october': 10, 'oct': 10, 'October': 10, 'Oct': 10,
        'november': 11, 'nov': 11, 'November': 11, 'Nov': 11,
        'december': 12, 'dec': 12, 'December': 12, 'Dec': 12
    }
    
    # Extract all month numbers mentioned in the query
    found_months_set = set()
    for month_name, month_num in month_names.items():
        # Match whole words only
        if re.search(r'\b' + month_name + r'\b', query_lower):
            found_months_set.add(month_num)
            logger.debug(f"  Found month: {month_name} ({month_num})")
    
    logger.info(f"extract_specific_months_from_query: Found {len(found_months_set)} unique months: {sorted(found_months_set)}")
    
    # Need at least 2 months to be a "specific months" query
    if len(found_months_set) < 2:
        logger.info(f"extract_specific_months_from_query: Not enough months found (need 2+, got {len(found_months_set)})")
        return []
    
    # Check for date range patterns (these are NOT specific month filters)
    has_from_to = 'from' in query_lower and 'to' in query_lower
    has_between_and = 'between' in query_lower and 'and' in query_lower
    # Also detect direct month-to-month range like "apr to jun" (no "from" keyword)
    has_month_to_month = bool(re.search(
        r'\b(january|jan|January|Jan|february|feb|February|Feb|march|mar|March|Mar|april|apr|April|Apr|may|May|june|jun|June|Jun|july|jul|July|Jul|august|aug|August|Aug|september|sept|sep|September|Sep|october|oct|October|Oct|november|nov|November|Nov|december|dec|December|Dec)\b'
        r'\s*(?:to|till|until|[-\u2013\u2014])\s*'
        r'(january|jan|January|Jan|february|feb|February|Feb|march|mar|March|Mar|april|apr|April|Apr|may|May|june|jun|June|Jun|july|jul|July|Jul|august|aug|August|Aug|september|sept|sep|September|Sep|october|oct|October|Oct|november|nov|November|Nov|december|dec|December|Dec)\b',
        query_lower
    ))
    is_date_range = has_from_to or has_between_and or has_month_to_month
    
    if is_date_range:
        logger.info(f"extract_specific_months_from_query: Detected date range pattern (from/to or between/and), NOT treating as specific months filter")
        return []
    
    # This is a valid multi-month query like "april and july"
    found_months_sorted = sorted(list(found_months_set))
    logger.info(f"✅ extract_specific_months_from_query: RETURNING specific months: {found_months_sorted}")
    return found_months_sorted


def has_specific_time_reference(query: str) -> bool:
    """
    Check if query has a specific time reference like month, date, quarter, year
    Returns True if specific time is mentioned, False for generic queries
    """
    query_lower = query.lower()
    
    # Specific time indicators
    time_keywords = [
        'january','January', 'february', 'February', 'march', 'March', 'april', 'April', 'may', 'May', 'june', 'June',
        'july', 'July', 'august', 'August', 'september', 'September', 'october', 'October', 'november', 'November', 'december', 'December',
        'jan', 'Jan', 'feb', 'Feb', 'mar', 'Mar', 'apr', 'Apr', 'jun', 'Jun', 'jul', 'Jul', 'aug', 'Aug', 'sep', 'Sep', 'oct', 'Oct', 'nov', 'Nov', 'dec', 'Dec',
        'current month', 'this month', 'last month',
        'current quarter', 'this quarter', 'last quarter',
        'current year', 'this year', 'last year', 'fy', 'financial year', 'last fy', 'current fy','this fy',
        'q1', 'q2', 'q3', 'q4',
        'month on month', 'quarter on quarter', 'year on year',
        'today', 'yesterday', 'week', 'days',
        r'last\s+\d+\s+months?', r'last\s+\d+\s+days?', r'last\s+\d+\s+years?', r'previous\s+\d+\s+years?', r'past\s+\d+\s+years?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',  # Date patterns
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',  # Date patterns
        r'\b20\d{2}\b'  # Year pattern
    ]
    
    for keyword in time_keywords:
        if re.search(keyword, query_lower):
            return True
    
    return False


def extract_specific_dates_from_query(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract specific dates or years from query
    Returns (from_date, to_date) in YYYY-MM-DD format or (None, None)
    """
    query_lower = query.lower()
    
    # Pattern 1: Specific year (e.g., "2024", "in 2023")
    year_pattern = r'\b(20\d{2})\b'
    year_matches = re.findall(year_pattern, query)
    
    # Pattern 2: Month name with optional year (e.g., "January 2024", "March")
    month_names = {
        'january': 1, 'jan': 1, 'January': 1, 'Jan': 1,
        'february': 2, 'feb': 2, 'February': 2, 'Feb': 2,
        'march': 3, 'mar': 3, 'March': 3, 'Mar': 3,
        'april': 4, 'apr': 4, 'April': 4, 'Apr': 4,
        'may': 5, 'May': 5,
        'june': 6, 'jun': 6, 'June': 6, 'Jun': 6,
        'july': 7, 'jul': 7, 'July': 7, 'Jul': 7,
        'august': 8, 'aug': 8, 'August': 8, 'Aug': 8,
        'september': 9, 'sep': 9, 'sept': 9, 'September': 9, 'Sep': 9,
        'october': 10, 'oct': 10, 'October': 10, 'Oct': 10,
        'november': 11, 'nov': 11, 'November': 11, 'Nov': 11,
        'december': 12, 'dec': 12, 'December': 12, 'Dec': 12
    }
    
    # Pattern 3: Full dates (DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD)
    date_pattern1 = r'\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b'  # DD-MM-YYYY or DD/MM/YYYY
    date_pattern2 = r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b'  # YYYY-MM-DD
    
    date_matches1 = re.findall(date_pattern1, query)
    date_matches2 = re.findall(date_pattern2, query)
    
    # Handle full date ranges
    if date_matches1 and len(date_matches1) >= 1:
        dates = []
        for match in date_matches1:
            day, month, year = match
            try:
                dt = date(int(year), int(month), int(day))
                dates.append(dt.isoformat())
            except ValueError:
                continue
        
        if len(dates) >= 2:
            return (dates[0], dates[1])
        elif len(dates) == 1:
            return (dates[0], dates[0])
    
    if date_matches2 and len(date_matches2) >= 1:
        dates = []
        for match in date_matches2:
            year, month, day = match
            try:
                dt = date(int(year), int(month), int(day))
                dates.append(dt.isoformat())
            except ValueError:
                continue
        
        if len(dates) >= 2:
            return (dates[0], dates[1])
        elif len(dates) == 1:
            return (dates[0], dates[0])

    # Handle day+month without year (e.g., "15 sep", "15 september", "sep 15")
    day_month_pattern1 = r'\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\b'
    day_month_pattern2 = r'\b(january|jan|February|Feb|march|mar|april|apr|may|May|june|jun|June|Jun|july|jul|July|Jul|august|aug|August|Aug|september|sept|September|Sep|october|oct|October|Oct|november|nov|November|Nov|december|dec|December|Dec)\s*(?:of\s+)?(\d{1,2})(?:st|nd|rd|th)?\b'
    dm1 = re.search(day_month_pattern1, query_lower)
    dm2 = re.search(day_month_pattern2, query_lower)
    if dm1 or dm2:
        if dm1:
            day = int(dm1.group(1))
            month_name = dm1.group(2)
        else:
            month_name = dm2.group(1)
            day = int(dm2.group(2))
        month_name = month_name.lower()
        month_num = month_names.get(month_name)
        if month_num:
            # If an explicit year appears in the query (e.g., '15 Sep 2024'), prefer that year
            if year_matches:
                try:
                    year = int(year_matches[0])
                    logger.info(f"Day+month with explicit year found; using year {year}")
                except Exception:
                    year = None
            else:
                year = None

            # If no explicit year, choose the year so that the date falls within the current financial year
            if year is None:
                try:
                    fy_year = get_current_fy_for_months(now_kolkata().date())
                except Exception:
                    rd = now_kolkata().date()
                    fy_year = rd.year if rd.month >= 4 else rd.year - 1
                if month_num >= 4:
                    year = fy_year
                else:
                    year = fy_year + 1

            try:
                dt = date(year, month_num, day)
                return (dt.isoformat(), dt.isoformat())
            except ValueError:
                pass

    # Handle month name with year
    for month_name, month_num in month_names.items():
        if month_name in query_lower:
            month_year_pattern = rf'{month_name}\s*(20\d{{2}})'
            month_year_match = re.search(month_year_pattern, query_lower)
            
            if month_year_match:
                year = int(month_year_match.group(1))
            elif year_matches:
                year = int(year_matches[0])
            else:
                year = now_kolkata().year
            
            try:
                start_date = date(year, month_num, 1)
                if month_num == 12:
                    end_date = date(year, 12, 31)
                else:
                    end_date = date(year, month_num + 1, 1) - timedelta(days=1)
                
                return (start_date.isoformat(), end_date.isoformat())
            except ValueError:
                continue
    
    # Handle multiple explicit years like "2023, 2024, and 2025"
    unique_years = sorted({int(y) for y in year_matches})
    if len(unique_years) > 1 and not re.search(r'\b(from|to|till|until|upto)\b', query_lower):
        # Avoid mis-parsing date ranges containing explicit month spans.
        if not re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b', query_lower):
            fy_start = date(unique_years[0], 4, 1)
            fy_end = date(unique_years[-1] + 1, 3, 31)
            return (fy_start.isoformat(), fy_end.isoformat())

    # Handle specific year only (full financial year)
    if year_matches and len(year_matches) == 1:
        year = int(year_matches[0])
        fy_start = date(year, 4, 1)
        fy_end = date(year + 1, 3, 31)
        return (fy_start.isoformat(), fy_end.isoformat())
    
    return (None, None)


def get_date_range_for_query(query_lower: str, ref_date: Optional[date] = None) -> Tuple[str, str]:
    """
    Determine date range based on natural language query
    Returns (from_date, to_date) in YYYY-MM-DD format
    """
    if ref_date is None:
        ref_date = now_kolkata().date()
    
    logger.info(f"Processing date range for query: '{query_lower}' with ref_date: {ref_date}")
    
    # Check for "last N days" pattern
    last_days_pattern = r'last\s+(\d+)\s+days?'
    last_days_match = re.search(last_days_pattern, query_lower)
    if last_days_match:
        num_days = int(last_days_match.group(1))
        end_date = ref_date
        start_date = end_date - timedelta(days=num_days - 1)
        logger.info(f"Last {num_days} days: {start_date.isoformat()} to {end_date.isoformat()}")
        return (start_date.isoformat(), end_date.isoformat())

    # --- WEEK HANDLING ---
    # this week -> full current week (Monday to Sunday)
    if re.search(r"\bthis\s*/?\s*week\b", query_lower):
        start_of_week = ref_date - timedelta(days=ref_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        return (start_of_week.isoformat(), end_of_week.isoformat())

    # last week -> previous full week (Mon-Sun)
    if re.search(r"\blast\s*/?\s*week\b", query_lower):
        start_of_this_week = ref_date - timedelta(days=ref_date.weekday())
        start_last = start_of_this_week - timedelta(weeks=1)
        end_last = start_last + timedelta(days=6)
        return (start_last.isoformat(), end_last.isoformat())

    # last N weeks -> previous N full weeks (exclude current week)
    m_last_weeks = re.search(r"\blast\s+(\d+)\s+weeks?\b", query_lower)
    if m_last_weeks:
        n = int(m_last_weeks.group(1))
        start_of_this_week = ref_date - timedelta(days=ref_date.weekday())
        end = start_of_this_week - timedelta(days=1)  # end of last completed week (Sunday)
        start = end - timedelta(days=7 * n - 1)
        return (start.isoformat(), end.isoformat())
    
    # Check for specific months pattern early
    specific_months = extract_specific_months_from_query(query_lower)
    if specific_months:
        # FIX: Use current financial year, not calendar year
        if ref_date.month >= 4:
            fy_year = ref_date.year
        else:
            fy_year = ref_date.year - 1
        
        min_month = min(specific_months)
        max_month = max(specific_months)
        
        # Get date range covering all specified months in the current FY
        # Handle cross-year months (e.g., Jan-Mar are in next calendar year)
        if min_month >= 4:  # April or later in same calendar year
            start = date(fy_year, min_month, 1)
        else:  # Jan-Mar in next calendar year
            start = date(fy_year + 1, min_month, 1)
        
        if max_month >= 4:  # April or later in same calendar year
            if max_month == 12:
                end = date(fy_year, 12, 31)
            else:
                end = date(fy_year, max_month + 1, 1) - timedelta(days=1)
        else:  # Jan-Mar in next calendar year
            if max_month == 12:
                end = date(fy_year + 1, 12, 31)
            else:
                end = date(fy_year + 1, max_month + 1, 1) - timedelta(days=1)
        
        logger.info(f"Specific months {specific_months} detected for FY {fy_year}-{fy_year+1}: {start.isoformat()} to {end.isoformat()}")
        return (start.isoformat(), end.isoformat())
    
    # --- PRIORITY HANDLING: THIS MONTH / CURRENT MONTH ---
    if re.search(r"\b(this month|current month|this month's|current month's)\b", query_lower):
        start = date(ref_date.year, ref_date.month, 1)
        return (start.isoformat(), ref_date.isoformat())

    # --- PRIORITY HANDLING: LAST N YEARS ---
    m_last_years = re.search(r"\b(?:last|previous|past)\s+(\d+)\s+years?\b", query_lower)
    if m_last_years:
        num_years = int(m_last_years.group(1))
        current_fy_start_year = ref_date.year if ref_date.month >= 4 else ref_date.year - 1
        start_year = current_fy_start_year - num_years
        start = date(start_year, 4, 1)
        end = date(current_fy_start_year, 3, 31)
        logger.info(f"Last {num_years} years: {start.isoformat()} to {end.isoformat()} (previous complete FYs only)")
        return (start.isoformat(), end.isoformat())

    # --- PRIORITY HANDLING: LAST MONTH ---
    if re.search(r"\blast month\b", query_lower):
        first_of_current = date(ref_date.year, ref_date.month, 1)
        last_month_end = first_of_current - timedelta(days=1)
        last_month_start = date(last_month_end.year, last_month_end.month, 1)
        return (last_month_start.isoformat(), last_month_end.isoformat())

    # --- PRIORITY HANDLING: LAST N MONTHS ---
    m_last_months = re.search(r"\blast\s+(\d+)\s+months?\b", query_lower)
    if m_last_months:
        num_months = int(m_last_months.group(1))
        if num_months <= 0:
            # fall back to default behavior
            pass
        else:
            # End is last day of previous month (exclude current month)
            first_of_current = date(ref_date.year, ref_date.month, 1)
            end_date = first_of_current - timedelta(days=1)
            # Start is first day of the month (num_months-1) months before end_date
            start_month = (end_date.replace(day=1) - relativedelta(months=num_months - 1))
            start_date = start_month.replace(day=1)
            logger.info(f"Last {num_months} months (excluding current month): {start_date.isoformat()} to {end_date.isoformat()}")
            return (start_date.isoformat(), end_date.isoformat())
        
    # 1. Specific date or month extraction
    specific_from, specific_to = extract_specific_dates_from_query(query_lower)
    if specific_from and specific_to:
        # If query also contains a month-range, a 'from ... to ...' construct, or a quarter token,
        # prefer the more specific parsing further below instead of treating a lone month/year
        # or year as the final answer (which previously caused 'q4 2024' -> full FY and
        # 'from april to sep 2024' -> only April).
        if (re.search(r'\bfrom\b.*\b(?:to|till|until|upto)\b', query_lower)
            or re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\s*(?:to|till|until|[-–—])\s*(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b", query_lower)
            or re.search(r'\bq[1-4]\b', query_lower)):
            # Let following handlers (month-range, quarter, from...to...) decide
            pass
        else:
            return (specific_from, specific_to)

    # 2. 'from ... to ...' or 'from ...' semantics
    def _parse_fragment_to_date_range(fragment: str) -> Tuple[Optional[date], Optional[date]]:
        fragment = fragment.strip()
        if not fragment:
            return (None, None)
        # Full date DD-MM-YYYY or YYYY-MM-DD
        m1 = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b", fragment)
        if m1:
            d, m, y = m1.groups()
            try:
                dt = date(int(y), int(m), int(d))
                return (dt, dt)
            except Exception:
                pass
        m2 = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", fragment)
        if m2:
            y, m, d = m2.groups()
            try:
                dt = date(int(y), int(m), int(d))
                return (dt, dt)
            except Exception:
                pass
        # Day + month without year
        dm1 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\b", fragment)
        dm2 = re.search(r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s*(?:of\s+)?(\d{1,2})(?:st|nd|rd|th)?\b", fragment)
        if dm1 or dm2:
            if dm1:
                day = int(dm1.group(1))
                month_name = dm1.group(2)
            else:
                month_name = dm2.group(1)
                day = int(dm2.group(2))
            month_name = month_name.lower()
            month_num = {
                'january':1,'jan':1,'february':2,'feb':2,'march':3,'mar':3,'april':4,'apr':4,'may':5,'june':6,'jun':6,
                'july':7,'jul':7,'august':8,'aug':8,'september':9,'sep':9,'sept':9,'october':10,'oct':10,'november':11,'nov':11,'december':12,'dec':12
            }.get(month_name)
            if month_num:
                fy_year = get_current_fy_for_months(now_kolkata().date())
                year_for_month = fy_year if month_num >= 4 else fy_year + 1
                try:
                    dt = date(year_for_month, month_num, day)
                    return (dt, dt)
                except Exception:
                    return (None, None)
        # Month with optional year
        my = re.search(r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s*(20\d{2})?\b", fragment)
        if my:
            month_name = my.group(1).lower()
            month_num = {
                'january':1,'jan':1,'february':2,'feb':2,'march':3,'mar':3,'april':4,'apr':4,'may':5,'june':6,'jun':6,
                'july':7,'jul':7,'august':8,'aug':8,'september':9,'sep':9,'sept':9,'october':10,'oct':10,'november':11,'nov':11,'december':12,'dec':12
            }.get(month_name)
            if month_num:
                if my.group(2):
                    year = int(my.group(2))
                else:
                    fy_year = get_current_fy_for_months(now_kolkata().date())
                    year = fy_year if month_num >= 4 else fy_year + 1
                start = date(year, month_num, 1)
                if month_num == 12:
                    end = date(year, 12, 31)
                else:
                    end = date(year, month_num + 1, 1) - timedelta(days=1)
                return (start, end)
        # Year only
        yonly = re.search(r"\b(20\d{2})\b", fragment)
        if yonly:
            y = int(yonly.group(1))
            return (date(y,4,1), date(y+1,3,31))
        return (None, None)

    def _is_end_of_range_today(fragment: str) -> bool:
        if not fragment:
            return False
        fragment = fragment.lower()
        return any(tok in fragment for tok in ("date", "today", "now", "to date", "till date"))

    # Handle explicit "from ... till date" / "from ... to date" / "from ... until date" constructs
    if re.search(r"\bfrom\b.*\b(?:to|till|until|upto)\b.*\b(?:date|today|now|current date)\b", query_lower):
        m_explicit_today = re.search(
            r"\bfrom\s+(.+?)\s+(?:to|till|until|upto)\b.*\b(?:date|today|now|current date)\b",
            query_lower
        )
        if m_explicit_today:
            start_text = m_explicit_today.group(1)
            s_from, s_to = _parse_fragment_to_date_range(start_text)
            start = s_from or s_to
            if start:
                return (start.isoformat(), ref_date.isoformat())

    # Handle month-to-month ranges like "april to sep 2024" or "april 2021 to march 2023"
    m_month_range = re.search(
        r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s*(20\d{2})?\s*(?:to|till|until|[-–—])\s*(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s*(20\d{2})?\b",
        query_lower
    )
    if m_month_range:
        start_m = m_month_range.group(1).lower()
        start_year_grp = m_month_range.group(2)
        end_m = m_month_range.group(3).lower()
        end_year_grp = m_month_range.group(4)
        month_map = {
            'january':1,'jan':1,'february':2,'feb':2,'march':3,'mar':3,'april':4,'apr':4,'may':5,'june':6,'jun':6,
            'july':7,'jul':7,'august':8,'aug':8,'september':9,'sep':9,'sept':9,'october':10,'oct':10,'november':11,'nov':11,'december':12,'dec':12
        }
        start_month = month_map.get(start_m)
        end_month = month_map.get(end_m)
        if start_month and end_month:
            start_year = int(start_year_grp) if start_year_grp else None
            end_year = int(end_year_grp) if end_year_grp else None

            if start_year is not None and end_year is not None:
                pass
            elif start_year is not None and end_year is None:
                end_year = start_year if end_month >= start_month else start_year + 1
            elif start_year is None and end_year is not None:
                if start_month <= end_month:
                    start_year = end_year
                else:
                    start_year = end_year - 1
            else:
                fy_year = get_current_fy_for_months(now_kolkata().date())
                start_year = fy_year if start_month >= 4 else fy_year + 1
                end_year = fy_year if end_month >= 4 else fy_year + 1

            start = date(start_year, start_month, 1)
            if end_month == 12:
                end = date(end_year, 12, 31)
            else:
                end = date(end_year, end_month + 1, 1) - timedelta(days=1)
            return (start.isoformat(), end.isoformat())

    # --- LAST N QUARTERS ---
    m_last_quarters = re.search(r"\blast\s+(\d+)\s+quarters?\b", query_lower)
    if m_last_quarters:
        num_q = int(m_last_quarters.group(1))
        # End = last day of the previous quarter (exclude current quarter)
        # Determine current quarter (financial)
        cur_mon = ref_date.month
        cur_q = get_quarter_from_month(cur_mon)
        # compute last quarter end
        if cur_q == 1:
            last_q_end = date(ref_date.year, 3, 31)
        elif cur_q == 2:
            last_q_end = date(ref_date.year, 6, 30)
        elif cur_q == 3:
            last_q_end = date(ref_date.year, 9, 30)
        else:
            last_q_end = date(ref_date.year, 12, 31)

        # Move back (num_q - 1) quarters to get start quarter
        months_back = 3 * num_q
        start_month_dt = (last_q_end.replace(day=1) - relativedelta(months=months_back - 1))
        start = date(start_month_dt.year, start_month_dt.month, 1)
        end = last_q_end
        return (start.isoformat(), end.isoformat())

    # from...to...
    m_from_to = re.search(r"\bfrom\s+(.+?)\s+(?:to|till|until|upto)\s+(.+?)\b", query_lower)
    if m_from_to:
        sfrag = m_from_to.group(1)
        efrag = m_from_to.group(2)
        s_from, s_to = _parse_fragment_to_date_range(sfrag)
        e_from, e_to = _parse_fragment_to_date_range(efrag)
        start = s_from or s_to
        end = e_to or e_from

        # If end is missing or explicitly refers to today, interpret as today
        if _is_end_of_range_today(efrag) or end is None:
            end = ref_date

        # Align years when one fragment has explicit year and the other doesn't
        s_has_year = bool(re.search(r"\b(20\d{2})\b", sfrag))
        e_has_year = bool(re.search(r"\b(20\d{2})\b", efrag))

        def safe_date(y, m, d):
            # return a valid date clipping day to last day of month when needed
            if m == 12:
                last_day = 31
            else:
                last_day = (date(y, m+1, 1) - timedelta(days=1)).day
            return date(y, m, min(d, last_day))

        if start and end:
            if not s_has_year and e_has_year:
                ey = end.year
                sm = start.month
                sd = start.day
                if sm <= end.month:
                    start = safe_date(ey, sm, sd)
                else:
                    start = safe_date(ey - 1, sm, sd)

            if s_has_year and not e_has_year:
                sy = start.year
                em = end.month
                ed = end.day
                if start.month <= em:
                    end = safe_date(sy, em, ed)
                else:
                    end = safe_date(sy + 1, em, ed)

            return (start.isoformat(), end.isoformat())
        # If start exists but end is None (shouldn't happen now), return start..today
        if start and not end:
            return (start.isoformat(), ref_date.isoformat())
    # from ... (to date)
    m_from = re.search(r"\bfrom\s+(.+?)\b", query_lower)
    if m_from:
        sfrag = m_from.group(1)
        s_from, s_to = _parse_fragment_to_date_range(sfrag)
        start = s_from or s_to
        if start:
            return (start.isoformat(), now_kolkata().date().isoformat())
    # till/until/upto ... -> from start of current FY to specified end
    m_till = re.search(r"\b(?:till|until|upto)\s+(.+?)\b", query_lower)
    if m_till:
        efrag = m_till.group(1)
        e_from, e_to = _parse_fragment_to_date_range(efrag)
        end = e_to or e_from
        if _is_end_of_range_today(efrag):
            end = ref_date
        if end:
            if now_kolkata().date().month >= 4:
                fy_start = date(now_kolkata().date().year,4,1)
            else:
                fy_start = date(now_kolkata().date().year-1,4,1)
            return (fy_start.isoformat(), end.isoformat())
    # detect year in query
    year_pattern = r'\b(20\d{2})\b'
    year_matches = re.findall(year_pattern, query_lower)
    target_year = int(year_matches[0]) if year_matches else None

    # Detect explicit 'FY' mentions like 'fy2025', 'fy 2025' or standalone 'fy'
    fy_explicit = False
    if re.search(r'\bfy\b', query_lower) or re.search(r'\bfy\s*20\d{2}\b', query_lower) or re.search(r'\bfy20\d{2}\b', query_lower):
        fy_explicit = True

    # Decide between financial vs calendar interpretation for explicit-year quarters.
    # Default: treat explicit-year quarters as FINANCIAL quarters (Apr-Mar) unless query explicitly requests calendar.
    is_financial_flag = None
    if re.search(r'\b(calendar|calendar year|cal)\b', query_lower):
        is_financial_flag = False
    elif fy_explicit or re.search(r'\b(financial|financial year|financially)\b', query_lower) or re.search(r'\bfy\b', query_lower):
        is_financial_flag = True
    else:
        # Default behavior: financial
        is_financial_flag = True

    # If user asked explicitly for FY (e.g., 'fy2025' or 'fy 2025'), detect it
    m_fy_year = re.search(r'\bfy\s*(20\d{2})\b|\bfy(20\d{2})\b', query_lower)
    # Don't treat 'fy2025' as full-FY if quarter tokens are also present (e.g., 'q1 and q2 FY2025')
    if m_fy_year and not re.search(r'\bq[1-4]\b', query_lower):
        y = int(m_fy_year.group(1) or m_fy_year.group(2))
        fy_start = date(y, 4, 1)
        fy_end = date(y + 1, 3, 31)
        return (fy_start.isoformat(), fy_end.isoformat())

    # If fy token present but quarters also present, ensure target_year picks up the fy year (e.g., 'q1 and q2 FY2025')
    if not target_year and m_fy_year:
        target_year = int(m_fy_year.group(1) or m_fy_year.group(2))

    # Handle multiple quarter tokens like 'q1 and q2 2025'
    q_tokens = re.findall(r'\bq([1-4])\b', query_lower)
    if q_tokens and len(q_tokens) > 1:
        q_nums = sorted(set(int(x) for x in q_tokens))
        year = target_year
        if year is None:
            # Use current financial year as context
            year = ref_date.year if ref_date.month >= 4 else ref_date.year - 1

        def _quarter_start_end_fin(q, y):
            if q == 1:
                return date(y, 4, 1), date(y, 6, 30)
            elif q == 2:
                return date(y, 7, 1), date(y, 9, 30)
            elif q == 3:
                return date(y, 10, 1), date(y, 12, 31)
            else:
                return date(y + 1, 1, 1), date(y + 1, 3, 31)

        def _quarter_start_end_cal(q, y):
            if q == 1:
                return date(y, 1, 1), date(y, 3, 31)
            elif q == 2:
                return date(y, 4, 1), date(y, 6, 30)
            elif q == 3:
                return date(y, 7, 1), date(y, 9, 30)
            else:
                return date(y, 10, 1), date(y, 12, 31)

        if is_financial_flag:
            start = _quarter_start_end_fin(q_nums[0], year)[0]
            end = _quarter_start_end_fin(q_nums[-1], year)[1]
        else:
            start = _quarter_start_end_cal(q_nums[0], year)[0]
            end = _quarter_start_end_cal(q_nums[-1], year)[1]

        return (start.isoformat(), end.isoformat())

    # --- ENHANCED QUARTER LOGIC WITH YEAR DETECTION ---
    
    # Pattern 1: "Q1 2024", "Q2 of 2024", "Q3 in 2024", etc. Allow separators like '-', '/', or spaces
    # Also accept optional 'fy' prefix: "Q1 FY2024", "q2 fy 2023"
    quarter_year_pattern = r'\bq([1-4])\s*(?:of|in|for)?[\s\-_/]*\s*(?:fy\s*)?(20\d{2})\b'
    quarter_year_match = re.search(quarter_year_pattern, query_lower)
    
    # Pattern 2: "2024 Q1", "2024-Q2", etc. Also handle 'FY 2024 Q1' by accepting optional 'fy' before year
    year_quarter_pattern = r'\b(?:fy\s*)?(20\d{2})\s*[-_/]*\s*q([1-4])\b'
    year_quarter_match = re.search(year_quarter_pattern, query_lower)

    # Pattern 2b: 'FY2024 Q1' or 'fy2024q1' already covered; but keep explicit match for 'fy 2024 q1'
    fy_year_quarter_pattern = r'\bfy\s*(20\d{2})\s*[-_/]*\s*q([1-4])\b'
    fy_year_quarter_match = re.search(fy_year_quarter_pattern, query_lower)

    # Pattern 3: Just "Q1", "Q2", etc. (without year)
    quarter_only_pattern = r'\bq([1-4])\b'
    quarter_only_match = re.search(quarter_only_pattern, query_lower) if not (quarter_year_match or year_quarter_match or fy_year_quarter_match) else None

    # Decide between financial vs calendar interpretation for explicit-year quarters.
    # Default: treat explicit-year quarters as FINANCIAL quarters (Apr-Mar) unless query explicitly requests calendar.
    is_financial_flag = None
    if re.search(r'\b(calendar|calendar year|cal)\b', query_lower):
        is_financial_flag = False
    elif re.search(r'\b(financial|fy|financial year|financially)\b', query_lower):
        is_financial_flag = True
    else:
        # Default behavior: financial
        is_financial_flag = True

    # Handle quarter with explicit year
    if quarter_year_match:
        quarter_num = int(quarter_year_match.group(1))
        year = int(quarter_year_match.group(2))
        logger.info(f"Found quarter {quarter_num} with explicit year {year} (financial={is_financial_flag})")

        if is_financial_flag:
            # FINANCIAL: Q1 -> Apr-Jun (year), Q4 -> Jan-Mar (year+1)
            if quarter_num == 1:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 2:
                return (f"{year}-07-01", f"{year}-09-30")
            elif quarter_num == 3:
                return (f"{year}-10-01", f"{year}-12-31")
            else:
                return (f"{year+1}-01-01", f"{year+1}-03-31")
        else:
            # CALENDAR: Q1 -> Jan-Mar, Q4 -> Oct-Dec (same calendar year)
            if quarter_num == 1:
                return (f"{year}-01-01", f"{year}-03-31")
            elif quarter_num == 2:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 3:
                return (f"{year}-07-01", f"{year}-09-30")
            else:
                return (f"{year}-10-01", f"{year}-12-31")

    # Handle year then quarter format
    if year_quarter_match:
        year = int(year_quarter_match.group(1))
        quarter_num = int(year_quarter_match.group(2))
        logger.info(f"Found explicit year {year} quarter {quarter_num} (financial={is_financial_flag})")

        if is_financial_flag:
            if quarter_num == 1:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 2:
                return (f"{year}-07-01", f"{year}-09-30")
            elif quarter_num == 3:
                return (f"{year}-10-01", f"{year}-12-31")
            else:
                return (f"{year+1}-01-01", f"{year+1}-03-31")
        else:
            if quarter_num == 1:
                return (f"{year}-01-01", f"{year}-03-31")
            elif quarter_num == 2:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 3:
                return (f"{year}-07-01", f"{year}-09-30")
            else:
                return (f"{year}-10-01", f"{year}-12-31")
    # Handle quarter with "last year / last fy / previous year" context
    if quarter_only_match and re.search(
        r'\b(last year|last fy|previous year|previous fy)\b', query_lower
    ):
        quarter_num = int(quarter_only_match.group(1))
        # Compute the start year of last FY
        if ref_date.month >= 4:
            year = ref_date.year - 1   # Apr-Dec 2025 → last FY = 2024-25
        else:
            year = ref_date.year - 2   # Jan-Mar 2026 → last FY = 2024-25
        logger.info(
            f"Found Q{quarter_num} in 'last year/fy' context, using last FY year {year}"
        )
        if quarter_num == 1:
            return (f"{year}-04-01", f"{year}-06-30")
        elif quarter_num == 2:
            return (f"{year}-07-01", f"{year}-09-30")
        elif quarter_num == 3:
            return (f"{year}-10-01", f"{year}-12-31")
        else:
            return (f"{year+1}-01-01", f"{year+1}-03-31")
    # Handle quarter without explicit year but year mentioned elsewhere (use target_year context)
    if quarter_only_match and target_year and not quarter_year_match and not year_quarter_match:
        quarter_num = int(quarter_only_match.group(1))
        year = target_year
        logger.info(f"Found quarter {quarter_num} with context year {year} (financial={is_financial_flag})")

        if is_financial_flag:
            if quarter_num == 1:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 2:
                return (f"{year}-07-01", f"{year}-09-30")
            elif quarter_num == 3:
                return (f"{year}-10-01", f"{year}-12-31")
            else:
                return (f"{year+1}-01-01", f"{year+1}-03-31")
        else:
            # Treat as calendar quarter when explicit year is in context but 'calendar' specified
            if quarter_num == 1:
                return (f"{year}-01-01", f"{year}-03-31")
            elif quarter_num == 2:
                return (f"{year}-04-01", f"{year}-06-30")
            elif quarter_num == 3:
                return (f"{year}-07-01", f"{year}-09-30")
            else:
                return (f"{year}-10-01", f"{year}-12-31")
    
    # Handle quarter without any year (use current FY)
    if quarter_only_match:
        quarter_num = int(quarter_only_match.group(1))
        year = ref_date.year if ref_date.month >= 4 else ref_date.year - 1
        logger.info(f"Found quarter {quarter_num}, using current FY year {year}")
        
        if quarter_num == 1:
            return (f"{year}-04-01", f"{year}-06-30")
        elif quarter_num == 2:
            return (f"{year}-07-01", f"{year}-09-30")
        elif quarter_num == 3:
            return (f"{year}-10-01", f"{year}-12-31")
        else:  # Q4
            return (f"{year+1}-01-01", f"{year+1}-03-31")

    # --- CURRENT FY ---
    if "this year" in query_lower or "current year" in query_lower or "this fy" in query_lower or "current fy" in query_lower:
        if ref_date.month >= 4:
            return (f"{ref_date.year}-04-01", f"{ref_date.year+1}-03-31")
        else:
            return (f"{ref_date.year-1}-04-01", f"{ref_date.year}-03-31")

    # --- LAST FY ---
    if "last year" in query_lower or "previous year" in query_lower or "last fy" in query_lower or "previous fy" in query_lower:
        if ref_date.month >= 4:
            # If we're in Apr-Dec, last FY is the previous FY (year-1 to year)
            fy_start = f"{ref_date.year-1}-04-01"
            fy_end = f"{ref_date.year}-03-31"
        else:
            # If we're in Jan-Mar, current FY spans (year-1 to year), so last FY is (year-2 to year-1)
            fy_start = f"{ref_date.year-2}-04-01"
            fy_end = f"{ref_date.year-1}-03-31"
        logger.info(f"Last year: {fy_start} to {fy_end}")
        return (fy_start, fy_end)

    # --- CURRENT QUARTER ---
    if re.search(r"\b(this quarter|current quarter)\b", query_lower):
        current_quarter = get_quarter_from_month(ref_date.month)
        if ref_date.month >= 4:
            fy_year = ref_date.year
        else:
            fy_year = ref_date.year - 1
        
        logger.info(f"Current quarter calculation: ref_date={ref_date}, month={ref_date.month}, quarter=Q{current_quarter}, fy_year={fy_year}")
        
        if current_quarter == 1:
            start = date(fy_year, 4, 1)
            end = date(fy_year, 6, 30)
        elif current_quarter == 2:
            start = date(fy_year, 7, 1)
            end = date(fy_year, 9, 30)
        elif current_quarter == 3:
            start = date(fy_year, 10, 1)
            end = date(fy_year, 12, 31)
        else:  # Q4
            start = date(fy_year + 1, 1, 1)
            end = date(fy_year + 1, 3, 31)
        
        logger.info(f"Current quarter date range: {start.isoformat()} to {end.isoformat()}")
        return (start.isoformat(), end.isoformat())

    # --- LAST QUARTER ---
    if re.search(r"\blast quarter\b", query_lower):
        current_quarter = get_quarter_from_month(ref_date.month)
        last_quarter = 4 if current_quarter == 1 else current_quarter - 1
        
        logger.info(f"Last quarter calculation: ref_date={ref_date}, month={ref_date.month}, current_quarter=Q{current_quarter}, last_quarter=Q{last_quarter}")
        
        if ref_date.month >= 4:
            fy_year = ref_date.year
        else:
            fy_year = ref_date.year - 1
        
        # Adjust year for last quarter if it's Q4 and we're in Q1
        if current_quarter == 1:
            fy_year = fy_year - 1
        
        if last_quarter == 1:
            start = date(fy_year, 4, 1)
            end = date(fy_year, 6, 30)
        elif last_quarter == 2:
            start = date(fy_year, 7, 1)
            end = date(fy_year, 9, 30)
        elif last_quarter == 3:
            start = date(fy_year, 10, 1)
            end = date(fy_year, 12, 31)
        else:  # Q4
            start = date(fy_year + 1, 1, 1)
            end = date(fy_year + 1, 3, 31)
        
        logger.info(f"Last quarter date range: {start.isoformat()} to {end.isoformat()}")
        return (start.isoformat(), end.isoformat())

    # --- MONTH ON MONTH ---
    if "month on month" in query_lower or "monthly" in query_lower or "per month" in query_lower:
        if ref_date.month >= 4:
            return (f"{ref_date.year}-04-01", f"{ref_date.year+1}-03-31")
        else:
            return (f"{ref_date.year-1}-04-01", f"{ref_date.year}-03-31")

    # --- QUARTER ON QUARTER ---
    if "quarter on quarter" in query_lower or "quarterly" in query_lower or "qoq" in query_lower:
        if ref_date.month >= 4:
            return (f"{ref_date.year}-04-01", f"{ref_date.year+1}-03-31")
        else:
            return (f"{ref_date.year-1}-04-01", f"{ref_date.year}-03-31")

    # --- YEAR ON YEAR ---
    if "year on year" in query_lower or "yearly" in query_lower or "year-on-year" in query_lower or "yoy" in query_lower:
        # Show last 5 financial years including current year
        if ref_date.month >= 4:
            end_year = ref_date.year + 1
            start_year = ref_date.year - 4
        else:
            end_year = ref_date.year
            start_year = ref_date.year - 5
        
        fy_start = f"{start_year}-04-01"
        fy_end = f"{end_year}-03-31"
        logger.info(f"Year on year: {fy_start} to {fy_end} (5 FYs)")
        return (fy_start, fy_end)

    # --- DEFAULT = CURRENT FY ---
    # Always computed fresh from now_kolkata() so it rolls over automatically on April 1
    if ref_date.month >= 4:
        fy_from = f"{ref_date.year}-04-01"
        fy_to = f"{ref_date.year+1}-03-31"
    else:
        fy_from = f"{ref_date.year-1}-04-01"
        fy_to = f"{ref_date.year}-03-31"
    logger.info(f"Default current FY: {fy_from} to {fy_to}")
    return (fy_from, fy_to)



def extract_project_product_filters(query: str) -> Dict[str, Any]:
    query_lower = query.lower() if query else ""
    result = {
        "has_project_filter": False,
        "has_product_filter": False,
        "project_categories": [],
        "product_categories": [],
        "is_project_wise": False,
        "is_product_wise": False,
        "is_owner_wise": False,
        "is_request_type_wise": False,
        "top_n": None
    }

    result["is_project_wise"] = any(term in query_lower for term in [
        "project wise", "project-wise", "projectwise", "by project", "project categories"
    ])

    result["is_product_wise"] = any(term in query_lower for term in [
        "product wise", "product-wise", "productwise", "by product", "product categories"
    ])
    
    result["is_owner_wise"] = any(term in query_lower for term in [
        "owner wise", "owner-wise", "ownerwise", "by owner"
    ])
    
    result["is_request_type_wise"] = any(term in query_lower for term in [
        "request type", "service request type", "as per request type", "by request type"
    ])

    # TOP N
    for pattern in [r'top\s+(\d+)', r'first\s+(\d+)', r'highest\s+(\d+)', r'best\s+(\d+)', r'largest\s+(\d+)', r'most\s+(\d+)']:
        m = re.search(pattern, query_lower)
        if m:
            try:
                result["top_n"] = int(m.group(1))
                break
            except Exception:
                pass

    def normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    def tokens(text: str):
        t = normalize(text)
        return t.split() if t else []

    def tokens_in_sequence(sub_tokens, full_tokens) -> bool:
        if not sub_tokens:
            return False
        n = len(sub_tokens)
        for i in range(len(full_tokens) - n + 1):
            if full_tokens[i:i+n] == sub_tokens:
                return True
        return False

    q_tokens = tokens(query_lower)

    # Build first-token map for project alias matching.
    first_token_to_projects = {}
    for category in PROJECT_CATEGORIES_LIST:
        cat_tokens = tokens(category)
        if cat_tokens:
            first_token_to_projects.setdefault(cat_tokens[0], []).append(category)

    for category in sorted(PROJECT_CATEGORIES_LIST, key=lambda s: len(tokens(s)), reverse=True):
        cat_tokens = tokens(category)
        if tokens_in_sequence(cat_tokens, q_tokens):
            result["project_categories"].append(category)
            result["has_project_filter"] = True
            continue

        # Support short alias matching for unique first tokens like "wmcc" -> "wmcc sec 32".
        if cat_tokens and cat_tokens[0] in q_tokens:
            matching_projects = first_token_to_projects.get(cat_tokens[0], [])
            if len(matching_projects) == 1:
                result["project_categories"].append(category)
                result["has_project_filter"] = True
                continue

    for product in sorted(PRODUCT_CATEGORIES_LIST, key=lambda s: len(tokens(s)), reverse=True):
        prod_tokens = tokens(product)
        if tokens_in_sequence(prod_tokens, q_tokens):
            result["product_categories"].append(product)
            result["has_product_filter"] = True

    # If user explicitly mentioned 'product' and we found product matches, avoid treating those as project matches
    if result["product_categories"] and any(term in query_lower for term in ["product", "product wise", "by product", "product-wise"]):
        if not any(term in query_lower for term in ["project", "project wise", "by project", "project-wise"]):
            result["project_categories"] = []
            result["has_project_filter"] = False

    # Prefer product matches over project matches when tokens overlap (e.g., 'eden' vs 'eden garden')
    prod_token_sets = [set(tokens(p)) for p in result["product_categories"]]
    new_projects = []
    for p in result["project_categories"]:
        p_tokens = set(tokens(p))
        # If any product's token set is a subset of the project's token set, prefer product and drop the project
        if any(prod_tokens.issubset(p_tokens) for prod_tokens in prod_token_sets):
            logger.info(f"Preferring product match over project: dropping project '{p}' because of product overlap")
            continue
        new_projects.append(p)
    result["project_categories"] = new_projects
    result["has_project_filter"] = bool(result["project_categories"])

    return result


def apply_project_product_filters_to_intent(
    intent: QueryIntent,
    project_product_info: Dict[str, Any],
    query: str
) -> QueryIntent:
    """
    Apply project/product filters to the intent.
    """
    q_lower = query.lower() if query else ""
    
    # --- Project filters ---
    if project_product_info.get("has_project_filter"):
        cats = project_product_info.get("project_categories", [])
        if len(cats) == 1:
            intent.filters["project_c"] = {"=": cats[0]}
        elif len(cats) > 1:
            intent.filters["project_c"] = {"$in": cats}

    # --- Product filters ---
    if project_product_info.get("has_product_filter"):
        prods = project_product_info.get("product_categories", [])
        if len(prods) == 1:
            intent.filters["product_category_c"] = {"$like": prods[0]}
        elif len(prods) > 1:
            intent.filters["product_category_c"] = {"$in": prods}

    # --- Grouping when asked 'wise' ---
    if project_product_info.get("is_project_wise"):
        if "project_c" not in intent.select:
            intent.select.append("project_c")
        if "project_c" not in intent.group_by:
            intent.group_by.append("project_c")
        if intent.action == "filter":
            intent.action = "aggregate"
        if "count" not in intent.metrics:
            intent.metrics.append("count")

    if project_product_info.get("is_product_wise"):
        if "product_category_c" not in intent.select:
            intent.select.append("product_category_c")
        if "product_category_c" not in intent.group_by:
            intent.group_by.append("product_category_c")
        if intent.action == "filter":
            intent.action = "aggregate"
        if "count" not in intent.metrics:
            intent.metrics.append("count")
    
    if project_product_info.get("is_owner_wise"):
        if "owner_name_c" not in intent.select:
            intent.select.append("owner_name_c")
        if "owner_name_c" not in intent.group_by:
            intent.group_by.append("owner_name_c")
        if intent.action == "filter":
            intent.action = "aggregate"
        if "count" not in intent.metrics:
            intent.metrics.append("count")
    
    if project_product_info.get("is_request_type_wise"):
        if "service_request_type_c" not in intent.select:
            intent.select.append("service_request_type_c")
        if "service_request_type_c" not in intent.group_by:
            intent.group_by.append("service_request_type_c")
        if intent.action == "filter":
            intent.action = "aggregate"
        if "count" not in intent.metrics:
            intent.metrics.append("count")

    # Ensure quarter-wise queries get quarter grouping even when the LLM returns financial_year only.
    if re.search(r'\bquarter\s*wise\b|\bquarterwise\b|\bby quarter\b|\bquarterly\b', q_lower):
        if "quarter" not in intent.group_by:
            intent.group_by.append("quarter")
        if "quarter" not in intent.select:
            intent.select.append("quarter")
        if intent.action == "filter":
            intent.action = "aggregate"
        if "count" not in intent.metrics:
            intent.metrics.append("count")

    def get_current_financial_year_range(ref_date: date):
        """Return start and end dates of the current financial year."""
        if ref_date.month >= 4:
            fy_start = date(ref_date.year, 4, 1)
            fy_end = date(ref_date.year + 1, 3, 31)
        else:
            fy_start = date(ref_date.year - 1, 4, 1)
            fy_end = date(ref_date.year, 3, 31)
        return fy_start, fy_end
    
    def get_specific_year_fy_range(year: int):
        """Return FY range for a specific year"""
        fy_start = date(year, 4, 1)
        fy_end = date(year + 1, 3, 31)
        return fy_start, fy_end

    is_qoq = any(term in q_lower for term in ["quarter on quarter", "qoq", "quarterly"])
    
    if is_qoq:
        year_pattern = r'\b(20\d{2})\b'
        year_matches = re.findall(year_pattern, q_lower)
        
        if year_matches:
            year = int(year_matches[0])
            fy_start, fy_end = get_specific_year_fy_range(year)
            logger.info(f"Quarter-on-quarter for year {year}: {fy_start} to {fy_end}")
        else:
            ref_date = now_kolkata().date()
            fy_start, fy_end = get_current_financial_year_range(ref_date)
            logger.info(f"Quarter-on-quarter for current FY: {fy_start} to {fy_end}")
        
        intent.time_range = TimeRange(**{"from": fy_start.isoformat(), "to": fy_end.isoformat()})
        intent.action = "trend"
        
        if "year" not in intent.group_by:
            intent.group_by.append("year")
        if "quarter" not in intent.group_by:
            intent.group_by.append("quarter")
        if "count" not in intent.metrics:
            intent.metrics.append("count")

    if project_product_info.get("top_n") is not None:
        intent.limit = project_product_info["top_n"]
        if "count DESC" not in intent.order_by and "count" in intent.metrics:
            intent.order_by = ["count DESC"]
        
        if not has_specific_time_reference(query):
            ref_date = now_kolkata().date()
            fy_start, fy_end = get_current_financial_year_range(ref_date)
            intent.time_range = TimeRange(**{"from": fy_start.isoformat(), "to": fy_end.isoformat()})

    if (project_product_info.get("is_project_wise") or project_product_info.get("is_product_wise") or project_product_info.get("is_owner_wise") or project_product_info.get("is_request_type_wise")):
        if not has_specific_time_reference(query) and not is_qoq:
            ref_date = now_kolkata().date()
            fy_start, fy_end = get_current_financial_year_range(ref_date)
            intent.time_range = TimeRange(**{"from": fy_start.isoformat(), "to": fy_end.isoformat()})

    return intent


def robust_extract_json_from_text(text: Optional[Any]) -> Dict[str, Any]:
    if text is None:
        return {}
    if not isinstance(text, str):
        try:
            text = json.dumps(text)
        except Exception:
            text = str(text)
    text = re.sub(r"```json|```", "", text)
    candidates = re.findall(r"\{[\s\S]*\}", text)
    for cand in candidates:
        cand_clean = re.sub(r"(?<!\$)'", '"', cand)
        cand_clean = re.sub(r",\s*([}\]])", r"\1", cand_clean)
        try:
            parsed = json.loads(cand_clean)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def llm_question_to_intent(user_question: str) -> QueryIntent:
    if model is None:
        raise ValueError("WatsonX model is not available")
    
    today_str = now_kolkata().date().strftime("%B %d, %Y")

    prompt = INTENT_PROMPT_TEMPLATE.substitute(
        question=user_question,
        today=today_str
    )
    raw = model.generate_text(prompt=prompt)
    raw_text = raw if isinstance(raw, str) else json.dumps(raw)
    
    logger.info(f"LLM Raw Response: {raw_text}")
    
    intent_dict = robust_extract_json_from_text(raw_text)
    if not intent_dict:
        raise ValueError("LLM did not return valid JSON intent.")
    
    if 'limit' not in intent_dict or intent_dict['limit'] is None:
        intent_dict['limit'] = None
    
    if 'time_range' in intent_dict and intent_dict['time_range']:
        tr = intent_dict['time_range']
        intent_dict['time_range'] = TimeRange(**tr)
    
    # Extract specific months from query
    specific_months = extract_specific_months_from_query(user_question)
    if specific_months:
        intent_dict['specific_months'] = specific_months
        # Ensure month grouping for specific month queries
        if 'month' not in intent_dict.get('group_by', []):
            if 'group_by' not in intent_dict:
                intent_dict['group_by'] = []
            intent_dict['group_by'].append('month')
        if intent_dict.get('action') == 'filter':
            intent_dict['action'] = 'aggregate'
    
    return QueryIntent(**intent_dict)

INTENT_PROMPT_TEMPLATE = Template("""
You are an intent extractor for Salesforce Case SQL queries.
🚨 **CRITICAL INSTRUCTIONS**:
- Output **only one valid JSON object** that strictly matches the schema below.
- Do not include any explanations, markdown, or text outside the JSON.
- Do not wrap JSON in triple backticks or ```json
- Today's date is ${today}
- **IMPORTANT**: All quarters are FINANCIAL YEAR quarters (Apr-Mar), NOT calendar year quarters
- **CRITICAL**: NEVER create filters on opened_date_c field - time filtering is handled by time_range only

DATASET FIELDS:
- service_request_id_c (string, unique ID)
- lead_id_c (string, linked lead)
- action_taken_c (string, notes of action taken - use for "action taken" queries)
- description_c (string, full description)
- feedback_c (string, customer feedback like satisfied, dissatisfied, forcefully closed, non-contactable, duplicate, other/exceptional case,in follow up, channel partner/broker )
- opportunity_c (string, opportunity linked)
- re_assigned_by_c (string, person who reassigned case - use for "re assigned to/by" queries)
- service_request_number_c (string, case number)
- service_request_type_c (string, category - use for "service request type" queries)
- service_sub_catogery_c (string, sub-category - use for "service sub category" queries)
- subject_c (string, subject line - use for "subject" queries)
- opened_date_c (string, case opened date in DD-MM-YYYY format)
- service_request_last_modified_date_c (string, case last modified date)
- service_request_owner_c (string, owner of the service request - use for "service request owner" queries)
- project_c (string, project name like "wave city", "wave estate" - use for "project wise" queries)
- product_category_c (string, product category like "veridia", "dream homes" - use for "product wise" queries)
- owner_name_c (string, owner name - use for "owner wise" or "owner name" queries)
- opportunity_name_c (string, opportunity name - use for "opportunity wise" queries)

INTENT JSON SCHEMA:
{
  "action": "filter | aggregate | trend",
  "metrics": ["count"],
  "select": [list of fields to SELECT],
  "group_by": [list of fields to GROUP BY],
  "filters": {
    "field_name": {"=": value} OR {"$$like": "substring"} OR {"$$in": [values]}
  },
  "time_range": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
  "order_by": ["field ASC|DESC"],
  "limit": null,
  "comparison_groups": []
}

FIELD MAPPING RULES:
1. "action taken" → use field: action_taken_c
2. "re assigned to/by" → use field: re_assigned_by_c
3. "service request owner" → use field: service_request_owner_c
4. "service request type" or "request type" or "as per request type" → use field: service_request_type_c
5. "service sub category" → use field: service_sub_catogery_c
6. "subject" → use field: subject_c
7. "project wise" or specific project name → use field: project_c
8. "product wise" or specific product name → use field: product_category_c
9. "opportunity wise cases" or specific opportunity name → use field: opportunity_name_c
10. "owner wise cases", "owner name", "owner wise" → use field: owner_name_c (NOT service_request_owner_c)

**CRITICAL: OWNER WISE QUERIES**
- For "owner wise", "owner name", "by owner" queries → ALWAYS use owner_name_c
- Add owner_name_c to BOTH select and group_by
- Example: "owner wise cases" → {"select": ["owner_name_c"], "group_by": ["owner_name_c"]}

**YEAR WISE QUERIES**
- For "year wise", "yearly", "by year", "each year" queries:
  - Set "action": "aggregate"
  - **CRITICAL**: Add "financial_year" to group_by (NOT "year")
  - If filtering by specific years, set appropriate time_range

**LAST N YEARS QUERIES**
- For "last 3 years", "last 2 years", "last 5 years", etc:
  - Set "action": "aggregate"
  - **CRITICAL**: Add "financial_year" to group_by (NOT "year")
  - Calculate time_range from PREVIOUS complete FY backwards
  - Example: "last 3 years" (today is Jan 2026, current FY is 2026-27):
    - {"from": "2023-04-01", "to": "2026-03-31"}
    - This covers FY 2023-24, 2024-25, 2025-26 (3 complete FYs)

**MULTIPLE MONTHS IN SAME QUERY**
- If query mentions multiple specific months (e.g., "april and july", "january and march"):
  - Set "action": "aggregate"
  - Add "month" to group_by AND to select
  - Set time_range to current financial year
  - **CRITICAL**: Do NOT add any filters on opened_date_c
  - The month filtering will be handled automatically by the backend

**INLINE SERVICE REQUEST TYPE / SUB-CATEGORY FILTERS**
- If the user mentions a specific service request type value by name (e.g., \"query\", \"complaint\", \"request\") WITHOUT using structural grouping language (\"request type wise\", \"by request type\"), add a filter: `{\"service_request_type_c\": {\"$$like\": \"<value>\"}}`
- If the user mentions a sub-category value inline (e.g., \"customer payment related\", \"registry related\"), add a filter: `{\"service_sub_catogery_c\": {\"$$like\": \"<value>\"}}` — do NOT use service_request_type_c for sub-category values
- Examples:
  - \"how many query generated\" → `{\"service_request_type_c\": {\"$$like\": \"query\"}}`
  - \"customer payment related cases\" → `{\"service_sub_catogery_c\": {\"$$like\": \"customer payment\"}}`
  - \"registry related cases\" → `{\"service_sub_catogery_c\": {\"$$like\": \"registry\"}}`


- For queries like "total cases vs satisfied cases":
  - Set "action": "aggregate"
  - Add categories to "comparison_groups" list
  - Example: {"comparison_groups": ["total", "satisfied"]}

TOP N HANDLING:
- If query contains "top N", "first N", "highest N", "best N", "largest N", "most N":
  - Set "limit": N (the number specified)
  - Add "order_by": ["count DESC"] to sort by count descending
  - For grouped queries, add the grouping field to SELECT

DATE HANDLING RULES:
**CRITICAL: ALL QUARTERS ARE FINANCIAL YEAR QUARTERS (April to March)**

1. **Specific Date**: If user mentions exact date, set that date range
2. **Specific Month and Year**: If user mentions "January 2024", set range for that entire month
3. **Specific Year Only**: If user mentions just a year (e.g., "2024"), use that financial year
4. **Last N Years**: If query says "last 3 years", "last 2 years", "last 5 years":
   - Calculate from PREVIOUS complete FY backwards (exclude current FY)
   - "last 3 years" (current FY is 2026-27) → {"from": "2023-04-01", "to": "2027-03-31"}
   - This covers FY 2023-24, 2024-25, 2025-26 (3 complete FYs, excludes current 2026-27)
   - **MUST use "financial_year" in group_by, NOT "year"**
5. **Last N Days**: If query says "last 5 days", "last 7 days":
   - Set time_range from N days ago to today
6. **Current/This Month**: Current month range
7. **Last Month**: Previous month range
8. **Current/This Year**: Current financial year
9. **Last Year**: Previous financial year
10. **Current/This Quarter**: Based on current date
11. **Last Quarter**: Previous quarter

**FINANCIAL YEAR QUARTERS:**
12. **Q1 YEAR**: Apr-Jun of that year
13. **Q2 YEAR**: Jul-Sep of that year
14. **Q3 YEAR**: Oct-Dec of that year
15. **Q4 YEAR**: Jan-Mar of NEXT calendar year

16. **Month on Month** or "per month": trend with month grouping, set time_range to current FY
17. **Quarter on Quarter**: trend with quarter grouping
18. **Year on Year**: trend with financial_year grouping
19. **Generic Queries (no specific time)**: Use current financial year

IMPORTANT RULES:
- Always set "metrics": ["count"] for counting queries
- Always set time_range with proper dates
- NEVER set "limit" unless explicitly requested (like "top 5", "first 10")
- For grouping queries, add the grouping field to both "select" and "group_by"
- Use {"$$like": "substring"} for partial text matching
- Use {"=": value} for exact matching
- For owner wise queries, ALWAYS use owner_name_c field
- For year wise or "last N years" queries, ALWAYS use "financial_year" in group_by
- **CRITICAL**: NEVER add filters on opened_date_c field - use time_range only
- For specific month queries (e.g., "april and july"), only set time_range and add "month" to group_by - do NOT add date filters
- For "as per request type" or "request type" queries, use service_request_type_c and add it to both select and group_by

User question: "$question"

OUTPUT ONLY THE JSON:
""")

def build_query_from_intent(intent: QueryIntent) -> Select:
    logger.info(f"build_query_from_intent: Starting with intent.specific_months = {intent.specific_months}")
    
    sel_cols = [case_table.c[s] for s in intent.select if s in case_table.c]
    group_cols = [case_table.c[g] for g in intent.group_by if g in case_table.c]
    special_group_cols = []

    for g in intent.group_by:
        if g == 'year':
            special_group_cols.append(func.year(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT))).label('year'))
        elif g == 'month':
            special_group_cols.append(func.month(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT))).label('month'))
        elif g == 'quarter':
            month_col = func.month(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)))
            quarter_num = case(
                (month_col.between(4, 6), 1),
                (month_col.between(7, 9), 2),
                (month_col.between(10, 12), 3),
                (month_col.between(1, 3), 4),
                else_=None
            ).label('quarter_num')
            quarter_label = case(
                (month_col.between(4, 6), "Q1 (Apr-Jun)"),
                (month_col.between(7, 9), "Q2 (Jul-Sep)"),
                (month_col.between(10, 12), "Q3 (Oct-Dec)"),
                (month_col.between(1, 3), "Q4 (Jan-Mar)"),
                else_=None
            ).label('quarter')
            special_group_cols.append(func.year(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT))).label('year'))
            special_group_cols.append(quarter_num)
            special_group_cols.append(quarter_label)
        elif g == 'financial_year':
            month_expr = func.month(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)))
            year_expr = func.year(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)))
            fy_expr = case(
                (month_expr <= 3,
                 func.concat(
                     func.cast(year_expr - 1, String),
                     "-",
                     func.cast(year_expr, String)
                 )),
                (month_expr >= 4,
                 func.concat(
                     func.cast(year_expr, String),
                     "-",
                     func.cast(year_expr + 1, String)
                 )),
                else_=None
            ).label("financial_year")
            special_group_cols.append(fy_expr)

    agg_cols = []
    if intent.metrics:
        for metric in intent.metrics:
            if metric.lower() == "count":
                agg_cols.append(func.count(case_table.c.service_request_id_c).label("count"))
    
    # Include all group columns in SELECT for aggregates
    if intent.action == 'trend' and special_group_cols:
        selected_columns = special_group_cols + agg_cols + group_cols
    elif intent.metrics or intent.group_by:
        selected_columns = group_cols + special_group_cols + agg_cols
    else:
        selected_columns = sel_cols or [case_table]
    
    if not selected_columns:
        selected_columns = [case_table]

    stmt = select(*selected_columns)
    conditions = []

    # Filters
    for field, cond in intent.filters.items():
        col = case_table.c.get(field)
        if col is None:
            continue
        if isinstance(cond, dict):
            if any((op == "$like" or op == "$$like") and str(v).strip() == "" for op, v in cond.items()):
                continue
            for op, v in cond.items():
                if op == "$in":
                    if field == "product_category_c" or field == "project_c":
                        eq_conditions = []
                        for val in v:
                            try:
                                norm_val = re.sub(r"[\s_-]+", " ", str(val).lower())
                            except Exception:
                                norm_val = str(val).lower()
                            norm_col = func.regexp_replace(func.lower(col), '[\\s_-]+', ' ')
                            eq_conditions.append(norm_col == norm_val)
                        conditions.append(or_(*eq_conditions))
                    elif field in [
                        "feedback_c", "service_request_type_c", "service_sub_catogery_c",
                        "subject_c", "action_taken_c", "re_assigned_by_c",
                        "service_request_owner_c", "owner_name_c"
                    ]:
                        normalized_values = [str(item).lower() for item in v]
                        conditions.append(func.lower(col).in_(normalized_values))
                    else:
                        conditions.append(col.in_(v))
                elif op == "$like":
                    like_val = str(v).lower()
                    like_parts = [part.strip() for part in re.split(r'\b(?:and|ans|or)\b|[,;/]+', like_val) if part.strip()]
                    if len(like_parts) > 1:
                        conditions.append(or_(*[func.lower(col).like(f"%{part}%") for part in like_parts]))
                    else:
                        conditions.append(func.lower(col).like(f"%{like_val}%"))
                elif op in ("=", "eq"):
                    if field in ["feedback_c", "service_request_type_c", "service_sub_catogery_c", 
                                "subject_c", "action_taken_c", "re_assigned_by_c", 
                                "service_request_owner_c", "product_category_c", "project_c", "owner_name_c"]:
                        # For product/project we normalize and compare equality
                        if field in ("product_category_c", "project_c"):
                            try:
                                norm_val = re.sub(r"[\s_-]+", " ", str(v).lower())
                            except Exception:
                                norm_val = str(v).lower()
                            norm_col = func.regexp_replace(func.lower(col), '[\\s_-]+', ' ')
                            conditions.append(norm_col == norm_val)
                        # For owner_name_c prefer partial matches to support short names
                        elif field == "owner_name_c":
                            conditions.append(func.lower(col).like(f"%{str(v).lower()}%"))
                        else:
                            conditions.append(func.lower(col) == str(v).lower())
                    else:
                        conditions.append(col == v)
                elif op == "$gt":
                    conditions.append(col > v)
                elif op == "$lt":
                    conditions.append(col < v)
                elif op == "$gte":
                    conditions.append(col >= v)
                elif op == "$lte":
                    conditions.append(col <= v)
                elif op == "$between":
                    if len(v) == 2:
                        conditions.append(col.between(v[0], v[1]))
        else:
            if field in ["feedback_c", "service_request_type_c", "service_sub_catogery_c", 
                        "subject_c", "action_taken_c", "re_assigned_by_c", 
                        "service_request_owner_c", "product_category_c", "project_c", "owner_name_c"]:
                if field in ("product_category_c", "project_c"):
                    try:
                        norm_val = re.sub(r"[\s_-]+", " ", str(cond).lower())
                    except Exception:
                        norm_val = str(cond).lower()
                    norm_col = func.regexp_replace(func.lower(col), '[\\s_-]+', ' ')
                    conditions.append(norm_col == norm_val)
                else:
                    if field == "owner_name_c":
                        conditions.append(func.lower(col).like(f"%{str(cond).lower()}%"))
                    else:
                        conditions.append(func.lower(col) == str(cond).lower())
            else:
                conditions.append(col == cond)
    
    # Exclude NULL values from grouped columns
    for g in intent.group_by:
        if g in case_table.c:
            col = case_table.c[g]
            conditions.append(col.isnot(None))
            conditions.append(col != '')

    # Add month filtering for specific months
    if intent.specific_months:
        logger.info(f"📍 build_query_from_intent: APPLYING month filter for {intent.specific_months}")
        month_col = func.month(func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)))
        month_condition = month_col.in_(intent.specific_months)
        conditions.append(month_condition)
        logger.info(f"✅ Month filter condition added: month IN {intent.specific_months}")
    else:
        logger.info(f"ℹ️ build_query_from_intent: No specific_months to filter (intent.specific_months={intent.specific_months})")

    # Time range
    tr_from = intent.time_range.from_ if intent.time_range else None
    tr_to = intent.time_range.to if intent.time_range else None
    if tr_from and tr_to:
        conditions.append(
            func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)).between(
                func.date_parse(tr_from, "%Y-%m-%d"),
                func.date_parse(tr_to, "%Y-%m-%d")
            )
        )

    if conditions:
        stmt = stmt.where(and_(*conditions))

    # Group by
    all_group_cols = group_cols + special_group_cols
    if all_group_cols:
        stmt = stmt.group_by(*all_group_cols)

    # Order by - ✅ Auto-enforce DESC for numeric aggregates
    if intent.order_by:
        for order in intent.order_by:
            if " DESC" in order.upper():
                field = order.replace(" DESC", "").replace(" desc", "").strip()
                if field == "count":
                    stmt = stmt.order_by(func.count(case_table.c.service_request_id_c).desc())
                elif field in case_table.c:
                    stmt = stmt.order_by(case_table.c[field].desc())
            elif " ASC" in order.upper():
                field = order.replace(" ASC", "").replace(" asc", "").strip()
                if field == "count":
                    stmt = stmt.order_by(func.count(case_table.c.service_request_id_c).asc())
                elif field in case_table.c:
                    stmt = stmt.order_by(case_table.c[field].asc())
            else:
                # ✅ Default to DESC for count, keep original for other fields
                if order == "count":
                    stmt = stmt.order_by(func.count(case_table.c.service_request_id_c).desc())
                elif order in case_table.c:
                    stmt = stmt.order_by(case_table.c[order])
    else:
        # ✅ Auto-add DESC ordering for count if no order_by specified
        if intent.metrics and "count" in [m.lower() for m in intent.metrics]:
            stmt = stmt.order_by(func.count(case_table.c.service_request_id_c).desc())

    if intent.limit is not None and intent.limit > 0:
        stmt = stmt.limit(intent.limit)

    return stmt


def build_comparison_query(intent: QueryIntent, project_filter: str = None) -> List[Dict[str, Any]]:
    """Handle comparison queries"""
    results = []
    
    base_conditions = []
    tr_from = intent.time_range.from_ if intent.time_range else None
    tr_to = intent.time_range.to if intent.time_range else None
    
    if tr_from and tr_to:
        base_conditions.append(
            func.date(func.date_parse(case_table.c.opened_date_c, DATE_FORMAT)).between(
                func.date_parse(tr_from, "%Y-%m-%d"),
                func.date_parse(tr_to, "%Y-%m-%d")
            )
        )
    
    if project_filter:
        try:
            norm_val = re.sub(r"[\s_-]+", " ", str(project_filter).lower())
        except Exception:
            norm_val = str(project_filter).lower()
        norm_col = func.regexp_replace(func.lower(case_table.c.project_c), '[\\s_-]+', ' ')
        base_conditions.append(norm_col == norm_val)
    
    for group in intent.comparison_groups:
        conditions = base_conditions.copy()
        
        if group.lower() == "satisfied":
            conditions.append(func.lower(case_table.c.feedback_c) == "satisfied")
        elif group.lower() == "dissatisfied":
            conditions.append(func.lower(case_table.c.feedback_c) == "dissatisfied")

        elif group.lower() == "non-contactable":
            conditions.append(func.lower(case_table.c.feedback_c) == "non-contactable")

        elif group.lower() == "forcefully closed":
            conditions.append(func.lower(case_table.c.feedback_c) == "forcefully closed")   

        elif group.lower() == "duplicate":
            conditions.append(func.lower(case_table.c.feedback_c) == "duplicate")     
        
        stmt = select(func.count(case_table.c.service_request_id_c).label("count"))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        
        compiled_sql = str(stmt.compile(engine, compile_kwargs={"literal_binds": True}))
        logger.info(f"Comparison query for {group}: {compiled_sql}")
        rows = run_presto_query(compiled_sql)
        
        count = rows[0]['count'] if rows else 0
        results.append({"category": group.title(), "count": count})
    
    if "total" in [g.lower() for g in intent.comparison_groups]:
        stmt = select(func.count(case_table.c.service_request_id_c).label("count"))
        if base_conditions:
            stmt = stmt.where(and_(*base_conditions))
        compiled_sql = str(stmt.compile(engine, compile_kwargs={"literal_binds": True}))
        logger.info(f"Total count query: {compiled_sql}")
        rows = run_presto_query(compiled_sql)
        count = rows[0]['count'] if rows else 0
        results.insert(0, {"category": "Total", "count": count})
    
    return results


def run_presto_query(sql: str) -> List[Dict[str, Any]]:
    logger.info(f"Executing SQL: {sql}")
    try:
        with prestodb.dbapi.connect(
            host=PRESTO_HOST,
            port=PRESTO_PORT,
            user=PRESTO_USER,
            catalog=CATALOG,
            schema=SCHEMA,
            http_scheme="https",
            auth=prestodb.auth.BasicAuthentication(PRESTO_USER, PRESTO_PWD),
        ) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            logger.info(f"Query returned {len(rows)} rows")
            if cur.description is not None:
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in rows]
            return []
    except Exception as e:
        logger.error(f"Error executing SQL: {str(e)}")
        raise


# ------------------- FastAPI -------------------
app = FastAPI(title="Cases SQL Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class QueryReq(BaseModel):
    question: str


class QueryResp(BaseModel):
    intent: Dict[str, Any]
    sql: str
    data: List[Dict[str, Any]]
    totals: Dict[str, Union[int, float]]


class DebugResp(BaseModel):
    total_records: List[Dict[str, Any]]
    feedback_values: List[Dict[str, Any]]
    date_range: List[Dict[str, Any]]
    sample_data: List[Dict[str, Any]]
    date_format_check: List[Dict[str, Any]]


@app.post("/generate-sql", response_model=QueryResp)
def generate_sql(req: QueryReq):
    try:
        logger.info(f"Processing question: {req.question}")
        
        query_lower = req.question.lower()
        
        # Extract specific months FIRST
        specific_months = extract_specific_months_from_query(req.question)
        logger.info(f"🔍 extract_specific_months_from_query returned: {specific_months}")
        if specific_months:
            logger.info(f"🔍 Detected specific months query: {specific_months}")
        else:
            logger.info(f"🔍 No specific months detected")
        
        # Extract project/product information 
        project_product_info = extract_project_product_filters(req.question)
        logger.info(f"Project/Product info: {project_product_info}")
        
        # Get intent from LLM
        intent_obj = llm_question_to_intent(req.question)
        logger.info(f"Generated intent: {intent_obj.model_dump()}")

        # FORCE OVERRIDE for week queries before further intent cleanup
        if re.search(r"\bthis\s*/?\s*week\b", query_lower) or re.search(r"\blast\s*/?\s*week\b", query_lower) or re.search(r"\blast\s+\d+\s+weeks?\b", query_lower):
            from_date, to_date = get_date_range_for_query(query_lower)
            intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
            logger.info(f"Week query override: set time_range to {from_date} - {to_date}")

        # FORCE OVERRIDE for explicit 'till date' / 'to date' / 'until date' / 'upto date' ranges
        if re.search(r"\bfrom\b.*\b(?:to|till|until|upto)\b.*\b(?:date|today|now|current date)\b", query_lower) or re.search(r"\b(?:till|until|upto)\s+(?:date|today|now|current date)\b", query_lower):
            from_date, to_date = get_date_range_for_query(query_lower)
            intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
            logger.info(f"Date-range override for '... till date' query: set time_range to {from_date} - {to_date}")

        # CRITICAL: Remove any incorrect opened_date_c filters that LLM might have added
        if "opened_date_c" in intent_obj.filters:
            logger.warning(f"⚠️ Removing incorrect opened_date_c filter: {intent_obj.filters['opened_date_c']}")
            del intent_obj.filters["opened_date_c"]

        # SANITIZE LLM-PLACED FILTERS: remove vacuous filters and move date tokens from text filters into time_range
        for fkey, fval in list(intent_obj.filters.items()):
            try:
                # Normalize string representations for inspection
                candidate_vals = []
                if isinstance(fval, dict):
                    # take first value from dict filters for inspection
                    for op, vv in fval.items():
                        candidate_vals.append(str(vv).lower() if vv is not None else "")
                else:
                    candidate_vals.append(str(fval).lower())

                combined = " ".join(candidate_vals)

                # Remove vacuous LIKE filters like '' or '%' or '%%'
                if any(v.strip() == "" or re.fullmatch(r"%+", v.strip()) for v in candidate_vals):
                    logger.info(f"Removing vacuous LIKE filter on {fkey}: {fval}")
                    del intent_obj.filters[fkey]
                    continue

                # If a quarter token slipped into a non-time filter (e.g., service_request_type_c: 'q4' or '%q4%'), treat it as a quarter query
                if re.search(r"q[1-4]", combined):
                    logger.info(f"Detected quarter token in filter {fkey}={fval}; moving to time_range")
                    del intent_obj.filters[fkey]
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    # keep existing grouping but ensure metrics
                    if intent_obj.action == "filter":
                        intent_obj.action = "aggregate"
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    continue

                # If a month+year (e.g., 'april 2024') slipped into a filter, move to time_range
                month_year_pattern = r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s*(20\d{2})\b'
                if re.search(month_year_pattern, combined):
                    logger.info(f"Detected month+year token in filter {fkey}={fval}; moving to time_range")
                    del intent_obj.filters[fkey]
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    if intent_obj.action == "filter":
                        intent_obj.action = "aggregate"
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    continue

                # If a specific year like '2024' slipped into a filter, move to time_range
                if re.search(r"20\d{2}", combined) and not any(k in combined for k in ["project","product"]):
                    logger.info(f"Detected year token in filter {fkey}={fval}; moving to time_range")
                    del intent_obj.filters[fkey]
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    if intent_obj.action == "filter":
                        intent_obj.action = "aggregate"
                    if "financial_year" not in intent_obj.group_by and ("year" in intent_obj.group_by or "yearly" in query_lower or "year" in query_lower):
                        intent_obj.group_by.append("financial_year")
                        intent_obj.select.append("financial_year")
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    continue

            except Exception:
                continue

        # Apply specific months if detected
        if specific_months:
            logger.info(f"✅ Applying specific months: {specific_months}")
            intent_obj.specific_months = specific_months
            logger.info(f"✅ intent_obj.specific_months is now set to: {intent_obj.specific_months}")
            
            # Ensure month grouping
            if 'month' not in intent_obj.group_by:
                intent_obj.group_by.append('month')
            if 'month' not in intent_obj.select:
                intent_obj.select.append('month')
            
            # Set action to aggregate
            if intent_obj.action == 'filter':
                intent_obj.action = 'aggregate'
            
            # Ensure metrics includes count
            if 'count' not in intent_obj.metrics:
                intent_obj.metrics.append('count')
            
            # Set time_range to current FY
            ref_date = now_kolkata().date()
            if ref_date.month >= 4:
                fy_start = f"{ref_date.year}-04-01"
                fy_end = f"{ref_date.year + 1}-03-31"
            else:
                fy_start = f"{ref_date.year - 1}-04-01"
                fy_end = f"{ref_date.year}-03-31"
            
            intent_obj.time_range = TimeRange(**{"from": fy_start, "to": fy_end})
            logger.info(f"✅ Set FY time range: {fy_start} to {fy_end}")
            logger.info(f"✅ Final intent_obj after setting specific_months: action={intent_obj.action}, group_by={intent_obj.group_by}, specific_months={intent_obj.specific_months}")
        else:
            logger.info(f"ℹ️ No specific_months detected for this query")

        # FORCE OVERRIDE for "this quarter" queries
        if re.search(r"\b(this quarter|current quarter)\b", query_lower):
            logger.info(f"FORCING date override for 'this quarter' query")
            from_date, to_date = get_date_range_for_query(query_lower)
            intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
            logger.info(f"Overridden time range: {from_date} to {to_date}")
        
        # Apply project/product filters to intent
        intent_obj = apply_project_product_filters_to_intent(intent_obj, project_product_info, req.question)
        logger.info(f"After project/product filters: {intent_obj.model_dump()}")

        # CLEANUP: If product(s) were detected but user did NOT explicitly ask for project-wise results,
        # prefer product filters and remove accidental project filters that LLM may have added.
        try:
            explicit_project_terms = any(term in query_lower for term in ["project", "project wise", "project-wise", "projectwise", "by project", "projects", "project categories"])
            explicit_product_terms = any(term in query_lower for term in ["product", "product wise", "product-wise", "productwise", "by product"])            
            explicit_project_names = bool(project_product_info.get("project_categories"))

            if project_product_info.get("product_categories") and not explicit_project_terms and not project_product_info.get("is_project_wise"):
                if "project_c" in intent_obj.filters:
                    logger.info("Removing accidental project filter because product was specified without project context")
                    del intent_obj.filters["project_c"]
                # also remove project from select/group_by if present and not explicitly requested
                if "project_c" in intent_obj.select and not explicit_project_terms:
                    intent_obj.select = [s for s in intent_obj.select if s != "project_c"]
                if "project_c" in intent_obj.group_by and not explicit_project_terms:
                    intent_obj.group_by = [g for g in intent_obj.group_by if g != "project_c"]

            if "project_c" in intent_obj.filters and not explicit_project_names and explicit_project_terms:
                # If the user asked about projects generally but no explicit project names were detected,
                # avoid restricting the result to a wrong project list returned by the LLM.
                logger.info("Removing project filter because project request is generic and no explicit project names were detected")
                del intent_obj.filters["project_c"]

        except Exception:
            pass

        # -------------------------------------------------------------------------
        # TIME RANGE OVERRIDE SECTION
        # Priority order (highest to lowest):
        #   1. specific_months  — handled above, skips all overrides below
        #   2. Explicit time keywords in query (this quarter, last N months, etc.)
        #   3. Generic / no time reference → ALWAYS force current FY fresh from server clock
        # -------------------------------------------------------------------------

        if not specific_months:
            if not has_specific_time_reference(req.question):
                # No time reference at all → always force current FY.
                # This guarantees FY rolls over correctly on April 1 regardless of
                # what the LLM returned (which may be stale from training data).
                from_date, to_date = get_date_range_for_query(query_lower)
                intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                logger.info(f"No time reference detected — forced current FY: {from_date} to {to_date}")
            else:
                # Query has a specific time reference — apply targeted overrides below.

                # FORCE OVERRIDE for Month-on-Month queries — always use current FY
                if any(term in query_lower for term in ["month on month", "month-on-month", "mom", "per month", "monthly"]):
                    logger.info("FORCING date override for 'month on month' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    # Ensure month grouping and trend action
                    intent_obj.action = "trend"
                    if "month" not in intent_obj.group_by:
                        intent_obj.group_by.append("month")
                    if "month" not in intent_obj.select:
                        intent_obj.select.append("month")
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    logger.info(f"Overridden time range for MoM: {from_date} to {to_date}")

                # FORCE OVERRIDE for single specific month (e.g. "cases in April", "April cases")
                # Only fires when exactly ONE month is named and no date-range connectors present
                elif (not re.search(r'\b(from|between|to|till|until)\b', query_lower)
                      and not re.search(r'\bq[1-4]\b', query_lower)
                      and re.search(
                          r'\b(january|february|march|april|may|june|july|august|'
                          r'september|october|november|december|'
                          r'jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b',
                          query_lower)
                      and len(extract_specific_months_from_query(query_lower)) == 0):
                    # Single month — let get_date_range_for_query resolve it (handles with/without year)
                    logger.info("FORCING date override for single specific month query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range for single month: {from_date} to {to_date}")

                # FORCE OVERRIDE for "last N months" queries — LLM often gets this wrong
                elif re.search(r"\blast\s+\d+\s+months?\b", query_lower):
                    logger.info("FORCING date override for 'last N months' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range: {from_date} to {to_date}")

                # FORCE OVERRIDE for "last N years" queries — must use previous complete FYs
                elif re.search(r"\b(?:last|previous|past)\s+\d+\s+years?\b", query_lower):
                    logger.info("FORCING date override for 'last N years' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    if "financial_year" not in intent_obj.group_by:
                        intent_obj.group_by.append("financial_year")
                    if "financial_year" not in intent_obj.select:
                        intent_obj.select.append("financial_year")
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    intent_obj.action = "aggregate"
                    logger.info(f"Overridden time range for last N years: {from_date} to {to_date}")

                # FORCE OVERRIDE for week queries
                elif re.search(r"\bthis\s*/?\s*week\b", query_lower) or re.search(r"\blast\s*/?\s*week\b", query_lower) or re.search(r"\blast\s+\d+\s+weeks?\b", query_lower):
                    logger.info("FORCING date override for week-based query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range: {from_date} to {to_date}")

                # FORCE OVERRIDE for from/to/till/until queries
                elif re.search(r"\bfrom\b.*\b(?:to|till|until|upto)\b", query_lower):
                    logger.info("FORCING date override for from/to/till/until query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range: {from_date} to {to_date}")

                # FORCE OVERRIDE for "last quarter" queries
                elif re.search(r"\blast quarter\b", query_lower):
                    logger.info(f"FORCING date override for 'last quarter' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range: {from_date} to {to_date}")

                # FORCE OVERRIDE for "last year" queries
                elif re.search(r"\blast year\b", query_lower) or re.search(r"\bprevious year\b", query_lower) or re.search(r"\blast fy\b", query_lower) or re.search(r"\bprevious fy\b", query_lower):
                    logger.info(f"FORCING date override for 'last year' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range: {from_date} to {to_date}")

                # FORCE OVERRIDE for "year on year" queries
                elif any(term in query_lower for term in ["year on year", "year-on-year", "yearly", "year onyear", "yoy"]):
                    logger.info(f"FORCING date override for 'year on year' query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    # Ensure trend and financial_year grouping
                    intent_obj.action = "trend"
                    if "financial_year" not in intent_obj.group_by:
                        intent_obj.group_by.append("financial_year")
                    if "financial_year" not in intent_obj.select:
                        intent_obj.select.append("financial_year")
                    if "count" not in intent_obj.metrics:
                        intent_obj.metrics.append("count")
                    logger.info(f"Overridden time range for YoY: {from_date} to {to_date}")

                # # FORCE OVERRIDE for quarter queries (Q1, Q2, Q3, Q4 with or without year)
                # elif re.search(r'\bq[1-4]\b', query_lower):
                #     logger.info(f"FORCING date override for quarter query")
                #     from_date, to_date = get_date_range_for_query(query_lower)
                #     intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                #     logger.info(f"Overridden time range for quarter: {from_date} to {to_date}")

                # FORCE OVERRIDE for quarter queries (Q1, Q2, Q3, Q4 with or without year)
                elif re.search(r'\bq[1-4]\b', query_lower):
                    logger.info(f"FORCING date override for quarter query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range for quarter: {from_date} to {to_date}")
                    # If "last year/fy" context, also strip any LLM-added "last year" grouping
                    # that would conflict (financial_year group is still fine to keep)

                # FORCE OVERRIDE for specific year queries (e.g., "2024", "in 2023")
                # but only when no month/quarter keywords are also present (those are handled above)
                elif re.search(r'\b(20\d{2})\b', query_lower) and not any(
                    term in query_lower for term in [
                        "year on year", "year-on-year",
                        "january", "february", "march", "april", "may", "june",
                        "july", "august", "september", "october", "november", "december"
                    ]
                ):
                    logger.info(f"FORCING date override for specific year query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range for year: {from_date} to {to_date}")

                # FORCE OVERRIDE for month + year queries (e.g., "January 2024", "March 2023")
                elif re.search(
                    r'\b(january|february|march|april|may|june|july|august|september|october|november|december|'
                    r'jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\s+(20\d{2})\b',
                    query_lower
                ):
                    logger.info(f"FORCING date override for month+year query")
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Overridden time range for month+year: {from_date} to {to_date}")

                # Fallback: time reference detected but no range set by LLM
                elif not intent_obj.time_range or (not intent_obj.time_range.from_ and not intent_obj.time_range.to):
                    from_date, to_date = get_date_range_for_query(query_lower)
                    intent_obj.time_range = TimeRange(**{"from": from_date, "to": to_date})
                    logger.info(f"Fallback: set time range from query analysis: {from_date} to {to_date}")

        # Ensure metrics are set for counting queries
        if not intent_obj.metrics and intent_obj.action in ["aggregate", "trend"]:
            intent_obj.metrics = ["count"]
            logger.info("Added 'count' metric")
        
        logger.info(f"📋 Final intent: {intent_obj.model_dump()}")
        logger.info(f"📋 Final intent.specific_months: {intent_obj.specific_months}")
        
        # Handle comparison queries separately
        if intent_obj.comparison_groups:
            logger.info("Handling comparison query")
            project_filter = None
            if "project_c" in intent_obj.filters:
                filter_val = intent_obj.filters["project_c"]
                if isinstance(filter_val, dict) and "=" in filter_val:
                    project_filter = filter_val["="]
            
            comparison_results = build_comparison_query(intent_obj, project_filter)
            
            return QueryResp(
                intent=intent_obj.model_dump(),
                sql="Comparison query - multiple SQL statements executed",
                data=comparison_results,
                totals=calculate_master_totals(comparison_results)
            )
        
        # Regular query processing
        stmt = build_query_from_intent(intent_obj)
        compiled_sql = str(stmt.compile(engine, compile_kwargs={"literal_binds": True}))
        logger.info(f"Generated SQL: {compiled_sql}")
        
        rows = run_presto_query(compiled_sql)

        # Format results
        formatted_rows = []
        month_map = {
            1: "January", 2: "February", 3: "March", 4: "April",
            5: "May", 6: "June", 7: "July", 8: "August", 
            9: "September", 10: "October", 11: "November", 12: "December"
        }

        for r in rows:
            row = {}
            
            # Handle group by fields
            for g in intent_obj.group_by:
                if g == "financial_year":
                    row["financial_year"] = r.get("financial_year")
                elif g == "year":
                    row["year"] = r.get("year")
                elif g == "quarter":
                    quarter_num = r.get("quarter_num")
                    if quarter_num is not None:
                        row["quarter"] = r.get("quarter", f"Q{quarter_num}")
                        row["year"] = r.get("year")
                    else:
                        row["quarter"] = r.get("quarter", "Unknown")
                elif g == "month":
                    month_num = r.get("month")
                    if month_num is not None:
                        row["month"] = month_map.get(int(month_num), f"Month {month_num}")
                    else:
                        row["month"] = r.get("month", "Unknown")
                elif g == "owner_name_c":
                    row["owner_name"] = r.get("owner_name_c")
                elif g == "product_category_c":
                    row["product_category"] = r.get("product_category_c")
                elif g == "project_c":
                    row["project"] = r.get("project_c")
                elif g == "service_request_type_c":
                    row["service_request_type"] = r.get("service_request_type_c")
                else:
                    # Handle regular fields
                    row[g] = r.get(g)
            
            # Handle metrics
            for metric in intent_obj.metrics:
                if metric.lower() == "count":
                    row["count"] = r.get("count", 0)
            
            # If no group by and no metrics, include all fields
            if not intent_obj.group_by and not intent_obj.metrics:
                for key, value in r.items():
                    if key not in row:
                        row[key] = value
            
            formatted_rows.append(row)

        logger.info(f"Query returned {len(formatted_rows)} formatted rows")
        totals = calculate_master_totals(formatted_rows)
        return QueryResp(
            intent=intent_obj.model_dump(), 
            sql=compiled_sql, 
            data=formatted_rows,
            totals=totals
        )
    except Exception as e:
        logger.error(f"Error processing query '{req.question}': {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")
