from fastapi import FastAPI, Body
import pandas as pd
import prestodb
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai import Credentials
from typing import Optional, Tuple, Dict, Any, List, Iterable, Union
import os
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import logging
from datetime import datetime, timedelta
import re
from calendar import monthrange

# Fields containing these markers will NOT be summed
NON_ADDITIVE_MARKERS = ["%", ":"]


def _is_additive_key(key: str) -> bool:
    return not any(marker in key for marker in NON_ADDITIVE_MARKERS)


def _normalize_to_rows(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


def calculate_master_totals(data: Any) -> Dict[str, Union[int, float]]:
    rows = _normalize_to_rows(data)
    totals: Dict[str, float] = {}
    for row in rows:
        for key, value in row.items():
            if not _is_additive_key(key):
                continue
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
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
        logging.FileHandler("funnel_tool.log", mode="a", encoding="utf-8"),
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

app = FastAPI(title="Watsonx Funnel Analytics Tool")

# --------------------------------------------
# Presto Query Helper
# --------------------------------------------
def query_presto(catalog: str, schema: str, sql: str) -> pd.DataFrame:
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
# BUG 1 FIX: Project name extractor
# Add or extend PROJECT_ALIASES with your actual project names.
# Keys are lowercase substrings the user might type; values are the
# exact strings stored in project_c (also lowercase for comparison).
# --------------------------------------------
PROJECT_ALIASES: Dict[str, str] = {
    "wave city":            "wave city",
    "wavecity":             "wave city",
    "wave executive":       "wave executive floors",
    "wave amore":           "wave amore",
    # add more as needed, e.g.:
    # "my project name":  "exact db value",
}

def extract_project_filter(question: str) -> Optional[str]:
    """
    Return the canonical project name (as stored in project_c, lowercase)
    if the question mentions a known project, else None.
    """
    q = question.lower()
    for alias, canonical in PROJECT_ALIASES.items():
        if alias in q:
            logger.info(f"Project filter detected: '{canonical}'")
            return canonical
    return None


# --------------------------------------------
# BUG 2 FIX: customer_feedback_c filter extractor
# --------------------------------------------
def extract_feedback_filter(question: str) -> Optional[str]:
    """
    Return the exact customer_feedback_c value to filter on, or None.
    Order matters: check 'not interested' before 'interested'.
    """
    q = question.lower()
    if re.search(r'\bnot[- ]?interested\b', q):
        logger.info("Feedback filter detected: 'not interested'")
        return "not interested"
    if re.search(r'\binterested\b', q):
        logger.info("Feedback filter detected: 'interested'")
        return "interested"
    if re.search(r'\bjunk\b', q):
        logger.info("Feedback filter detected: 'junk'")
        return "junk"
    return None


# --------------------------------------------
# Funnel Computation
# --------------------------------------------
def compute_funnel(leads: pd.DataFrame, events: pd.DataFrame, opps: pd.DataFrame):
    logger.info("Starting funnel computation...")

    if leads is None or events is None or opps is None:
        logger.error("One or more input dataframes are None.")
        return {}

    for df, name in [(leads, "leads"), (events, "events"), (opps, "opps")]:
        for col in df.columns:
            df[col] = df[col].fillna("").astype(str)
        logger.debug(f"{name} columns converted to string types")

    # --- LEAD METRICS ---
    total_leads = len(leads)
    cf = leads.get("customer_feedback_c", pd.Series([""] * total_leads)).str.strip().str.lower()

    junk_leads = (cf == "junk").sum()
    sol_leads = (cf == "interested").sum()

    # BUG 2 FIX: exclude blank / null / nan rows from valid leads count
    valid_leads = (cf != "junk").sum()

    # --- EVENT METRICS ---
    subject = events.get("subject_c", pd.Series([""] * len(events))).str.strip().str.lower()
    status = events.get("appointment_status_c", pd.Series([""] * len(events))).str.strip().str.lower()
    meeting_booked = (subject == "personal appointment booked").sum()
    meeting_done = ((subject == "personal appointment booked") & (status == "completed")).sum()

    # --- OPPORTUNITY METRICS ---
    sales_col = opps.get("sales_order_number_c", pd.Series([""] * len(opps))).astype(str).str.strip().str.lower()
    sales_done = ((sales_col != "") & (sales_col != "nan")).sum()

    # --- DERIVED RATIOS ---
    junk_percent = round((junk_leads / total_leads) * 100, 2) if total_leads else 0
    tl_vl = round(total_leads / valid_leads, 2) if valid_leads else 0
    vl_sol = round(valid_leads / sol_leads, 2) if sol_leads else 0
    sol_mb = round(sol_leads / meeting_booked, 2) if meeting_booked else 0
    mb_md = round(meeting_booked / meeting_done, 2) if meeting_done else 0
    md_sd = round(meeting_done / sales_done, 2) if sales_done else 0
    tl_sd = round(total_leads / sales_done, 2) if sales_done else 0

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
        "TL:SD": tl_sd
    }

    logger.info(f"Funnel computation completed: {funnel_data}")
    return sort_funnel_by_numeric_desc(funnel_data)


