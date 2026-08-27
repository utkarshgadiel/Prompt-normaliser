from fastapi import FastAPI, Body
import pandas as pd
import prestodb
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai import Credentials
from typing import Optional, Tuple, Dict, Any, Iterable, List
import os
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import logging
from datetime import datetime, timedelta
import re
from calendar import monthrange
from dateutil import parser as date_parser
from typing import Any, Dict, List, Union

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

    final_totals = {}
    for k, v in totals.items():
        if float(v).is_integer():
            final_totals[k] = int(v)
        else:
            final_totals[k] = round(v, 2)

    return final_totals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("funnel_tool.log", mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).with_name(".env.funnel"))

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
                    if isinstance(metric_value, (int, float)) and not any(marker in metric_name for marker in ["%", ":"]):
                        return -metric_value  # Negative for descending order
                return 0
            
            sorted_items = sorted(data.items(), key=get_sort_key)
            
            if return_as_list:
                return [
                    {"name": name, **metrics}
                    for name, metrics in sorted_items
                ]
            
            return dict(sorted_items)
    
    return data


def extract_project_name_v2(question: str) -> Optional[str]:
    """
    Enhanced version with better handling of all project names including:
    - wave city
    - wave estate  
    - wave executive floors
    - wave amore
    - wmcc sec 32
    - wmcc
    """
    question_lower = question.lower()
    
    known_projects = [
        'wave executive floors',
        'executive floors',  # Alternative
        'wmcc sec 32',
        'wmcc',
        'wave amore',
        'amore',  # Could be project or product
        'wave city',
        'wave estate',
        'wave one'
    ]

    known_products_with_wave_prefix = [
        'wave galleria',
        'wave garden',
    ]

    for prod in known_products_with_wave_prefix:
        if prod in question_lower:
            logger.info(f"Skipping project extraction - '{prod}' is a product, not a project")
            return None
    
    for project in known_projects:
        pattern = r'\b' + re.escape(project) + r'\b(?!\s+(?:month|quarter|year|mom|qoq|on|wise|breakdown))'
        
        if re.search(pattern, question_lower):
            logger.info(f"Extracted known project name: '{project}'")
            
            project_mapping = {
                'executive floors': 'wave executive floors',
                'amore': 'wave amore',  # Only if it's clearly a project context
                'wmcc': 'wmcc sec 32',  # Map to full name if needed
            }
            
            canonical_name = project_mapping.get(project, project)
            
            if project == 'amore':
                if 'wave' in question_lower and question_lower.index('wave') < question_lower.index('amore'):
                    canonical_name = 'wave amore'
                else:
                    continue
            
            return canonical_name
    
    patterns = [
        r'\b(?:of|for|in)\s+(wave\s+\w+(?:\s+\w+)*?)(?:\s+(?:month|quarter|year|mom|qoq|last|this|breakdown)|\s*$)',
        r'\b(?:of|for|in)\s+(wmcc(?:\s+sec\s+\d+)?)(?:\s+(?:month|quarter|year|mom|qoq|last|this|breakdown)|\s*$)',
        r'\b(wave\s+\w+(?:\s+\w+)*?|wmcc(?:\s+sec\s+\d+)?)\s+(?:funnel|data|metrics|report|analysis)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, question_lower)
        if match:
            project_name = match.group(1).strip()
            temporal_words = ['month', 'quarter', 'year', 'on', 'wise', 'breakdown', 'mom', 'qoq', 'yoy', 'last', 'this']
            words = project_name.split()
            cleaned_words = [w for w in words if w not in temporal_words]
            if cleaned_words:
                project_name = ' '.join(cleaned_words).strip()
                # ── CRITICAL: never return a known wave-prefix product as a project ──
                if project_name in ['wave galleria', 'wave garden']:
                    logger.info(f"Pattern matched '{project_name}' but it is a product, not a project - skipping")
                    continue
                if 'wave' in project_name or 'wmcc' in project_name:
                    logger.info(f"Extracted project name via pattern: '{project_name}'")
                    return project_name
    return None




import re
from typing import List, Optional

def extract_product_names(question: str) -> Optional[List[str]]:
    """
    Extract MULTIPLE product names from a question.

    Handles:
    - "veridia and eligo"
    - "eden veridia and eligo"
    - "show funnel for veridia, eligo"
    - "show me product wise funnel for eden in 2025"
    - "show me funnel for eden last year"

    Returns:
        List[str] or None
    """

    question_lower = question.lower()

    project_names = [
        'wave executive floors',
        'executive floors',
        'wmcc sec 32',
        'wave city',
        'wave estate',
        'wave amore',
        'wave one',
        'wmcc'
    ]

    known_products_with_wave_prefix = [
        'wave galleria',
        'wave garden',
    ]

    has_wave_product = any(
        prod in question_lower
        for prod in known_products_with_wave_prefix
    )

    if not has_wave_product:
        for project in project_names:
            if project in question_lower:
                logger.info(
                    f"Skipping product extraction - found project name: '{project}'"
                )
                return None

    temporal_keywords = {
        'month', 'quarter', 'year',
        'mom', 'qoq', 'yoy',
        'last', 'this',
        'breakdown', 'comparison',
        'trend', 'analysis',
        'from', 'to', 'till',
        'between', 'funnel',
        'data', 'report',
        'metrics',

        'april', 'may', 'june',
        'july', 'august', 'september',
        'october', 'november', 'december',
        'january', 'february', 'march',

        'show', 'me', 'the',
        'give', 'get', 'fetch',
        'display', 'see',
        'view', 'tell',
        'find', 'search',

        'product', 'products',
        'wise', 'all',
        'any', 'some',
        'for', 'of',
        'in', 'at', 'on',
        'a', 'an',

        'q1', 'q2', 'q3', 'q4',
        'week', 'day',
        'monthly',
        'quarterly',
        'yearly',

        'fy',
        'current',
        'previous'
    }

    known_product_patterns = [
        r'\b(\d+\s*bhk)\b',
        r'\b(studio)(?!\s+apartment)\b',
        r'\b(penthouse)\b',
        r'\b(villa)\b',
        r'\b(apartment)\b',
        r'\b(duplex)\b',
        r'\b(triplex)\b',
    ]

    found_products = []

    if 'amore' in question_lower:
        wave_pos = question_lower.find('wave')
        amore_pos = question_lower.find('amore')

        if wave_pos == -1 or wave_pos > amore_pos:
            found_products.append('amore')

    for pattern in known_product_patterns:
        for match in re.finditer(pattern, question_lower):
            product_name = match.group(1).strip()

            if product_name not in found_products:
                found_products.append(product_name)

    trigger_pattern = (
        r'\b(?:of|for)\s+([\w\s,]+?)'
        r'(?:\s+(?:month|quarter|year|mom|qoq|last|this|from|to|'
        r'breakdown|funnel|data|report)|\s*$)'
    )

    trigger_matches = re.findall(
        trigger_pattern,
        question_lower
    )

    for raw in trigger_matches:

        parts = re.split(r'\band\b|,', raw)

        for part in parts:

            candidate = part.strip()

            # Remove relative FY phrases only
            candidate = re.sub(
                r'\b(last year|previous year|this year|current year|'
                r'last fy|current fy|previous fy)\b',
                '',
                candidate
            )

            # Remove only trailing "in 2025"
            candidate = re.sub(
                r'\s+in\s+20\d{2}$',
                '',
                candidate
            )

            # Remove only trailing FY
            candidate = re.sub(
                r'\s+fy\s*20\d{2}$',
                '',
                candidate
            )

            # FIX: strip a bare trailing year with no "in"/"fy" before it,
            # e.g. "wave garden 2022" -> "wave garden". Without this, the
            # year survives into the product name because digits aren't in
            # temporal_keywords and the two regexes above only match when
            # "in"/"fy" immediately precedes the year.
            candidate = re.sub(
                r'\s+(?:19|20)\d{2}$',
                '',
                candidate
            )

            # Do not treat relative numeric durations as product names,
            # e.g. "last 5 years" can be captured as "last 5".
            candidate = re.sub(
                r'\b(?:last|past|previous)\s+\d+\s*$',
                '',
                candidate
            )

            candidate = candidate.strip()

            words = candidate.split()

            while words and words[-1] in temporal_keywords:
                words.pop()

            candidate = " ".join(words)

            if not words:
                continue

            candidate = " ".join(words)

            if (
                candidate
                and candidate not in temporal_keywords
                and candidate not in project_names
                and len(candidate) >= 2
                and candidate not in found_products
            ):
                found_products.append(candidate)

    if not found_products:

        and_split_pattern = (
            r'\b([\w]+(?:\s+[\w]+)?)\s+and\s+'
            r'([\w]+(?:\s+[\w]+)?)\b'
        )

        and_match = re.search(
            and_split_pattern,
            question_lower
        )

        if and_match:

            candidates = [
                and_match.group(1).strip(),
                and_match.group(2).strip()
            ]

            for candidate in candidates:

                words = candidate.split()

                while words and words[-1] in temporal_keywords:
                    words.pop()
                if not words:
                    continue

                candidate = " ".join(words)

                if (
                    candidate
                    and candidate not in temporal_keywords
                    and candidate not in project_names
                    and len(candidate) >= 2
                    and candidate not in found_products
                ):
                    found_products.append(candidate)

    if found_products:
        logger.info(
            f"Extracted product names: {found_products}"
        )
        return found_products

    # extract_product_name (singular) is not defined; found_products is already
    # the authoritative result at this point — just return None if empty.
    return None

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

def compute_funnel(leads: pd.DataFrame, events: pd.DataFrame, opps: pd.DataFrame):
    """
    Compute funnel numbers. To avoid zeros for meeting counts we use
    flexible matching for booked/done.
    """
    logger.info("Starting funnel computation...")
    if leads is None or events is None or opps is None:
        logger.error("One or more input dataframes are None.")
        return {}

    for df, name in [(leads, "leads"), (events, "events"), (opps, "opps")]:
        for col in df.columns:
            df[col] = df[col].fillna("").astype(str)
        logger.debug(f"{name} columns converted to string types")

    total_leads = len(leads)
    cf = leads.get("customer_feedback_c", pd.Series([""]*total_leads)).str.strip().str.lower()
    junk_leads = (cf == "junk").sum()
    sol_leads = (cf == "interested").sum()
    valid_leads = (cf != "junk").sum()

    subj = events.get("subject_c", pd.Series([""]*len(events))).str.strip().str.lower()
    status = events.get("appointment_status_c", pd.Series([""]*len(events))).str.strip().str.lower()

    meeting_booked_mask = subj.str.contains(r'appointment', na=False) & subj.str.contains(r'book', na=False)
    meeting_booked_mask = meeting_booked_mask | (subj == "personal appointment booked")
    meeting_booked = meeting_booked_mask.sum()

    meeting_done_mask = status.str.contains(r'complete|done', na=False)
    meeting_done_mask = meeting_done_mask | subj.str.contains(r'completed|done', na=False)
    meeting_done = meeting_done_mask.sum()

    sales_col = opps.get("sales_order_number_c", pd.Series([""]*len(opps))).astype(str).str.strip().str.lower()
    sales_done = ((sales_col != "") & (sales_col != "nan")).sum()

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

    logger.info(f"Funnel computation completed: {funnel_data}")
    return sort_funnel_by_numeric_desc(funnel_data)


def compute_product_wise_funnel(leads: pd.DataFrame, events: pd.DataFrame, opps: pd.DataFrame, 
                                 header_col: str = "product_category_c", 
                                 project_filter: Optional[str] = None,
                                 product_filter: Union[str, List[str], None] = None,
                                 top_n: Optional[int] = None):
    """
    Compute funnel grouped by a unified product column.
    If project_filter is provided, it FIRST filters by project, THEN extracts products from that filtered data.
    If product_filter is provided, it returns data for ONLY that specific product.
    
    NOTE: Opportunities table uses project_category_c as the product column (not product_category_c)
    """
    logger.info(f"Starting dynamic product-wise funnel computation based on '{header_col}'...")
    if project_filter:
        logger.info(f"Applying project filter: '{project_filter}'")
    if product_filter:
        logger.info(f"Applying product filter: '{product_filter}'")

    for df, name in [(leads, "leads"), (events, "events"), (opps, "opps")]:
        if "project_c" not in df.columns:
            df["project_c"] = ""

    if "product_category_c" not in leads.columns:
        leads["product_category_c"] = ""
    if "product_category_c" not in events.columns:
        events["product_category_c"] = ""
    
    if "project_category_c" in opps.columns:
        opps["product_category_c"] = opps["project_category_c"].fillna("").astype(str)
    elif "product_category_c" not in opps.columns:
        opps["product_category_c"] = ""

    if project_filter:
        project_filter_lower = project_filter.strip().lower()
        
        leads["__project_normalized__"] = leads.get("project_c", pd.Series([""]*len(leads))).fillna("").astype(str).str.strip().str.lower()
        events["__project_normalized__"] = events.get("project_c", pd.Series([""]*len(events))).fillna("").astype(str).str.strip().str.lower()
        opps["__project_normalized__"] = opps.get("project_c", pd.Series([""]*len(opps))).fillna("").astype(str).str.strip().str.lower()
        
        leads = leads[leads["__project_normalized__"] == project_filter_lower].copy()
        events = events[events["__project_normalized__"] == project_filter_lower].copy()
        opps = opps[opps["__project_normalized__"] == project_filter_lower].copy()
        
        logger.info(f"After project filter: {len(leads)} leads, {len(events)} events, {len(opps)} opportunities")
        
        if leads.empty and events.empty and opps.empty:
            logger.warning(f"No data found for project: '{project_filter}'")
            return {"message": f"No data found for project: '{project_filter}'"}
        
        for df in (leads, events, opps):
            if "__project_normalized__" in df.columns:
                df.drop(columns="__project_normalized__", inplace=True)

    leads["__col_normalized__"] = leads["product_category_c"].fillna("").astype(str).str.strip().str.lower()
    events["__col_normalized__"] = events["product_category_c"].fillna("").astype(str).str.strip().str.lower()
    opps["__col_normalized__"] = opps["product_category_c"].fillna("").astype(str).str.strip().str.lower()

    if product_filter:
        if isinstance(product_filter, str):
            product_filter_list = [_normalize_product_name(product_filter).lower()]
        else:
            product_filter_list = [_normalize_product_name(p).lower() for p in product_filter]

        def _make_mask(series: pd.Series, filters: list) -> pd.Series:
            mask = pd.Series([False] * len(series), index=series.index)
            for f in filters:
                norm = _normalize_product_name(f)
                norm_hyphen = norm.replace(" ", "-")
                if _is_partial_product(f):
                    mask = mask | series.str.contains(re.escape(norm), na=False)
                else:
                    mask = mask | (series == norm)
                    if norm != norm_hyphen:
                        mask = mask | (series == norm_hyphen)
            return mask

        leads = leads[_make_mask(leads["__col_normalized__"], product_filter_list)].copy()
        events = events[_make_mask(events["__col_normalized__"], product_filter_list)].copy()
        opps = opps[_make_mask(opps["__col_normalized__"], product_filter_list)].copy()

        logger.info(f"After product filter {product_filter_list}: {len(leads)} leads, {len(events)} events, {len(opps)} opportunities")

        if leads.empty and events.empty and opps.empty:
            logger.warning(f"No data found for product(s): {product_filter_list}")
            return {"message": f"No data found for product(s): {product_filter_list}"}

        unique_values = list(pd.concat([
            leads["__col_normalized__"],
            events["__col_normalized__"],
            opps["__col_normalized__"]
        ]).dropna().unique())
        unique_values = [v for v in unique_values if v and v.strip()]
    else:
        unique_values = pd.concat([
            leads["__col_normalized__"], 
            events["__col_normalized__"], 
            opps["__col_normalized__"]
        ]).unique()
        
        unique_values = [v for v in unique_values if v and v.strip()]

        if not project_filter:
            exclude_projects = ["wave executive floors", "wave amore", "wave city", "wave one"]
            unique_values = [v for v in unique_values if v not in exclude_projects]
    
    logger.info(f"Found {len(unique_values)} unique products" + 
                (f" in project '{project_filter}'" if project_filter else "") +
                (f" (filtered to '{product_filter}')" if product_filter else "") +
                f": {unique_values}")

    if not unique_values:
        logger.warning("No products found after filtering!")
        return {"message": "No products found with the given filters"}

    output = {}
    for val in unique_values:
        leads_p = leads[leads["__col_normalized__"] == val].copy()
        events_p = events[events["__col_normalized__"] == val].copy()
        opps_p = opps[opps["__col_normalized__"] == val].copy()
        
        display_name = val.title() if val else "Unknown"
        
        original_values = pd.concat([
            leads[leads["__col_normalized__"] == val]["product_category_c"],
            events[events["__col_normalized__"] == val]["product_category_c"],
            opps[opps["__col_normalized__"] == val]["product_category_c"]
        ]).dropna()
        
        if not original_values.empty:
            display_name = original_values.mode()[0] if len(original_values.mode()) > 0 else display_name

        logger.info(f"Computing funnel for product '{display_name}': {len(leads_p)} leads, {len(events_p)} events, {len(opps_p)} opps")

        if leads_p.empty and events_p.empty and opps_p.empty:
            output[display_name] = {
                k: 0 for k in [
                    "Total Leads", "Valid Leads", "Junk Leads", "SOL Leads (Interested)",
                    "Meeting Booked", "Meeting Done", "Sales Done", "Junk %", "TL:VL",
                    "VL:SOL", "SOL:MB", "MB:MD", "MD:SD", "TL:SD", "VL:SD", "SOL:SD", "MB:SD"
                ]
            }
            continue

        metrics = compute_funnel(leads_p, events_p, opps_p)
        output[display_name] = metrics

    for df in (leads, events, opps):
        if "__col_normalized__" in df.columns:
            df.drop(columns="__col_normalized__", inplace=True)

    logger.info("Dynamic product-wise funnel computation completed.")
    sorted_output = sort_funnel_by_numeric_desc(output, return_as_list=True)
    if top_n is not None and isinstance(sorted_output, list):
        sorted_output = sorted_output[:top_n]
        logger.info(f"Returning top {top_n} products.")
    return sorted_output


MONTH_NAMES = {
    "january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,"may":5,"june":6,"jun":6,
    "july":7,"jul":7,"august":8,"aug":8,"september":9,"sep":9,"october":10,"oct":10,"november":11,"nov":11,"december":12,"dec":12
}

def fiscal_quarter_start_end(fy_year: int, q: int) -> Tuple[datetime, datetime]:
    if q == 1:
        s = datetime(fy_year, 4, 1); e = datetime(fy_year, 6, 30)
    elif q == 2:
        s = datetime(fy_year, 7, 1); e = datetime(fy_year, 9, 30)
    elif q == 3:
        s = datetime(fy_year, 10, 1); e = datetime(fy_year, 12, 31)
    else:
        s = datetime(fy_year + 1, 1, 1); e = datetime(fy_year + 1, 3, 31)
    return s, e

def month_start_end(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day)
    return start, end

def parse_explicit_range(text: str) -> Optional[Tuple[datetime, datetime]]:
    """
    Try many explicit range patterns. Handles:
      - '1 june 2021 to 13 june 2021'
      - '1st june 2021 to 13th june 2021'   <- ordinal suffixes now supported
      - 'april 2024 to june 2024'
      - 'april to june' (map to current FY)
      - 'from 1-6-2021 to 13-6-2021'

    FIX 1: Ordinal suffixes (st/nd/rd/th) are stripped at the top so that
            inputs like "1st June 2021" parse correctly.
    FIX 2: All datetime.strptime calls use .title() on the input string
            to handle lowercased month names (e.g. 'jan' -> 'Jan', 'january' -> 'January').
            Python's %b and %B directives are case-sensitive and require title-case.
    """
    # ── NEW: strip ordinal suffixes so "1st june" -> "1 june", "15th april" -> "15 april" ──
    text = re.sub(r'(\d{1,2})(st|nd|rd|th)\b', r'\1', text, flags=re.IGNORECASE)

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
        if not m:
            continue
        groups = m.groups()

        if len(groups) >= 3 and groups[-1] and groups[-1].isdigit():
            month_start = groups[0].strip(); month_end = groups[1].strip(); year = int(groups[2])
            if month_start.lower() in MONTH_NAMES and month_end.lower() in MONTH_NAMES:
                start_month_num = MONTH_NAMES[month_start.lower()]
                end_month_num = MONTH_NAMES[month_end.lower()]
                start_year = year if start_month_num >= 4 else year + 1
                end_year = year if end_month_num >= 4 else year + 1
                a_dt = datetime(start_year, start_month_num, 1)
                last_day = monthrange(end_year, end_month_num)[1]
                b_dt = datetime(end_year, end_month_num, last_day)
                return a_dt, b_dt

        a = groups[0].strip()
        b = groups[1].strip() if len(groups) > 1 else None
        if not b:
            continue
        if (a.lower() in MONTH_NAMES and b.lower() in MONTH_NAMES and
            not any(ch.isdigit() for ch in a) and not any(ch.isdigit() for ch in b)):
            start_month_num = MONTH_NAMES[a.lower()]
            end_month_num = MONTH_NAMES[b.lower()]
            start_year = fy_start_year if start_month_num >= 4 else fy_start_year + 1
            end_year = fy_start_year if end_month_num >= 4 else fy_start_year + 1
            a_dt = datetime(start_year, start_month_num, 1)
            last_day = monthrange(end_year, end_month_num)[1]
            b_dt = datetime(end_year, end_month_num, last_day)
            return a_dt, b_dt

        # ── FIX 2: use .title() so that lowercased month names parse correctly ──
        date_formats = ["%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%B %Y", "%b %Y"]
        a_dt = None; b_dt = None
        for fmt in date_formats:
            try:
                a_dt = datetime.strptime(a.title(), fmt)
                break
            except Exception:
                pass
        for fmt in date_formats:
            try:
                b_dt = datetime.strptime(b.title(), fmt)
                break
            except Exception:
                pass
        if a_dt is None or b_dt is None:
            continue
        if re.match(r'^[A-Za-z]+\s+\d{4}$', a):
            a_dt = a_dt.replace(day=1)
        if re.match(r'^[A-Za-z]+\s+\d{4}$', b):
            last = monthrange(b_dt.year, b_dt.month)[1]
            b_dt = b_dt.replace(day=last)
        return a_dt, b_dt
    return None

def generate_month_range(start_month:int, end_month:int, start_year:int, end_year:int) -> Iterable[Tuple[str, Dict[str,str]]]:
    """
    Generate months from start_month/start_year to end_month/end_year inclusive.
    Used for mom_range monthly breakdowns.
    """
    cur_year = start_year
    cur_month = start_month
    months = []
    while True:
        s, e = month_start_end(cur_year, cur_month)
        label = f"{s.strftime('%B')} {s.year}"
        months.append((label, {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}))
        if cur_year == end_year and cur_month == end_month:
            break
        if cur_month == 12:
            cur_month = 1
            cur_year += 1
        else:
            cur_month += 1
    for item in months:
        yield item


def parse_question_dates(question: str) -> Dict[str, Any]:
    """
    Return a dict describing the period(s) to run the funnel on.
    All year/fy handling is strict fiscal Apr->Mar.

    FIX 1: Ordinal suffixes (1st, 2nd, 3rd, 15th, etc.) are stripped at the
            very top so every downstream branch handles them transparently.
    FIX 2: All datetime.strptime calls use .title() on input tokens so that
            lowercased month names (produced by q = question.lower()) parse correctly
            with Python's case-sensitive %b / %B directives.
    FIX 3: Multi-FY QoQ: "fy 2022 and fy 2023" now returns type=qoq_multi_fy
            instead of being collapsed into a single merged date range.
    """
    q = (question or "").strip().lower()

    # ── Strip ordinal suffixes: "1st" -> "1", "15th" -> "15", "2nd" -> "2" ──
    q = re.sub(
        r'(\d{1,2})(st|nd|rd|th)\b',
        r'\1',
        q
    )

    today = datetime.today()
    current_year = today.year
    current_month = today.month
    fy_start_year = current_year if current_month >= 4 else current_year - 1
    fy_start = datetime(fy_start_year, 4, 1)
    fy_end = datetime(fy_start_year + 1, 3, 31)
    logger.info(f"Parsing temporal query: '{q}' (current FY start {fy_start_year})")

    # ---------------------------------------------------------
    # Multi-FY QoQ: "fy 2022 and fy 2023", "fy2022 & fy2023"
    # MUST come BEFORE fy_range and fy_single so that the single-FY
    # branch does not short-circuit on the first "fy YYYY" it finds.
    # ---------------------------------------------------------
    m = re.search(
        r'\bfy\s*(20\d{2})\s*(?:and|&|,)\s*fy\s*(20\d{2})\b',
        q
    )
    if m:
        y1 = int(m.group(1))
        y2 = int(m.group(2))
        fy_years = sorted([y1, y2])
        logger.info(f"Detected multi-FY QoQ: {fy_years}")
        return {
            "type": "qoq_multi_fy",
            "fy_years": fy_years
        }

    fy_range = re.search(
        r'\b(?:fy|financial year)\s*(\d{2,4})\s*[-/]\s*(\d{2,4})\b',
        q
    )

    if fy_range:
        start_year = fy_range.group(1)
        end_year = fy_range.group(2)

        if len(start_year) == 2:
            start_year = int("20" + start_year)
        else:
            start_year = int(start_year)

        if len(end_year) == 2:
            end_year = int(str(start_year)[:2] + end_year)
        else:
            end_year = int(end_year)

        start_dt = datetime(start_year, 4, 1)
        end_dt = datetime(end_year, 3, 31)

        return {
            "type": "single",
            "period": {
                "start": start_dt.strftime("%d-%m-%Y"),
                "end": end_dt.strftime("%d-%m-%Y")
            }
        }


    fy_single = re.search(
        r'\b(?:fy|financial year)\s*(\d{2,4})\b',
        q
    )

    if fy_single:
        year = fy_single.group(1)

        if len(year) == 2:
            year = int("20" + year)
        else:
            year = int(year)

        start_dt = datetime(year, 4, 1)
        end_dt = datetime(year + 1, 3, 31)

        return {
            "type": "single",
            "period": {
                "start": start_dt.strftime("%d-%m-%Y"),
                "end": end_dt.strftime("%d-%m-%Y")
            }
        }

    quarterwise_multi_year = re.search(
        r'\b(?:quarter\s*wise|quarterly|qoq|quarter\s*on\s*quarter)\b.*?\b(20\d{2})\s*(?:and|&|,)\s*(20\d{2})\b',
        q
    )
    if quarterwise_multi_year:
        y1 = int(quarterwise_multi_year.group(1))
        y2 = int(quarterwise_multi_year.group(2))
        fy_years = sorted([y1, y2])
        logger.info(f"Detected quarter-wise multi-FY query: {fy_years}")
        return {
            "type": "qoq_multi_fy",
            "fy_years": fy_years
        }

    quarterwise_month = re.search(
        r'\b(?:quarter\s*wise|quarterly|qoq|quarter\s*on\s*quarter)\b.*?\b('
        + '|'.join(MONTH_NAMES.keys())
        + r')\b.*?\b(20\d{2})\b',
        q
    )
    if quarterwise_month:
        month_num = MONTH_NAMES[quarterwise_month.group(1)]
        fy_year = int(quarterwise_month.group(2)) if month_num >= 4 else int(quarterwise_month.group(2)) - 1
        quarter = ((month_num - 4) % 12) // 3 + 1
        logger.info(f"Detected quarter-wise month query: {quarterwise_month.group(1)} {quarterwise_month.group(2)} -> Q{quarter} FY {fy_year}")
        return {
            "type": "qoq_range",
            "start_quarter": quarter,
            "end_quarter": quarter,
            "fy_start_year": fy_year
        }

    m = re.search(r'\b(?:month\s*on\s*month|mom|monthly|month\s*wise|month\s*by\s*month)\b.*?\b(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(?:to|-|till)\s+(' + '|'.join(MONTH_NAMES.keys()) + r')(?:\s+(\d{4}))?\b', q)
    if m:
        m1 = m.group(1)
        m2 = m.group(2)
        ytok = m.group(3)
        m1_num = MONTH_NAMES[m1]
        m2_num = MONTH_NAMES[m2]
        
        if ytok:
            year = int(ytok)
            start_year = year if m1_num >= 4 else year + 1
            end_year = year if m2_num >= 4 else year + 1
        else:
            start_year = fy_start_year if m1_num >= 4 else fy_start_year + 1
            end_year = fy_start_year if m2_num >= 4 else fy_start_year + 1
        
        logger.info(f"Detected MOM range: {m1} ({m1_num}) to {m2} ({m2_num}), years: {start_year}-{end_year}")
        return {
            "type": "mom_range",
            "start_month": m1_num,
            "end_month": m2_num,
            "start_year": start_year,
            "end_year": end_year
        }
    
    m = re.search(r'\b(?:quarter\s*on\s*quarter|qoq|quarterly|quarter\s*wise)\b.*?\bq(?:uarter)?\s*([1-4])\s+(?:to|-|till)\s+q(?:uarter)?\s*([1-4])(?:\s+(?:of\s*|for\s*)?(?:fy\s*)?(\d{4}))?\b', q)
    if m:
        q1 = int(m.group(1))
        q2 = int(m.group(2))
        year_tok = m.group(3)
        fy_year = int(year_tok) if year_tok else fy_start_year
        
        logger.info(f"Detected QOQ range: Q{q1} to Q{q2} of FY {fy_year}")
        return {
            "type": "qoq_range",
            "start_quarter": q1,
            "end_quarter": q2,
            "fy_start_year": fy_year
        }

    # Intra-month day range: "5 to 10 june 2024"
    # Negative lookbehind (?<!\d) prevents matching trailing digits of a 4-digit year (e.g. "26" from "2026")
    m = re.search(r'(?<!\d)(\d{1,2})\s*(?:to|-|till)\s*(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')(?:\s+(\d{4}))?', q)
    if m:
        sd = int(m.group(1)); ed = int(m.group(2)); mn = m.group(3); ytok = m.group(4)
        # Extra guard: reject if either day value is > 31 (means we matched part of a year)
        if sd > 31 or ed > 31:
            logger.warning(f"Intra-month day range skipped — values out of range: {sd}, {ed}")
        else:
            month_num = MONTH_NAMES[mn]
            year = int(ytok) if ytok else (fy_start_year if month_num >= 4 else fy_start_year + 1)
            try:
                s_dt = datetime(year, month_num, sd); e_dt = datetime(year, month_num, ed)
                return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}
            except Exception:
                logger.warning("Invalid intra-month day range detected.")

    m = re.search(
        r'(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')\s*(\d{4})?\s*'
        r'(?:to|-|till)\s*'
        r'(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')\s*(\d{4})?',
        q
    )
    if m:
        d1 = int(m.group(1))
        m1_name = m.group(2)
        y1 = m.group(3)

        d2 = int(m.group(4))
        m2_name = m.group(5)
        y2 = m.group(6)

        m1_num = MONTH_NAMES[m1_name]
        m2_num = MONTH_NAMES[m2_name]

        if y1:
            year1 = int(y1)
        else:
            year1 = fy_start_year if m1_num >= 4 else fy_start_year + 1

        if y2:
            year2 = int(y2)
        else:
            year2 = fy_start_year if m2_num >= 4 else fy_start_year + 1

        try:
            start_dt = datetime(year1, m1_num, d1)
            end_dt = datetime(year2, m2_num, d2)
            return {
                "type": "single",
                "period": {
                    "start": start_dt.strftime("%d-%m-%Y"),
                    "end": end_dt.strftime("%d-%m-%Y")
                }
            }
        except:
            pass
            

    explicit = parse_explicit_range(q)
    
    # ---------------------------------------------------------
    # Multi year:
    # "2022 and 2023"
    # "2022 & 2023"
    # "2022, 2023"
    # DO NOT trigger for:
    # "Jan 2023 till Jan 2024"
    # DO NOT trigger when "fy" keyword is present (handled above)
    # ---------------------------------------------------------

    m = re.search(
        r'\b(20\d{2})\s*(?:and|&|,)\s*(20\d{2})\b',
        q
    )

    if m and 'fy' not in q:

        y1 = int(m.group(1))
        y2 = int(m.group(2))

        start_year = min(y1, y2)
        end_year = max(y1, y2)

        start_dt = datetime(
            start_year,
            4,
            1
        )

        end_dt = datetime(
            end_year + 1,
            3,
            31
        )

        return {
            "type":"single",
            "period":{
                "start":start_dt.strftime("%d-%m-%Y"),
                "end":end_dt.strftime("%d-%m-%Y")
            }
        }    
    # ---------------------------------------------------------
    # Handles:
    # 1 jan 2026 till date
    # 15 march 2025 till date
    # (ordinal suffixes already stripped above, so "1st jan" -> "1 jan")
    # ---------------------------------------------------------
    

    m = re.search(
        r'(\d{1,2})\s+'
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|'
        r'january|february|march|april|june|july|august|'
        r'september|october|november|december)'
        r'\s+'
        r'(20\d{2})\s+'
        r'(?:to|till)\s+date',
        q
    )

    if m:

        day = int(m.group(1))
        month = MONTH_NAMES[m.group(2)]
        year = int(m.group(3))

        try:

            start_dt = datetime(
                year,
                month,
                day
            )

            return {
                "type":"single",
                "period":{
                    "start":start_dt.strftime("%d-%m-%Y"),
                    "end":today.strftime("%d-%m-%Y")
                }
            }

        except Exception:
            pass
    if explicit:
        s_dt, e_dt = explicit
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    if re.search(r'(mom|month\s*on\s*month|monthly).*?(last\s*year|previous\s*year|last\s*fy)', q) or \
       re.search(r'(last\s*year|previous\s*year|last\s*fy).*?(mom|month\s*on\s*month|monthly)', q):
        py = fy_start_year - 1
        return {"type": "mom_all", "fy_start_year": py}

    if re.search(r'(mom|month\s*on\s*month|monthly).*?last\s*quarter', q) or \
       re.search(r'last\s*quarter.*?(mom|month\s*on\s*month|monthly)', q):      
        if 4 <= current_month <= 6:
            cur_q = 1; fy_now = current_year
        elif 7 <= current_month <= 9:
            cur_q = 2; fy_now = current_year
        elif 10 <= current_month <= 12:
            cur_q = 3; fy_now = current_year
        else:
            cur_q = 4; fy_now = current_year - 1
        last_q = cur_q - 1
        if last_q == 0:
            last_q = 4; fy_for_last_q = fy_now - 1
        else:
            fy_for_last_q = fy_now
        return {"type": "mom_all", "fy_start_year": fy_for_last_q, "quarter": last_q}

    if re.search(r'(mom|month\s*on\s*month|monthly).*?this\s*quarter', q) or \
       re.search(r'this\s*quarter.*?(mom|month\s*on\s*month|monthly)', q):                      
        if 4 <= current_month <= 6:
            cur_q = 1; fy_now = current_year
        elif 7 <= current_month <= 9:
            cur_q = 2; fy_now = current_year
        elif 10 <= current_month <= 12:
            cur_q = 3; fy_now = current_year
        else:
            cur_q = 4; fy_now = current_year - 1
        return {"type": "mom_all", "fy_start_year": fy_now, "quarter": cur_q}       

    if re.search(r'(qoq|quarterly|quarter\s*on\s*quarter).*?(last\s*year|previous\s*year|last\s*fy)', q) or \
       re.search(r'(last\s*year|previous\s*year|last\s*fy).*?(qoq|quarterly|quarter\s*on\s*quarter)', q):
        py = fy_start_year - 1
        return {"type": "qoq_all", "fy_start_year": py}

    m = re.search(r'\b(?:last|past|previous)\s+(\d{1,2})\s+years?\b', q)
    if m:
        n_years = int(m.group(1))
        last_completed_fy = fy_start_year - 1
        first_fy = last_completed_fy - n_years + 1
        return {
            "type": "single",
            "period": {
                "start": datetime(first_fy, 4, 1).strftime("%d-%m-%Y"),
                "end": datetime(last_completed_fy + 1, 3, 31).strftime("%d-%m-%Y")
            }
        }

    if " and " in q and not re.search(r'\b(to|till|from|-)\b', q):
        mlist = re.findall(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\b', q)
        # De-dupe while preserving order, e.g. "april, may and june" or
        # "april may and june" (comma is optional / dropped by \b word match).
        seen = set()
        mlist_unique = []
        for mn in mlist:
            if mn not in seen:
                seen.add(mn)
                mlist_unique.append(mn)
        # FIX: was hard-capped at exactly 2 months (`len(mlist) == 2`), so any
        # 3+ month list ("april, may and june") fell through to later branches
        # instead of being recognized as a month list.
        if len(mlist_unique) >= 2:
            month_list = []
            ytok = re.search(r'\b(\d{4})\b', q)
            for mn in mlist_unique:
                mn_num = MONTH_NAMES[mn.lower()]
                if ytok:
                    year = int(ytok.group(1))
                    start_year = year if mn_num >= 4 else year + 1
                else:
                    start_year = fy_start_year if mn_num >= 4 else fy_start_year + 1
                s, e = month_start_end(start_year, mn_num)
                month_list.append({"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")})
            return {"type": "mom_list", "months": month_list}

    if " and " in q and not re.search(r'\b(to|till|from|-)\b', q):
        qmatches = re.findall(r'\bq(?:uarter)?\s*([1-4])\b', q)
        if len(qmatches) == 2:
            q1 = int(qmatches[0]); q2 = int(qmatches[1])
            yom = re.search(r'\b(\d{4})\b', q)
            fy_year = fy_start_year if not yom else int(yom.group(1))
            return {"type": "qoq_list", "quarters": [{"quarter": q1, "fy_year": fy_year}, {"quarter": q2, "fy_year": fy_year}]}

    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+months?', q)
    if m:
        n = int(m.group(1))
        end_dt = (today.replace(day=1) - timedelta(days=1))
        if re.search(r'\b(monthly|month\s*wise|breakdown|each)\b', q):
            return {"type": "mom_last_n", "n_months": n, "end_date": end_dt}
        else:
            start_dt = end_dt.replace(day=1)
            for _ in range(n - 1):
                if start_dt.month == 1:
                    start_dt = start_dt.replace(year=start_dt.year - 1, month=12, day=1)
                else:
                    start_dt = start_dt.replace(month=start_dt.month - 1, day=1)
            last_day = monthrange(end_dt.year, end_dt.month)[1]
            end_dt_adj = end_dt.replace(day=last_day)
            return {"type": "single", "period": {"start": start_dt.strftime("%d-%m-%Y"), "end": end_dt_adj.strftime("%d-%m-%Y")}}

    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+months?\s+(?:last\s*year|previous\s*year|last\s*fy)', q)
    if m:
        n = int(m.group(1))
        py = fy_start_year - 1
        fy_end_prev = datetime(py + 1, 3, 31)
        end_dt = fy_end_prev
        if re.search(r'\b(monthly|month\s*wise|breakdown|each)\b', q):
            return {"type": "mom_last_n", "n_months": n, "end_date": end_dt}
        else:
            start_dt = end_dt.replace(day=1)
            for _ in range(n - 1):
                if start_dt.month == 1:
                    start_dt = start_dt.replace(year=start_dt.year - 1, month=12, day=1)
                else:
                    start_dt = start_dt.replace(month=start_dt.month - 1, day=1)
            return {"type": "single", "period": {"start": start_dt.strftime("%d-%m-%Y"), "end": end_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+quarters?', q)
    if m:
        n = int(m.group(1))
        if 4 <= current_month <= 6:
            cur_q = 1; fy_now = current_year
        elif 7 <= current_month <= 9:
            cur_q = 2; fy_now = current_year
        elif 10 <= current_month <= 12:
            cur_q = 3; fy_now = current_year
        else:
            cur_q = 4; fy_now = current_year - 1
        prev_q = cur_q - 1
        prev_fy = fy_now
        if prev_q < 1:
            prev_q = 4
            prev_fy -= 1
        if re.search(r'\b(quarterly|quarter\s*wise|breakdown|each)\b', q):
            return {"type": "qoq_last_n", "n_quarters": n, "current_quarter": prev_q, "current_fy": prev_fy}
        else:
            start_q = prev_q - n + 1
            start_fy = prev_fy
            while start_q < 1:
                start_q += 4
                start_fy -= 1
            s_dt, _ = fiscal_quarter_start_end(start_fy, start_q)
            _, e_dt = fiscal_quarter_start_end(prev_fy, prev_q)
            return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'(?:last|past|previous)\s+(\d{1,2})\s+quarters?\s+(?:last\s*year|previous\s*year|last\s*fy)', q)
    if m:
        n = int(m.group(1))
        py = fy_start_year - 1
        current_q = 4
        current_fy = py
        if re.search(r'\b(quarterly|quarter\s*wise|breakdown|each)\b', q):
            return {"type": "qoq_last_n", "n_quarters": n, "current_quarter": current_q, "current_fy": current_fy}
        else:
            start_q = 4 - n + 1
            start_fy = py
            s_dt, _ = fiscal_quarter_start_end(start_fy, start_q)
            _, e_dt = fiscal_quarter_start_end(current_fy, current_q)
            return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(?:month[-\s]?on[-\s]?month|mom|monthly|monthly for fiscal)(?:\s*(?:of\s*)?(?:fy\s*)?(\d{4}))?\b', q)
    if m:
        year_tok = m.group(1)
        fy = int(year_tok) if year_tok else fy_start_year
        return {"type": "mom_all", "fy_start_year": fy}

    if re.search(r'\b(?:mom|monthly|month\s*on\s*month).*?(?:last\s*year|previous\s*year|last\s*fy)\b', q) or \
       re.search(r'\b(?:last\s*year|previous\s*year|last\s*fy).*?(?:mom|monthly|month\s*on\s*month)\b', q):
        return {"type": "mom_all", "fy_start_year": fy_start_year - 1}

    m = re.search(r'\b(?:quarter[-\s]?on[-\s]?quarter|qoq|quarterly|quarters this fy)(?:\s*(?:of\s*|for\s*)?(?:fy\s*)?(\d{4}))?\b', q)
    if m:
        year_tok = m.group(1)
        fy = int(year_tok) if year_tok else fy_start_year
        return {"type": "qoq_all", "fy_start_year": fy}

    if re.search(r'\b(?:qoq|quarterly|quarter\s*on\s*quarter).*?(?:last\s*year|previous\s*year|last\s*fy)\b', q) or \
       re.search(r'\b(?:last\s*year|previous\s*year|last\s*fy).*?(?:qoq|quarterly|quarter\s*on\s*quarter)\b', q):
        return {"type": "qoq_all", "fy_start_year": fy_start_year - 1}

    if re.search(r'\b(year[-\s]?on[-\s]?year|yoy|year on year|y-o-y)\b', q):
        last_fy = fy_start_year - 1
        this_fy = fy_start_year
        return {
            "type": "yoy",
            "periods": {
                "last_year": {
                    "start": datetime(last_fy, 4, 1).strftime("%d-%m-%Y"),
                    "end": datetime(last_fy + 1, 3, 31).strftime("%d-%m-%Y")
                },
                "this_year": {
                    "start": datetime(this_fy, 4, 1).strftime("%d-%m-%Y"),
                    "end": datetime(this_fy + 1, 3, 31).strftime("%d-%m-%Y")
                }
            }
        }

    # ---------------------------------------------------------
    # Handles:
    # from jan 2026 till april 2026
    # from jan 2026 till date
    # from 1 jan 2026 till date        (ordinals already stripped above)
    # from 1 jan 2026 till 13 may 2026
    # ---------------------------------------------------------

    m = re.search(
        r'from\s+'
        r'(?:(\d{1,2})\s+)?'     # optional day
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|'
        r'january|february|march|april|june|july|august|'
        r'september|october|november|december)'
        r'\s+(20\d{2})\s+'
        r'(?:to|till)\s+'
        r'(?:(\d{1,2})\s+)?'     # optional end day
        r'(date|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|'
        r'january|february|march|april|june|july|august|'
        r'september|october|november|december)'
        r'(?:\s+(20\d{2}))?',
        q
    )

    if m:

        start_day = int(m.group(1)) if m.group(1) else 1
        start_month = MONTH_NAMES[m.group(2)]
        start_year = int(m.group(3))

        end_day = int(m.group(4)) if m.group(4) else None
        end_token = m.group(5)

        if end_token == "date":
            end_dt = today

        else:
            end_month = MONTH_NAMES[end_token]

            # FIX: if year missing use start year
            end_year = int(m.group(6)) if m.group(6) else start_year

            if end_day:
                end_dt = datetime(
                    end_year,
                    end_month,
                    end_day
                )
            else:
                last_day = monthrange(
                    end_year,
                    end_month
                )[1]

                end_dt = datetime(
                    end_year,
                    end_month,
                    last_day
                )

        start_dt = datetime(
            start_year,
            start_month,
            start_day
        )

        return {
            "type": "single",
            "period": {
                "start": start_dt.strftime("%d-%m-%Y"),
                "end": end_dt.strftime("%d-%m-%Y")
            }
        }


    # ---------------------------------------------------------
    # Explicit range handling:
    # from dec 2025 till may 2026
    # from dec 2025 till date
    # ---------------------------------------------------------

    m = re.search(
        r'from\s+'
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|'
        r'january|february|march|april|june|july|august|'
        r'september|october|november|december)'
        r'\s+(20\d{2})\s+'
        r'(?:to|till)\s+'
        r'(date|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|'
        r'january|february|march|april|june|july|august|'
        r'september|october|november|december)'
        r'(?:\s+(20\d{2}))?',
        q.lower()
    )

    if m:

        start_month = MONTH_NAMES[m.group(1)]
        start_year = int(m.group(2))

        end_token = m.group(3)

        if end_token == "date":
            end_year = today.year
            end_month = today.month
        else:
            end_month = MONTH_NAMES[end_token]
            end_year = int(m.group(4))

        start_dt = datetime(
            start_year,
            start_month,
            1
        )

        last_day = monthrange(
            end_year,
            end_month
        )[1]

        end_dt = datetime(
            end_year,
            end_month,
            last_day
        )

        return {
            "type": "single",
            "period": {
                "start": start_dt.strftime("%d-%m-%Y"),
                "end": end_dt.strftime("%d-%m-%Y")
            }
        }


    # ---------------------------------------------------------
    # Quarter with year: "Q4 2024", "q4 of 2024", "quarter 4 2024"
    # MUST come BEFORE the plain year fallback so that "Q4 2024"
    # is not consumed by the year regex first.
    # ---------------------------------------------------------
    m = re.search(r'\b(?:q(?:uarter)?\s*[-\s]*([1-4])|quarter\s+([1-4]))\b(?:\s*(?:of\s*)?(?:fy\s*)?(\d{4}))?', q)
    if m:
        qnum = int(m.group(1) or m.group(2))
        year_tok = m.group(3)
        fy_year = int(year_tok) if year_tok else fy_start_year
        s_dt, e_dt = fiscal_quarter_start_end(fy_year, qnum)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(20\d{2})\b', q)
    if m:
        month_num = MONTH_NAMES[m.group(1)]
        year = int(m.group(2))
        s_dt, e_dt = month_start_end(year, month_num)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    # ---------------------------------------------------------
    # Financial year handling:
    # eden in 2025
    # -> Apr 2025 to Mar 2026
    # ---------------------------------------------------------

    m = re.search(r'\b(20\d{2})\b', q)

    if m:
        y = int(m.group(1))

        s_dt = datetime(y, 4, 1)
        e_dt = datetime(y + 1, 3, 31)

        return {
            "type": "single",
            "period": {
                "start": s_dt.strftime("%d-%m-%Y"),
                "end": e_dt.strftime("%d-%m-%Y")
            }
        }
    m = re.search(r'(?:last|past)\s+(\d{1,4})\s+days?', q)
    if m:
        n = int(m.group(1))
        end_dt = today
        start_dt = today - timedelta(days=n-1)
        return {"type": "single", "period": {"start": start_dt.strftime("%d-%m-%Y"), "end": end_dt.strftime("%d-%m-%Y")}}

    # ---------------------------------------------------------
    # Week handling:
    #   "this week"          -> Mon–Sun of current ISO week
    #   "last week"          -> Mon–Sun of previous ISO week
    #   "last 2 weeks" / "last N weeks"  -> rolling N*7 days ending yesterday
    #   "last 2 weeks weekly" / "week wise" -> weekly breakdown (mom_last_n style)
    # ---------------------------------------------------------

    # "last N weeks" must come BEFORE "last week" so "last 2 weeks" isn't
    # partially matched as "last" + stray text.
    m = re.search(r'\blast\s+(\d+)\s+weeks?\b', q)
    if m:
        n_weeks = int(m.group(1))
        # end = yesterday (last complete day), start = n_weeks*7 days before that
        end_dt   = today - timedelta(days=1)
        start_dt = end_dt - timedelta(weeks=n_weeks) + timedelta(days=1)
        return {"type": "single", "period": {
            "start": start_dt.strftime("%d-%m-%Y"),
            "end":   end_dt.strftime("%d-%m-%Y")
        }}

    if re.search(r'\blast week\b', q):
        # ISO week: Monday=0 … Sunday=6
        days_since_monday = today.weekday()          # 0=Mon … 6=Sun
        last_week_end   = today - timedelta(days=days_since_monday + 1)   # last Sunday
        last_week_start = last_week_end - timedelta(days=6)               # last Monday
        return {"type": "single", "period": {
            "start": last_week_start.strftime("%d-%m-%Y"),
            "end":   last_week_end.strftime("%d-%m-%Y")
        }}

    if re.search(r'\bthis week\b', q):
        days_since_monday = today.weekday()
        this_week_start = today - timedelta(days=days_since_monday)       # this Monday
        this_week_end   = this_week_start + timedelta(days=6)             # this Sunday
        return {"type": "single", "period": {
            "start": this_week_start.strftime("%d-%m-%Y"),
            "end":   this_week_end.strftime("%d-%m-%Y")
        }}

    if re.search(r'\blast month\b', q):
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return {"type": "single", "period": {"start": last_month_start.strftime("%d-%m-%Y"), "end": last_month_end.strftime("%d-%m-%Y")}}
    if re.search(r'\bthis month\b', q):
        start = today.replace(day=1)
        last_day = monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)
        return {"type": "single", "period": {"start": start.strftime("%d-%m-%Y"), "end": end.strftime("%d-%m-%Y")}}

    if re.search(r'\b(this year|current year|this fy|this fiscal year)\b', q):
        fy_year = fy_start_year
        start_dt = datetime(fy_year, 4, 1)
        end_dt = datetime(fy_year + 1, 3, 31)
        return {"type": "single", "period": {"start": start_dt.strftime("%d-%m-%Y"), "end": end_dt.strftime("%d-%m-%Y")}}

    if re.search(r'\b(last year|previous year|last fy|previous fy)\b', q):
        fy_year = fy_start_year - 1
        start_dt = datetime(fy_year, 4, 1)
        end_dt = datetime(fy_year + 1, 3, 31)
        return {"type": "single", "period": {"start": start_dt.strftime("%d-%m-%Y"), "end": end_dt.strftime("%d-%m-%Y")}}

    if re.search(r'\blast quarter\b', q):
        if 4 <= current_month <= 6:
            cur_q = 1; fy_now = current_year
        elif 7 <= current_month <= 9:
            cur_q = 2; fy_now = current_year
        elif 10 <= current_month <= 12:
            cur_q = 3; fy_now = current_year
        else:
            cur_q = 4; fy_now = current_year - 1
        last_q = cur_q - 1
        if last_q == 0:
            last_q = 4; fy_for_last_q = fy_now - 1
        else:
            fy_for_last_q = fy_now
        s_dt, e_dt = fiscal_quarter_start_end(fy_for_last_q, last_q)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    if re.search(r'\bthis quarter\b', q):
        if 4 <= current_month <= 6:
            cur_q = 1; fy_now = current_year
        elif 7 <= current_month <= 9:
            cur_q = 2; fy_now = current_year
        elif 10 <= current_month <= 12:
            cur_q = 3; fy_now = current_year
        else:
            cur_q = 4; fy_now = current_year - 1
        s_dt, e_dt = fiscal_quarter_start_end(fy_now, cur_q)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\b\s+(?:till|to)\s+date', q)
    if m:
        month_name = m.group(1)
        month_num = MONTH_NAMES[month_name]

        if month_num >= 4:
            year = fy_start_year
        else:
            year = fy_start_year + 1

        start_dt = datetime(year, month_num, 1)
        end_dt = today

        if start_dt > today:
            return {
                "type": "single",
                "period": {
                    "start": fy_start.strftime("%d-%m-%Y"),
                    "end": today.strftime("%d-%m-%Y")
                }
            }

        return {
            "type": "single",
            "period": {
                "start": start_dt.strftime("%d-%m-%Y"),
                "end": today.strftime("%d-%m-%Y")
            }
        }

    m = re.search(
        r'(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')\s*(\d{4})?\s+(?:till|to)\s+date',
        q
    )

    if m:
        day = int(m.group(1))
        month_name = m.group(2)
        year_token = m.group(3)
        month_num = MONTH_NAMES[month_name]

        if year_token:
            year = int(year_token)
        else:
            if month_num >= 4:
                year = fy_start_year
            else:
                year = fy_start_year + 1

        try:
            start_dt = datetime(year, month_num, day)

            if start_dt > today:
                start_dt = fy_start

            return {
                "type": "single",
                "period": {
                    "start": start_dt.strftime("%d-%m-%Y"),
                    "end": today.strftime("%d-%m-%Y")
                }
            }
        except:
            pass

    m = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MONTH_NAMES.keys()) + r')(?:\s+(\d{4}))?\b', q)
    if m:
        d = int(m.group(1)); mn = m.group(2); ytok = m.group(3)
        mn_num = MONTH_NAMES[mn]
        year = int(ytok) if ytok else (fy_start_year if mn_num >= 4 else fy_start_year + 1)
        try:
            dt = datetime(year, mn_num, d)
            return {"type": "single", "period": {"start": dt.strftime("%d-%m-%Y"), "end": dt.strftime("%d-%m-%Y")}}
        except Exception:
            logger.warning("Invalid specific date parsed.")

    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(\d{4})\b', q)
    if m:
        mn = m.group(1); ytok = m.group(2)
        mn_num = MONTH_NAMES[mn]
        y = int(ytok)
        s_dt, e_dt = month_start_end(y, mn_num)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(' + '|'.join(MONTH_NAMES.keys()) + r')\b(?!\s+\d{4})(?!\s+to\b)(?!\s+and\b)', q)
    if m:
        mn = m.group(1)
        mn_num = MONTH_NAMES[mn]
        y = fy_start_year if mn_num >= 4 else fy_start_year + 1
        s_dt, e_dt = month_start_end(y, mn_num)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(\d{4})-(\d{2})\b', q)
    if m:
        y = int(m.group(1)); mo = int(m.group(2))
        s_dt, e_dt = month_start_end(y, mo)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    m = re.search(r'\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})\b', q)
    if m:
        tok = m.group(1)
        for sep, fmt in [('-', "%d-%m-%Y"), ('/', "%d/%m/%Y"), ('.', "%d.%m.%Y")]:
            if sep in tok:
                try:
                    dt = datetime.strptime(tok, fmt)
                    return {"type": "single", "period": {"start": dt.strftime("%d-%m-%Y"), "end": dt.strftime("%d-%m-%Y")}}
                except Exception:
                    continue

    m = re.search(r'\b(?:fy\s*)?(20\d{2})(?:\s*-\s*(?:20)?\d{2})?\b', q)
    if m:
        y = int(m.group(1))
        s_dt = datetime(y, 4, 1)
        e_dt = datetime(y + 1, 3, 31)
        return {"type": "single", "period": {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}}

    logger.info("No date pattern matched — defaulting to current fiscal year.")
    return {"type": "single", "period": {"start": fy_start.strftime("%d-%m-%Y"), "end": fy_end.strftime("%d-%m-%Y")}}


