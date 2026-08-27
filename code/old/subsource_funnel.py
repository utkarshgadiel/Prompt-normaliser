from fastapi import FastAPI, Query
import pandas as pd
import prestodb
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai import Credentials
from typing import Optional
import os
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import logging
from datetime import datetime, timedelta
from fastapi import Body
import re
from dateutil import parser as date_parser
from calendar import monthrange
from typing import Any, Dict, List, Union

# Fields containing these markers will NOT be summed
NON_ADDITIVE_MARKERS = ["%", ":"]


def _is_additive_key(key: str) -> bool:
    """
    Decide whether a column/metric should be summed.
    """
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

# --------------------------------------------
# Logging Configuration
# --------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("funnel_source_tool.log", mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --------------------------------------------
# Load Environment Variables
# --------------------------------------------
load_dotenv(Path(__file__).with_name(".env.funnel"))

# --------------------------------------------
# Watsonx + Presto Configuration
# --------------------------------------------
CATALOG = os.getenv("PRESTO_CATALOG")
LEAD_SCHEMA = os.getenv("PRESTO_LEAD_SCHEMA")
EVENT_SCHEMA = os.getenv("PRESTO_EVENT_SCHEMA")
OPP_SCHEMA = os.getenv("PRESTO_OPPO_SCHEMA")

LEAD_TABLE = os.getenv("TABLE_LEAD")
EVENT_TABLE = os.getenv("TABLE_EVENT")
OPP_TABLE = os.getenv("TABLE_OPPO")

hostname = os.getenv("PRESTO_HOST")
portnumber = int(os.getenv("PRESTO_PORT", "30984"))
username = os.getenv("PRESTO_USERNAME")
password = os.getenv("PRESTO_PASSWORD")

# Watsonx Foundation Model credentials (optional - kept as in your original file)
creds = Credentials(
    url=os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
    api_key=os.getenv("WATSONX_API_KEY")
)

model = ModelInference(
    model_id=os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct"),
    credentials=creds,
    project_id=os.getenv("WATSONX_PROJECT_ID", "4152f31e-6a49-40aa-9b62-0ecf629aae42"),
    params={"temperature": 0, "max_new_tokens": 300}
)

# --------------------------------------------
# FastAPI Setup
# --------------------------------------------
app = FastAPI(title="Watsonx Source-Wise Funnel Analytics Tool")

# --------------------------------------------
# Fiscal Year Helper Functions
# --------------------------------------------
def get_fy_for_date(date_obj):
    """Get fiscal year start year for a given date (FY runs April to March)"""
    if date_obj.month >= 4:
        return date_obj.year
    else:
        return date_obj.year - 1

def sort_funnel_by_numeric_desc(data: Any, return_as_list: bool = False) -> Any:
    """
    Sort nested funnel dictionaries by numeric values in descending order.
    Works with various funnel output structures:
    - Dict[user, Dict[metric, value]] -> Sorts users by total/first numeric metric
    - Dict[project, Dict[user, Dict[metric, value]]] -> Sorts projects and users
    
    Args:
        data: The funnel data to sort
        return_as_list: If True, returns a list of objects with 'name' key instead of dict
                       This ensures order is preserved when transmitted through JSON APIs
    """
    if not isinstance(data, dict) or not data:
        return data
    
    # Check if this is a nested structure (all values are dicts)
    first_value = next(iter(data.values()))
    
    if isinstance(first_value, dict):
        # Check if it's doubly nested (project -> user -> metrics)
        first_inner_value = next(iter(first_value.values()), None) if first_value else None
        
        if isinstance(first_inner_value, dict):
            # Doubly nested: Sort projects, then sort users within each project
            sorted_data = {}
            for key in sorted(data.keys()):
                sorted_data[key] = sort_funnel_by_numeric_desc(data[key], return_as_list=return_as_list)
            return sorted_data
        else:
            # Single nested: Sort by first numeric metric value
            def get_sort_key(item):
                key, metrics = item
                # Find first numeric value in metrics dict
                for metric_name, metric_value in metrics.items():
                    if isinstance(metric_value, (int, float)) and not any(marker in metric_name for marker in ["%", ":"]):
                        return -metric_value  # Negative for descending order
                return 0
            
            sorted_items = sorted(data.items(), key=get_sort_key)
            
            # Return as list if requested (preserves order in JSON)
            if return_as_list:
                return [
                    {"name": name, **metrics}
                    for name, metrics in sorted_items
                ]
            
            return dict(sorted_items)
    
    return data



def get_fy_dates(fy_year):
    """Get start and end dates for a fiscal year"""
    start_date = datetime(fy_year, 4, 1)
    end_date = datetime(fy_year + 1, 3, 31)
    return start_date, end_date

def get_current_fy():
    """Get current fiscal year start year"""
    today = datetime.today()
    return get_fy_for_date(today)

def get_quarter_dates(fy_year, quarter):
    """
    Get start and end dates for a fiscal quarter
    Q1: Apr-Jun, Q2: Jul-Sep, Q3: Oct-Dec, Q4: Jan-Mar
    """
    
    if quarter == 1:
        start = datetime(fy_year, 4, 1)
        end = datetime(fy_year, 6, 30)
    elif quarter == 2:
        start = datetime(fy_year, 7, 1)
        end = datetime(fy_year, 9, 30)
    elif quarter == 3:
        start = datetime(fy_year, 10, 1)
        end = datetime(fy_year, 12, 31)
    elif quarter == 4:
        start = datetime(fy_year + 1, 1, 1)
        end = datetime(fy_year + 1, 3, 31)
    else:
        raise ValueError("Quarter must be 1, 2, 3, or 4")
    
    return start, end

def get_current_quarter():
    """Get current fiscal quarter (1-4) and fiscal year"""
    today = datetime.today()
    fy_year = get_fy_for_date(today)
    
    month = today.month
    
    if 4 <= month <= 6:
        quarter = 1
    elif 7 <= month <= 9:
        quarter = 2
    elif 10 <= month <= 12:
        quarter = 3
    else:  # 1-3
        quarter = 4
    
    return fy_year, quarter

def get_month_fy_year(month_num):
    """Get the fiscal year for a given month number in current FY context"""
    current_fy = get_current_fy()
    if month_num >= 4:  # Apr-Dec
        return current_fy
    else:  # Jan-Mar
        return current_fy + 1

# --------------------------------------------
# Presto Query Helper with Logging
# --------------------------------------------
def query_presto(catalog: str, schema: str, sql: str) -> pd.DataFrame:
    """Run query on Watsonx.data Presto with detailed logging."""
    logger.info(f"Executing Presto query on catalog '{catalog}' and schema '{schema}'...")
    logger.debug(f"SQL Query:\n{sql}")

    try:
        conn = prestodb.dbapi.connect(
            host=hostname,
            port=portnumber,
            user=username,
            catalog=catalog,
            schema=schema,
            http_scheme="https",
            auth=prestodb.auth.BasicAuthentication(username, password)
        )
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        df = pd.DataFrame(rows, columns=cols)
        logger.info(f"Query executed successfully — {len(df)} rows fetched from '{schema}'.")
        return df

    except Exception as e:
        logger.error(f"Error executing query on catalog '{catalog}' schema '{schema}': {e}", exc_info=True)
        raise