# --------------------------------------------
# Product-Wise Funnel (Dynamic)
# --------------------------------------------
def compute_product_wise_funnel(
    leads: pd.DataFrame,
    events: pd.DataFrame,
    opps: pd.DataFrame,
    header_col: str = "project_c"
):
    logger.info(f"Starting dynamic product-wise funnel computation based on '{header_col}'...")

    for df in (leads, events, opps):
        if header_col not in df.columns:
            df[header_col] = ""
        df["__col_normalized__"] = df[header_col].fillna("").astype(str).str.strip().str.lower()

    unique_values = pd.concat([
        leads["__col_normalized__"],
        events["__col_normalized__"],
        opps["__col_normalized__"]
    ]).unique()
    unique_values = [v for v in unique_values if v]

    exclude_projects = ["wave executive floors", "wave amore"]
    unique_values = [v for v in unique_values if v not in exclude_projects]
    output = {}

    for val in unique_values:
        leads_p = leads[leads["__col_normalized__"] == val].copy()
        events_p = events[events["__col_normalized__"] == val].copy()
        opps_p = opps[opps["__col_normalized__"] == val].copy()

        display_name = val.title()

        if leads_p.empty and events_p.empty and opps_p.empty:
            output[display_name] = {k: 0 for k in [
                "Total Leads", "Valid Leads", "Junk Leads", "SOL Leads (Interested)",
                "Meeting Booked", "Meeting Done", "Sales Done", "Junk %", "TL:VL",
                "VL:SOL", "SOL:MB", "MB:MD", "MD:SD"
            ]}
            continue

        metrics = compute_funnel(leads_p, events_p, opps_p)
        output[display_name] = metrics

    for df in (leads, events, opps):
        if "__col_normalized__" in df.columns:
            df.drop(columns="__col_normalized__", inplace=True)

    logger.info("Dynamic product-wise funnel computation completed.")
    return sort_funnel_by_numeric_desc(output, return_as_list=True)


# --------------------------------------------
# NLP Date Parser Helpers
# --------------------------------------------
MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
}


def sort_funnel_by_numeric_desc(data: Any, return_as_list: bool = False) -> Any:
    if not isinstance(data, dict) or not data:
        return data

    first_value = next(iter(data.values()))

    if isinstance(first_value, dict):
        first_inner_value = next(iter(first_value.values()), None) if first_value else None

        if isinstance(first_inner_value, dict):
            sorted_data = {}
            for key in sorted(data.keys()):
                sorted_data[key] = sort_funnel_by_numeric_desc(data[key], return_as_list=return_as_list)
            return sorted_data
        else:
            def get_sort_key(item):
                key, metrics = item
                for metric_name, metric_value in metrics.items():
                    if isinstance(metric_value, (int, float)) and not any(
                        marker in metric_name for marker in ["%", ":"]
                    ):
                        return -metric_value
                return 0

            sorted_items = sorted(data.items(), key=get_sort_key)

            if return_as_list:
                return [{"name": name, **metrics} for name, metrics in sorted_items]

            return dict(sorted_items)

    return data


def fiscal_quarter_start_end(fy_year: int, q: int) -> Tuple[datetime, datetime]:
    if q == 1:
        return datetime(fy_year, 4, 1), datetime(fy_year, 6, 30)
    elif q == 2:
        return datetime(fy_year, 7, 1), datetime(fy_year, 9, 30)
    elif q == 3:
        return datetime(fy_year, 10, 1), datetime(fy_year, 12, 31)
    else:  # q == 4
        return datetime(fy_year + 1, 1, 1), datetime(fy_year + 1, 3, 31)