def months_of_fiscal_year(fy_start_year: int) -> Iterable[Tuple[str, Dict[str,str]]]:
    for m in range(4, 13):
        s, e = month_start_end(fy_start_year, m)
        label = f"{s.strftime('%B')} {s.year}"
        yield label, {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}
    for m in range(1, 4):
        s, e = month_start_end(fy_start_year + 1, m)
        label = f"{s.strftime('%B')} {s.year}"
        yield label, {"start": s.strftime("%d-%m-%Y"), "end": e.strftime("%d-%m-%Y")}

def quarters_of_fiscal_year(fy_start_year: int) -> Iterable[Tuple[str, Dict[str,str]]]:
    q1_s, q1_e = fiscal_quarter_start_end(fy_start_year, 1)
    q2_s, q2_e = fiscal_quarter_start_end(fy_start_year, 2)
    q3_s, q3_e = fiscal_quarter_start_end(fy_start_year, 3)
    q4_s, q4_e = fiscal_quarter_start_end(fy_start_year, 4)
    yield (f"Q1 {fy_start_year}-{fy_start_year+1} (Apr–Jun)", {"start": q1_s.strftime("%d-%m-%Y"), "end": q1_e.strftime("%d-%m-%Y")})
    yield (f"Q2 {fy_start_year}-{fy_start_year+1} (Jul–Sep)", {"start": q2_s.strftime("%d-%m-%Y"), "end": q2_e.strftime("%d-%m-%Y")})
    yield (f"Q3 {fy_start_year}-{fy_start_year+1} (Oct–Dec)", {"start": q3_s.strftime("%d-%m-%Y"), "end": q3_e.strftime("%d-%m-%Y")})
    yield (f"Q4 {fy_start_year}-{fy_start_year+1} (Jan–Mar)", {"start": q4_s.strftime("%d-%m-%Y"), "end": q4_e.strftime("%d-%m-%Y")})