# --------------------------------------------
# Source-Wise Funnel Metrics (WITH Events) 
# --------------------------------------------
def compute_source_funnel_metrics(leads: pd.DataFrame, opps: pd.DataFrame, events: pd.DataFrame):
    """
    Compute funnel metrics for source-wise analysis WITH event data.
    """
    logger.info("Starting source funnel metrics computation with events...")

    if leads is None or opps is None or events is None:
        logger.error("One or more input dataframes are None.")
        return {}

    # Ensure all critical columns exist and are string-typed
    for df, name in [(leads, "leads"), (opps, "opps"), (events, "events")]:
        for col in df.columns:
            df[col] = df[col].fillna("").astype(str)
        logger.debug(f"{name} columns converted to string types")

    # --- LEAD METRICS ---
    total_leads = len(leads)
    cf = leads.get("customer_feedback_c", pd.Series([""]*total_leads)).str.strip().str.lower()
    junk_leads = (cf == "junk").sum()
    sol_leads = (cf == "interested").sum()
    valid_leads = (cf != "junk").sum()

    # --- OPPORTUNITY METRICS ---
    sales_col = opps.get("sales_order_number_c", pd.Series([""]*len(opps))).astype(str).str.strip().str.lower()
    sales_done = (sales_col != "") & (sales_col != "nan")
    sales_done = sales_done.sum()

    # --- EVENT METRICS ---
    event_subject = events.get("Subject_c", pd.Series([""]*len(events))).str.strip().str.lower()
    meeting_booked = (event_subject == "personal appointment booked").sum()
    
    event_status = events.get("Appointment_Status_c", pd.Series([""]*len(events))).str.strip().str.lower()
    meeting_done = ((event_subject == "personal appointment booked") & 
                    (event_status == "completed")).sum()

    # --- DERIVED RATIOS ---
    junk_percent = round((junk_leads / total_leads) * 100, 2) if total_leads else 0
    tl_vl = round(total_leads / valid_leads, 2) if valid_leads else 0
    vl_sol = round(valid_leads / sol_leads, 2) if sol_leads else 0
    sol_mb = round(sol_leads / meeting_booked, 2) if meeting_booked else 0
    mb_md = round(meeting_booked / meeting_done, 2) if meeting_done else 0
    md_sd = round(meeting_done / sales_done, 2) if sales_done else 0
    tl_sd = round(total_leads / sales_done, 2) if sales_done else 0
    vl_sd = round(valid_leads / sales_done, 2) if sales_done else 0
    sol_sd = round(sol_leads / sales_done, 2) if sales_done else 0
    mb_sd = round(meeting_booked / sales_done, 2) if sales_done else 0

    funnel_data = {
        "Total Leads": int(total_leads),
        "Valid Leads": int(valid_leads),
        "Junk Leads": int(junk_leads),
        "SOL Leads (Interested)": int(sol_leads),
        "Meeting Booked": int(meeting_booked),
        "Meeting Done": int(meeting_done),
        "Sales Done": int(sales_done),
        "Junk %": junk_percent,
        "TL:VL": tl_vl,
        "VL:SOL": vl_sol,
        "SOL:MB": sol_mb,
        "MB:MD": mb_md,
        "MD:SD": md_sd,
        "TL:SD": tl_sd,
        "VL:SD": vl_sd,
        "SOL:SD": sol_sd,
        "MB:SD": mb_sd
    }

    logger.info(f"Source funnel computation completed: {funnel_data}")
    return sort_funnel_by_numeric_desc(funnel_data)

# --------------------------------------------
# Source-Wise Funnel (Dynamic with Events)
# --------------------------------------------
def compute_source_wise_funnel(leads: pd.DataFrame, opps: pd.DataFrame, events: pd.DataFrame, header_col: str = "Lead_Source_Sub_Category_c"):
    """
    Compute funnel metrics dynamically based on unique values in Lead_Source_Sub_Category_c column.
    Maps events using OwnerId from leads.
    """
    logger.info(f"Starting dynamic source-wise funnel computation based on '{header_col}'...")

    # Ensure the column exists in leads and opps
    for df in (leads, opps):
        if header_col not in df.columns:
            df[header_col] = ""
        df["__col_normalized__"] = df[header_col].fillna("").astype(str).str.strip().str.lower()

    # Ensure OwnerId columns exist
    if "OwnerId" not in leads.columns:
        leads["OwnerId"] = ""
    if "OwnerId" not in events.columns:
        events["OwnerId"] = ""

    leads["__owner_normalized__"] = leads["OwnerId"].fillna("").astype(str).str.strip()
    events["__owner_normalized__"] = events["OwnerId"].fillna("").astype(str).str.strip()

    # Get all unique non-empty values
    unique_values = pd.concat([leads["__col_normalized__"], opps["__col_normalized__"]]).unique()
    unique_values = [v for v in unique_values if v]

    output = {}

    for val in unique_values:
        leads_s = leads[leads["__col_normalized__"] == val].copy()
        opps_s = opps[opps["__col_normalized__"] == val].copy()
        
        owner_ids = leads_s["__owner_normalized__"].unique()
        events_s = events[events["__owner_normalized__"].isin(owner_ids)].copy()

        display_name = val.title()

        if leads_s.empty and opps_s.empty:
            output[display_name] = {
                "Total Leads": 0, "Valid Leads": 0, "Junk Leads": 0,
                "SOL Leads (Interested)": 0, "Meeting Booked": 0, "Meeting Done": 0,
                "Sales Done": 0, "Junk %": 0, "TL:VL": 0, "VL:SOL": 0,
                "SOL:MB": 0, "MB:MD": 0, "MD:SD": 0, "TL:SD": 0,
                "VL:SD": 0, "SOL:SD": 0, "MB:SD": 0
            }
            continue

        metrics = compute_source_funnel_metrics(leads_s, opps_s, events_s)
        output[display_name] = metrics

    # Clean up helper columns
    for df in (leads, opps, events):
        if "__col_normalized__" in df.columns:
            df.drop(columns="__col_normalized__", inplace=True, errors='ignore')
        if "__owner_normalized__" in df.columns:
            df.drop(columns="__owner_normalized__", inplace=True, errors='ignore')

    logger.info("Dynamic source-wise funnel computation completed.")
    return sort_funnel_by_numeric_desc(output, return_as_list=True)

# --------------------------------------------
# Advanced Date Parser (Complete Function)
# --------------------------------------------
def extract_subsource_from_question(question: str) -> Optional[str]:
    """
    Extract sub-source name from a natural language question using a
    word-by-word approach that stops immediately at any time/analytics keyword.

    Supported patterns:
      - "for <name> ..."          e.g. "funnel for facebook this month"
      - "of <name> ..."           e.g. "of 99acres last week"
      - "sub-source: <name> ..."  e.g. "sub-source: housing.com from jan"
      - "subsource <name> ..."    e.g. "subsource naukri.com this quarter"
      - "<name> data/funnel/..."  e.g. "housing.com data for last month"
      - "<name> leads/funnel ..." e.g. "facebook funnel last month"

    Returns the extracted sub-source string (lower-stripped) or None.
    """
    STOP_WORDS = {
        'from', 'in', 'between', 'during', 'this', 'last', 'previous', 'current', 'next',
        'month', 'week', 'day', 'year', 'quarter', 'today', 'date', 'till', 'until',
        'mom', 'yoy', 'qoq', 'monthly', 'weekly', 'quarterly', 'yearly', 'annually',
        'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
        'september', 'october', 'november', 'december',
        'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
        'fy', 'on', 'and', 'the', 'a', 'an', 'all', 'data', 'leads', 'lead', 'report',
        'funnel', 'metrics', 'metric', 'subsource', 'sub', 'source',
        'show', 'give', 'get', 'display', 'tell', 'me', 'wise', 'of', 'for',
    }
    # Words that immediately follow a name token and confirm it is a name
    NOISE_AFTER = {'data', 'leads', 'lead', 'report', 'funnel', 'metrics', 'metric', 'results', 'numbers', 'stats'}

    def _clean(w):
        """Strip surrounding punctuation and return lowercased token."""
        return re.sub(r'^[:,-]+|[:,-]+$', '', w.lower())

    def _norm(w):
        """Remove non-name chars for stop-word matching."""
        return re.sub(r'[^a-z0-9._/-]', '', _clean(w))

    def _is_name_token(w):
        c = _norm(w)
        if not c or len(c) < 2:
            return False
        if c in STOP_WORDS:
            return False
        if re.fullmatch(r'20\d{2}|19\d{2}', c) or re.fullmatch(r'q[1-4]', c):
            return False
        return True

    def _collect(words):
        """Collect consecutive name tokens, stopping at any stop-word."""
        parts = []
        for w in words:
            c = _norm(w)
            if not c or c in STOP_WORDS:
                break
            if re.fullmatch(r'20\d{2}|19\d{2}', c) or re.fullmatch(r'q[1-4]', c):
                break
            parts.append(c)
            if len(parts) == 4:
                break
        name = ' '.join(parts).strip()
        return name if len(name) > 1 else None

    q = question.lower().strip()
    words = re.split(r'\s+', q)

    i = 0
    while i < len(words):
        w = words[i]
        w_base = re.sub(r'[^a-z0-9_-]', '', w)

        # Anchor: 'for' or 'of'
        if w_base in ('for', 'of'):
            name = _collect(words[i + 1:])
            if name:
                return name

        # Anchor: 'subsource', 'sub-source', 'sub_source'
        if w_base == 'subsource' or re.match(r'^sub-?source', w_base):
            start = i + 1
            if start < len(words) and re.fullmatch(r'[:\-]+', words[start]):
                start += 1
            name = _collect(words[start:])
            if name:
                return name

        # Anchor: 'sub' followed by 'source[:]' as two tokens
        if w_base == 'sub' and i + 1 < len(words):
            nxt_base = re.sub(r'[^a-z]', '', words[i + 1])
            if nxt_base == 'source':
                start = i + 2
                if start < len(words) and re.fullmatch(r'[:\-]+', words[start]):
                    start += 1
                name = _collect(words[start:])
                if name:
                    return name

        # Pattern: <name_token> immediately followed by a noise word
        # e.g. "housing.com data for ..." or "facebook funnel last month"
        if _is_name_token(w) and i + 1 < len(words):
            nxt = _norm(words[i + 1])
            if nxt in NOISE_AFTER:
                return _norm(w)

        i += 1
    return None