def month_start_end(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    last_day = monthrange(year, month)[1]
    return start, datetime(year, month, last_day)


def parse_explicit_range(text: str) -> Optional[Tuple[datetime, datetime, bool]]:
    today = datetime.today()
    fy_start_year = today.year if today.month >= 4 else today.year - 1

    patterns = [
        r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+(?:to|-)\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})',
        r'([A-Za-z]+\s+\d{4})\s+(?:to|-)\s+([A-Za-z]+\s+\d{4})',
        r'\b([A-Za-z]+)\s+(?:to|-)\s+([A-Za-z]+)\b(?!\s*\d)',
        r'\b([A-Za-z]+)\s+(?:to|-)\s+([A-Za-z]+)\s+(\d{4})',
        r'(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})\s+(?:to|-)\s+(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})',
        r'from\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+(?:to|-)\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})',
        r'from\s+([A-Za-z]+\s+\d{4})\s+(?:to|-)\s+([A-Za-z]+\s+\d{4})',
        r'from\s+([A-Za-z]+)\s+(?:to|-)\s+([A-Za-z]+)(?!\s*\d)',
        r'from\s+(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})\s+(?:to|-)\s+(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})'
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            groups = m.groups()
            is_month_range = False

            if len(groups) == 3 and groups[2] and groups[2].isdigit():
                month_start_str = groups[0].strip()
                month_end_str = groups[1].strip()
                year = int(groups[2])
                if month_start_str.lower() in MONTH_NAMES and month_end_str.lower() in MONTH_NAMES:
                    s_mn = MONTH_NAMES[month_start_str.lower()]
                    e_mn = MONTH_NAMES[month_end_str.lower()]
                    s_yr = year if s_mn >= 4 else year + 1
                    e_yr = year if e_mn >= 4 else year + 1
                    a_dt = datetime(s_yr, s_mn, 1)
                    b_dt = datetime(e_yr, e_mn, monthrange(e_yr, e_mn)[1])
                    return a_dt, b_dt, True

            a = groups[0].strip()
            b = groups[1].strip() if len(groups) > 1 else None
            if not b:
                continue

            a_dt = None
            b_dt = None

            if (a.lower() in MONTH_NAMES and b.lower() in MONTH_NAMES and
                    not any(c.isdigit() for c in a) and not any(c.isdigit() for c in b)):
                s_mn = MONTH_NAMES[a.lower()]
                e_mn = MONTH_NAMES[b.lower()]
                s_yr = fy_start_year if s_mn >= 4 else fy_start_year + 1
                e_yr = fy_start_year if e_mn >= 4 else fy_start_year + 1
                a_dt = datetime(s_yr, s_mn, 1)
                b_dt = datetime(e_yr, e_mn, monthrange(e_yr, e_mn)[1])
                return a_dt, b_dt, True

            if re.match(r'^[A-Za-z]+\s+\d{4}$', a) and re.match(r'^[A-Za-z]+\s+\d{4}$', b):
                is_month_range = True

            date_formats = [
                "%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%B %Y", "%b %Y"
            ]
            for fmt in date_formats:
                try:
                    a_dt = datetime.strptime(a, fmt)
                    break
                except Exception:
                    pass
            for fmt in date_formats:
                try:
                    b_dt = datetime.strptime(b, fmt)
                    break
                except Exception:
                    pass

            if a_dt is None or b_dt is None:
                continue

            if re.match(r'^[A-Za-z]+\s+\d{4}$', a):
                a_dt = a_dt.replace(day=1)
            if re.match(r'^[A-Za-z]+\s+\d{4}$', b):
                b_dt = b_dt.replace(day=monthrange(b_dt.year, b_dt.month)[1])

            return a_dt, b_dt, is_month_range

    return None


def detect_and_split_query(question: str) -> List[str]:
    q = question.strip().lower()
    if ' and ' not in q:
        return [question]
    parts = [part.strip() for part in q.split(' and ')]
    parts = [p for p in parts if p]
    if len(parts) > 1:
        logger.info(f"Detected 'AND' query: split into {len(parts)} parts: {parts}")
        return parts
    return [question]


def generate_months_in_range(start_dt: datetime, end_dt: datetime) -> List[Tuple[str, Dict[str, str]]]:
    months = []
    current = start_dt.replace(day=1)
    while current <= end_dt:
        last_day = monthrange(current.year, current.month)[1]
        month_end = datetime(current.year, current.month, last_day)
        label = f"{current.strftime('%B')} {current.year}"
        months.append((label, {
            "start": current.strftime("%d-%m-%Y"),
            "end": month_end.strftime("%d-%m-%Y")
        }))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)
    return months