def _normalize_product_name(name: str) -> str:
    """
    Normalize a product name for consistent comparison.
    Converts hyphens to spaces and strips extra whitespace.
    e.g. "veridia-6" -> "veridia 6", "eden-5" -> "eden 5"
    """
    return re.sub(r'\s+', ' ', name.replace('-', ' ')).strip()


def _is_partial_product(name: str) -> bool:
    """
    Determine if a product name is a partial/prefix search or an exact search.

    Logic:
    - If the name ends with a digit (e.g. "veridia 6", "veridia-6", "eden 5"),
      treat as EXACT match.
    - If the name has no trailing digit (e.g. "veridia", "eden"), treat as PARTIAL
      (LIKE) match so it catches all variants like "veridia 5", "veridia 6", etc.
    """
    normalized = _normalize_product_name(name)
    return not bool(re.search(r'\d+\s*$', normalized))



def _build_product_where(product_filter_list, column_name="product_category_c"):
    """
    Build SQL WHERE clause for product filters.
    Removes years/numbers accidentally extracted as products.
    """

    if not product_filter_list:
        return ""

    # remove numeric tokens like 2022,2023
    products = []

    for p in product_filter_list:
        p = str(p).strip().lower()

        # skip pure numbers
        if p.isdigit():
            continue

        products.append(p)

    logger.info(f"Clean products: {products}")

    if not products:
        return ""

    conditions = []

    for p in products:
        conditions.append(
            f"LOWER({column_name}) LIKE '%{p}%'"
        )

    return " AND (" + " OR ".join(conditions) + ")"