def parse_date_from_question_complete(question: str):
    """
    Enhanced date parser that handles all requirements.
    ORDER MATTERS - more specific patterns must come before general ones.
    
    Returns: (start_date, end_date, comparison_start, comparison_end, period_type)
    """
    question = question.lower()
    today = datetime.today()
    current_fy = get_current_fy()
    
    start_date = end_date = comparison_start = comparison_end = None
    period_type = "single"

        # ==================== PRIORITY 0: MULTIPLE PERIODS WITH "AND" ====================
    
    # Check for multiple months (e.g., "april and june", "april and june and august")
    month_and_pattern = r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+and\s+'
    if re.search(month_and_pattern, question):
        # Extract all months mentioned
        all_months = re.findall(r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
        
        if len(all_months) >= 2:
            periods = []
            for month_str, year_str in all_months:
                month_num = date_parser.parse(month_str + " 2000").month
                if year_str:
                    year = int(year_str)
                else:
                    year = get_month_fy_year(month_num)
                
                start = datetime(year, month_num, 1)
                last_day = monthrange(year, month_num)[1]
                end = datetime(year, month_num, last_day)
                
                periods.append({
                    "name": f"{month_str.title()} {year}",
                    "start_date": start.strftime("%d-%m-%Y"),
                    "end_date": end.strftime("%d-%m-%Y")
                })
            
            logger.info(f"Detected multiple months with 'and': {[p['name'] for p in periods]}")
            return {
                "type": "multiple_periods",
                "periods": periods
            }
    
    # Check for multiple quarters (e.g., "q1 and q2", "q1 and q3 fy 2023")
    quarter_and_pattern = r'\b(?:q|quarter)\s*([1-4])\s+and\s+(?:q|quarter)\s*([1-4])'
    quarter_match = re.search(quarter_and_pattern, question)
    if quarter_match:
        quarters = [int(quarter_match.group(1)), int(quarter_match.group(2))]
        
        # Check for more quarters
        additional_quarters = re.findall(r'\band\s+(?:q|quarter)\s*([1-4])', question[quarter_match.end():])
        quarters.extend([int(q) for q in additional_quarters])
        
        # Check for year
        year_match = re.search(r'(?:fy\s*)?(\d{4})', question)
        fy_year = int(year_match.group(1)) if year_match else current_fy
        
        periods = []
        for q_num in quarters:
            start, end = get_quarter_dates(fy_year, q_num)
            periods.append({
                "name": f"Q{q_num} FY{fy_year}-{fy_year+1}",
                "start_date": start.strftime("%d-%m-%Y"),
                "end_date": end.strftime("%d-%m-%Y")
            })
        
        logger.info(f"Detected multiple quarters with 'and': {[p['name'] for p in periods]}")
        return {
            "type": "multiple_periods",
            "periods": periods
        }
    
    # Check for multiple specific dates (e.g., "20 june and 25 july")
    date_and_pattern = r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+and\s+'
    if re.search(date_and_pattern, question):
        all_dates = re.findall(r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
        
        if len(all_dates) >= 2:
            periods = []
            for day_str, month_str, year_str in all_dates:
                day = int(day_str)
                month_num = date_parser.parse(month_str + " 2000").month
                if year_str:
                    year = int(year_str)
                else:
                    year = get_month_fy_year(month_num)
                
                date_obj = datetime(year, month_num, day)
                periods.append({
                    "name": f"{day} {month_str.title()} {year}",
                    "start_date": date_obj.strftime("%d-%m-%Y"),
                    "end_date": date_obj.strftime("%d-%m-%Y")
                })
            
            logger.info(f"Detected multiple dates with 'and': {[p['name'] for p in periods]}")
            return {
                "type": "multiple_periods",
                "periods": periods
            }
    
     # 1. MOM for specific FY or "last year" or date range
    mom_match = re.search(r'\b(month[-\s]?on[-\s]?month|mom|monthly|month\s*wise)\b', question)
    if mom_match:
        # Check for month range first (e.g., "monthly april to sep", "mom june to december")
        month_range_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+to\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
        
        if month_range_match:
            month1_str = month_range_match.group(1)
            year1_str = month_range_match.group(2)
            month2_str = month_range_match.group(3)
            year2_str = month_range_match.group(4)
            
            month1_num = date_parser.parse(month1_str + " 2000").month
            month2_num = date_parser.parse(month2_str + " 2000").month
            
            # Determine years
            if year2_str:
                year2 = int(year2_str)
                year1 = int(year1_str) if year1_str else year2
            elif year1_str:
                year1 = int(year1_str)
                year2 = year1
            else:
                year1 = get_month_fy_year(month1_num)
                year2 = get_month_fy_year(month2_num)
            
            try:
                start_date = datetime(year1, month1_num, 1)
                last_day = monthrange(year2, month2_num)[1]
                end_date = datetime(year2, month2_num, last_day)
                logger.info(f"Detected MOM for month range: {start_date.date()} to {end_date.date()}")
                return start_date, end_date, None, None, "mom"
            except ValueError as e:
                logger.warning(f"Invalid month range for MOM: {e}")
        
        # Check for day range within months (e.g., "monthly 1 june to 12 july")
        day_range_match = re.search(r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
        
        if day_range_match:
            day1 = int(day_range_match.group(1))
            month1_str = day_range_match.group(2)
            year1_str = day_range_match.group(3)
            day2 = int(day_range_match.group(4))
            month2_str = day_range_match.group(5)
            year2_str = day_range_match.group(6)
            
            month1_num = date_parser.parse(month1_str + " 2000").month
            month2_num = date_parser.parse(month2_str + " 2000").month
            
            # Determine years
            if year1_str and year2_str:
                year1 = int(year1_str)
                year2 = int(year2_str)
            elif year1_str:
                year1 = int(year1_str)
                year2 = year1 if month2_num >= month1_num else year1 + 1
            elif year2_str:
                year2 = int(year2_str)
                year1 = year2 if month1_num <= month2_num else year2 - 1
            else:
                year1 = get_month_fy_year(month1_num)
                year2 = get_month_fy_year(month2_num)
            
            try:
                start_date = datetime(year1, month1_num, day1)
                end_date = datetime(year2, month2_num, day2)
                logger.info(f"Detected MOM for date range: {start_date.date()} to {end_date.date()}")
                return start_date, end_date, None, None, "mom"
            except ValueError as e:
                logger.warning(f"Invalid date range for MOM: {e}")
        
        # Check for specific year
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', question)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', question)
        
        if year_match:
            target_fy = int(year_match.group(1))
            logger.info(f"Detected MOM for FY {target_fy}")
            start_date, end_date = get_fy_dates(target_fy)
            return start_date, end_date, None, None, "mom"
        elif last_year_match:
            target_fy = current_fy - 1
            logger.info(f"Detected MOM for last FY {target_fy}")
            start_date, end_date = get_fy_dates(target_fy)
            return start_date, end_date, None, None, "mom"
        else:
            # Default to current FY
            start_date, end_date = get_fy_dates(current_fy)
            logger.info("Detected MOM for current FY")
            return start_date, end_date, None, None, "mom"


    # 2. QOQ for specific FY or "last year"
    qoq_match = re.search(r'\b(quarter[-\s]?on[-\s]?quarter|qoq|quarterly)\b', question)
    if qoq_match:
        # Check for specific year
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', question)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', question)
        
        if year_match:
            target_fy = int(year_match.group(1))
            logger.info(f"Detected QOQ for FY {target_fy}")
            start_date, end_date = get_fy_dates(target_fy)
            return start_date, end_date, None, None, "qoq"
        elif last_year_match:
            target_fy = current_fy - 1
            logger.info(f"Detected QOQ for last FY {target_fy}")
            start_date, end_date = get_fy_dates(target_fy)
            return start_date, end_date, None, None, "qoq"
        else:
            # Default to current FY
            start_date, end_date = get_fy_dates(current_fy)
            logger.info("Detected QOQ for current FY")
            return start_date, end_date, None, None, "qoq"

    # 3. YOY for specific years or "last year"
    yoy_match = re.search(r'\b(year[-\s]?on[-\s]?year|yoy|y-o-y)\b', question)
    if yoy_match:
        # Check for specific year
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', question)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', question)
        
        if year_match:
            target_fy = int(year_match.group(1))
            logger.info(f"Detected YOY for FY {target_fy} vs FY {target_fy-1}")
            start_date, end_date = get_fy_dates(target_fy)
            comparison_start, comparison_end = get_fy_dates(target_fy - 1)
            return start_date, end_date, comparison_start, comparison_end, "yoy"
        elif last_year_match:
            target_fy = current_fy - 1
            logger.info(f"Detected YOY for last FY: FY {target_fy} vs FY {target_fy-1}")
            start_date, end_date = get_fy_dates(target_fy)
            comparison_start, comparison_end = get_fy_dates(target_fy - 1)
            return start_date, end_date, comparison_start, comparison_end, "yoy"
        else:
            # Default: compare current FY with last FY
            start_date, end_date = get_fy_dates(current_fy)
            comparison_start, comparison_end = get_fy_dates(current_fy - 1)
            logger.info("Detected YOY for current FY vs last FY")
            return start_date, end_date, comparison_start, comparison_end, "yoy"

    # ==================== PRIORITY 2: DATE RANGES ====================
    
    # Pattern 1: "1 to 12 june" or "1-12 june" or "1 to 12 june 2024"
    match = re.search(r'(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
    if match:
        day1 = int(match.group(1))
        day2 = int(match.group(2))
        month_str = match.group(3)
        year_str = match.group(4)
        
        month_num = date_parser.parse(month_str + " 2000").month
        if year_str:
            year = int(year_str)
        else:
            year = get_month_fy_year(month_num)
        
        try:
            start_date = datetime(year, month_num, day1)
            end_date = datetime(year, month_num, day2)
            logger.info(f"Detected date range within month: {start_date.date()} to {end_date.date()}")
            return start_date, end_date, None, None, "single"
        except ValueError as e:
            logger.warning(f"Invalid date range: {day1}-{day2}/{month_num}/{year}: {e}")

    # Pattern 2: "1 june to 12 june" or "1 june to 12 july" or "1 june 2024 to 12 july 2024"
    match = re.search(r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?', question)
    if match:
        day1 = int(match.group(1))
        month1_str = match.group(2)
        year1_str = match.group(3)
        day2 = int(match.group(4))
        month2_str = match.group(5)
        year2_str = match.group(6)
        
        month1_num = date_parser.parse(month1_str + " 2000").month
        month2_num = date_parser.parse(month2_str + " 2000").month
        
        # Determine years — fully independent resolution for each side
        if year1_str and year2_str:
            year1 = int(year1_str)
            year2 = int(year2_str)
        elif year1_str:
            year1 = int(year1_str)
            # If month2 is ahead of month1, same year; if behind, next year
            year2 = year1 if month2_num >= month1_num else year1 + 1
        elif year2_str:
            year2 = int(year2_str)
            # Infer year1: if month1 is before month2, same year; else previous year
            year1 = year2 if month1_num <= month2_num else year2 - 1
        else:
            year1 = get_month_fy_year(month1_num)
            year2 = get_month_fy_year(month2_num)
        
        try:
            start_date = datetime(year1, month1_num, day1)
            end_date = datetime(year2, month2_num, day2)
            logger.info(f"Detected date range: {start_date.date()} to {end_date.date()}")
            return start_date, end_date, None, None, "single"
        except ValueError as e:
            logger.warning(f"Invalid date range: {e}")

    
    # ==================== TILL DATE / TODAY PATTERNS ====================
    # Patterns: "from april till date", "from 15 april till date",
    #           "from april 2024 till date", "from 15 april 2024 till date"
    till_date_patterns = [
        # "from 15 april 2024 till date/today"
        (r'from\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\s+(?:till\s+date|till\s+today|to\s+date|to\s+today|until\s+today|until\s+date)', 'day_month_year'),
        # "from 15 april till date/today"
        (r'from\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(?:till\s+date|till\s+today|to\s+date|to\s+today|until\s+today|until\s+date)', 'day_month'),
        # "from april 2024 till date/today"
        (r'from\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\s+(?:till\s+date|till\s+today|to\s+date|to\s+today|until\s+today|until\s+date)', 'month_year'),
        # "from april till date/today"
        (r'from\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(?:till\s+date|till\s+today|to\s+date|to\s+today|until\s+today|until\s+date)', 'month_only'),
    ]
    for pat, fmt in till_date_patterns:
        m = re.search(pat, question)
        if m:
            try:
                if fmt == 'day_month_year':
                    day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
                    month_num = date_parser.parse(month_str + " 2000").month
                    start_date = datetime(year, month_num, day)
                elif fmt == 'day_month':
                    day, month_str = int(m.group(1)), m.group(2)
                    month_num = date_parser.parse(month_str + " 2000").month
                    year = get_month_fy_year(month_num)
                    start_date = datetime(year, month_num, day)
                elif fmt == 'month_year':
                    month_str, year = m.group(1), int(m.group(2))
                    month_num = date_parser.parse(month_str + " 2000").month
                    start_date = datetime(year, month_num, 1)
                elif fmt == 'month_only':
                    month_str = m.group(1)
                    month_num = date_parser.parse(month_str + " 2000").month
                    year = get_month_fy_year(month_num)
                    start_date = datetime(year, month_num, 1)
                end_date = today
                logger.info(f"Detected till-date range: {start_date.date()} to {end_date.date()}")
                return start_date, end_date, None, None, "single"
            except Exception as e:
                logger.warning(f"Failed to parse till-date pattern: {e}")

    # ==================== WEEK PATTERNS ====================
    # "this week", "last week", "last 2 weeks", "last n weeks"
    if re.search(r'\bthis\s+week\b', question):
        # Week starts Monday
        days_since_monday = today.weekday()
        start_date = today - timedelta(days=days_since_monday)
        end_date = start_date + timedelta(days=6)
        logger.info(f"Detected this week: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"

    if re.search(r'\blast\s+week\b', question):
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)
        start_date = this_monday - timedelta(days=7)
        end_date = this_monday - timedelta(days=1)
        logger.info(f"Detected last week: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"

    last_n_weeks = re.search(r'last\s+(\d+)\s+weeks?', question)
    if last_n_weeks:
        n_weeks = int(last_n_weeks.group(1))
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)
        # Last N complete weeks: ends last Sunday, goes back n*7 days
        end_date = this_monday - timedelta(days=1)
        start_date = this_monday - timedelta(days=n_weeks * 7)
        logger.info(f"Detected last {n_weeks} weeks: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"

    # 1. Last N Days do not count current date in last n days
    last_days_match = re.search(r'last\s+(\d+)\s+days?', question)
    if last_days_match:
        n_days = int(last_days_match.group(1))
        end_date = today - timedelta(days=1)
        start_date = end_date - timedelta(days=n_days)
        logger.info(f"Detected last {n_days} days")
        return start_date, end_date, None, None, "single"
    
    # 2. Last N Months (EXCLUDING current month)
    last_months_match = re.search(r'last\s+(\d+)\s+months?', question)
    if last_months_match:
        n_months = int(last_months_match.group(1))
        
        # Start from last month (not current)
        if today.month == 1:
            end_month = 12
            end_year = today.year - 1
        else:
            end_month = today.month - 1
            end_year = today.year
        
        # Calculate start month
        start_month = end_month - n_months + 1
        start_year = end_year
        
        # Handle year rollback
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        
        start_date = datetime(start_year, start_month, 1)
        last_day = monthrange(end_year, end_month)[1]
        end_date = datetime(end_year, end_month, last_day)
        
        logger.info(f"Detected last {n_months} months (excluding current): {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"
    
    # 3. Last N Quarters (EXCLUDING current quarter)
    last_quarters_match = re.search(r'last\s+(\d+)\s+quarters?', question)
    if last_quarters_match:
        n_quarters = int(last_quarters_match.group(1))
        
        # Get current quarter
        current_fy_year, current_q = get_current_quarter()
        
        # Start from last quarter (not current)
        if current_q == 1:
            end_fy = current_fy_year - 1
            end_q = 4
        else:
            end_fy = current_fy_year
            end_q = current_q - 1
        
        # Calculate start quarter
        start_q = end_q - n_quarters + 1
        start_fy = end_fy
        
        # Handle FY rollback
        while start_q <= 0:
            start_q += 4
            start_fy -= 1
        
        start_date, _ = get_quarter_dates(start_fy, start_q)
        _, end_date = get_quarter_dates(end_fy, end_q)
        
        logger.info(f"Detected last {n_quarters} quarters (excluding current): Q{start_q} FY{start_fy} to Q{end_q} FY{end_fy}")
        return start_date, end_date, None, None, "single"
    
    # 4. Month on Month (MoM) - All months of current FY
    if re.search(r'\bmom\b|\bmonth\s+on\s+month\b', question):
        start_date, end_date = get_fy_dates(current_fy)
        logger.info("Detected MoM - returning current FY")
        return start_date, end_date, None, None, "mom"
    
    # 5. This/Current Month
    if re.search(r'\bthis\s+month\b|\bcurrent\s+month\b', question):
        start_date = datetime(today.year, today.month, 1)
        end_date = datetime(today.year, today.month, monthrange(today.year, today.month)[1])
        logger.info(f"Detected this month: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"
    
    # 6. Last/Previous Month
    if re.search(r'\blast\s+month\b|\bprevious\s+month\b', question):
        prev_month = 12 if today.month == 1 else today.month - 1
        prev_year = today.year - 1 if today.month == 1 else today.year
        start_date = datetime(prev_year, prev_month, 1)
        end_date = datetime(prev_year, prev_month, monthrange(prev_year, prev_month)[1])
        logger.info(f"Detected last month: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"
    
    # 7. Year on Year (YoY) - Current FY and Last FY data
    if re.search(r'\byoy\b|\byear\s+on\s+year\b', question):
        start_date, end_date = get_fy_dates(current_fy)
        comparison_start, comparison_end = get_fy_dates(current_fy - 1)
        logger.info("Detected YoY")
        return start_date, end_date, comparison_start, comparison_end, "yoy"
    
    # 8. This Year / Current Year (Fiscal)
    if re.search(r'\bthis\s+year\b|\bcurrent\s+year\b', question):
        start_date, end_date = get_fy_dates(current_fy)
        logger.info(f"Detected this year: FY {current_fy}")
        return start_date, end_date, None, None, "single"
    
    # 9. Last Year / Previous Year (Fiscal)
    if re.search(r'\blast\s+year\b|\bprevious\s+year\b|\blast\s+fy\b|\bprevious\s+fy\b', question):
        start_date, end_date = get_fy_dates(current_fy - 1)
        logger.info(f"Detected last year: FY {current_fy - 1}")
        return start_date, end_date, None, None, "single"
    
    # 10. Quarter on Quarter (QoQ) - All quarters of current FY
    if re.search(r'\bqoq\b|\bquarter\s+on\s+quarter\b', question):
        start_date, end_date = get_fy_dates(current_fy)
        logger.info("Detected QoQ - returning current FY")
        return start_date, end_date, None, None, "qoq"
    
    # 11. This Quarter / Current Quarter
    if re.search(r'\bthis\s+quarter\b|\bcurrent\s+quarter\b', question):
        current_fy_year, current_q = get_current_quarter()
        start_date, end_date = get_quarter_dates(current_fy_year, current_q)
        logger.info(f"Detected this quarter: Q{current_q}")
        return start_date, end_date, None, None, "single"
    
    # 12. Last Quarter / Previous Quarter
    if re.search(r'\blast\s+quarter\b|\bprevious\s+quarter\b', question):
        current_fy_year, current_q = get_current_quarter()
        if current_q == 1:
            prev_fy = current_fy_year - 1
            prev_q = 4
        else:
            prev_fy = current_fy_year
            prev_q = current_q - 1
        start_date, end_date = get_quarter_dates(prev_fy, prev_q)
        logger.info(f"Detected last quarter: Q{prev_q}")
        return start_date, end_date, None, None, "single"
    
    # 13. Specific Quarter (Q1, Q2, Q3, Q4, Quarter 1, Quarter 2, etc.) - BEFORE DATE RANGES
    quarter_match = re.search(r'\b(?:q|quarter)\s*([1-4])\b', question)
    if quarter_match:
        quarter_num = int(quarter_match.group(1))
        year_match = re.search(r'(?:q|quarter)\s*[1-4]\s+(?:fy\s*)?(\d{4})', question)
        if year_match:
            fy_year = int(year_match.group(1))
        else:
            fy_year = current_fy
        start_date, end_date = get_quarter_dates(fy_year, quarter_num)
        logger.info(f"Detected quarter {quarter_num} for FY {fy_year}: {start_date.date()} to {end_date.date()}")
        return start_date, end_date, None, None, "single"
    
    # 14. Specific Date with Day + Month + Year (MUST BE BEFORE RANGES)
    specific_date_with_year_pattern = r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b(?!\s+to\b)'
    match = re.search(specific_date_with_year_pattern, question)
    if match:
        try:
            day = int(match.group(1))
            month_str = match.group(2)
            year = int(match.group(3))
            month_num = date_parser.parse(month_str + " 2000").month
            
            parsed_date = datetime(year, month_num, day)
            logger.info(f"Detected specific date with year: {parsed_date.date()}")
            return parsed_date, parsed_date, None, None, "single"
        except Exception as e:
            logger.warning(f"Failed to parse specific date with year: {e}")
    
    # 15. Specific Date with Day + Month (no year) - like "20 sep", "5 june"
    specific_date_no_year_pattern = r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b(?!\s+\d{4})(?!\s+to\b)'
    match = re.search(specific_date_no_year_pattern, question)
    if match:
        try:
            day = int(match.group(1))
            month_str = match.group(2)
            month_num = date_parser.parse(month_str + " 2000").month
            year = get_month_fy_year(month_num)
            
            parsed_date = datetime(year, month_num, day)
            logger.info(f"Detected specific date without year: {parsed_date.date()}")
            return parsed_date, parsed_date, None, None, "single"
        except Exception as e:
            logger.warning(f"Failed to parse specific date without year: {e}")
    
    # 16. Custom Date Range - PRIORITY: Check before specific dates
    date_range_patterns = [
        # Format: "1 to 12 june" or "1 june to 12 june"
        r'(\d{1,2})\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?',
        r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?',
        # Format: "20 june 2024 to 25 july 2024" or "20 sep 2022"
        r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})',
        # Format: "august to oct 2022" or "august 2022 to oct 2022"
        r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+to\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})',
        r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\s+to\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})',
        # Format: "5 april 2024 to 10 october 2024"
        r'(\d{1,2}\s+\w+\s+\d{4})\s+to\s+(\d{1,2}\s+\w+\s+\d{4})',
        # Format: "april 2024 to june 2025"
        r'(\w+\s+\d{4})\s+to\s+(\w+\s+\d{4})',
        # Format: "01-04-2024 to 30-06-2025"
        r'(\d{1,2}[-/]\d{1,2}[-/]\d{4})\s+to\s+(\d{1,2}[-/]\d{1,2}[-/]\d{4})',
        r'from\s+(\d{1,2}\s+\w+\s+\d{4})\s+to\s+(\d{1,2}\s+\w+\s+\d{4})',
        r'from\s+(\w+\s+\d{4})\s+to\s+(\w+\s+\d{4})'
    ]
    
    for pattern in date_range_patterns:
        match = re.search(pattern, question)
        if match:
            try:
                groups = match.groups()
                
                # Handle "1 to 12 june" or "1 to 12 june 2024"
                if pattern == date_range_patterns[0]:
                    day1 = int(groups[0])
                    day2 = int(groups[1])
                    month_str = groups[2]
                    year = int(groups[3]) if groups[3] else get_month_fy_year(date_parser.parse(month_str + " 2000").month)
                    month_num = date_parser.parse(month_str + " 2000").month
                    
                    start_date = datetime(year, month_num, day1)
                    end_date = datetime(year, month_num, day2)
                    return start_date, end_date, None, None, "single"
                
                # Handle "1 june to 12 june" or "1 june to 12 july 2024"
                elif pattern == date_range_patterns[1]:
                    day1 = int(groups[0])
                    month1_str = groups[1]
                    day2 = int(groups[2])
                    month2_str = groups[3]
                    year = int(groups[4]) if groups[4] else current_fy
                    
                    month1_num = date_parser.parse(month1_str + " 2000").month
                    month2_num = date_parser.parse(month2_str + " 2000").month
                    
                    # Determine years for each month in FY context
                    year1 = get_month_fy_year(month1_num) if not groups[4] else year
                    year2 = get_month_fy_year(month2_num) if not groups[4] else year
                    
                    start_date = datetime(year1, month1_num, day1)
                    end_date = datetime(year2, month2_num, day2)
                    return start_date, end_date, None, None, "single"
                
                # Handle "20 sep 2022 to 25 oct 2022"
                elif pattern == date_range_patterns[2]:
                    day1 = int(groups[0])
                    month1_str = groups[1]
                    year1 = int(groups[2])
                    day2 = int(groups[3])
                    month2_str = groups[4]
                    year2 = int(groups[5])
                    
                    month1_num = date_parser.parse(month1_str + " 2000").month
                    month2_num = date_parser.parse(month2_str + " 2000").month
                    
                    start_date = datetime(year1, month1_num, day1)
                    end_date = datetime(year2, month2_num, day2)
                    return start_date, end_date, None, None, "single"
                
                # Handle "august to oct 2022"
                elif pattern == date_range_patterns[3]:
                    month1_str = groups[0]
                    month2_str = groups[1]
                    year = int(groups[2])
                    
                    month1_num = date_parser.parse(month1_str + " 2000").month
                    month2_num = date_parser.parse(month2_str + " 2000").month
                    
                    start_date = datetime(year, month1_num, 1)
                    last_day = monthrange(year, month2_num)[1]
                    end_date = datetime(year, month2_num, last_day)
                    return start_date, end_date, None, None, "single"
                
                # Handle "august 2022 to oct 2022"
                elif pattern == date_range_patterns[4]:
                    month1_str = groups[0]
                    year1 = int(groups[1])
                    month2_str = groups[2]
                    year2 = int(groups[3])
                    
                    month1_num = date_parser.parse(month1_str + " 2000").month
                    month2_num = date_parser.parse(month2_str + " 2000").month
                    
                    start_date = datetime(year1, month1_num, 1)
                    last_day = monthrange(year2, month2_num)[1]
                    end_date = datetime(year2, month2_num, last_day)
                    return start_date, end_date, None, None, "single"
                
                # Generic handlers for remaining patterns
                else:
                    start_str, end_str = groups[0], groups[1] if len(groups) >= 2 else groups[0]
                    parsed_start = date_parser.parse(start_str, dayfirst=True)
                    parsed_end = date_parser.parse(end_str, dayfirst=True)
                    
                    # Check if day is specified
                    has_start_day = bool(re.search(r'^\d{1,2}\s', start_str))
                    has_end_day = bool(re.search(r'^\d{1,2}\s', end_str))
                    
                    if not has_start_day:
                        parsed_start = parsed_start.replace(day=1)
                    if not has_end_day:
                        last_day = monthrange(parsed_end.year, parsed_end.month)[1]
                        parsed_end = parsed_end.replace(day=last_day)
                    
                    return parsed_start, parsed_end, None, None, "single"
                    
            except Exception as e:
                logger.warning(f"Failed to parse date range with pattern {pattern}: {e}")
                continue
    
    # 16b. Mixed patterns: "15 april to may" or "april to 10 may"
    # "day month [year] to month [year]"
    mixed_dm_m = re.search(
        r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+to\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?(?!\s+\d)',
        question)
    if mixed_dm_m:
        try:
            day1 = int(mixed_dm_m.group(1))
            m1_str = mixed_dm_m.group(2); y1s = mixed_dm_m.group(3)
            m2_str = mixed_dm_m.group(4); y2s = mixed_dm_m.group(5)
            mn1 = date_parser.parse(m1_str + " 2000").month
            mn2 = date_parser.parse(m2_str + " 2000").month
            y1 = int(y1s) if y1s else get_month_fy_year(mn1)
            y2 = int(y2s) if y2s else get_month_fy_year(mn2)
            start_date = datetime(y1, mn1, day1)
            end_date = datetime(y2, mn2, monthrange(y2, mn2)[1])
            logger.info(f"Detected mixed day+month to month: {start_date.date()} to {end_date.date()}")
            return start_date, end_date, None, None, "single"
        except Exception as e:
            logger.warning(f"Failed mixed day+month to month parse: {e}")

    # "month [year] to day month [year]"
    mixed_m_dm = re.search(
        r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?\s+to\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+(\d{4}))?',
        question)
    if mixed_m_dm:
        try:
            m1_str = mixed_m_dm.group(1); y1s = mixed_m_dm.group(2)
            day2 = int(mixed_m_dm.group(3))
            m2_str = mixed_m_dm.group(4); y2s = mixed_m_dm.group(5)
            mn1 = date_parser.parse(m1_str + " 2000").month
            mn2 = date_parser.parse(m2_str + " 2000").month
            y1 = int(y1s) if y1s else get_month_fy_year(mn1)
            y2 = int(y2s) if y2s else get_month_fy_year(mn2)
            start_date = datetime(y1, mn1, 1)
            end_date = datetime(y2, mn2, day2)
            logger.info(f"Detected mixed month to day+month: {start_date.date()} to {end_date.date()}")
            return start_date, end_date, None, None, "single"
        except Exception as e:
            logger.warning(f"Failed mixed month to day+month parse: {e}")

    # 17. Month-only Range
    month_range_pattern = r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+to\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b'
    match = re.search(month_range_pattern, question)
    if match:
        start_month_str, end_month_str = match.groups()
        start_month_num = date_parser.parse(start_month_str + " 2000").month
        end_month_num = date_parser.parse(end_month_str + " 2000").month
        
        start_year = get_month_fy_year(start_month_num)
        end_year = get_month_fy_year(end_month_num)
        
        start_date = datetime(start_year, start_month_num, 1)
        last_day = monthrange(end_year, end_month_num)[1]
        end_date = datetime(end_year, end_month_num, last_day)
        return start_date, end_date, None, None, "single"
    
    # 18. Specific Date with year (e.g., "20 june 2024", "20 sep 2022", "05-06-2024")
    specific_date_patterns = [
        r'\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b(?!\s+to\b)',
        r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b(?!\s+to\b)'
    ]
    
    for pattern in specific_date_patterns:
        match = re.search(pattern, question)
        if match:
            try:
                date_str = match.group(0)
                parsed_date = date_parser.parse(date_str, dayfirst=True)
                # Return the SAME date for both start and end (single day)
                return parsed_date, parsed_date, None, None, "single"
            except Exception as e:
                logger.warning(f"Failed to parse specific date: {e}")
                continue
    
    # 19. Single Month WITH Year (e.g., "may 2025", "may 2021")
    single_month_year_pattern = r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b(?!\s+to\b)'
    match = re.search(single_month_year_pattern, question)
    if match:
        try:
            month_str = match.group(1)
            year = int(match.group(2))
            month_num = date_parser.parse(month_str + " 2000").month
            
            start_date = datetime(year, month_num, 1)
            last_day = monthrange(year, month_num)[1]
            end_date = datetime(year, month_num, last_day)
            return start_date, end_date, None, None, "single"
        except Exception as e:
            logger.warning(f"Failed to parse single month with year: {e}")
            pass
    
    # 20. Single Month WITHOUT Year
    single_month_pattern = r'\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b(?!\s+\d{4})(?!\s+to\b)'
    match = re.search(single_month_pattern, question)
    if match:
        try:
            month_name = match.group(1)
            month_num = date_parser.parse(month_name + " 2000").month
            year = get_month_fy_year(month_num)
            start_date = datetime(year, month_num, 1)
            last_day = monthrange(year, month_num)[1]
            end_date = datetime(year, month_num, last_day)
            return start_date, end_date, None, None, "single"
        except:
            pass
    
    # 21. FY Year or Standalone Year
    fy_match = re.search(r'fy\s*(\w+\s+)?(\d{4})', question)
    if fy_match:
        fy_year = int(fy_match.group(2))
        start_date, end_date = get_fy_dates(fy_year)
        return start_date, end_date, None, None, "single"
    
    year_only_match = re.search(r'\b(20\d{2}|19\d{2})\b', question)
    if year_only_match:
        fy_year = int(year_only_match.group(1))
        start_date, end_date = get_fy_dates(fy_year)
        return start_date, end_date, None, None, "single"
    
    # 22. Default: Current FY
    start_date, end_date = get_fy_dates(current_fy)
    logger.info(f"No specific date detected, using current FY")
    return start_date, end_date, None, None, "single"