# --------------------------------------------
# parse_question_dates  (BUG 3 FIX: week logic added)
# --------------------------------------------
def parse_question_dates(question: str) -> Dict[str, Any]:
    q = (question or "").strip().lower()
    today = datetime.today()
    logger.info(f"Parsing question: '{q}'")

    current_year = today.year
    current_month = today.month
    fy_start_year = current_year if current_month >= 4 else current_year - 1
    fy_start = datetime(fy_start_year, 4, 1)
    fy_end = datetime(fy_start_year + 1, 3, 31)

    # ---- AND queries ----
    split_parts = detect_and_split_query(q)
    if len(split_parts) > 1:
        periods = []
        for part in split_parts:
            parsed_part = parse_question_dates(part)
            if parsed_part.get("type") == "single" and "period" in parsed_part:
                periods.append({"label": part.title(), "range": parsed_part["period"]})
            elif parsed_part.get("type") == "multiple_periods":
                periods.extend(parsed_part["periods"])
        if periods:
            logger.info(f"Generated multiple periods: {[p['label'] for p in periods]}")
            return {"type": "multiple_periods", "periods": periods}

    # ---- last N months ----
    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+months?', q)
    if m:
        n = int(m.group(1))
        periods = []
        for i in range(1, n + 1):
            target_year = current_year
            target_month = current_month - i
            while target_month <= 0:
                target_month += 12
                target_year -= 1
            s_dt, e_dt = month_start_end(target_year, target_month)
            periods.append({
                "label": f"{s_dt.strftime('%B')} {s_dt.year}",
                "range": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}
            })
        periods.reverse()
        return {"type": "last_n_months", "n": n, "periods": periods}

    # ---- last N quarters ----
    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+quarters?', q)
    if m:
        n = int(m.group(1))
        if current_month >= 4 and current_month <= 6:
            cur_q, fy_year_now = 1, current_year
        elif current_month >= 7 and current_month <= 9:
            cur_q, fy_year_now = 2, current_year
        elif current_month >= 10 and current_month <= 12:
            cur_q, fy_year_now = 3, current_year
        else:
            cur_q, fy_year_now = 4, current_year - 1
        periods = []
        for i in range(1, n + 1):
            q_num = cur_q - i
            fy_year = fy_year_now
            while q_num <= 0:
                q_num += 4
                fy_year -= 1
            s_dt, e_dt = fiscal_quarter_start_end(fy_year, q_num)
            periods.append({
                "label": f"Q{q_num} FY{fy_year}-{fy_year + 1}",
                "range": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}
            })
        periods.reverse()
        return {"type": "last_n_quarters", "n": n, "periods": periods}

    # ---- explicit date range ----
    explicit = parse_explicit_range(q)
    if explicit:
        s_dt, e_dt, is_month_range = explicit
        if is_month_range:
            periods = [{"label": lbl, "range": rng}
                       for lbl, rng in generate_months_in_range(s_dt, e_dt)]
            return {
                "type": "monthly_range",
                "periods": periods,
                "range_description": f"{s_dt.strftime('%B %Y')} to {e_dt.strftime('%B %Y')}"
            }
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    # ---- date range within same month: "1 to 12 june" ----
    m = re.search(
        r'(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')(?:\s+(\d{4}))?', q
    )
    if m:
        start_day, end_day = int(m.group(1)), int(m.group(2))
        month_num = MONTH_NAMES[m.group(3)]
        year = int(m.group(4)) if m.group(4) else (fy_start_year if month_num >= 4 else fy_start_year + 1)
        try:
            return {"type": "single", "period": {
                "start": datetime(year, month_num, start_day).strftime("%d-%m-%Y"),
                "end": datetime(year, month_num, end_day).strftime("%d-%m-%Y")
            }}
        except ValueError:
            pass

    # ---- <month> till date ----
    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\b\s+(?:till|to)\s+date', q)
    if m:
        month_num = MONTH_NAMES[m.group(1)]
        year = fy_start_year if month_num >= 4 else fy_start_year + 1
        start_dt = datetime(year, month_num, 1)
        if start_dt > today:
            start_dt = fy_start
        return {"type": "single", "period": {
            "start": start_dt.strftime("%d-%m-%Y"), "end": today.strftime("%d-%m-%Y")
        }}

    # ---- <day> <month> [year] till date ----
    m = re.search(
        r'(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')\s*(\d{4})?\s+(?:till|to)\s+date', q
    )
    if m:
        day = int(m.group(1))
        month_num = MONTH_NAMES[m.group(2)]
        year = int(m.group(3)) if m.group(3) else (fy_start_year if month_num >= 4 else fy_start_year + 1)
        try:
            start_dt = datetime(year, month_num, day)
            if start_dt > today:
                start_dt = fy_start
            return {"type": "single", "period": {
                "start": start_dt.strftime("%d-%m-%Y"), "end": today.strftime("%d-%m-%Y")
            }}
        except Exception:
            pass

    # ---- MOM ----
    mom_match = re.search(r'\b(month[-\s]?on[-\s]?month|mom|monthly|month[-\s]?wise)\b', q)
    if mom_match:
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', q)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', q)
        if year_match:
            target_fy = int(year_match.group(1))
            return {"type": "mom_all", "fy_start_year": target_fy,
                    "fy_start": datetime(target_fy, 4, 1).strftime("%d-%m-%Y"),
                    "fy_end": datetime(target_fy + 1, 3, 31).strftime("%d-%m-%Y")}
        elif last_year_match:
            target_fy = fy_start_year - 1
            return {"type": "mom_all", "fy_start_year": target_fy,
                    "fy_start": datetime(target_fy, 4, 1).strftime("%d-%m-%Y"),
                    "fy_end": datetime(target_fy + 1, 3, 31).strftime("%d-%m-%Y")}
        else:
            return {"type": "mom_all", "fy_start_year": fy_start_year,
                    "fy_start": fy_start.strftime("%d-%m-%Y"),
                    "fy_end": fy_end.strftime("%d-%m-%Y")}

    # ---- QOQ ----
    qoq_match = re.search(r'\b(quarter[-\s]?on[-\s]?quarter|qoq|quarterly|quarter[-\s]?wise)\b', q)
    if qoq_match:
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', q)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', q)
        if year_match:
            return {"type": "qoq_all", "fy_start_year": int(year_match.group(1))}
        elif last_year_match:
            return {"type": "qoq_all", "fy_start_year": fy_start_year - 1}
        else:
            return {"type": "qoq_all", "fy_start_year": fy_start_year}

    # ---- YOY ----
    yoy_match = re.search(r'\b(year[-\s]?on[-\s]?year|yoy|y-o-y|year[-\s]?wise)\b', q)
    if yoy_match:
        year_match = re.search(r'(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})', q)
        last_year_match = re.search(r'\b(?:last|previous)\s+(?:fy|year|financial\s+year)\b', q)
        if year_match:
            target_fy = int(year_match.group(1))
            return {"type": "yoy_two", "last_year": target_fy - 1, "this_year": target_fy}
        elif last_year_match:
            target_fy = fy_start_year - 1
            return {"type": "yoy_two", "last_year": target_fy - 1, "this_year": target_fy}
        else:
            return {"type": "yoy_two", "last_year": fy_start_year - 1, "this_year": fy_start_year}

    # ---- last N days ----
    m = re.search(r'(?:last|past)\s+(\d{1,4})\s+days?', q)
    if m:
        n = int(m.group(1))
        return {"type": "single", "period": {
            "start": (today - timedelta(days=n - 1)).strftime("%d-%m-%Y"),
            "end": today.strftime("%d-%m-%Y")
        }}

    # ================================================================
    # BUG 3 FIX: Week-based date parsing
    # Must come BEFORE "last week" so "last N weeks" doesn't fall through
    # ================================================================

    # ---- last N weeks (excludes current week) ----
    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+weeks?', q)
    if m:
        n = int(m.group(1))
        days_since_monday = today.weekday()          # Monday = 0
        this_week_start = today - timedelta(days=days_since_monday)
        # End = last day of previous complete week (last Sunday)
        range_end = this_week_start - timedelta(days=1)
        # Start = n weeks before this_week_start
        range_start = this_week_start - timedelta(weeks=n)
        logger.info(f"last {n} weeks: {range_start.date()} → {range_end.date()}")
        return {"type": "single", "period": {
            "start": range_start.strftime("%d-%m-%Y"),
            "end": range_end.strftime("%d-%m-%Y")
        }}

    # ---- last week (single) ----
    if re.search(r'\blast\s+week\b', q):
        days_since_monday = today.weekday()
        this_week_start = today - timedelta(days=days_since_monday)
        last_week_end = this_week_start - timedelta(days=1)      # last Sunday
        last_week_start = last_week_end - timedelta(days=6)      # last Monday
        logger.info(f"last week: {last_week_start.date()} → {last_week_end.date()}")
        return {"type": "single", "period": {
            "start": last_week_start.strftime("%d-%m-%Y"),
            "end": last_week_end.strftime("%d-%m-%Y")
        }}

    # ---- this week ----
    if re.search(r'\bthis\s+week\b', q):
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)   # this Monday
        week_end = week_start + timedelta(days=6)                # this Sunday
        logger.info(f"this week: {week_start.date()} → {week_end.date()}")
        return {"type": "single", "period": {
            "start": week_start.strftime("%d-%m-%Y"),
            "end": week_end.strftime("%d-%m-%Y")
        }}

    # ================================================================

    # ---- this month / last month ----
    if re.search(r'\blast month\b', q):
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return {"type": "single", "period": {
            "start": last_month_start.strftime("%d-%m-%Y"),
            "end": last_month_end.strftime("%d-%m-%Y")
        }}

    if re.search(r'\bthis month\b', q):
        start = today.replace(day=1)
        end = today.replace(day=monthrange(today.year, today.month)[1])
        return {"type": "single", "period": {
            "start": start.strftime("%d-%m-%Y"), "end": end.strftime("%d-%m-%Y")
        }}

    # ---- this/last fiscal year ----
    if re.search(r'\b(last fy|last year|last fiscal year|last financial year)\b', q):
        last_fy = fy_start_year - 1
        return {"type": "single", "period": {
            "start": datetime(last_fy, 4, 1).strftime("%d-%m-%Y"),
            "end": datetime(last_fy + 1, 3, 31).strftime("%d-%m-%Y")
        }}

    if re.search(r'\b(this fy|this year|this fiscal year|this financial year|current fy|current fiscal year)\b', q):
        return {"type": "single", "period": {
            "start": fy_start.strftime("%d-%m-%Y"), "end": fy_end.strftime("%d-%m-%Y")
        }}

    # ---- fiscal quarter parsing ----
    m = re.search(r'\b(?:q(?:uarter)?\s*[-\s]*([1-4])|quarter\s+([1-4]))\b(?:\s*(?:of\s*)?(?:fy\s*)?(\d{4}))?', q)
    if m:
        qnum = int(m.group(1) or m.group(2))
        fy_year = int(m.group(3)) if m.group(3) else fy_start_year
        s_dt, e_dt = fiscal_quarter_start_end(fy_year, qnum)
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    # ---- this quarter / last quarter ----
    if re.search(r'\blast quarter\b', q):
        if current_month >= 4 and current_month <= 6:
            cur_q, fy_year_now = 1, current_year
        elif current_month >= 7 and current_month <= 9:
            cur_q, fy_year_now = 2, current_year
        elif current_month >= 10 and current_month <= 12:
            cur_q, fy_year_now = 3, current_year
        else:
            cur_q, fy_year_now = 4, current_year - 1
        last_q = cur_q - 1
        fy_for_last_q = fy_year_now if last_q > 0 else fy_year_now - 1
        if last_q == 0:
            last_q = 4
        s_dt, e_dt = fiscal_quarter_start_end(fy_for_last_q, last_q)
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    if re.search(r'\bthis quarter\b', q):
        if current_month >= 4 and current_month <= 6:
            cur_q, fy_year_now = 1, current_year
        elif current_month >= 7 and current_month <= 9:
            cur_q, fy_year_now = 2, current_year
        elif current_month >= 10 and current_month <= 12:
            cur_q, fy_year_now = 3, current_year
        else:
            cur_q, fy_year_now = 4, current_year - 1
        s_dt, e_dt = fiscal_quarter_start_end(fy_year_now, cur_q)
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    # ---- specific date: day + month name ----
    m = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')(?:\s+(\d{4}))?\b', q)
    if not m:
        m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(\d{1,2})(?:\s+(\d{4}))?\b', q)
        if m:
            month_num = MONTH_NAMES[m.group(1)]
            day = int(m.group(2))
            year = int(m.group(3)) if m.group(3) else (fy_start_year if month_num >= 4 else fy_start_year + 1)
            try:
                dt = datetime(year, month_num, day)
                return {"type": "single", "period": {
                    "start": dt.strftime("%d-%m-%Y"), "end": dt.strftime("%d-%m-%Y")
                }}
            except ValueError:
                pass
    else:
        day = int(m.group(1))
        month_num = MONTH_NAMES[m.group(2)]
        year = int(m.group(3)) if m.group(3) else (fy_start_year if month_num >= 4 else fy_start_year + 1)
        try:
            dt = datetime(year, month_num, day)
            return {"type": "single", "period": {
                "start": dt.strftime("%d-%m-%Y"), "end": dt.strftime("%d-%m-%Y")
            }}
        except ValueError:
            pass

    # ---- month name with optional year ----
    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\b(?:\s+(?:of\s*)?(?:fy\s*)?(\d{4}))?', q)
    if m:
        month_num = MONTH_NAMES[m.group(1)]
        year_token = m.group(2)
        if year_token:
            year = int(year_token)
            s_dt, e_dt = month_start_end(year, month_num) if month_num >= 4 else month_start_end(year + 1, month_num)
        else:
            year = fy_start_year if month_num >= 4 else fy_start_year + 1
            s_dt, e_dt = month_start_end(year, month_num)
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    # ---- YYYY-MM ----
    m = re.search(r'\b(\d{4})-(\d{2})\b', q)
    if m:
        s_dt, e_dt = month_start_end(int(m.group(1)), int(m.group(2)))
        return {"type": "single", "period": {
            "start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")
        }}

    # ---- numeric date ----
    m = re.search(r'\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})\b', q)
    if m:
        token = m.group(1)
        for sep, fmt in [('-', "%d-%m-%Y"), ('/', "%d/%m/%Y"), ('.', "%d.%m.%Y")]:
            if sep in token:
                try:
                    dt = datetime.strptime(token, fmt)
                    return {"type": "single", "period": {
                        "start": dt.strftime("%d-%m-%Y"), "end": dt.strftime("%d-%m-%Y")
                    }}
                except ValueError:
                    continue

    # ---- single year → financial year ----
    m = re.search(r'\b(?:fy\s*)?(?:financial\s+year\s+)?(20\d{2})(?:\s*-\s*(?:20)?\d{2})?\b', q)
    if m:
        y = int(m.group(1))
        logger.info(f"Year {y} interpreted as FY {y}-{y + 1}")
        return {"type": "single", "period": {
            "start": datetime(y, 4, 1).strftime("%d-%m-%Y"),
            "end": datetime(y + 1, 3, 31).strftime("%d-%m-%Y")
        }}

    # Fallback
    logger.info("No pattern matched; falling back to current fiscal year.")
    return {"type": "single", "period": {
        "start": fy_start.strftime("%d-%m-%Y"), "end": fy_end.strftime("%d-%m-%Y")
    }}