def run_funnel_for_range(range_dict: Dict[str, str], 
                         header_col: str = "product_category_c", 
                         project_filter: Optional[str] = None,
                         product_filter: Union[str, List[str], None] = None,
                         top_n: Optional[int] = None) -> Dict[str, Any]:
    """
    Query Presto for the range and compute product-wise funnel.
    If project_filter is provided, add WHERE clause for project_c column.
    If product_filter is provided (single str OR list of str), filter accordingly.
    Multiple products use IN clause to match all of them.
    """
    start_date = range_dict["start"]
    end_date = range_dict["end"]
    
    date_filter = f"""
        WHERE date_parse(replace(trim(created_date_c), '/', '-'), '%d-%m-%Y') 
        BETWEEN date_parse('{start_date}', '%d-%m-%Y') AND date_parse('{end_date}', '%d-%m-%Y')
    """
    
    project_where = ""
    if project_filter:
        project_filter_sql = project_filter.replace("'", "''")
        project_where = f" AND LOWER(TRIM(project_c)) = LOWER('{project_filter_sql}')"
        logger.info(f"Running funnel for {start_date} -> {end_date} with project filter: '{project_filter}'")
    
    if isinstance(product_filter, str):
        product_filter_list = [_normalize_product_name(product_filter)]
    elif isinstance(product_filter, list):
        product_filter_list = [_normalize_product_name(p) for p in product_filter]
    else:
        product_filter_list = None

    if product_filter_list:
        logger.info(f"Running funnel for {start_date} -> {end_date} with product filter: {product_filter_list}")

    product_where = _build_product_where(product_filter_list, "product_category_c")
    opp_product_where = _build_product_where(product_filter_list, "project_category_c")
    
    lead_sql = f"""
        SELECT lead_id_c, status, customer_feedback_c, created_date_c, product_category_c, project_c
        FROM {CATALOG}.{LEAD_SCHEMA}.{LEAD_TABLE}
        {date_filter}{project_where}{product_where}
    """
    
    event_sql = f"""
        SELECT activity_id_c, subject_c, appointment_status_c, created_date_c, ownername_c, product_category_c, lead_id_c, project_c
        FROM {CATALOG}.{EVENT_SCHEMA}.{EVENT_TABLE}
        {date_filter}{project_where}{product_where}
    """
    
    opp_sql = f"""
        SELECT opportunity_id_c, lead_id_c, sales_order_number_c, created_date_c, project_category_c, project_c
        FROM {CATALOG}.{OPP_SCHEMA}.{OPP_TABLE}
        {date_filter}{project_where}{opp_product_where}
    """

    try:
        leads = query_presto(CATALOG, LEAD_SCHEMA, lead_sql)
        events = query_presto(CATALOG, EVENT_SCHEMA, event_sql)
        opps = query_presto(CATALOG, OPP_SCHEMA, opp_sql)
    except Exception as e:
        logger.error(f"Error querying data: {e}")
        raise

    filter_desc = f"{start_date} to {end_date}"
    if project_filter:
        filter_desc += f" (project: {project_filter})"
    if product_filter_list:
        filter_desc += f" (product: {', '.join(product_filter_list)})"

    if leads.empty:
        logger.warning(f"No leads found for {filter_desc}")
        return {"status": "no_data", "message": f"No leads found for {filter_desc}"}

    product_funnel = compute_product_wise_funnel(
        leads, events, opps, 
        header_col=header_col, 
        project_filter=project_filter,
        product_filter=product_filter_list,
        top_n=top_n
    )
    totals = calculate_master_totals(product_funnel)
    
    result = {
        "status": "success", 
        "filters": filter_desc, 
        "product_wise_metrics": product_funnel,
        "totals": totals
    }
    
    if project_filter:
        result["project_filter"] = project_filter
    if product_filter_list:
        result["product_filter"] = product_filter_list
    
    return result