# --------------------------------------------
# Helper function to break down data by months
# --------------------------------------------
def get_monthly_breakdown(start_date, end_date, leads, opps, events):
    """Break down data month by month"""
    monthly_data = {}
    current = start_date.replace(day=1)
    
    while current <= end_date:
        month_start = current
        last_day = monthrange(current.year, current.month)[1]
        month_end = datetime(current.year, current.month, last_day)
        
        # Filter data for this month
        month_start_str = month_start.strftime("%d-%m-%Y")
        month_end_str = month_end.strftime("%d-%m-%Y")
        
        # Convert created_date_c to datetime for filtering
        leads_month = leads.copy()
        opps_month = opps.copy()
        events_month = events.copy()
        
        # Filter by date
        if 'created_date_c' in leads_month.columns:
            leads_month['date_parsed'] = pd.to_datetime(leads_month['created_date_c'], format='%d-%m-%Y', errors='coerce')
            leads_month = leads_month[(leads_month['date_parsed'] >= month_start) & (leads_month['date_parsed'] <= month_end)]
        
        if 'created_date_c' in opps_month.columns:
            opps_month['date_parsed'] = pd.to_datetime(opps_month['created_date_c'], format='%d-%m-%Y', errors='coerce')
            opps_month = opps_month[(opps_month['date_parsed'] >= month_start) & (opps_month['date_parsed'] <= month_end)]
        
        if 'created_date_c' in events_month.columns:
            events_month['date_parsed'] = pd.to_datetime(events_month['created_date_c'], format='%d-%m-%Y', errors='coerce')
            events_month = events_month[(events_month['date_parsed'] >= month_start) & (events_month['date_parsed'] <= month_end)]
        
        # Compute metrics for this month
        month_name = current.strftime("%B %Y")
        if not leads_month.empty:
            monthly_data[month_name] = compute_source_wise_funnel(leads_month, opps_month, events_month, "Lead_Source_Sub_Category_c")
        
        # Move to next month
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)
    
    return monthly_data