# --------------------------------------------
# Fiscal year month/quarter iterators
# --------------------------------------------
def months_of_fiscal_year(fy_start_year: int) -> Iterable[Tuple[str, Dict[str, str]]]:
    for mo in range(4, 13):
        s, e = month_start_end(fy_start_year, mo)
        yield f"{s.strftime('%B')} {s.year}", {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}
    for mo in range(1, 4):
        s, e = month_start_end(fy_start_year + 1, mo)
        yield f"{s.strftime('%B')} {s.year}", {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}


def quarters_of_fiscal_year(fy_start_year: int) -> Iterable[Tuple[str, Dict[str, str]]]:
    for qn in range(1, 5):
        s, e = fiscal_quarter_start_end(fy_start_year, qn)
        labels = {1: "Apr–Jun", 2: "Jul–Sep", 3: "Oct–Dec", 4: "Jan–Mar"}
        yield (
            f"Q{qn} {fy_start_year}-{fy_start_year + 1} ({labels[qn]})",
            {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}
        )


# --------------------------------------------
# run_funnel_for_range
# BUG 1 FIX: accepts project_filter and feedback_filter,
#            injects them as SQL WHERE clauses
# --------------------------------------------
def run_funnel_for_range(
    range_dict: Dict[str, str],
    header_col: str = "project_c",
    project_filter: Optional[str] = None,
    feedback_filter: Optional[str] = None,
) -> Dict[str, Any]:
    start_date = range_dict["start"]
    end_date = range_dict["end"]

    date_clause = (
        f"date_parse(replace(trim(created_date_c), '/', '-'), '%d-%m-%Y') "
        f"BETWEEN date_parse('{start_date}', '%d-%m-%Y') AND date_parse('{end_date}', '%d-%m-%Y')"
    )

    # BUG 1 FIX: project filter injected into SQL
    project_clause = ""
    if project_filter:
        safe_proj = project_filter.replace("'", "''")
        project_clause = f"AND lower(trim({header_col})) = '{safe_proj}'"

    # BUG 2 FIX: feedback filter injected into leads SQL only
    feedback_clause = ""
    if feedback_filter:
        safe_fb = feedback_filter.replace("'", "''")
        feedback_clause = f"AND lower(trim(customer_feedback_c)) = '{safe_fb}'"

    where_base = f"WHERE {date_clause} {project_clause}"
    where_leads = f"WHERE {date_clause} {project_clause} {feedback_clause}"

    logger.info(
        f"Running funnel for {start_date} -> {end_date} | "
        f"project={project_filter!r} | feedback={feedback_filter!r}"
    )

    header_sql_col = header_col if header_col else "project_c"

    lead_sql = f"""
        SELECT lead_id_c, status, customer_feedback_c, created_date_c, {header_sql_col} AS project_c
        FROM {CATALOG}.{LEAD_SCHEMA}.{LEAD_TABLE}
        {where_leads}
    """
    event_sql = f"""
        SELECT activity_id_c, subject_c, appointment_status_c, created_date_c,
               ownername_c, {header_sql_col} AS project_c, lead_id_c
        FROM {CATALOG}.{EVENT_SCHEMA}.{EVENT_TABLE}
        {where_base}
    """
    opp_sql = f"""
        SELECT opportunity_id_c, lead_id_c, sales_order_number_c, created_date_c,
               {header_sql_col} AS project_c
        FROM {CATALOG}.{OPP_SCHEMA}.{OPP_TABLE}
        {where_base}
    """

    leads = query_presto(CATALOG, LEAD_SCHEMA, lead_sql)
    events = query_presto(CATALOG, EVENT_SCHEMA, event_sql)
    opps = query_presto(CATALOG, OPP_SCHEMA, opp_sql)

    if leads.empty:
        logger.warning(f"No leads found for {start_date} to {end_date}")
        return {"status": "no_data", "message": f"No leads found for {start_date} to {end_date}"}

    product_funnel = compute_product_wise_funnel(leads, events, opps, header_col=header_col)
    totals = calculate_master_totals(product_funnel)
    return {
        "status": "success",
        "filters": f"{start_date} to {end_date}",
        "project_filter": project_filter,
        "feedback_filter": feedback_filter,
        "product_wise_metrics": product_funnel,
        "totals": totals
    }