def generate_quarter_range(start_q: int, end_q: int, fy_year: int) -> Iterable[Tuple[str, Dict[str,str]]]:
    """Generate quarterly periods between start and end quarters (inclusive)"""
    for q in range(start_q, end_q + 1):
        s_dt, e_dt = fiscal_quarter_start_end(fy_year, q)
        label = f"Q{q} {fy_year}-{fy_year+1}"
        yield label, {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}

def generate_last_n_months(n: int, end_date: datetime) -> Iterable[Tuple[str, Dict[str,str]]]:
    """Generate last N months (end_date is expected to be last day of final month)"""
    months_list = []
    current = end_date.replace(day=1)
    for _ in range(n):
        last_day = monthrange(current.year, current.month)[1]
        month_end = current.replace(day=last_day)
        label = f"{current.strftime('%B')} {current.year}"
        months_list.append((label, {"start": current.strftime("%d-%m-%Y"), "end": month_end.strftime("%d-%m-%Y")}))
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12, day=1)
        else:
            current = current.replace(month=current.month - 1, day=1)
    for item in reversed(months_list):
        yield item

def generate_last_n_quarters(n: int, current_q: int, current_fy: int) -> Iterable[Tuple[str, Dict[str,str]]]:
    """Generate last N quarters (current_q/current_fy should point to last COMPLETED quarter)"""
    quarters_list = []
    q = current_q
    fy = current_fy

    for _ in range(n):
        s_dt, e_dt = fiscal_quarter_start_end(fy, q)
        label = f"Q{q} {fy}-{fy+1}"
        quarters_list.append((label, {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}))
        q -= 1
        if q < 1:
            q = 4
            fy -= 1

    for item in reversed(quarters_list):
        yield item