# --------------------------------------------
# Helper function to break down data by quarters
# --------------------------------------------
def get_quarterly_breakdown(fy_year, leads, opps, events):
    """Break down data quarter by quarter for a fiscal year"""
    quarterly_data = {}
    
    for quarter in range(1, 5):
        q_start, q_end = get_quarter_dates(fy_year, quarter)
        
        # Filter data for this quarter
        leads_q = leads.copy()
        opps_q = opps.copy()
        events_q = events.copy()
        
        # Filter by date
        if 'created_date_c' in leads_q.columns:
            leads_q['date_parsed'] = pd.to_datetime(leads_q['created_date_c'], format='%d-%m-%Y', errors='coerce')
            leads_q = leads_q[(leads_q['date_parsed'] >= q_start) & (leads_q['date_parsed'] <= q_end)]
        
        if 'created_date_c' in opps_q.columns:
            opps_q['date_parsed'] = pd.to_datetime(opps_q['created_date_c'], format='%d-%m-%Y', errors='coerce')
            opps_q = opps_q[(opps_q['date_parsed'] >= q_start) & (opps_q['date_parsed'] <= q_end)]
        
        if 'created_date_c' in events_q.columns:
            events_q['date_parsed'] = pd.to_datetime(events_q['created_date_c'], format='%d-%m-%Y', errors='coerce')
            events_q = events_q[(events_q['date_parsed'] >= q_start) & (events_q['date_parsed'] <= q_end)]
        
        # Compute metrics for this quarter
        q_name = f"Q{quarter} FY{fy_year}-{fy_year+1}"
        if not leads_q.empty:
            quarterly_data[q_name] = compute_source_wise_funnel(leads_q, opps_q, events_q, "Lead_Source_Sub_Category_c")
    
    return quarterly_data