# --------------------------------------------
# POST /funnel endpoint
# --------------------------------------------
@app.post("/funnel")
async def post_funnel(payload: Dict[str, Any] = Body(...)):
    """
    POST /funnel
    Body:
      - question: str  OR  questions: [str, ...]
      - header_col: optional (defaults to project_c)
    """
    header_col = payload.get("header_col") or payload.get("group_by") or "project_c"
    raw_questions: List[str] = []

    if "question" in payload and isinstance(payload["question"], str) and payload["question"].strip():
        raw_questions.append(payload["question"].strip())
    if "questions" in payload and isinstance(payload["questions"], list):
        for q in payload["questions"]:
            if isinstance(q, str) and q.strip():
                raw_questions.append(q.strip())
    if not raw_questions and "q" in payload and isinstance(payload["q"], str) and payload["q"].strip():
        raw_questions.append(payload["q"].strip())

    if not raw_questions:
        return {"status": "error", "message": "Provide 'question' (string) or 'questions' (list) in the body."}

    results = {}

    for qtext in raw_questions:
        try:
            parsed = parse_question_dates(qtext)
            logger.info(f"Parsed for '{qtext}': {parsed}")

            # BUG 1 & 2 FIX: extract project and feedback filters from the question
            project_filter = extract_project_filter(qtext)
            feedback_filter = extract_feedback_filter(qtext)

            qres = None

            if parsed.get("type") == "single":
                qres = run_funnel_for_range(
                    parsed["period"], header_col=header_col,
                    project_filter=project_filter, feedback_filter=feedback_filter
                )

            elif parsed.get("type") == "compare":
                p1 = run_funnel_for_range(
                    parsed["period"], header_col=header_col,
                    project_filter=project_filter, feedback_filter=feedback_filter
                )
                p2 = run_funnel_for_range(
                    parsed["previous_period"], header_col=header_col,
                    project_filter=project_filter, feedback_filter=feedback_filter
                )
                qres = {"period": p1, "previous_period": p2}

            elif parsed.get("type") == "multiple_periods":
                aggregated = {}
                for period_info in parsed["periods"]:
                    aggregated[period_info["label"]] = run_funnel_for_range(
                        period_info["range"], header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"periods": aggregated}

            elif parsed.get("type") == "mom_all":
                aggregated = {}
                for label, rng in months_of_fiscal_year(parsed["fy_start_year"]):
                    aggregated[label] = run_funnel_for_range(
                        rng, header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"fy_start_year": parsed["fy_start_year"], "months": aggregated}

            elif parsed.get("type") == "qoq_all":
                aggregated = {}
                for label, rng in quarters_of_fiscal_year(parsed["fy_start_year"]):
                    aggregated[label] = run_funnel_for_range(
                        rng, header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"fy_start_year": parsed["fy_start_year"], "quarters": aggregated}

            elif parsed.get("type") == "yoy_two":
                last_y, this_y = parsed["last_year"], parsed["this_year"]
                agg = {}
                for y in (last_y, this_y):
                    s = datetime(y, 4, 1)
                    e = datetime(y + 1, 3, 31)
                    agg[f"FY {y}-{y + 1}"] = run_funnel_for_range(
                        {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")},
                        header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"years": agg}

            elif parsed.get("type") == "last_n_months":
                aggregated = {}
                for period_info in parsed["periods"]:
                    aggregated[period_info["label"]] = run_funnel_for_range(
                        period_info["range"], header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"n": parsed["n"], "months": aggregated}

            elif parsed.get("type") == "last_n_quarters":
                aggregated = {}
                for period_info in parsed["periods"]:
                    aggregated[period_info["label"]] = run_funnel_for_range(
                        period_info["range"], header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {"n": parsed["n"], "quarters": aggregated}

            elif parsed.get("type") == "monthly_range":
                aggregated = {}
                for period_info in parsed["periods"]:
                    aggregated[period_info["label"]] = run_funnel_for_range(
                        period_info["range"], header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                qres = {
                    "range_description": parsed.get("range_description", ""),
                    "months": aggregated
                }

            else:
                if "period" in parsed:
                    qres = run_funnel_for_range(
                        parsed["period"], header_col=header_col,
                        project_filter=project_filter, feedback_filter=feedback_filter
                    )
                else:
                    qres = {"status": "error", "message": "Unable to interpret query dates."}

            results[qtext] = {"parsed": parsed, "result": qres}

        except Exception as e:
            logger.exception(f"Error processing question '{qtext}'")
            results[qtext] = {"status": "error", "message": str(e)}

    # BUG NOTE: original code had `return` inside the for-loop (indented too deep).
    # Fixed: return is now outside the loop so ALL questions are processed.
    return {
        "status": "success",
        "count": len(raw_questions),
        "header_col": header_col,
        "responses": results
    }