def extract_top_n(question: str) -> Optional[int]:
    """
    Extract a 'top N' limit from phrases like:
      "top 5 products"  /  "top 10 product wise"  /  "show top 3"
    Returns the integer N, or None if not present.
    """
    m = re.search(r'\btop\s+(\d+)\b', question, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


@app.post("/funnel/product/question")
async def funnel_from_question(payload: Dict[str, Any] = Body(...)):
    """
    Body parameters:
      - question: str OR
      - questions: [str, ...]
      - header_col: optional (defaults to product_category_c)
      - project: optional (filter by specific project name)
      - product: optional (filter by specific product name)
    """
    header_col = payload.get("header_col") or payload.get("group_by") or "product_category_c"
    
    explicit_project_filter = payload.get("project") or payload.get("project_name")
    explicit_product_filter = payload.get("product") or payload.get("product_name")
    
    raw_questions = []

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
            question_project = None
            question_product = None
            
            if not explicit_project_filter:
                question_project = extract_project_name_v2(qtext)
                if question_project:
                    logger.info(f"Extracted project from question: '{question_project}'")
            
            if not explicit_product_filter:
                question_product = extract_product_names(qtext)
                if question_product:
                    logger.info(f"Extracted product(s) from question: {question_product}")
            
            active_project_filter = explicit_project_filter or question_project
            if explicit_product_filter:
                active_product_filter = [explicit_product_filter] if isinstance(explicit_product_filter, str) else explicit_product_filter
            else:
                active_product_filter = question_product  # already a list or None
            
            parsed = parse_question_dates(qtext)
            logger.info(f"Parsed for '{qtext}': {parsed}")
            
            if active_project_filter:
                logger.info(f"Using project filter: '{active_project_filter}'")
            if active_product_filter:
                logger.info(f"Using product filter: {active_product_filter}")
            
            # Extract optional top-N limit from the question ("top 5 products")
            question_top_n = extract_top_n(qtext)
            if question_top_n:
                logger.info(f"Top-N limit extracted: {question_top_n}")

            qres = None

            if parsed.get("type") == "single":
                qres = run_funnel_for_range(
                    parsed["period"], 
                    header_col=header_col, 
                    project_filter=active_project_filter,
                    product_filter=active_product_filter,
                    top_n=question_top_n
                )

            elif parsed.get("type") == "compare":
                p1 = run_funnel_for_range(
                    parsed["period"], 
                    header_col=header_col, 
                    project_filter=active_project_filter,
                    product_filter=active_product_filter,
                    top_n=question_top_n
                )
                p2 = run_funnel_for_range(
                    parsed["previous_period"], 
                    header_col=header_col, 
                    project_filter=active_project_filter,
                    product_filter=active_product_filter,
                    top_n=question_top_n
                )
                qres = {"period": p1, "previous_period": p2}

            elif parsed.get("type") == "mom_all":
                aggregated = {}
                if parsed.get("quarter"):
                    quarter_start, quarter_end = fiscal_quarter_start_end(
                        parsed["fy_start_year"], parsed["quarter"]
                    )
                    month_periods = generate_month_range(
                        quarter_start.month,
                        quarter_end.month,
                        quarter_start.year,
                        quarter_end.year
                    )
                else:
                    month_periods = months_of_fiscal_year(parsed["fy_start_year"])

                for label, rng in month_periods:
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"fy_start_year": parsed["fy_start_year"], "months": aggregated}

            elif parsed.get("type") == "mom_range":
                aggregated = {}
                for label, rng in generate_month_range(
                    parsed["start_month"], parsed["end_month"], 
                    parsed["start_year"], parsed["end_year"]
                ):
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"type": "monthly_range", "months": aggregated}

            elif parsed.get("type") == "mom_list":
                aggregated = {}
                i = 1
                for period in parsed["months"]:
                    label = f"Month_{i}"
                    aggregated[label] = run_funnel_for_range(
                        period, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                    i += 1
                qres = {"type": "month_list", "months": aggregated}

            elif parsed.get("type") == "mom_last_n":
                aggregated = {}
                end_date = parsed["end_date"]
                if isinstance(end_date, str):
                    end_date = datetime.strptime(end_date, "%d-%m-%Y")
                for label, rng in generate_last_n_months(parsed["n_months"], end_date):
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"type": "last_n_months", "n": parsed["n_months"], "months": aggregated}

            elif parsed.get("type") == "qoq_all":
                aggregated = {}
                for label, rng in quarters_of_fiscal_year(parsed["fy_start_year"]):
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"fy_start_year": parsed["fy_start_year"], "quarters": aggregated}

            elif parsed.get("type") == "qoq_range":
                aggregated = {}
                for label, rng in generate_quarter_range(
                    parsed["start_quarter"], parsed["end_quarter"], 
                    parsed["fy_start_year"]
                ):
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"type": "quarterly_range", "quarters": aggregated}

            elif parsed.get("type") == "qoq_list":
                aggregated = {}
                for qinfo in parsed["quarters"]:
                    qnum = qinfo["quarter"]
                    fy = qinfo["fy_year"]
                    s_dt, e_dt = fiscal_quarter_start_end(fy, qnum)
                    label = f"Q{qnum} {fy}-{fy+1}"
                    aggregated[label] = run_funnel_for_range(
                        {"start": s_dt.strftime("%d-%m-%Y"), "end": e_dt.strftime("%d-%m-%Y")}, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"type": "quarter_list", "quarters": aggregated}

            elif parsed.get("type") == "qoq_last_n":
                aggregated = {}
                for label, rng in generate_last_n_quarters(
                    parsed["n_quarters"], parsed["current_quarter"], 
                    parsed["current_fy"]
                ):
                    aggregated[label] = run_funnel_for_range(
                        rng, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"type": "last_n_quarters", "n": parsed["n_quarters"], "quarters": aggregated}

            # ---------------------------------------------------------
            # FIX 3 — Multi-FY QoQ handler
            # "Show me quarter wise funnel for wave garden in fy 2022 and fy 2023"
            # Returns quarters broken out separately per FY, not merged.
            # Response shape:
            # {
            #   "type": "qoq_multi_fy",
            #   "fiscal_years": {
            #     "FY 2022-2023": { "fy_start_year": 2022, "quarters": { ... } },
            #     "FY 2023-2024": { "fy_start_year": 2023, "quarters": { ... } }
            #   }
            # }
            # ---------------------------------------------------------
            elif parsed.get("type") == "qoq_multi_fy":
                aggregated = {}
                for fy_year in parsed["fy_years"]:
                    fy_label = f"FY {fy_year}-{fy_year + 1}"
                    fy_quarters = {}
                    for label, rng in quarters_of_fiscal_year(fy_year):
                        fy_quarters[label] = run_funnel_for_range(
                            rng,
                            header_col=header_col,
                            project_filter=active_project_filter,
                            product_filter=active_product_filter
                        )
                    aggregated[fy_label] = {
                        "fy_start_year": fy_year,
                        "quarters": fy_quarters
                    }
                qres = {"type": "qoq_multi_fy", "fiscal_years": aggregated}

            elif parsed.get("type") == "yoy":
                agg = {}
                for label, pr in parsed["periods"].items():
                    start_date = pr["start"]
                    end_date = pr["end"]
                    agg[label] = run_funnel_for_range(
                        {"start": start_date, "end": end_date}, 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                qres = {"yoy_comparison": agg}

            else:
                if "period" in parsed:
                    qres = run_funnel_for_range(
                        parsed["period"], 
                        header_col=header_col, 
                        project_filter=active_project_filter,
                        product_filter=active_product_filter
                    )
                else:
                    qres = {"status": "error", "message": "Unable to interpret query dates."}

            results[qtext] = {"parsed": parsed, "result": qres}
            if active_project_filter:
                results[qtext]["project_filter_applied"] = active_project_filter
            if active_product_filter:
                results[qtext]["product_filter_applied"] = active_product_filter
        except Exception as e:
            logger.exception(f"Error processing question '{qtext}'")
            results[qtext] = {"status": "error", "message": str(e)}

    response = {
        "status": "success", 
        "count": len(raw_questions), 
        "header_col": header_col, 
        "responses": results
    }
    
    if explicit_project_filter:
        response["project_filter"] = explicit_project_filter
    if explicit_product_filter:
        response["product_filter"] = [explicit_product_filter] if isinstance(explicit_product_filter, str) else explicit_product_filter
    
    return response