# --------------------------------------------
# API Endpoint
# --------------------------------------------
@app.post("/funnel/subsource/question")
async def source_funnel_from_question(payload: dict = Body(...)):
    question = payload.get("question", "")
    logger.info(f"Incoming request: {question}")
    
    # ---- Extract optional sub-source filter from the question ----
    subsource_filter = extract_subsource_from_question(question)
    if subsource_filter:
        logger.info(f"Sub-source filter detected: '{subsource_filter}'")

    def apply_subsource_filter(leads_df, opps_df, filter_val):
        """Filter leads and opps to a specific sub-source value (case-insensitive)."""
        if not filter_val:
            return leads_df, opps_df
        col = "Lead_Source_Sub_Category_c"
        if col in leads_df.columns:
            leads_df = leads_df[leads_df[col].fillna("").astype(str).str.strip().str.lower() == filter_val.lower()]
        if col in opps_df.columns:
            opps_df = opps_df[opps_df[col].fillna("").astype(str).str.strip().str.lower() == filter_val.lower()]
        return leads_df, opps_df

    # Parse dates using enhanced parser
    parse_result = parse_date_from_question_complete(question)
    
    # Check if it's a multiple periods query
    if isinstance(parse_result, dict) and parse_result.get("type") == "multiple_periods":
        logger.info("Processing multiple periods query")
        result = {
            "status": "success",
            "analysis_type": "Multiple Periods Analysis",
            "subsource_filter": subsource_filter,
            "periods_data": {}
        }
        
        for period in parse_result["periods"]:
            period_name = period["name"]
            start_str = period["start_date"]
            end_str = period["end_date"]
            
            logger.info(f"Processing period: {period_name} ({start_str} to {end_str})")
            
            # Build SQL queries for this period
            date_filter = f"""
                WHERE date_parse(replace(trim(created_date_c), '/', '-'), '%d-%m-%Y') 
                BETWEEN date_parse('{start_str}', '%d-%m-%Y') AND date_parse('{end_str}', '%d-%m-%Y')
            """
            
            lead_sql = f"""
                SELECT lead_id_c, status, customer_feedback_c, created_date_c, Lead_Source_Sub_Category_c, OwnerId
                FROM {CATALOG}.{LEAD_SCHEMA}.{LEAD_TABLE}
                {date_filter}
            """
            
            opp_sql = f"""
                SELECT opportunity_id_c, lead_id_c, sales_order_number_c, created_date_c, Lead_Source_Sub_Category_c
                FROM {CATALOG}.{OPP_SCHEMA}.{OPP_TABLE}
                {date_filter}
            """
            
            event_sql = f"""
                SELECT OwnerId, Subject_c, Appointment_Status_c, created_date_c
                FROM {CATALOG}.{EVENT_SCHEMA}.{EVENT_TABLE}
                {date_filter}
            """
            
            try:
                leads = query_presto(CATALOG, LEAD_SCHEMA, lead_sql)
                opps = query_presto(CATALOG, OPP_SCHEMA, opp_sql)
                events = query_presto(CATALOG, EVENT_SCHEMA, event_sql)
                
                leads, opps = apply_subsource_filter(leads, opps, subsource_filter)
                
                if not leads.empty:
                    period_metrics = compute_source_wise_funnel(leads, opps, events, "Lead_Source_Sub_Category_c")
                    result["periods_data"][period_name] = period_metrics
                else:
                    result["periods_data"][period_name] = {"message": "No data available for this period"}
                    
            except Exception as e:
                logger.error(f"Query failed for {period_name}: {e}")
                result["periods_data"][period_name] = {"error": str(e)}
        
        return result
    
    # Original single/range period logic
    start_date, end_date, comp_start, comp_end, period_type = parse_result
    
    if not start_date or not end_date:
        return {"status": "error", "message": "Could not parse dates from question"}
    
    # ... rest of your existing endpoint code remains the same
    # (Continue with your existing logic for single period, mom, qoq, yoy)
    
    start_str = start_date.strftime("%d-%m-%Y")
    end_str = end_date.strftime("%d-%m-%Y")
    
    logger.info(f"Date range: {start_str} to {end_str}, Period type: {period_type}")
    
    # Build SQL queries
    date_filter = f"""
        WHERE date_parse(replace(trim(created_date_c), '/', '-'), '%d-%m-%Y') 
        BETWEEN date_parse('{start_str}', '%d-%m-%Y') AND date_parse('{end_str}', '%d-%m-%Y')
    """
    
    lead_sql = f"""
        SELECT lead_id_c, status, customer_feedback_c, created_date_c, Lead_Source_Sub_Category_c, OwnerId
        FROM {CATALOG}.{LEAD_SCHEMA}.{LEAD_TABLE}
        {date_filter}
    """
    
    opp_sql = f"""
        SELECT opportunity_id_c, lead_id_c, sales_order_number_c, created_date_c, Lead_Source_Sub_Category_c
        FROM {CATALOG}.{OPP_SCHEMA}.{OPP_TABLE}
        {date_filter}
    """
    
    event_sql = f"""
        SELECT OwnerId, Subject_c, Appointment_Status_c, created_date_c
        FROM {CATALOG}.{EVENT_SCHEMA}.{EVENT_TABLE}
        {date_filter}
    """
    
    try:
        leads = query_presto(CATALOG, LEAD_SCHEMA, lead_sql)
        opps = query_presto(CATALOG, OPP_SCHEMA, opp_sql)
        events = query_presto(CATALOG, EVENT_SCHEMA, event_sql)
    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        return {"status":"error","message":str(e)}
    
    # Apply sub-source filter if provided
    leads, opps = apply_subsource_filter(leads, opps, subsource_filter)

    if leads.empty:
        logger.warning("No leads found for the selected period/sub-source")
        return {"status":"no_data","message":"No leads found for the selected period/sub-source"}
    
    result = {
        "status": "success",
        "period_type": period_type,
        "current_period": f"{start_str} to {end_str}",
        "subsource_filter": subsource_filter
    }
    
    # Handle different period types (mom, qoq, yoy, single)
    if period_type == "mom":
        logger.info("Processing MoM analysis")
        monthly_data = get_monthly_breakdown(start_date, end_date, leads, opps, events)
        result["monthly_breakdown"] = monthly_data
        result["analysis_type"] = "Month on Month"
        
    elif period_type == "qoq":
        logger.info("Processing QoQ analysis")
        current_fy = get_fy_for_date(start_date)
        quarterly_data = get_quarterly_breakdown(current_fy, leads, opps, events)
        result["quarterly_breakdown"] = quarterly_data
        result["analysis_type"] = "Quarter on Quarter"
        
    elif period_type == "yoy":
        logger.info("Processing YoY analysis")
        current_fy = get_fy_for_date(start_date)
        
        current_fy_metrics = compute_source_wise_funnel(leads, opps, events, "Lead_Source_Sub_Category_c")
        result[f"FY {current_fy}-{current_fy+1}"] = { "metrics": current_fy_metrics, "totals": calculate_master_totals(current_fy_metrics)}
        
        if comp_start and comp_end:
            comp_start_str = comp_start.strftime("%d-%m-%Y")
            comp_end_str = comp_end.strftime("%d-%m-%Y")
            
            comp_filter = f"""
                WHERE date_parse(replace(trim(created_date_c), '/', '-'), '%d-%m-%Y') 
                BETWEEN date_parse('{comp_start_str}', '%d-%m-%Y') AND date_parse('{comp_end_str}', '%d-%m-%Y')
            """
            
            comp_lead_sql = f"SELECT lead_id_c, status, customer_feedback_c, created_date_c, Lead_Source_Sub_Category_c, OwnerId FROM {CATALOG}.{LEAD_SCHEMA}.{LEAD_TABLE} {comp_filter}"
            comp_opp_sql = f"SELECT opportunity_id_c, lead_id_c, sales_order_number_c, created_date_c, Lead_Source_Sub_Category_c FROM {CATALOG}.{OPP_SCHEMA}.{OPP_TABLE} {comp_filter}"
            comp_event_sql = f"SELECT OwnerId, Subject_c, Appointment_Status_c, created_date_c FROM {CATALOG}.{EVENT_SCHEMA}.{EVENT_TABLE} {comp_filter}"
            
            try:
                comp_leads = query_presto(CATALOG, LEAD_SCHEMA, comp_lead_sql)
                comp_opps = query_presto(CATALOG, OPP_SCHEMA, comp_opp_sql)
                comp_events = query_presto(CATALOG, EVENT_SCHEMA, comp_event_sql)
                
                comp_leads, comp_opps = apply_subsource_filter(comp_leads, comp_opps, subsource_filter)
                
                if not comp_leads.empty:
                    last_fy_metrics = compute_source_wise_funnel(comp_leads, comp_opps, comp_events, "Lead_Source_Sub_Category_c")
                    result[f"FY {current_fy-1}-{current_fy}"] = {  "metrics": last_fy_metrics,  "totals": calculate_master_totals(last_fy_metrics)}
            except Exception as e:
                logger.warning(f"Last FY query failed: {e}")
        
        result["analysis_type"] = "Year on Year"
    else:
        source_funnel = compute_source_wise_funnel(leads, opps, events, "Lead_Source_Sub_Category_c")
        totals = calculate_master_totals(source_funnel)
        result["sub_source_wise_metrics"] = source_funnel
        result["totals"] = totals
    
    return result

# --------------------------------------------
# Health Check Endpoint
# --------------------------------------------
@app.get("/")
async def health_check():
    return {
        "status": "ok", 
        "message": "Enhanced Funnel Analytics API with MoM, YoY, QoQ support",
        "supported_features": [
            "Month on Month (MoM)",
            "Year on Year (YoY)",
            "Quarter on Quarter (QoQ)",
            "This/Last Month/Year/Quarter",
            "Q1/Q2/Q3/Q4",
            "Specific dates and ranges",
            "Last N days",
            "Fiscal Year (April-March)"
        ]
    }

# --------------------------------------------
# Run the application
# --------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
