# import requests
# import os
# import re
# import math
# import numpy as np
# from typing import Optional
# from typing import List, Dict, Any, Tuple, Union
# from fastapi import FastAPI, HTTPException
# from pydantic import BaseModel
# import pandas as pd
# from datetime import datetime, timedelta,date
# import json
# from dotenv import load_dotenv
# from dateutil import parser as date_parser
# from calendar import monthrange
# from difflib import get_close_matches
# load_dotenv()

# DATA_DICTIONARY = {
#     "qualified_leads": {
#         "keywords": ["qualified", "ql", "lead"],
#         "columns": [
#             "ql_target",
#             "ql_actual",
#             "total_leads"
#         ],
#         "report": "report_1"
#     },

#     "appointments": {
#         "keywords": ["appointment", "meeting", "completion","booked","activities"],
#         "columns": [
#             "appt_booked_actual",
#             "appt_booked_target",
#             "appt_completion_actual",
#             "appt_completion_target",
#             "total_activities"
#         ],
#         "report": "report_2"
#     },

#     "service_requests": {
#         "keywords": ["service request", "sr", "resolved"],
#         "columns": [
#             "sr_target",
#             "resolved_actual",
#             "total_sr"
#         ],
#         "report": "report_3"
#     }
# }

# KPI_COLUMN_MAP = {
#     "qualified_leads":[
#         "user_name",
#         "ql_actual",
#         "ql_target"
#     ],

#     "completion": [
#         "user_name",
#         "appt_completion_actual",
#         "appt_completion_target"
#     ],
#     "booked": [
#         "user_name",
#         "appt_booked_actual",
#         "appt_booked_target"
#     ],
#     "service_requests":[
#         "user_name",
#         "resolved_actual",
#         "sr_target"
#     ]
# }

# def get_salesforce_token() -> Optional[str]:
#     """
#     Get Salesforce access token using password flow (for dev only).
#     In production, prefer JWT Bearer or Client Credentials flow.
#     """
#     url = os.getenv("GET_TOKEN_URL")
    
#     if not url:
#         url = "https://waveinfratech.my.salesforce.com/services/oauth2/token"
#         return url
    
#     params = {
#         "grant_type": os.getenv("GRANT_TYPE"),                     # ← grant type
#         "client_id": os.getenv("CLIENT_ID"),           # ← your client id
#         "client_secret": os.getenv("CLIENT_SECRET"),   # ← your client secret
#         "token_url": os.getenv("TOKEN_URL")            # ← your token URL
#     }
    
#     try:
#         response = requests.post(url, params=params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
#         return data["access_token"]
#     except Exception as e:
#         print(f"Authentication failed: {e}")
#         return None
    

# def fetch_combined_reports(start_date: str, end_date: str, token: str) -> dict | None:
#     """
#     Fetch data from your custom combinedReports endpoint
#     Example: start_date="2024-09-01", end_date="2024-09-30"
#     """
#     print("Fetching combined reports...")
#     api_endpoint = os.getenv("API_ENDPOINT")
#     print(f"API Endpoint: {api_endpoint}")
#     api_url = f"{os.getenv('API_ENDPOINT')}?startDate={start_date}&endDate={end_date}"

#     print(f"Fetching data from {api_url}")
    
#     headers = {
#         "Authorization": f"Bearer {token}",
#         "Accept": "application/json"
#     }
    
#     try:
#         response = requests.get(api_url, headers=headers, timeout=15)
#         response.raise_for_status()
#         return response.json()
#     except requests.RequestException as e:
#         print(f"API call failed: {e}")
#         return None
    


# def parse_report_data(raw_data: dict) -> Dict[str, Any]:
#     """
#     Parse the combined reports JSON into a clean structure.
#     Returns dict with 'report1', 'report2', 'report3' keys.
#     """

#     if isinstance(raw_data, str):
#         try:
#             raw_data = json.loads(raw_data)
#         except json.JSONDecodeError as e:
#             raise ValueError("Invalid JSON string received") from e

#     print(f"Raw data keys: {list(raw_data.keys())}")
#     parsed = {}
    
#     print("Parsing report data...")
#     print("Type of data:", type(raw_data))
#     print(f"Raw data keys: {list(raw_data.keys())}")

#     appt_target_by_user = {}
#     for report_name in ["Report 1", "Report 2", "Report 3"]:
#         if report_name not in raw_data:
#             continue

        
            
#         report = raw_data[report_name]
#         # print(report)
#         groupings = report["groupingsDown"]["groupings"]
#         fact_map = report["factMap"]
        
#         rows = []
#         for group in groupings:
#             user_id = group["value"]
#             user_name = group["label"]
#             key = f"{group['key']}!T"
            
#             if key not in fact_map:
#                 continue
                
#             aggregates = fact_map[key]["aggregates"]
#             print(aggregates,"aggregates==============================")
#             row = None
#             if report_name == "Report 1":  # Leads / QL
#                 appt_target_by_user[user_id] = float(aggregates[2]["value"])
#                 row = {
#                     "user_id": user_id,
#                     "user_name": user_name,
#                     "ql_target": float(aggregates[0]["value"]),
#                     "ql_actual": float(aggregates[1]["value"]),
#                     "appt_booked_target": appt_target_by_user[user_id],
#                     "total_leads": int(aggregates[3]["value"])
#                 }
#             elif report_name == "Report 2":  # Activities
#                 row = {
#                     "user_id": user_id,
#                     "user_name": user_name,
#                     "appt_booked_actual": int(aggregates[0]["value"]),
#                     "appt_booked_target": appt_target_by_user.get(user_id, 0.0),
#                     "appt_completion_target": float(aggregates[1]["value"]),
#                     "appt_completion_actual": float(aggregates[2]["value"]),
#                     "total_activities": int(aggregates[3]["value"])
#                 }
#             elif report_name == "Report 3":  # Service Requests
#                 row = {
#                     "user_id": user_id,
#                     "user_name": user_name,
#                     "sr_target": float(aggregates[0]["value"]),
#                     "resolved_actual": float(aggregates[1]["value"]),
#                     "total_sr": int(aggregates[2]["value"])
#                 }
#             if row:
#                 rows.append(row)
        
#         parsed[report_name.lower().replace(" ", "_")] = {
#             "rows": rows,
#             "grand_total": fact_map.get("T!T", {}).get("aggregates", [])
#         }
    
#     return parsed




# app = FastAPI(title="CRM Performance Chatbot API")

# class QueryRequest(BaseModel):
#     query: str
#     start_date: str | None = None   # optional YYYY-MM-DD
#     end_date: str | None = None

# def simple_intent_extraction(query: str) -> str:
#     query = query.lower()
#     if "qualified" in query or "ql" in query or "lead" in query:
#         return "ql"
#     if "appointment" in query or "booked" in query or "completion" in query:
#         return "appointments"
#     if "service request" in query or "sr" in query or "resolved" in query:
#         return "sr"
#     return "overview"

# #---------------------Normlize text-------------------------
# def normalize(text: str):
#     return text.lower().replace(",", " ").replace("-", " ").replace("  ", " ").strip()

# def extract_year_from_text(text):
#     for part in text.split():
#         if part.isdigit() and len(part) == 4:
#             return int(part)
#     return None

# def get_fy_quarter(m):
#     if 4 <= m <= 6:   return 1
#     if 7 <= m <= 9:   return 2
#     if 10 <= m <= 12: return 3
#     return 4

# def get_current_fy():
#     today = datetime.today()
#     fy_start = today.year if today.month >= 4 else today.year - 1
#     return fy_start

# def parse_single_date(q: str | None) -> date | None:
#     """
#     Parse single date forms like:
#       - '15 april 2024'
#       - '15 april'
#       - '5th june 23'
#       - '15/04/2024' or '15-04-2024'
#     Returns a datetime.date or None.
#     """
#     if not q or not isinstance(q, str):
#         return None

#     original_q = q
#     q = q.strip().lower()

#     # First try: DD/MM/YYYY or DD-MM-YYYY
#     slash_match = re.match(r'^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$', q)
#     if slash_match:
#         day, month, year = map(int, slash_match.groups())
#         if year < 100:
#             year += 2000
#         try:
#             return datetime(year, month, day).date()
#         except ValueError:
#             return None

#     # Natural language: 15 april 2024, 5th june 23, etc.
#     m = re.search(
#         r'\b([0-3]?\d)(?:st|nd|rd|th)?\s+'
#         r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
#         r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
#         r'nov(?:ember)?|dec(?:ember)?)'
#         r'(?:[,\s]+(20\d{2}|\d{2}))?\b',
#         q
#     )
#     if not m:
#         return None

#     day = int(m.group(1))
#     month_name = m.group(2)
#     year_part = m.group(3)

#     month = extract_month_by_name(month_name)
#     if not month:
#         return None

#     if year_part:
#         year = int(year_part)
#         if len(year_part) == 2:
#             year += 2000
#     else:
#         fy = get_current_fy()
#         year = fy if month >= 4 else fy + 1

#     try:
#         return datetime(year, month, day).date()
#     except ValueError:
#         return None
    
# def detect_qoq(question: str):
#     import re
#     from datetime import datetime

#     text = question.lower().strip()

#     # ----------------------------------------
#     # 1) Detect user intent (QOQ / Quarterly)
#     # ----------------------------------------
#     trigger_keywords = [
#         "qoq", "quarter on quarter", "quarter-wise", "quarter wise",
#         "quater wise", "quarterly", "quarterwise"
#     ]
#     if not any(kw in text for kw in trigger_keywords):
#         return None

#     # ----------------------------------------
#     # 2) Determine current FY
#     # ----------------------------------------
#     today = datetime.today()
#     current_fy = today.year if today.month >= 4 else today.year - 1

#     # ----------------------------------------
#     # 3) Extract explicit year (2023, 2024…)
#     # ----------------------------------------
#     year_match = re.search(r"\b(20\d{2})\b", text)
#     explicit_year = int(year_match.group(1)) if year_match else None

#     # ----------------------------------------
#     # 4) Extract FY format like "FY24" or "fy2025"
#     # ----------------------------------------
#     fy_match = re.search(r"fy\s?(\d{2,4})", text)
#     explicit_fy = None
#     if fy_match:
#         fy_value = fy_match.group(1)
#         if len(fy_value) == 2:
#             explicit_fy = int("20" + fy_value)      # fy24 → 2024
#         else:
#             explicit_fy = int(fy_value)             # fy2024 → 2024

#     # ----------------------------------------
#     # 5) Detect LAST YEAR / PREVIOUS FY logic
#     # ----------------------------------------
#     if "last year" in text or "previous year" in text or "last fy" in text or "previous fy" in text:
#         target_fy = current_fy - 1

#     elif explicit_year:
#         target_fy = explicit_year

#     elif explicit_fy:
#         target_fy = explicit_fy

#     else:
#         # default → current FY
#         target_fy = current_fy

#     print(f"QOQ → Using FY{target_fy}")

#     # Generate all 4 quarters from Q1 to Q4
#     def quarter_dates(q, fy_year):
#         if q == 1:
#             return f"01-04-{fy_year}", f"30-06-{fy_year}"
#         elif q == 2:
#             return f"01-07-{fy_year}", f"30-09-{fy_year}"
#         elif q == 3:
#             return f"01-10-{fy_year}", f"31-12-{fy_year}"
#         elif q == 4:
#             return f"01-01-{fy_year + 1}", f"31-03-{fy_year + 1}"

#     quarters = []
#     for q in range(1, 5):
#         start, end = quarter_dates(q, target_fy)
#         quarters.append({
#             "quarter": f"Q{q} FY{target_fy}",
#             "start_date": start,
#             "end_date": end
#         })


#     return {
#         "type": "quarter_wise",
#         "fy": target_fy,
#         "quarters": quarters  # Always Q1 → Q2 → Q3 → Q4
#     }

# def detect_mom(question: str):
#     import re
#     from datetime import datetime
#     from calendar import monthrange

#     q = question.lower().strip()

#     # --------------------------------------------------
#     # 1) MOM INTENT CHECK
#     # --------------------------------------------------
#     mom_keywords = [
#         "mom",
#         "month on month",
#         "month-on-month",
#         "monthly",
#         "month wise",
#         "month over month"
#     ]

#     if not any(k in q for k in mom_keywords):
#         return None

#     today = datetime.today()

#     # --------------------------------------------------
#     # 2) FINANCIAL YEAR (APR–MAR)
#     # --------------------------------------------------
#     current_fy = today.year if today.month >= 4 else today.year - 1

#     FY_QUARTERS = {
#         1: (4, 6),    # Apr–Jun
#         2: (7, 9),    # Jul–Sep
#         3: (10, 12),  # Oct–Dec
#         4: (1, 3)     # Jan–Mar
#     }

#     # --------------------------------------------------
#     # 3) MONTH NORMALIZATION
#     # --------------------------------------------------
#     month_map = {
#         "jan": 1, "january": 1,
#         "feb": 2, "february": 2,
#         "mar": 3, "march": 3,
#         "apr": 4, "april": 4,
#         "may": 5,
#         "jun": 6, "june": 6,
#         "jul": 7, "july": 7,
#         "aug": 8, "august": 8,
#         "sep": 9, "september": 9,
#         "oct": 10, "october": 10,
#         "nov": 11, "november": 11,
#         "dec": 12, "december": 12
#     }

#     # --------------------------------------------------
#     # 4) EXTRACT YEAR / FY
#     # --------------------------------------------------
#     year_match = re.search(r"\b(20\d{2})\b", q)
#     specified_year = int(year_match.group(1)) if year_match else None

#     fy_match = re.search(r"\bfy\s?(\d{2})\b", q)
#     specified_fy = int("20" + fy_match.group(1)) if fy_match else None

#     # --------------------------------------------------
#     # 5) EXPLICIT MONTH RANGE
#     # --------------------------------------------------
#     month_range_regex = (
#         r"\b(" + "|".join(month_map.keys()) + r")\b\s*"
#         r"(to|till|-|and)\s*"
#         r"\b(" + "|".join(month_map.keys()) + r")\b"
#     )

#     m_range = re.search(month_range_regex, q)

#     # --------------------------------------------------
#     # 6) QUARTER DETECTION
#     # --------------------------------------------------
#     q_match = re.search(r"\bq([1-4])\b", q)
#     quarter = int(q_match.group(1)) if q_match else None

#     is_last_quarter = (
#         "last quarter" in q or
#         "previous quarter" in q
#     )

#     # --------------------------------------------------
#     # 7) RESOLVE TARGET MONTH RANGE
#     # --------------------------------------------------
#     if m_range:
#         sm = month_map[m_range.group(1)]
#         em = month_map[m_range.group(3)]
#         fy = specified_fy or specified_year or current_fy
#         year = fy if sm >= 4 else fy + 1

#     elif is_last_quarter:
#         # Determine current FY start year
#         current_fy = today.year if today.month >= 4 else today.year - 1

#         # Determine current quarter inside FY (Apr–Mar)
#         if 4 <= today.month <= 6:
#             curr_q = 1
#         elif 7 <= today.month <= 9:
#             curr_q = 2
#         elif 10 <= today.month <= 12:
#             curr_q = 3
#         else:
#             curr_q = 4  # Jan–Mar

#         # Determine last quarter
#         if curr_q == 1:
#             last_q = 4
#             fy = current_fy - 1
#         else:
#             last_q = curr_q - 1
#             fy = current_fy

#         FY_QUARTERS = {
#             1: (4, 6),
#             2: (7, 9),
#             3: (10, 12),
#             4: (1, 3)
#         }

#         sm, em = FY_QUARTERS[last_q]

#         # Year handling
#         if last_q == 4:
#             year = fy + 1   # Jan–Mar belongs to next calendar year
#         else:
#             year = fy

#     elif quarter:
#         fy = current_fy

#         if "last year" in q or "previous year" in q:
#             fy = current_fy - 1
#         # If user gives FY explicitly (e.g. fy24)
#         if specified_fy:
#             fy = specified_fy

#         # If user gives calendar year (e.g. 2024)
#         elif specified_year:
#             fy = specified_year  # treat as FY start year

#         sm, em = FY_QUARTERS[quarter]

#         # Year handling for calendar year mapping
#         if quarter == 4:
#             year = fy + 1  # Jan–Mar belongs to next calendar year
#         else:
#             year = fy

#     elif (
#         "last year" in q or
#         "previous year" in q or
#         "previous fy" in q
#     ):
#         fy = current_fy - 1
#         sm, em = 4, 3
#         year = fy

#     elif specified_fy or specified_year:
#         fy = specified_fy or specified_year
#         sm, em = 4, 3
#         year = fy

#     else:
#         # Default → current FY till today
#         fy = current_fy
#         sm = 4
#         em = today.month
#         year = fy

#     # --------------------------------------------------
#     # 8) GENERATE MONTH-WISE PERIODS
#     # --------------------------------------------------
#     periods = []

#     y = year
#     m = sm

#     while True:
#         _, last_day = monthrange(y, m)

#         start_date = f"01-{m:02d}-{y}"
#         end_date = f"{last_day:02d}-{m:02d}-{y}"

#         label = datetime(y, m, 1).strftime("%b %Y")

#         # Apply MTD only for current FY current month
#         if fy == current_fy and y == today.year and m == today.month:
#             end_date = today.strftime("%d-%m-%Y")
#             label += " (MTD)"

#         periods.append({
#             "label": label,
#             "start_date": start_date,
#             "end_date": end_date,
#             "period": f"{start_date} to {end_date}"
#         })

#         if m == em:
#             break

#         m += 1
#         if m > 12:
#             m = 1
#             if y is None:
#                 y = datetime.today().year
#             y += 1

#     return {
#         "type": "mom",
#         "fy": f"FY{fy}",
#         "periods": periods
#     }

# def detect_yoy(question: str):
#     text = question.lower().strip()

#     # Trigger keywords for YoY
#     trigger_keywords = [
#         "yoy", "year on year", "year-on-year", "year over year",
#         "last 3 years", "last three years", "past 3 years",
#         "yoy performance", "yearly comparison"
#     ]

#     if not any(kw in text for kw in trigger_keywords):
#         return None

#     from datetime import datetime
#     today = datetime.today()
#     current_fy = today.year if today.month >= 4 else today.year - 1

#     # We want last 3 COMPLETED financial years
#     # Example: Today = Nov 2025 → Current FY = 2025 → Completed = FY22, FY23, FY24
#     latest_completed_fy = current_fy - 1
#     years = [
#         latest_completed_fy - 2,  # e.g., FY22
#         latest_completed_fy - 1,  # e.g., FY23
#         latest_completed_fy,      # e.g., FY24
#         latest_completed_fy + 1   # e.g., FY25 (current FY, optional)
#     ]

#     print(f"YoY detected → Comparing last 3 FYs: {years}")

#     def fy_dates(fy_year: int):
#         return f"01-04-{fy_year}", f"31-03-{fy_year + 1}"

#     yoy_periods = []
#     for fy in years:
#         start, end = fy_dates(fy)
#         yoy_periods.append({
#             "year": f"FY{fy}",
#             "start_date": start,
#             "end_date": end
#         })

#     return {
#         "type": "yoy",
#         "years": years,
#         "periods": yoy_periods
#     }


# def parse_single_or_range_date(q: str | None):
#     if not q or not isinstance(q, str):
#         return None

#     q = q.strip()
#     q_lower = q.lower()

#     RANGE_WORDS = r'(?:to|till|until|thru|through|-|–|—)'
#     MONTH_PATTERN = (
#         r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
#         r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
#         r'nov(?:ember)?|dec(?:ember)?)'
#     )

#     # ------------------------------------------------------
#     # 1️⃣ SAME MONTH RANGE
#     # "15 to 30 april", "15 sep to 30 september 2024"
#     # ------------------------------------------------------
#     same_month_pattern = (
#         r'(\d{1,2}(?:st|nd|rd|th)?)\s*' +
#         RANGE_WORDS +
#         r'\s*(\d{1,2}(?:st|nd|rd|th)?)\s+' +
#         MONTH_PATTERN +
#         r'(?:[,\s]+(20\d{2}|\d{2}))?'
#     )

#     m = re.search(same_month_pattern, q_lower)
#     if m:
#         day1, day2, month, year = m.groups()
#         raw1 = f"{day1} {month}" + (f" {year}" if year else "")
#         raw2 = f"{day2} {month}" + (f" {year}" if year else "")

#         d1 = parse_single_date(raw1)
#         d2 = parse_single_date(raw2)
#         if d1 and d2 and d1 <= d2:
#             return d1, d2

#     # ------------------------------------------------------
#     # 2️⃣ DIFFERENT MONTH RANGE
#     # "15 sep to 30 oct"
#     # ------------------------------------------------------
#     diff_month_pattern = (
#         r'(\d{1,2}(?:st|nd|rd|th)?)\s+' +
#         MONTH_PATTERN +
#         r'\s*' +
#         RANGE_WORDS +
#         r'\s*(\d{1,2}(?:st|nd|rd|th)?)\s+' +
#         MONTH_PATTERN +
#         r'(?:[,\s]+(20\d{2}|\d{2}))?'
#     )

#     m = re.search(diff_month_pattern, q_lower)
#     if m:
#         day1, month1, day2, month2, year = m.groups()
#         raw1 = f"{day1} {month1}" + (f" {year}" if year else "")
#         raw2 = f"{day2} {month2}" + (f" {year}" if year else "")

#         d1 = parse_single_date(raw1)
#         d2 = parse_single_date(raw2)
#         if d1 and d2 and d1 <= d2:
#             return d1, d2

#     # ------------------------------------------------------
#     # 3️⃣ SLASH / HYPHEN RANGE
#     # ------------------------------------------------------
#     numeric_pattern = (
#         r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*' +
#         RANGE_WORDS +
#         r'\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
#     )

#     m = re.search(numeric_pattern, q_lower)
#     if m:
#         d1 = parse_single_date(m.group(1))
#         d2 = parse_single_date(m.group(2))
#         if d1 and d2 and d1 <= d2:
#             return d1, d2

#     # ------------------------------------------------------
#     # 4️⃣ SINGLE DATE FALLBACK
#     # ------------------------------------------------------
#     single = parse_single_date(q)
#     if single:
#         return single, single

#     return None

# def get_current_fy_year() -> int:
#     today = datetime.today()
#     return today.year if today.month >= 4 else today.year - 1

# def get_last_day_of_month(year: int, month: int) -> int:
#     """Safely return the last day of the given month/year."""
#     # List of days in each month (index 0 unused)
#     month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    
#     if month == 2:
#         # Check for leap year
#         if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
#             return 29
#         else:
#             return 28
#     else:
#         return month_days[month]

# def parse_multi_month_date(q: str) -> List[Tuple[str, str]] | None:
#     """
#     Rules:
#     - 'and', ','  → discrete months (ONLY those months)
#     - 'to'        → continuous range (fill months in between)
#     Financial Year: April–March
#     """

#     print("parse_multi_month_date=============================in function=====")

#     if not q or not isinstance(q, str):
#         return None

#     q_lower = q.lower().strip()

#     # -------------------------------------------------
#     # 🚫 HARD BLOCKS (wrong intent)
#     # -------------------------------------------------

#     # Exact date present → handled by date parser
#     if (
#         re.search(r'\b\d{1,2}(st|nd|rd|th)?\b', q_lower)
#         and re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', q_lower)
#     ):
#         return None

#     # Quarter intent → handled by quarter parser
#     if re.search(r'\bq[1-4]\b|quarter', q_lower):
#         return None

#     # Exact date range like "15 sep to 30 oct"
#     if re.search(
#         r'\b\d{1,2}\b.*\b(to|till|until|through|-|–|—)\b.*\b\d{1,2}\b',
#         q_lower
#     ):
#         return None

#     # Continuous month range with relative year → handled elsewhere
#     if (
#         ' to ' in q_lower
#         and re.search(r'\b(last|previous|this|current|next)\s+year\b', q_lower)
#     ):
#         return None

#     # Non-"to" range words should not enter here
#     if re.search(r'\b(till|until|through|-|–|—)\b', q_lower) and ' to ' not in q_lower:
#         return None

#     # -------------------------------------------------
#     # Detect continuous vs discrete
#     # -------------------------------------------------
#     is_continuous = ' to ' in q_lower

#     # -------------------------------------------------
#     # Relative year handling (ONLY for discrete months)
#     # -------------------------------------------------
#     fy_shift = 0
#     if re.search(r'\b(last|previous)\s+year\b', q_lower):
#         fy_shift = -1
#     elif re.search(r'\b(next)\s+year\b', q_lower):
#         fy_shift = 1

#     # -------------------------------------------------
#     # 1️⃣ YEAR-ONLY DETECTION
#     # -------------------------------------------------
#     year_pattern = r'\b(19\d{2}|20\d{2})\b'
#     years = [int(y) for y in re.findall(year_pattern, q_lower)]

#     month_check = re.search(
#         r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec',
#         q_lower
#     )

#     if years and not month_check:
#         years = sorted(set(years))
#         ranges = []

#         if is_continuous:
#             start_year = years[0]
#             end_year = years[-1]
#             ranges.append(
#                 (f"01-04-{start_year}", f"31-03-{end_year + 1}")
#             )
#         else:
#             for y in years:
#                 ranges.append(
#                     (f"01-04-{y}", f"31-03-{y + 1}")
#                 )

#         return ranges

#     # -------------------------------------------------
#     # 2️⃣ MONTH LOGIC
#     # -------------------------------------------------

#     q_normalized = re.sub(r'\s+and\s+|\s*,\s*', ' ', q_lower)

#     month_pattern = (
#         r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
#         r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
#         r'nov(?:ember)?|dec(?:ember)?)'
#     )

#     month_matches = re.findall(month_pattern, q_normalized)
#     if not month_matches:
#         return None

#     # Explicit year mentions
#     year_pattern = r'\b(20\d{2}|19\d{2}|\d{2})\b'
#     year_mentions = re.findall(year_pattern, q_lower)

#     explicit_years = []
#     for y in year_mentions:
#         yi = int(y)
#         if len(y) == 2:
#             yi += 2000
#         explicit_years.append(yi)

#     default_year = get_current_fy_year() + fy_shift

#     month_years = []

#     for i, month_name in enumerate(month_matches):
#         month_num = extract_month_by_name(month_name)
#         if not month_num:
#             continue

#         if explicit_years:
#             year = explicit_years[min(i, len(explicit_years) - 1)]
#         else:
#             year = default_year + 1 if month_num < 4 else default_year

#         month_years.append((year, month_num))

#     if not month_years:
#         return None

#     month_years = sorted(set(month_years))

#     # -------------------------------------------------
#     # 3️⃣ BUILD RESULT
#     # -------------------------------------------------
#     ranges: List[Tuple[str, str]] = []

#     if is_continuous:
#         start_year, start_month = month_years[0]
#         end_year, end_month = month_years[-1]

#         start_date = f"01-{start_month:02d}-{start_year}"
#         last_day = get_last_day_of_month(end_year, end_month)
#         end_date = f"{last_day:02d}-{end_month:02d}-{end_year}"

#         ranges.append((start_date, end_date))
#     else:
#         for year, month in month_years:
#             last_day = get_last_day_of_month(year, month)
#             start_date = f"01-{month:02d}-{year}"
#             end_date = f"{last_day:02d}-{month:02d}-{year}"
#             ranges.append((start_date, end_date))

#     return ranges

# def parse_multi_year_date(q: str) -> List[Tuple[str, str]] | None:
#     """
#     Rules:
#     - 'and', ','  → discrete years
#     - 'to'        → continuous range
#     Financial Year: April–March
#     """

#     print("parse_multi_year_date=============================in function=====")

#     if not q or not isinstance(q, str):
#         return None

#     q_lower = q.lower().strip()

#     # -------------------------------------------------
#     # 🚫 HARD BLOCKS
#     # -------------------------------------------------

#     # If month present → handled by month parser
#     if re.search(
#         r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec',
#         q_lower
#     ):
#         return None

#     # Quarter intent
#     if re.search(r'\bq[1-4]\b|quarter', q_lower):
#         return None

#     # -------------------------------------------------
#     # Detect continuous vs discrete
#     # -------------------------------------------------
#     is_continuous = ' to ' in q_lower

#     # -------------------------------------------------
#     # Extract Years
#     # -------------------------------------------------
#     year_pattern = r'\b(19\d{2}|20\d{2})\b'
#     years = [int(y) for y in re.findall(year_pattern, q_lower)]

#     if not years:
#         return None

#     years = sorted(set(years))

#     ranges: List[Tuple[str, str]] = []

#     # -------------------------------------------------
#     # Build Result
#     # -------------------------------------------------

#     if is_continuous:
#         start_year = years[0]
#         end_year = years[-1]

#         start_date = f"01-04-{start_year}"
#         end_date = f"31-03-{end_year + 1}"

#         ranges.append((start_date, end_date))

#     else:
#         for y in years:
#             start_date = f"01-04-{y}"
#             end_date = f"31-03-{y + 1}"
#             ranges.append((start_date, end_date))

#     return ranges



# def month_range_with_year(q: str):
#     """
#     Supports:
#       - apr-may
#       - apr to may
#       - apr & may
#       - apr-may 2024
#       - oct-feb last year
#     Financial Year: April–March
#     """

#     if not q or not isinstance(q, str):
#         return None

#     q = q.lower().strip()

#     # -----------------------------
#     # Month regex (same as yours)
#     # -----------------------------
#     month_regex = (
#         r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
#         r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
#         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
#     )

#     RANGE_REGEX = r"(?:to|till|until|thru|through|or|[-–—&])"
#     YEAR_REGEX  = r"(19\d{2}|20\d{2}|\d{2})"

#     # -----------------------------
#     # Relative year detection
#     # -----------------------------
#     is_last_year = bool(re.search(r"\b(last|previous)\s+year\b", q))
#     is_this_year = bool(re.search(r"\b(this|current)\s+year\b", q))

#     # -----------------------------
#     # Main regex
#     # -----------------------------
#     m = re.search(
#         rf"\b{month_regex}\b\s*{RANGE_REGEX}\s*\b{month_regex}\b(?:\s+{YEAR_REGEX})?",
#         q
#     )

#     print(m,'===========================================')

#     if not m:
#         return None

#     start_month_str = m.group(1)
#     end_month_str   = m.group(2)
#     year_str        = m.group(3)

#     # -----------------------------
#     # Month normalization
#     # -----------------------------
#     month_map = {
#         'jan': 1, 'january': 1,
#         'feb': 2, 'february': 2,
#         'mar': 3, 'march': 3,
#         'apr': 4, 'april': 4,
#         'may': 5,
#         'jun': 6, 'june': 6,
#         'jul': 7, 'july': 7,
#         'aug': 8, 'august': 8,
#         'sep': 9, 'sept': 9, 'september': 9,
#         'oct':10, 'october':10,
#         'nov':11, 'november':11,
#         'dec':12, 'december':12,
#     }

#     def norm(m):
#         return month_map.get(m[:3])

#     m1 = norm(start_month_str)
#     m2 = norm(end_month_str)

#     if not m1 or not m2:
#         return None

#     # -----------------------------
#     # Financial year resolution
#     # -----------------------------
#     today = datetime.today()
#     current_fy = today.year if today.month >= 4 else today.year - 1

#     if year_str:
#         fy = int(year_str)
#         if fy < 100:
#             fy += 2000
#     elif is_last_year:
#         fy = current_fy - 1
#     else:
#         fy = current_fy

#     # -----------------------------
#     # Assign years per month (FY aware)
#     # -----------------------------
#     y1 = fy if m1 >= 4 else fy + 1
#     y2 = fy if m2 >= 4 else fy + 1

#     if m2 < m1:
#         y2 += 1

#     # -----------------------------
#     # Build dates
#     # -----------------------------
#     start_date = f"01-{m1:02d}-{y1}"
#     end_day = monthrange(y2, m2)[1]
#     end_date = f"{end_day:02d}-{m2:02d}-{y2}"

#     return {
#         "start_date": start_date,
#         "end_date": end_date
#     }

# def extract_month_by_name(text):
#     months = {
#         "january": 1, "jan": 1,
#         "february": 2, "feb": 2,
#         "march": 3, "mar": 3,
#         "april": 4, "apr": 4,
#         "may": 5,
#         "june": 6, "jun": 6,
#         "july": 7, "jul": 7,
#         "august": 8, "aug": 8,
#         "september": 9, "sep": 9,
#         "october": 10, "oct": 10,
#         "november": 11, "nov": 11,
#         "december": 12, "dec": 12,
#     }
#     for word in text.split():
#         if word in months:
#             return months[word]
#     return None

# def detect_last_n_days(question: str):
#     text = question.lower().strip()

#     # --------------------------------------------
#     # 1. LAST YEAR / PREVIOUS FY (Highest Priority)
#     # --------------------------------------------
#     last_year_patterns = [
#         r"last\s+year\b",
#         r"previous\s+year\b",
#         r"last\s+financial\s+year\b",
#         r"last\s+fy\b",
#         r"previous\s+fy\b",
#         r"last\s+f\.?y\.?\b"
#     ]

#     multi_year_patterns = [
#         (r"last\s+(\d+)\s+years?", "years_number"),
#         (r"last\s+(one|two|three|four|five)\s+years?", "years_word"),
#     ]

#     today = datetime.today()
#     current_fy = today.year if today.month >= 4 else today.year - 1
#     current_q = get_fy_quarter(today.month)

#     word_to_num = {
#         "one": 1, "two": 2, "three": 3, "four": 4, "five": 5
#     }

#     for pattern in last_year_patterns:

#         if re.search(pattern, text):
#             last_fy = current_fy - 1
#             start_date = f"01-04-{last_fy}"
#             end_date = f"31-03-{last_fy + 1}"

#             return {
#                 "type": "last_financial_year",
#                 "fy": last_fy,
#                 "label": f"Last Financial Year (FY{last_fy})",
#                 "start_date": start_date,
#                 "end_date": end_date
#             }
    
#     for pattern, ptype in multi_year_patterns:
#         match = re.search(pattern, text)
#         if match:
#             # Convert word to number
#             if ptype == "years_number":
#                 n = int(match.group(1))
#             else:
#                 n = word_to_num[match.group(1)]

#             # Current FY
#             today = datetime.today()
#             current_fy = today.year if today.month >= 4 else today.year - 1

#             # Last N full FY ranges
#             start_fy = current_fy - n   # N financial years back
#             end_fy = current_fy - 1     # Last completed FY

#             start_date = f"01-04-{start_fy}"
#             end_date = f"31-03-{end_fy + 1}"

#             return {
#                 "type": "last_n_financial_years",
#                 "years": n,
#                 "label": f"Last {n} Financial Years (FY{start_fy}–FY{end_fy})",
#                 "start_date": start_date,
#                 "end_date": end_date
#             }

#     # --------------------------------------------
#     # 2. Keywords Patterns
#     # --------------------------------------------
#     patterns = [
#         (r"last\s+(\d+)\s+days?", "days"),
#         (r"past\s+(\d+)\s+days?", "days"),

#         (r"last\s+(\d+)\s+months?", "months"),
#         (r"last\s+(one|two|three|four|five|six)\s+months?", "months_word"),

#         (r"last\s+1\s+month\b", "month_single"),
#         (r"last\s+one\s+month\b", "month_single"),
#         (r"last\s+month\b", "month_single"),

#         (r"last\s+week\b", "week"),
#         (r"past\s+week\b", "week"),

#         (r"last\s+quarter\b", "quarter"),
#         (r"past\s+quarter\b", "quarter"),

#         (r"last\s+(\d+)\s+quarters?", "quarters_number"),
#         (r"last\s+(one|two|three|four)\s+quarters?", "quarters_word"),
#     ]

#     word_to_num = {
#         "one": 1, "two": 2, "three": 3,
#         "four": 4, "five": 5, "six": 6
#     }

#     # Helper: compute last N full quarters
#     def compute_last_n_quarters(n):
#         end_q = current_q - 1
#         end_fy = current_fy

#         if end_q <= 0:
#             end_q = 4
#             end_fy -= 1

#         start_q = end_q - (n - 1)
#         start_fy = end_fy

#         while start_q <= 0:
#             start_q += 4
#             start_fy -= 1

#         q_dates = {
#             1: ("01-04", "30-06"),
#             2: ("01-07", "30-09"),
#             3: ("01-10", "31-12"),
#             4: ("01-01", "31-03")
#         }

#         start_day, start_month = q_dates[start_q][0].split("-")
#         end_day, end_month = q_dates[end_q][1].split("-")

#         start_date = f"{start_day}-{start_month}-{start_fy}"
#         end_date = f"{end_day}-{end_month}-{end_fy}"

#         return {
#             "type": "last_n_quarters",
#             "label": f"Last {n} Quarters (Q{start_q} FY{start_fy} → Q{end_q} FY{end_fy})",
#             "start_date": start_date,
#             "end_date": end_date
#         }

#     # --------------------------------------------
#     # 3. PATTERN PROCESS LOOP
#     # --------------------------------------------
#     for pattern, ptype in patterns:
#         match = re.search(pattern, text)
#         if not match:
#             continue

#         # --- Last Month (full previous month) ---
#         if ptype == "month_single":
#             first_of_this_month = today.replace(day=1)
#             last_day_prev = first_of_this_month - timedelta(days=1)
#             first_day_prev = last_day_prev.replace(day=1)

#             return {
#                 "type": "last_full_month",
#                 "label": f"Last Month ({first_day_prev.strftime('%b %Y')})",
#                 "start_date": first_day_prev.strftime("%d-%m-%Y"),
#                 "end_date": last_day_prev.strftime("%d-%m-%Y")
#             }

#         # --- Last Week (Mon–Sun) ---
#         if ptype == "week":
#             days_back = today.weekday() + 7
#             last_monday = today - timedelta(days=days_back)
#             last_sunday = last_monday + timedelta(days=6)

#             return {
#                 "type": "last_full_week",
#                 "label": "Last Week (Mon–Sun)",
#                 "start_date": last_monday.strftime("%d-%m-%Y"),
#                 "end_date": last_sunday.strftime("%d-%m-%Y")
#             }

#         # --- Last N Quarters (number) ---
#         if ptype == "quarters_number":
#             return compute_last_n_quarters(int(match.group(1)))

#         # --- Last N Quarters (word) ---
#         if ptype == "quarters_word":
#             return compute_last_n_quarters(word_to_num.get(match.group(1), 1))

#         # --- Last Quarter ---
#         if ptype == "quarter":
#             return compute_last_n_quarters(1)

#         # --- Last N Full Months (word) ---
#         if ptype == "months_word":
#             n = word_to_num.get(match.group(1), 1)

#             first_of_this_month = today.replace(day=1)
#             end_date_dt = first_of_this_month - timedelta(days=1)
#             start_date_dt = end_date_dt.replace(day=1)

#             for _ in range(n - 1):
#                 start_date_dt = (start_date_dt.replace(day=1) - timedelta(days=1)).replace(day=1)

#             return {
#                 "type": "last_n_full_months",
#                 "months": n,
#                 "label": f"Last {n} Months (Full Months)",
#                 "start_date": start_date_dt.strftime("%d-%m-%Y"),
#                 "end_date": end_date_dt.strftime("%d-%m-%Y")
#             }

#         # --- Last N Full Months (numeric) ---
#         if ptype == "months":
#             n = int(match.group(1))

#             first_of_this_month = today.replace(day=1)
#             end_date_dt = first_of_this_month - timedelta(days=1)
#             start_date_dt = end_date_dt.replace(day=1)

#             for _ in range(n - 1):
#                 start_date_dt = (start_date_dt.replace(day=1) - timedelta(days=1)).replace(day=1)

#             return {
#                 "type": "last_n_full_months",
#                 "months": n,
#                 "label": f"Last {n} Months (Full Months)",
#                 "start_date": start_date_dt.strftime("%d-%m-%Y"),
#                 "end_date": end_date_dt.strftime("%d-%m-%Y")
#             }

#         # --- Last N Days ---
#         if ptype == "days":
#             days = int(match.group(1))
#             start_date = (today - timedelta(days=days - 1)).strftime("%d-%m-%Y")
#             end_date = today.strftime("%d-%m-%Y")

#             return {
#                 "type": "last_n_days",
#                 "days": days,
#                 "label": f"Last {days} Days",
#                 "start_date": start_date,
#                 "end_date": end_date
#             }

#     return None


# def detect_this_period(question: str):
#     text = question.lower().strip()
#     today = datetime.today()
#     print('Detecting this period --------------------')
#     # Determine current FY
#     current_fy = today.year if today.month >= 4 else today.year - 1

#     # Helper: Get current quarter
#     current_q = get_fy_quarter(today.month)



#     # ===================================================================
#     # 1. THIS MONTH
#     # ===================================================================
#     if re.search(r"\bthis\s+month\b|\bcurrent\s+month\b", text):
#         start = today.replace(day=1).strftime("%d-%m-%Y")
#         end = today.strftime("%d-%m-%Y")
#         return {
#             "type": "this_month",
#             "label": f"This Month (MTD) – {today.strftime('%b %Y')}",
#             "start_date": start,
#             "end_date": end
#         }

#     # ===================================================================
#     # 2. THIS QUARTER
#     # ===================================================================
#     if re.search(r"\bthis\s+quarter\b|\bcurrent\s+quarter\b|\bqtd\b", text):
#         if current_q == 1:
#             start = f"01-04-{current_fy}"
#             end = f"30-06-{current_fy}"
#         elif current_q == 2:
#             start = f"01-07-{current_fy}"
#             end = f"30-09-{current_fy}"
#         elif current_q == 3:
#             start = f"01-10-{current_fy}"
#             end = f"31-12-{current_fy}"
#         else:  # current_q == 4
#             start = f"01-01-{current_fy + 1}"
#             end = f"31-03-{current_fy + 1}"

#         return {
#             "type": "this_quarter",
#             "label": f"This Quarter (Q{current_q} FY{current_fy})",
#             "start_date": start,
#             "end_date": end
#         }
#     # ===================================================================
#     # 3. THIS YEAR / THIS FY
#     # ===================================================================
#     if re.search(r"\bthis\s+year\b|\bthis\s+fy\b|\bcurrent\s+year\b|\bcurrent\s+fy\b", text):
#         start = f"01-04-{current_fy}"
#         end = today.strftime("%d-%m-%Y")
#         return {
#             "type": "this_fy",
#             "label": f"This Financial Year (FY{current_fy} YTD)",
#             "start_date": start,
#             "end_date": end
#         }

#     # ===================================================================
#     # 4. THIS WEEK (Monday to Today)
#     # ===================================================================
#     if re.search(r"\bthis\s+week\b|\bcurrent\s+week\b", text):
#         monday = today - timedelta(days=today.weekday())
#         start = monday.strftime("%d-%m-%Y")
#         end = today.strftime("%d-%m-%Y")
#         return {
#             "type": "this_week",
#             "label": "This Week (Mon–Today)",
#             "start_date": start,
#             "end_date": end
#         }
#     return None

# def detect_year_range_logic(question: str):
#     import re

#     text = question.lower().strip()

#     year_range_patterns = [
#         r"(20\d{2})\s*(to|and|-|–)\s*(20\d{2})",            # 2022 to 2024, 2022-2024
#         r"fy\s*(20\d{2})\s*(to|-|–)\s*fy\s*(20\d{2})",      # FY 2022 to FY 2024
#         r"fy\s*(\d{2})\s*(to|-|–)\s*fy\s*(\d{2})",          # FY22 to FY24
#         r"fy\s*(\d{2})\s*(to|-|–)\s*(\d{2})",               # FY22-24 (common)
#     ]

#     print(year_range_patterns,'----------------------------')
#     for pattern in year_range_patterns:
#         match = re.search(pattern, text)
#         if match:
#             y1, _, y2 = match.groups()

#             # Convert 2-digit year → 4-digit
#             if len(y1) == 2:
#                 y1 = "20" + y1
#             if len(y2) == 2:
#                 y2 = "20" + y2

#             y1 = int(y1)
#             y2 = int(y2)

#             start_year = min(y1, y2)
#             end_year = max(y1, y2)

#             start_date = f"01-04-{start_year}"
#             end_date = f"31-03-{end_year + 1}"

#             return {
#                 "type": "year_range",
#                 "label": f"FY {start_year} to FY {end_year}",
#                 "start_date": start_date,
#                 "end_date": end_date
#             }

#     return None

# def _normalize_to_rows(data: Any) -> List[Dict[str, Any]]:
#     """
#     Normalize different response shapes into a list of rows.
#     Supported:
#     - List[Dict]  -> SQL-style output
#     - Dict[str, Dict] -> Funnel-style output
#     """
#     if isinstance(data, list):
#         return [row for row in data if isinstance(row, dict)]

#     if isinstance(data, dict):
#         # Funnel-style: { "Wave City": { ...metrics... }, ... }
#         if all(isinstance(v, dict) for v in data.values()):
#             return list(data.values())

#     return []
# NON_ADDITIVE_MARKERS = ["%", ":"]

# def _is_additive_key(key: str) -> bool:
#     """
#     Decide whether a column/metric should be summed.
#     """
#     return not any(marker in key for marker in NON_ADDITIVE_MARKERS)

# def calculate_master_totals(data: Any) -> Dict[str, Union[int, float]]:
#     """
#     MASTER aggregation logic:
#     - Column-wise totals
#     - Sums ALL additive numeric fields
#     - Ignores % and ratio fields
#     - Works for wrapped response shapes
#     """

#     # 🔥 UNWRAP if response contains "data"
#     if isinstance(data, dict) and "data" in data:
#         rows = data["data"]
#     elif isinstance(data, list):
#         rows = data
#     else:
#         return {}

#     totals: Dict[str, float] = {}

#     for row in rows:
#         if not isinstance(row, dict):
#             continue

#         for key, value in row.items():

#             # Skip percentage / ratio fields
#             if "pct" in key.lower() or "%" in key.lower():
#                 continue

#             if isinstance(value, (int, float)):
#                 totals[key] = totals.get(key, 0) + value

#     # Clean formatting
#     final_totals = {}
#     for k, v in totals.items():
#         if float(v).is_integer():
#             final_totals[k] = int(v)
#         else:
#             final_totals[k] = round(v, 2)

#     return final_totals

# def month_year_range_parser(q: str):
#     """
#     Supports:
#         - april 2022 - april 2025
#         - april 2022 to april 2025
#         - april 2022 & april 2025
#         - april 2022 through april 2025
    
#     Returns:
#         {
#             "start_date": "DD-MM-YYYY",
#             "end_date": "DD-MM-YYYY"
#         }
#     """

#     if not q or not isinstance(q, str):
#         return None

#     q = q.lower().strip()

#     month_regex = (
#         r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
#         r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
#         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
#     )

#     year_regex = r"(19\d{2}|20\d{2})"
#     # range_regex = r"(?:to|till|until|thru|through|and|[-–—&])"

#     range_regex = r"(?:\s*(?:to|till|until|thru|through|and|-|–|—|&)\s*)"

#     # pattern = rf"\b{month_regex}\s+{year_regex}\b\s*{range_regex}\s*\b{month_regex}\s+{year_regex}\b"
#     pattern = rf"\b{month_regex}\s+{year_regex}{range_regex}{month_regex}\s+{year_regex}\b"
#     match = re.search(pattern, q)

#     if not match:
#         return None

#     start_month_str = match.group(1)
#     start_year_str  = match.group(2)
#     end_month_str   = match.group(3)
#     end_year_str    = match.group(4)

#     month_map = {
#         'jan': 1, 'january': 1,
#         'feb': 2, 'february': 2,
#         'mar': 3, 'march': 3,
#         'apr': 4, 'april': 4,
#         'may': 5,
#         'jun': 6, 'june': 6,
#         'jul': 7, 'july': 7,
#         'aug': 8, 'august': 8,
#         'sep': 9, 'sept': 9, 'september': 9,
#         'oct':10, 'october':10,
#         'nov':11, 'november':11,
#         'dec':12, 'december':12,
#     }

#     m1 = month_map.get(start_month_str[:3])
#     m2 = month_map.get(end_month_str[:3])

#     if not m1 or not m2:
#         return None

#     y1 = int(start_year_str)
#     y2 = int(end_year_str)

#     start_date = f"01-{m1:02d}-{y1}"
#     end_day = monthrange(y2, m2)[1]
#     end_date = f"{end_day:02d}-{m2:02d}-{y2}"

#     return {
#         "start_date": start_date,
#         "end_date": end_date
#     }



# # --------------------------------------------
# # Date Parsing Logic
# # --------------------------------------------
# def parse_dates_from_question(question: str):
#     q = normalize(question)

#     start_date, end_date = None, None
#     print("=========================================================")

#     # -------------------------------------------------------
#     # 1️⃣ Detect Quarter (q1, q2, q3, q4)
#     # -------------------------------------------------------
#     if "q1" in q or "quarter 1" in q:
#         year = extract_year_from_text(q) or get_current_fy()
#         return f"01-04-{year}", f"30-06-{year}"

#     if "q2" in q or "quarter 2" in q:
#         year = extract_year_from_text(q) or get_current_fy()
#         return f"01-07-{year}", f"30-09-{year}"

#     if "q3" in q or "quarter 3" in q:
#         year = extract_year_from_text(q) or get_current_fy()
#         return f"01-10-{year}", f"31-12-{year}"

#     if "q4" in q or "quarter 4" in q:
#         year = extract_year_from_text(q) or get_current_fy()
#         year = year + 1
#         return f"01-01-{year}", f"31-03-{year}"
    
#     # -------------------------------------------------------
#     # 3️⃣ between date like "5 june to 10 june 2024"
#     # -------------------------------------------------------
#     date_pair = parse_single_or_range_date(q)
#     if date_pair:
#         d1, d2 = date_pair
#         return d1.strftime("%d-%m-%Y"), d2.strftime("%d-%m-%Y")
    
#     # # -------------------------------------------------------
#     # # 6️⃣ Detect explicit date range: "april 2023 to june 2024"
#     # # -------------------------------------------------------

#     range_data  = month_year_range_parser(q)
#     print(range_data,'========range_data============')
#     if range_data:
#         return range_data

#     # -------------------------------------------------------
#     # 2️⃣ Detect explicit date range: "april 2024 to june 2024"
#     # -------------------------------------------------------
#     month_range = month_range_with_year(q)
#     if month_range:
#         return month_range["start_date"],month_range["end_date"]
    
#     print("===========================hello=================================")

#     # # -------------------------------------------------------
#     # # 6️⃣ Month range WITHOUT year (april to september)
#     # # -------------------------------------------------------
#     # month_range_wy = month_range_without_year(q)
#     # if month_range_wy:
#     #     return month_range_wy['start_date'],month_range_wy['end_date']
    
#     # -------------------------------------------------------
#     # 4️⃣ Single Month WITH year
#     # -------------------------------------------------------
#     month = extract_month_by_name(q)
#     year = extract_year_from_text(q)

#     if month and year:
#         start_date = f"01-{month:02d}-{year}"
#         last_day = monthrange(year, month)[1]
#         end_date = f"{last_day:02d}-{month:02d}-{year}"
#         return start_date, end_date

#     # -------------------------------------------------------
#     # 5️⃣ Single Month WITHOUT year (FY logic)
#     # -------------------------------------------------------
#     if month and not year:
#         fy = get_current_fy()
#         year = fy if month >= 4 else fy + 1
#         start_date = f"01-{month:02d}-{year}"
#         last_day = monthrange(year, month)[1]
#         end_date = f"{last_day:02d}-{month:02d}-{year}"
#         return start_date, end_date
    
    
#     # -------------------------------------------------------
#     # Additional check for "last n days"
#     # -------------------------------------------------------
#     last_n_days = detect_last_n_days(question)
#     if last_n_days:
#         return last_n_days["start_date"], last_n_days["end_date"]
    
#     # -------------------------------------------------------
#     # Additional check for "this period"
#     # -------------------------------------------------------
#     this_period = detect_this_period(question)
#     if this_period:
#         return this_period["start_date"], this_period["end_date"]


#     # -------------------------------------------------------
#     # 7️⃣ FY detection
#     # -------------------------------------------------------
#     if "fy" in q or "financial year" in q or "f y" in q:
#         year = extract_year_from_text(q)
#         if year:
#             return f"01-04-{year}", f"31-03-{year+1}"
    
#     year_range = detect_year_range_logic(question)
#     if year_range:
#         print("===========================================")
#         return year_range["start_date"], year_range["end_date"]

#     # Standalone year → interpret as FY
#     year = extract_year_from_text(q)
#     if year:
#         return f"01-04-{year}", f"31-03-{year+1}"
 
#     # -------------------------------------------------------
#     # 8️⃣ Default → current FY
#     # -------------------------------------------------------
#     fy = get_current_fy()
#     return f"01-04-{fy}", f"31-03-{fy+1}"

# def detect_zero_null_patterns(question: str) -> Dict[str, Any]:
#     """
#     Detect patterns for zero/null value questions
    
#     Examples:
#     - "Are there users with targets but no actuals"
#     - "Show users with zero actual"
#     - "Users without any actual performance"
#     - "Find users having targets but actual is zero"
#     - "List users where actual is null"
#     """
    
#     q_lower = question.lower()
    
#     zero_null_pattern = {
#         "has_zero_null": False,
#         "field": None,  # Which field is zero/null (actual, target, etc.)
#         "condition": None,  # The condition type
#         "inverse_field": None  # The field that should NOT be zero (e.g., target)
#     }
    
#     # Patterns for "no", "zero", "null", "without", "missing"

#     zero_indicators = [
#         r'no(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',
#         r'null(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',

#         r'without(?:\s+\w+){0,3}\s+(?:any\s+)?(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',
#         r'missing(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',

#         r'(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)\s+'
#         r'(?:is|are|equals?)\s+(?:zero|0|null)',

#         r'(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)\s+'
#         r'(?:is|are)\s+(?:not\s+present|absent|empty)',
#     ]

    
#     for pattern in zero_indicators:
#         match = re.search(pattern, q_lower)
#         if match:
#             field = match.group(1)
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = field
#             zero_null_pattern["condition"] = "equals_zero"
#             break

#     is_zero = ["zero", "no ", "without ", "haven't ", "didn't ", "not any"]
#     has_zero = any(z in q_lower for z in is_zero)
#     if has_zero:

#         field_detected = False
#         # Special handling: "zero actual of sr"
#         if  not field_detected and ("book" in q_lower or "booking" in q_lower or "booked" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "booked"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True
            
        
#         if not field_detected and ("complete" in q_lower or "completion" in q_lower or "completed" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "completion"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True
        
#         if not field_detected and ("resolve" in q_lower or "resolved" in q_lower or "resolution" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "resolved"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True
        
#         if not field_detected and ("qualif" in q_lower or "ql" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "qualified"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True

#         if not field_detected and ("leads" in q_lower or "lead" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "lead"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True
        
#         if not field_detected and ("target" in q_lower):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "target"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True

#         if not field_detected and any(z in q_lower for z in ["sr","service","case","service request","service requests"]):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "sr"
#             zero_null_pattern["condition"] = "equals_zero"
#             field_detected = True

#         if not field_detected and any(z in q_lower for z in ["appintment","appintments"]):
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = "appointment"
#             zero_null_pattern["condition"] = "equals_zero" 
#             field_detected = True

    
#     # Patterns for "but" constructions: "with X but no Y"
#     # This means: X > 0 AND Y = 0
#     but_patterns = [
#         r'with\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|null)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)',
#         r'having\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|null)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)',
#         r'(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|without|missing)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)'
#     ]
    
#     for pattern in but_patterns:
#         match = re.search(pattern, q_lower)
#         if match:
#             inverse_field = match.group(1).rstrip('s')  # Remove plural 's'
#             zero_field = match.group(2).rstrip('s')
            
#             zero_null_pattern["has_zero_null"] = True
#             zero_null_pattern["field"] = zero_field
#             zero_null_pattern["inverse_field"] = inverse_field
#             zero_null_pattern["condition"] = "inverse_zero"
#             break
    
#     return zero_null_pattern



# def resolve_user_from_tokens(query: str, df: pd.DataFrame) -> List[str]:
#     """
#     Resolve multiple users mentioned in a query using token overlap scoring.
#     Returns list of matched user names.
#     """

#     if "user_name" not in df.columns:
#         return []

#     tokens = re.findall(r"[A-Za-z]+", query.lower())
#     if not tokens:
#         return []

#     resolved = []

#     for user in df["user_name"].dropna().unique():
#         user_tokens = user.lower().split()

#         # Count overlaps
#         overlap = sum(1 for t in tokens if t in user_tokens)

#         # Require at least one strong signal (first name or last name)
#         if overlap >= 1:
#             resolved.append((user, overlap))

#     # Sort by confidence
#     resolved.sort(key=lambda x: x[1], reverse=True)

#     # Return only names
#     return [u for u, _ in resolved if u != 'Service Request Queue']

# def make_json_safe(obj):
#     if isinstance(obj, float):
#         if math.isnan(obj) or math.isinf(obj):
#             return None
#         return obj
#     elif isinstance(obj, dict):
#         return {k: make_json_safe(v) for k, v in obj.items()}
#     elif isinstance(obj, list):
#         return [make_json_safe(v) for v in obj]
#     else:
#         return obj
    
# def parse_question_intent(query: str) -> dict:
#     q = query.lower()

#     intent = {
#         "metric": None,
#         "action": "show",
#         "aggregation": None,
#         "limit": None,
#         "threshold": None,
#         "condition": None,
#         "raw_query": q,
#         "zero_filters": [],
#     }

#     # Detect metric
#     for metric, meta in DATA_DICTIONARY.items():
#         if any(k in q for k in meta["keywords"]):
#             intent["metric"] = metric
#             break

#     intent["zero_null"] = detect_zero_null_patterns(q)

#     # Ranking
#     if any(w in q for w in ["top", "highest", "best", "most"]):
#         intent["action"] = "top"
#         intent["aggregation"] = "max"

#     elif any(w in q for w in ["bottom", "lowest", "worst", "least"]):
#         intent["action"] = "bottom"
#         intent["aggregation"] = "min"

#     # Average
#     elif any(w in q for w in ["average", "avg", "mean"]):
#         intent["aggregation"] = "avg"

#     # Compare
#     elif any(w in q for w in ["compare", "vs", "target"]):
#         intent["action"] = "compare"

#     # Rate / percentage
#     if any(w in q for w in ["rate", "percentage", "%"]):
#         intent["aggregation"] = "rate"
#     # gap
#     if any(w in q for w in ["gap", "difference", "diff", "variance", "delta"]):
#         intent["aggregation"] = "gap"

#     # Top / Bottom N
#     match = re.search(r"\b(top|bottom)\s+(\d+)", q)
#     if match:
#         intent["limit"] = int(match.group(2))
#     elif intent["action"] in ["top", "bottom"]:
#         intent["limit"] = 10  # default FIX

#     # Threshold extraction (50%, above 30%, etc.)
#     threshold_match = re.search(r"(\d+)\s*%", q)
#     if threshold_match:
#         intent["threshold"] = int(threshold_match.group(1))

#     intent["condition"] = parse_advanced_conditions(q)

#     return intent

# def parse_advanced_conditions(q: str) -> dict | None:

#     left = "actual"
#     right = "target"

#     if "target" in q and "actual" in q:
#         if q.index("target") < q.index("actual"):
#             left, right = "target", "actual"
#         else:
#             left, right = "actual", "target"
            
#     if any(w in q for w in [
#         "less than or equal",
#         "less than equal",
#         "at most",
#         "no more than",
#         "up to",
#         "did not exceed",
#         "not exceeded",
#         "not exceed",
#         "within target",
#         "not achieved",
#         "not achieve"
#         "not met",
#         "not hit",
#         "not reached"
#     ]):
#         return {"op": "<=", "left": left, "right": right}
    
#     if any(w in q for w in [
#         "achieved",
#         "met",
#         "hit",
#         "reached"
#     ]):
#         return {"op": ">=", "left": left, "right": right}

#     if any(w in q for w in ["exceeded", "greater than", "above","highest"]):
#         return {"op": ">", "left": left, "right": right}

#     if any(w in q for w in ["failed", "below", "less than",'under',"missed","lowest"]):
#         return {"op": "<", "left": left, "right": right}

#     if any(w in q for w in ["equal", "exactly"]):
#         return {"op": "=", "left": left, "right": right}

#     return None


# # -------------------------------------------------
# # Metric-specific column resolver
# # -------------------------------------------------
# def resolve_columns(metric: str, raw_q: str):
#     mappings = {
#         "qualified_leads": {
#             "actual": "ql_actual",
#             "target": "ql_target",
#             "rate": "ql_rate",
#             "total":"total_leads",
#             "rate_type": "achievement",
#             "label": "QL Achievement Rate (%)"
#         },
#         "appointments": {
#             "booked_actual": "appt_booked_actual",
#             "booked_target": "appt_booked_target",
#             "completion_actual": "appt_completion_actual",
#             "completion_target": "appt_completion_target",
#             "completion_base": "appt_booked_actual"  # used for rate
            
#         },
#         "service_requests": {
#             "actual": "resolved_actual",
#             "target": "sr_target",
#             "rate": "sr_rate",
#             "total":"total_sr",
#             "rate_type": "achievement",
#             "label": "Service Request Resolution Rate (%)",
#         }
#     }

#     metric_map = mappings.get(metric, {})

#     # If appointment → decide booked vs completion
#     if metric == "appointments":
#         q_lower = raw_q.lower()
#         if any(w in q_lower for w in ["booked", "book", "booking"]):
#             return {
#                 "actual": metric_map["booked_actual"],
#                 "target": metric_map["booked_target"],
#                 "rate_type": "achievement",
#                 "rate": "booked_rate",
#                 "total":"total_activities",
#                 "label": "Appointment Booking Achievement (%)"
#             }
#         else:
#             return {
#                 "actual": metric_map["completion_actual"],
#                 "target": metric_map["completion_target"],
#                 "base": metric_map["completion_base"],
#                 "rate": "completion_rate",
#                 "rate_type": "efficiency",
#                 "total":"total_activities",
#                 "label": "Appointment Completion Rate (%)"
                
#             }

#     return metric_map

# def apply_zero_null_filter(
#     df: pd.DataFrame,
#     zero_null: Dict[str, Any],
#     cols: Dict[str, str],
#     metric: str
# ) -> pd.DataFrame:
#     """
#     Apply zero/null filtering to the DataFrame
    
#     Handles patterns like:
#     - "users with targets but no actuals" → target > 0 AND actual = 0
#     - "users with zero actual" → actual = 0
#     - "users without any target" → target = 0
#     """
    
#     condition_type = zero_null.get("condition")
#     field = zero_null.get("field")
#     inverse_field = zero_null.get("inverse_field")
    
#     if condition_type == "equals_zero":
#         # Simple zero check: field = 0
#         print(field, metric)
#         col_name = normalize_field_name(field, metric)
#         print(col_name,df.columns,'==========col_name========')
#         if col_name in df.columns:
#             df = df[df[col_name] == 0]
#             print(f"Applied zero filter: {col_name} = 0")
    
#     elif condition_type == "inverse_zero":
#         # Complex pattern: inverse_field > 0 AND field = 0
#         zero_col = normalize_field_name(field, metric)
#         inverse_col = normalize_field_name(inverse_field, metric)
        
#         if zero_col in df.columns and inverse_col in df.columns:
#             df = df[(df[inverse_col] > 0) & (df[zero_col] == 0)]
#             print(f"Applied inverse zero filter: {inverse_col} > 0 AND {zero_col} = 0")
    
#     return df

# def normalize_field_name(field: str, metric: str) -> str:
#     """
#     Normalize field names from natural language to database columns
    
#     Examples:
#     - "actual" + "qualified_leads" → "ql_actual"
#     - "target" + "booked" → "appt_booked_target"
#     """
    
#     field_lower = field.lower().rstrip('s')  # Remove plural
#     field_lower = field.lower().replace(' ','_')  # Remove plural
#     print(field_lower,'=field_lower')
#     print(metric,'=metric')
    
#     # Map natural language to column prefixes
#     field_map = {
#         "actual": "_actual",
#         "target": "_target",
#         "lead": "total_leads",
#         "qualified":"_actual",
#         "appointment": "total_activities",
#         "completion": "_actual",
#         "booked": "_actual",
#         "resolved": "_actual",
#         "sr": "total_sr",
#         'service_request':"total_sr"
#     }
    
#     # Metric-specific mappings
#     if metric == "qualified_leads":
#         if "actual" in field_lower or "qualified" in field_lower:
#             return "ql_actual"
#         elif "target" in field_lower:
#             return "ql_target"
#         elif "lead" in field_lower:
#             return "total_leads"
    
#     elif metric == "booked":
#         if "actual" in field_lower or "booked" in field_lower:
#             return "appt_booked_actual"
#         elif "target" in field_lower:
#             return "appt_booked_target"
#         elif "appointment" in field_lower:
#             return "total_activities"
    
#     elif metric == "completion":
#         if "actual" in field_lower or "completion" in field_lower:
#             return "appt_completion_actual"
#         elif "target" in field_lower:
#             return "appt_completion_target"
#         elif "appointment" in field_lower:
#             return "total_activities"    
    
#     elif metric == "service_requests":
#         if "actual" in field_lower or "resolved" in field_lower:
#             return "resolved_actual"
#         elif "target" in field_lower:
#             return "sr_target"
#         elif "sr" in field_lower or "service_request" in field_lower:
#             return "total_sr"
    
#     # Fallback
#     suffix = field_map.get(field_lower, "_actual")
#     return f"{metric}{suffix}"


# def resolve_question(parsed_data: dict, intent: dict):
#     metric = intent.get("metric")
#     action = intent.get("action")
#     aggregation = intent.get("aggregation")
#     raw_q = intent.get("raw_query", "").lower()
#     zero_filters = intent.get("zero_filters", [])
#     zero_null = intent.get("zero_null", {})

#     if not metric:
#         return {"response": "Please specify what you want to analyze."}

#     config = DATA_DICTIONARY.get(metric)
#     if not config:
#         return {"response": "Metric not supported."}

#     # -------------------------------------------------
#     # Load dataframe
#     # -------------------------------------------------
#     report_key = config["report"]
#     df = pd.DataFrame(parsed_data.get(report_key, {}).get("rows", []))

#     if df.empty:
#         return {"response": "No data available."}

#     df.columns = [c.lower() for c in df.columns]

#     required_cols = [c.lower() for c in config["columns"] if c.lower() in df.columns]
#     base_cols = ["user_name"] + required_cols
#     df = df[base_cols]
    
#     users = resolve_user_from_tokens(raw_q,df)
#     print(users)
#     if users:
#         df = df[df["user_name"].isin(users)]
#     else:
#         df = df.copy()

#     cols = resolve_columns(metric, raw_q)

#     if zero_null and zero_null.get("has_zero_null"):
#         if metric == "appointments":
#             if any(w in raw_q for w in ["booked", "book", "booking"]) :
#                 sub_metric = "booked"
#             else:
#                 sub_metric = "completion"
#         elif metric == "qualified_leads":
#             sub_metric = "qualified_leads"
#         elif metric == "service_requests":
#             sub_metric = "service_requests"
#         df = apply_zero_null_filter(df, zero_null, cols, sub_metric)

#     # -------------------------------------------------
#     # RATE / ACHIEVEMENT LOGIC
#     # -------------------------------------------------
#     if aggregation == "rate":
#         if not cols or "rate_type" not in cols:
#             return {"response": "Rate calculation not supported for this metric."}

#         rate_col = cols["rate"]
#         rate_type = cols["rate_type"]

#         if rate_type == "achievement":
#             actual_col = cols["actual"]
#             target_col = cols["target"]

#             df = df[df[target_col] > 0]
#             df[rate_col] = ((df[actual_col] / df[target_col]) * 100).round(2)

#         elif rate_type == "efficiency":
#             actual_col = cols["actual"]
#             base_col = cols["base"]

#             df = df[df[base_col] > 0]
#             df[rate_col] = ((df[actual_col] / df[base_col]) * 100).round(2)

#             df[rate_col] = df[rate_col].clip(upper=100)
#         else:
#             return {"response": "Unknown rate type."}

#         # Threshold filtering
#         if intent.get("threshold") is not None and intent.get("condition"):
#             op = intent["condition"]["op"]
#             threshold = intent["threshold"]

#             if op == ">":
#                 df = df[df[rate_col] > threshold]
#             elif op == "<":
#                 df = df[df[rate_col] < threshold]
#             elif op == "=":
#                 df = df[df[rate_col] == threshold]
#             elif op == ">=":
#                 df = df[df[rate_col] >= threshold]
#             elif op == "<=":
#                 df = df[df[rate_col] <= threshold]

#         # Top / Bottom
#         if action in ["top", "bottom"]:
#             limit = intent.get("limit", 5)
#             df = df.sort_values(
#                 rate_col,
#                 ascending=(action == "bottom")
#             ).head(limit)

#         return {
#             "metric": metric,
#             "kpi": cols["label"],
#             "count": len(df),
#             "data": df.sort_values(rate_col, ascending=False)
#                     .to_dict(orient="records")
#         }

#     # -------------------------------------------------
#     # Gap / Diffrence LOGIC
#     # -------------------------------------------------
#     if aggregation == "gap":
#         if not cols:
#             return {"response": "Rate calculation not supported for this metric."}

#         actual_col = cols["actual"]
#         target_col = cols["target"]

#         # Exclude zero targets for rate logic
#         df = df[df[target_col] > 0]
#         df = df[df[actual_col] > 0]


#         df["gap"] = (df[target_col] - df[actual_col]).abs().round(2)

#         if intent.get("condition") is not None:
#             op = intent["condition"]["op"]
#             left_col = actual_col if intent["condition"]["left"] == "actual" else target_col
#             right_col = target_col if intent["condition"]["right"] == "target" else actual_col

#             if op == ">":
#                 df = df[df[left_col] > df[right_col]]
#             elif op == "<":
#                 df = df[df[left_col] < df[right_col]]
#             elif op == "=":
#                 df = df[df[left_col] == df[right_col]]
#             elif op == ">=":
#                 df = df[df[left_col] >= df[right_col]]
#             elif op == "<=":
#                 df = df[df[left_col] <= df[right_col]]


#         # Top / Bottom
#         if action in ["top", "bottom"]:
#             limit = intent.get("limit", 5)
#             df = df.sort_values(
#                 "gap",
#                 ascending=(action == "bottom")
#             ).head(limit)

#         return {
#             "metric": metric,
#             "kpi": cols["label"],
#             "count": len(df),
#             "data": df.to_dict(orient="records")
#         }

#     # -------------------------------------------------
#     # TARGET vs ACTUAL (COMPARE)
#     # -------------------------------------------------
#     if action == "compare" and cols:
#         actual_col = cols["actual"]
#         target_col = cols["target"]
#         df = df[df[target_col] > 0]
#         achievement_pct = actual_col.replace("_actual","")
#         achievement_pct = achievement_pct+"_achievement_pct"
#         df[achievement_pct] = (
#             df[actual_col] / df[target_col].replace(0, np.nan) * 100
#         ).round(1)

#         if intent.get("condition"):
#             op = intent["condition"]["op"]
#             left_col = actual_col if intent["condition"]["left"] == "actual" else target_col
#             right_col = target_col if intent["condition"]["right"] == "target" else actual_col

#             if op == ">":
#                 df = df[df[left_col] > df[right_col]]
#             elif op == "<":
#                 df = df[df[left_col] < df[right_col]]
#             elif op == "=":
#                 df = df[df[left_col] == df[right_col]]
#             elif op == ">=":
#                 df = df[df[left_col] >= df[right_col]]
#             elif op == "<=":
#                 df = df[df[left_col] <= df[right_col]]

#         print(df)
#         return {
#             "metric": metric,
#             "comparison": "actual vs target",
#             "data": df.to_dict(orient="records")
#         }

#     # -------------------------------------------------
#     # TOP / BOTTOM (NORMAL, NON-RATE)
#     # -------------------------------------------------
#     if action in ["top", "bottom"]:
#         numeric_cols = df.select_dtypes(include="number").columns.tolist()

#         if "actual" in raw_q:
#             sort_col = next((c for c in numeric_cols if c.endswith("_actual")))
#         elif "target" in raw_q:
#             sort_col = next((c for c in numeric_cols if c.endswith("_target")))
#         else:
#             sort_col = next(
#                         (c for c in numeric_cols if c.endswith("_actual")),
#                         numeric_cols[0]
#                     )
#         limit = intent.get("limit", 5)

#         df = df.sort_values(
#             sort_col,
#             ascending=(action == "bottom")
#         ).head(limit)

#         return {
#             "metric": metric,
#             "sorted_by": sort_col,
#             "limit": limit,
#             "data": df.to_dict(orient="records")
#         }

#     # -------------------------------------------------
#     # AVERAGE
#     # -------------------------------------------------
#     if aggregation == "avg":
#         return {
#             "metric": metric,
#             "average": df.select_dtypes(include="number")
#                         .mean()
#                         .round(2)
#                         .to_dict()
#         }
    
#     if intent.get("condition") is not None:
#         if not cols:
#             return {"response": "Rate calculation not supported for this metric."}
        
#         actual_col = cols["actual"]
#         target_col = cols["target"]

#         op = intent["condition"]["op"]
#         if op == ">":
#             df = df[df[actual_col] > df[target_col]]
#         elif op == "<":
#             df = df[df[actual_col] < df[target_col]]
#         elif op == "=":
#             df = df[df[actual_col] == df[target_col]]
#         elif op == ">=":
#             df = df[df[actual_col] >= df[target_col]]
#         elif op == "<=":
#             df = df[df[actual_col] <= df[target_col]]  

            
#     # -------------------------------------------------
#     # DEFAULT FALLBACK
#     # -------------------------------------------------
#     return {
#         "metric": metric,
#         "count": len(df),
#         "data": df.to_dict(orient="records")
#     }

# def sort_funnel_by_numeric_desc(data: Any) -> Any:
#     """
#     Sort nested funnel dictionaries by numeric values in descending order.
#     Works with various funnel output structures:
#     - Dict[user, Dict[metric, value]] -> Sorts users by total/first numeric metric
#     - Dict[project, Dict[user, Dict[metric, value]]] -> Sorts projects and users
#     """
#     if not isinstance(data, dict) or not data:
#         return data
    
#     # Check if this is a nested structure (all values are dicts)
#     first_value = next(iter(data.values()))
    
#     if isinstance(first_value, dict):
#         # Check if it's doubly nested (project -> user -> metrics)
#         first_inner_value = next(iter(first_value.values()), None) if first_value else None
        
#         if isinstance(first_inner_value, dict):
#             # Doubly nested: Sort projects, then sort users within each project
#             sorted_data = {}
#             for key in sorted(data.keys()):
#                 sorted_data[key] = sort_funnel_by_numeric_desc(data[key])
#             return sorted_data
#         else:
#             # Single nested: Sort by first numeric metric value
#             def get_sort_key(item):
#                 key, metrics = item
#                 # Find first numeric value in metrics dict
#                 for metric_name, metric_value in metrics.items():
#                     if isinstance(metric_value, (int, float)) and not any(marker in metric_name for marker in ["%", ":"]):
#                         return -metric_value  # Negative for descending order
#                 return 0
            
#             return dict(sorted(data.items(), key=get_sort_key))
#     return data


# @app.post("/ask")
# async def ask_performance_question(request: QueryRequest):
#     token = get_salesforce_token()
#     if not token:
#         raise HTTPException(500, "Authentication failed")
    

#     qoq_result = detect_qoq(request.query)
#     if qoq_result:
#         quarters = qoq_result["quarters"]
#         analysis_type = qoq_result["type"]
#         fy = qoq_result["fy"]
#         all_quarter_results = []

#         print(f"Detected {analysis_type.upper()} for FY{fy} → Running {len(quarters)} quarters")

#         for qtr in quarters:
#             print(qtr,'quarter details --------------------')
#             start_date = qtr["start_date"]
#             end_date = qtr["end_date"]
#             label = qtr["quarter"]

#             start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
#             end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

#             raw_data = fetch_combined_reports(start_date, end_date, token)
#             if not raw_data:
#                 raise HTTPException(500, "Failed to fetch CRM data")

#             parsed = parse_report_data(raw_data)
#             intent = parse_question_intent(request.query)
#             response = resolve_question(parsed, intent)

#             result = {
#                     "quarter": label,
#                     "period": f"{start_date} to {end_date}",
#                     "CRE_GRE": sort_funnel_by_numeric_desc(response)
#                 }
#             all_quarter_results.append(result)

#             # ✅ Extract only rows
#             if isinstance(response, dict) and "data" in response:
#                 all_quarter_results.extend(response["data"])
#             elif isinstance(response, list):
#                 all_quarter_results.extend(response)
#             # Return structured QoQ or Quarter-wise response
#         totals = calculate_master_totals(all_quarter_results)

#         return {
#             "status": "success",
#             "analysis_type": analysis_type,
#             "fy": f"FY{fy}",
#             "totals":totals,
#             "data": sort_funnel_by_numeric_desc(all_quarter_results)
#         }
#     elif (yoy_result := detect_yoy(request.query)):
#         periods = yoy_result["periods"]
#         all_year_results = []

#         print(f"Running YoY analysis → {len(periods)} years")

#         for period in periods:
#             start_date = period["start_date"]
#             end_date = period["end_date"]
#             label = period["year"]
#             start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
#             end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

#             raw_data = fetch_combined_reports(start_date, end_date, token)
#             if not raw_data:
#                 raise HTTPException(500, "Failed to fetch CRM data")

#             parsed = parse_report_data(raw_data)
#             intent = parse_question_intent(request.query)
#             response = resolve_question(parsed, intent)


#             result = {
#                     "quarter": label,
#                     "period": f"{start_date} to {end_date}",
#                     "funnel": sort_funnel_by_numeric_desc(response)
#                 }
#             all_year_results.append(result)

#             # ✅ Extract only rows
#             if isinstance(response, dict) and "data" in response:
#                 all_year_results.extend(response["data"])
#             elif isinstance(response, list):
#                 all_year_results.extend(response)

#             # Return structured QoQ or Quarter-wise response
#         totals = calculate_master_totals(all_year_results)
#         return {
#             "status": "success",
#             "analysis_type": "year_on_year",
#             "fy": "Last 3 completed financial years",
#             "totals":totals,
#             "data": sort_funnel_by_numeric_desc(all_year_results)
#         }
    
#     elif (mom_result := detect_mom(request.query)):
#         periods = mom_result["periods"]
#         all_month_results = []

#         print(f"Running MoM analysis → {len(periods)} Months")

#         for period in periods:
#             start_date = period["start_date"]
#             end_date = period["end_date"]
#             label = period["label"]

#             start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
#             end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

#             raw_data = fetch_combined_reports(start_date, end_date, token)
#             if not raw_data:
#                 raise HTTPException(500, "Failed to fetch CRM data")

#             parsed = parse_report_data(raw_data)
#             intent = parse_question_intent(request.query)
#             response = resolve_question(parsed, intent)

            
#             all_month_results.append({
#                 "month": label,
#                 "period": period["period"],
#                 "funnel": sort_funnel_by_numeric_desc(response)
#             })
            
#             # ✅ Extract only rows
#             if isinstance(response, dict) and "data" in response:
#                 all_month_results.extend(response["data"])
#             elif isinstance(response, list):
#                 all_month_results.extend(response)

#         totals = calculate_master_totals(all_month_results)
#         return {
#             "status": "success",
#             "analysis_type": "month_on_month",
#             "comparison": "Last 6 months + Current MTD",
#             "totals":totals,
#             "data": all_month_results  # May → Jun → Jul → Aug → Sep → Oct → Nov (MTD)
#         }
#     elif (mul_year := parse_multi_year_date(request.query)):

#         filters_label = ""
#         yearly_results = []

#         intent = parse_question_intent(request.query)

#         for start_str, end_str in mul_year:

#             start_dt = datetime.strptime(start_str, "%d-%m-%Y")
#             end_dt = datetime.strptime(end_str, "%d-%m-%Y")

#             start_date = start_dt.strftime("%Y-%m-%d")
#             end_date = end_dt.strftime("%Y-%m-%d")

#             raw_data = fetch_combined_reports(start_date, end_date, token)
#             if not raw_data:
#                 continue

#             parsed = parse_report_data(raw_data)
#             response = resolve_question(parsed, intent)

#             # Extract rows
#             if isinstance(response, dict) and "data" in response:
#                 rows = response["data"]
#             elif isinstance(response, list):
#                 rows = response
#             else:
#                 rows = []

#             totals = calculate_master_totals(rows)

#             # Create FY label
#             fy_label = f"FY {start_dt.year}-{str(end_dt.year)[-2:]}"

#             yearly_results.append({
#                 "label": fy_label,
#                 "totals": totals,
#                 "data": sort_funnel_by_numeric_desc(rows)
#             })
#         return {
#             "status": "success",
            
#             "filter": filters_label,
#             "data": yearly_results
#         }
#     elif (mul_month := parse_multi_month_date(request.query)):

#         filters_label = ""
#         combined_response = []

#         for start_str, end_str in mul_month:

#             start_date = datetime.strptime(start_str, "%d-%m-%Y").strftime("%Y-%m-%d")
#             end_date = datetime.strptime(end_str, "%d-%m-%Y").strftime("%Y-%m-%d")

#             raw_data = fetch_combined_reports(start_date, end_date, token)
#             if not raw_data:
#                 continue

#             parsed = parse_report_data(raw_data)
#             intent = parse_question_intent(request.query)
#             response = resolve_question(parsed, intent)

#             # ✅ Extract only rows
#             if isinstance(response, dict) and "data" in response:
#                 combined_response.extend(response["data"])
#             elif isinstance(response, list):
#                 combined_response.extend(response)

#         totals = calculate_master_totals(combined_response)
#         return {
#             "status": "success",
#             "filter": filters_label,
#             "totals": totals,
#             "data": sort_funnel_by_numeric_desc(combined_response)
#         }
#     else:

#         start_date, end_date  = parse_dates_from_question(request.query)

#         start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
#         end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

#         raw_data = fetch_combined_reports(start_date, end_date, token)
#         if not raw_data:
#             raise HTTPException(500, "Failed to fetch CRM data")
        
#         parsed = parse_report_data(raw_data)
        
#         intent = parse_question_intent(request.query)
        
#         response = resolve_question(parsed, intent)

#         # Extract rows
#         if isinstance(response, dict) and "data" in response:
#             rows = response["data"]
#         elif isinstance(response, list):
#             rows = response
#         else:
#             rows = []

#         totals = calculate_master_totals(rows)
#         return make_json_safe({
#             "intent": intent,
#             "period": {
#                 "start_date": start_date,
#                 "end_date": end_date
#             },
#             "totals":totals,
#             "data": sort_funnel_by_numeric_desc(rows)
#         })

# # Run with: uvicorn your_file:app --reload






import requests
import os
import re
import math
import numpy as np
from typing import Optional
from typing import List, Dict, Any, Tuple, Union
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pandas as pd
from datetime import datetime, timedelta,date
import json
from dotenv import load_dotenv
from dateutil import parser as date_parser
from calendar import monthrange
from difflib import get_close_matches
load_dotenv()

DATA_DICTIONARY = {
    "qualified_leads": {
        "keywords": ["qualified", "ql", "lead"],
        "columns": [
            "ql_target",
            "ql_actual",
            "total_leads"
        ],
        "report": "report_1"
    },

    "appointments": {
        "keywords": ["appointment", "meeting", "completion","booked","activities"],
        "columns": [
            "appt_booked_actual",
            "appt_booked_target",
            "appt_completion_actual",
            "appt_completion_target",
            "total_activities"
        ],
        "report": "report_2"
    },

    "service_requests": {
        "keywords": ["service request", "sr", "resolved"],
        "columns": [
            "sr_target",
            "resolved_actual",
            "total_sr"
        ],
        "report": "report_3"
    }
}

KPI_COLUMN_MAP = {
    "qualified_leads":[
        "user_name",
        "ql_actual",
        "ql_target"
    ],

    "completion": [
        "user_name",
        "appt_completion_actual",
        "appt_completion_target"
    ],
    "booked": [
        "user_name",
        "appt_booked_actual",
        "appt_booked_target"
    ],
    "service_requests":[
        "user_name",
        "resolved_actual",
        "sr_target"
    ]
}

def get_salesforce_token() -> Optional[str]:
    """
    Get Salesforce access token using password flow (for dev only).
    In production, prefer JWT Bearer or Client Credentials flow.
    """
    url = os.getenv("GET_TOKEN_URL")
    
    if not url:
        url = "https://waveinfratech.my.salesforce.com/services/oauth2/token"
        return url
    
    params = {
        "grant_type": os.getenv("GRANT_TYPE"),                     # ← grant type
        "client_id": os.getenv("CLIENT_ID"),           # ← your client id
        "client_secret": os.getenv("CLIENT_SECRET"),   # ← your client secret
        "token_url": os.getenv("TOKEN_URL")            # ← your token URL
    }
    
    try:
        response = requests.post(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["access_token"]
    except Exception as e:
        print(f"Authentication failed: {e}")
        return None
    

def fetch_combined_reports(start_date: str, end_date: str, token: str) -> dict | None:
    """
    Fetch data from your custom combinedReports endpoint
    Example: start_date="2024-09-01", end_date="2024-09-30"
    """
    print("Fetching combined reports...")
    api_endpoint = os.getenv("API_ENDPOINT")
    print(f"API Endpoint: {api_endpoint}")
    api_url = f"{os.getenv('API_ENDPOINT')}?startDate={start_date}&endDate={end_date}"

    print(f"Fetching data from {api_url}")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"API call failed: {e}")
        return None
    


def parse_report_data(raw_data: dict) -> Dict[str, Any]:
    """
    Parse the combined reports JSON into a clean structure.
    Returns dict with 'report1', 'report2', 'report3' keys.
    """

    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError as e:
            raise ValueError("Invalid JSON string received") from e

    print(f"Raw data keys: {list(raw_data.keys())}")
    parsed = {}
    
    print("Parsing report data...")
    print("Type of data:", type(raw_data))
    print(f"Raw data keys: {list(raw_data.keys())}")

    appt_target_by_user = {}
    for report_name in ["Report 1", "Report 2", "Report 3"]:
        if report_name not in raw_data:
            continue

        
            
        report = raw_data[report_name]
        # print(report)
        groupings = report["groupingsDown"]["groupings"]
        fact_map = report["factMap"]
        
        rows = []
        for group in groupings:
            user_id = group["value"]
            user_name = group["label"]
            key = f"{group['key']}!T"
            
            if key not in fact_map:
                continue
                
            aggregates = fact_map[key]["aggregates"]
            print(aggregates,"aggregates==============================")
            row = None
            if report_name == "Report 1":  # Leads / QL
                appt_target_by_user[user_id] = float(aggregates[2]["value"])
                row = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "ql_target": float(aggregates[0]["value"]),
                    "ql_actual": float(aggregates[1]["value"]),
                    "appt_booked_target": appt_target_by_user[user_id],
                    "total_leads": int(aggregates[3]["value"])
                }
            elif report_name == "Report 2":  # Activities
                row = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "appt_booked_actual": int(aggregates[0]["value"]),
                    "appt_booked_target": appt_target_by_user.get(user_id, 0.0),
                    "appt_completion_target": float(aggregates[1]["value"]),
                    "appt_completion_actual": float(aggregates[2]["value"]),
                    "total_activities": int(aggregates[3]["value"])
                }
            elif report_name == "Report 3":  # Service Requests
                row = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "sr_target": float(aggregates[0]["value"]),
                    "resolved_actual": float(aggregates[1]["value"]),
                    "total_sr": int(aggregates[2]["value"])
                }
            if row:
                rows.append(row)
        
        parsed[report_name.lower().replace(" ", "_")] = {
            "rows": rows,
            "grand_total": fact_map.get("T!T", {}).get("aggregates", [])
        }
    
    return parsed




app = FastAPI(title="CRM Performance Chatbot API")

class QueryRequest(BaseModel):
    query: str
    start_date: str | None = None   # optional YYYY-MM-DD
    end_date: str | None = None

def simple_intent_extraction(query: str) -> str:
    query = query.lower()
    if "qualified" in query or "ql" in query or "lead" in query:
        return "ql"
    if "appointment" in query or "booked" in query or "completion" in query:
        return "appointments"
    if "service request" in query or "sr" in query or "resolved" in query:
        return "sr"
    return "overview"

#---------------------Normlize text-------------------------
def normalize(text: str):
    return text.lower().replace(",", " ").replace("-", " ").replace("  ", " ").strip()

def extract_year_from_text(text):
    for part in text.split():
        if part.isdigit() and len(part) == 4:
            return int(part)
    return None

def get_fy_quarter(m):
    if 4 <= m <= 6:   return 1
    if 7 <= m <= 9:   return 2
    if 10 <= m <= 12: return 3
    return 4

def get_current_fy():
    today = datetime.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    return fy_start

def parse_single_date(q: str | None) -> date | None:
    """
    Parse single date forms like:
      - '15 april 2024'
      - '15 april'
      - '5th june 23'
      - '15/04/2024' or '15-04-2024'
    Returns a datetime.date or None.
    """
    if not q or not isinstance(q, str):
        return None

    original_q = q
    q = q.strip().lower()

    # First try: DD/MM/YYYY or DD-MM-YYYY
    slash_match = re.match(r'^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$', q)
    if slash_match:
        day, month, year = map(int, slash_match.groups())
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None

    # Natural language: 15 april 2024, 5th june 23, etc.
    m = re.search(
        r'\b([0-3]?\d)(?:st|nd|rd|th)?\s+'
        r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
        r'nov(?:ember)?|dec(?:ember)?)'
        r'(?:[,\s]+(20\d{2}|\d{2}))?\b',
        q
    )
    if not m:
        return None

    day = int(m.group(1))
    month_name = m.group(2)
    year_part = m.group(3)

    month = extract_month_by_name(month_name)
    if not month:
        return None

    if year_part:
        year = int(year_part)
        if len(year_part) == 2:
            year += 2000
    else:
        fy = get_current_fy()
        year = fy if month >= 4 else fy + 1

    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None
    
def detect_qoq(question: str):
    import re
    from datetime import datetime

    text = question.lower().strip()

    # ----------------------------------------
    # 1) Detect user intent (QOQ / Quarterly)
    # ----------------------------------------
    trigger_keywords = [
        "qoq", "quarter on quarter", "quarter-wise", "quarter wise",
        "quater wise", "quarterly", "quarterwise"
    ]
    if not any(kw in text for kw in trigger_keywords):
        return None

    # ----------------------------------------
    # 2) Determine current FY
    # ----------------------------------------
    today = datetime.today()
    current_fy = today.year if today.month >= 4 else today.year - 1

    # ----------------------------------------
    # 3) Extract explicit year (2023, 2024…)
    # ----------------------------------------
    year_match = re.search(r"\b(20\d{2})\b", text)
    explicit_year = int(year_match.group(1)) if year_match else None

    # ----------------------------------------
    # 4) Extract FY format like "FY24" or "fy2025"
    # ----------------------------------------
    fy_match = re.search(r"fy\s?(\d{2,4})", text)
    explicit_fy = None
    if fy_match:
        fy_value = fy_match.group(1)
        if len(fy_value) == 2:
            explicit_fy = int("20" + fy_value)      # fy24 → 2024
        else:
            explicit_fy = int(fy_value)             # fy2024 → 2024

    # ----------------------------------------
    # 5) Detect LAST YEAR / PREVIOUS FY logic
    # ----------------------------------------
    if "last year" in text or "previous year" in text or "last fy" in text or "previous fy" in text:
        target_fy = current_fy - 1

    elif explicit_year:
        target_fy = explicit_year

    elif explicit_fy:
        target_fy = explicit_fy

    else:
        # default → current FY
        target_fy = current_fy

    print(f"QOQ → Using FY{target_fy}")

    # Generate all 4 quarters from Q1 to Q4
    def quarter_dates(q, fy_year):
        if q == 1:
            return f"01-04-{fy_year}", f"30-06-{fy_year}"
        elif q == 2:
            return f"01-07-{fy_year}", f"30-09-{fy_year}"
        elif q == 3:
            return f"01-10-{fy_year}", f"31-12-{fy_year}"
        elif q == 4:
            return f"01-01-{fy_year + 1}", f"31-03-{fy_year + 1}"

    quarters = []
    for q in range(1, 5):
        start, end = quarter_dates(q, target_fy)
        quarters.append({
            "quarter": f"Q{q} FY{target_fy}",
            "start_date": start,
            "end_date": end
        })


    return {
        "type": "quarter_wise",
        "fy": target_fy,
        "quarters": quarters  # Always Q1 → Q2 → Q3 → Q4
    }

def detect_mom(question: str):
    import re
    from datetime import datetime
    from calendar import monthrange

    q = question.lower().strip()

    # --------------------------------------------------
    # 1) MOM INTENT CHECK
    # --------------------------------------------------
    mom_keywords = [
        "mom",
        "month on month",
        "month-on-month",
        "monthly",
        "month wise",
        "month over month"
    ]

    if not any(k in q for k in mom_keywords):
        return None

    today = datetime.today()

    # --------------------------------------------------
    # 2) FINANCIAL YEAR (APR–MAR)
    # --------------------------------------------------
    current_fy = today.year if today.month >= 4 else today.year - 1

    FY_QUARTERS = {
        1: (4, 6),    # Apr–Jun
        2: (7, 9),    # Jul–Sep
        3: (10, 12),  # Oct–Dec
        4: (1, 3)     # Jan–Mar
    }

    # --------------------------------------------------
    # 3) MONTH NORMALIZATION
    # --------------------------------------------------
    month_map = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12
    }

    # --------------------------------------------------
    # 4) EXTRACT YEAR / FY
    # --------------------------------------------------
    year_match = re.search(r"\b(20\d{2})\b", q)
    specified_year = int(year_match.group(1)) if year_match else None

    fy_match = re.search(r"\bfy\s?(\d{2})\b", q)
    specified_fy = int("20" + fy_match.group(1)) if fy_match else None

    # --------------------------------------------------
    # 5) EXPLICIT MONTH RANGE
    # --------------------------------------------------
    month_range_regex = (
        r"\b(" + "|".join(month_map.keys()) + r")\b\s*"
        r"(to|till|-|and)\s*"
        r"\b(" + "|".join(month_map.keys()) + r")\b"
    )

    m_range = re.search(month_range_regex, q)

    # --------------------------------------------------
    # 6) QUARTER DETECTION
    # --------------------------------------------------
    q_match = re.search(r"\bq([1-4])\b", q)
    quarter = int(q_match.group(1)) if q_match else None

    is_last_quarter = (
        "last quarter" in q or
        "previous quarter" in q
    )

    # --------------------------------------------------
    # 7) RESOLVE TARGET MONTH RANGE
    # --------------------------------------------------
    if m_range:
        sm = month_map[m_range.group(1)]
        em = month_map[m_range.group(3)]
        fy = specified_fy or specified_year or current_fy
        year = fy if sm >= 4 else fy + 1

    elif is_last_quarter:
        # Determine current FY start year
        current_fy = today.year if today.month >= 4 else today.year - 1

        # Determine current quarter inside FY (Apr–Mar)
        if 4 <= today.month <= 6:
            curr_q = 1
        elif 7 <= today.month <= 9:
            curr_q = 2
        elif 10 <= today.month <= 12:
            curr_q = 3
        else:
            curr_q = 4  # Jan–Mar

        # Determine last quarter
        if curr_q == 1:
            last_q = 4
            fy = current_fy - 1
        else:
            last_q = curr_q - 1
            fy = current_fy

        FY_QUARTERS = {
            1: (4, 6),
            2: (7, 9),
            3: (10, 12),
            4: (1, 3)
        }

        sm, em = FY_QUARTERS[last_q]

        # Year handling
        if last_q == 4:
            year = fy + 1   # Jan–Mar belongs to next calendar year
        else:
            year = fy

    elif quarter:
        fy = current_fy

        if "last year" in q or "previous year" in q:
            fy = current_fy - 1
        # If user gives FY explicitly (e.g. fy24)
        if specified_fy:
            fy = specified_fy

        # If user gives calendar year (e.g. 2024)
        elif specified_year:
            fy = specified_year  # treat as FY start year

        sm, em = FY_QUARTERS[quarter]

        # Year handling for calendar year mapping
        if quarter == 4:
            year = fy + 1  # Jan–Mar belongs to next calendar year
        else:
            year = fy

    elif (
        "last year" in q or
        "previous year" in q or
        "previous fy" in q
    ):
        fy = current_fy - 1
        sm, em = 4, 3
        year = fy

    elif specified_fy or specified_year:
        fy = specified_fy or specified_year
        sm, em = 4, 3
        year = fy

    else:
        # Default → current FY till today
        fy = current_fy
        sm = 4
        em = today.month
        year = fy

    # --------------------------------------------------
    # 8) GENERATE MONTH-WISE PERIODS
    # --------------------------------------------------
    periods = []

    y = year
    m = sm

    while True:
        _, last_day = monthrange(y, m)

        start_date = f"01-{m:02d}-{y}"
        end_date = f"{last_day:02d}-{m:02d}-{y}"

        label = datetime(y, m, 1).strftime("%b %Y")

        # Apply MTD only for current FY current month
        if fy == current_fy and y == today.year and m == today.month:
            end_date = today.strftime("%d-%m-%Y")
            label += " (MTD)"

        periods.append({
            "label": label,
            "start_date": start_date,
            "end_date": end_date,
            "period": f"{start_date} to {end_date}"
        })

        if m == em:
            break

        m += 1
        if m > 12:
            m = 1
            if y is None:
                y = datetime.today().year
            y += 1

    return {
        "type": "mom",
        "fy": f"FY{fy}",
        "periods": periods
    }

def detect_yoy(question: str):
    text = question.lower().strip()

    # Trigger keywords for YoY
    trigger_keywords = [
        "yoy", "year on year", "year-on-year", "year over year",
        "last 3 years", "last three years", "past 3 years",
        "yoy performance", "yearly comparison"
    ]

    if not any(kw in text for kw in trigger_keywords):
        return None

    from datetime import datetime
    today = datetime.today()
    current_fy = today.year if today.month >= 4 else today.year - 1

    # We want last 3 COMPLETED financial years
    # Example: Today = Nov 2025 → Current FY = 2025 → Completed = FY22, FY23, FY24
    latest_completed_fy = current_fy - 1
    years = [
        latest_completed_fy - 2,  # e.g., FY22
        latest_completed_fy - 1,  # e.g., FY23
        latest_completed_fy,      # e.g., FY24
        latest_completed_fy + 1   # e.g., FY25 (current FY, optional)
    ]

    print(f"YoY detected → Comparing last 3 FYs: {years}")

    def fy_dates(fy_year: int):
        return f"01-04-{fy_year}", f"31-03-{fy_year + 1}"

    yoy_periods = []
    for fy in years:
        start, end = fy_dates(fy)
        yoy_periods.append({
            "year": f"FY{fy}",
            "start_date": start,
            "end_date": end
        })

    return {
        "type": "yoy",
        "years": years,
        "periods": yoy_periods
    }


def parse_single_or_range_date(q: str | None):
    if not q or not isinstance(q, str):
        return None

    q = q.strip()
    q_lower = q.lower()

    RANGE_WORDS = r'(?:to|till|until|thru|through|-|–|—)'
    MONTH_PATTERN = (
        r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
        r'nov(?:ember)?|dec(?:ember)?)'
    )

    # ------------------------------------------------------
    # 1️⃣ SAME MONTH RANGE
    # "15 to 30 april", "15 sep to 30 september 2024"
    # ------------------------------------------------------
    same_month_pattern = (
        r'(\d{1,2}(?:st|nd|rd|th)?)\s*' +
        RANGE_WORDS +
        r'\s*(\d{1,2}(?:st|nd|rd|th)?)\s+' +
        MONTH_PATTERN +
        r'(?:[,\s]+(20\d{2}|\d{2}))?'
    )

    m = re.search(same_month_pattern, q_lower)
    if m:
        day1, day2, month, year = m.groups()
        raw1 = f"{day1} {month}" + (f" {year}" if year else "")
        raw2 = f"{day2} {month}" + (f" {year}" if year else "")

        d1 = parse_single_date(raw1)
        d2 = parse_single_date(raw2)
        if d1 and d2 and d1 <= d2:
            return d1, d2

    # ------------------------------------------------------
    # 2️⃣ DIFFERENT MONTH RANGE
    # "15 sep to 30 oct"
    # ------------------------------------------------------
    diff_month_pattern = (
        r'(\d{1,2}(?:st|nd|rd|th)?)\s+' +
        MONTH_PATTERN +
        r'\s*' +
        RANGE_WORDS +
        r'\s*(\d{1,2}(?:st|nd|rd|th)?)\s+' +
        MONTH_PATTERN +
        r'(?:[,\s]+(20\d{2}|\d{2}))?'
    )

    m = re.search(diff_month_pattern, q_lower)
    if m:
        day1, month1, day2, month2, year = m.groups()
        raw1 = f"{day1} {month1}" + (f" {year}" if year else "")
        raw2 = f"{day2} {month2}" + (f" {year}" if year else "")

        d1 = parse_single_date(raw1)
        d2 = parse_single_date(raw2)
        if d1 and d2 and d1 <= d2:
            return d1, d2

    # ------------------------------------------------------
    # 3️⃣ SLASH / HYPHEN RANGE
    # ------------------------------------------------------
    numeric_pattern = (
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*' +
        RANGE_WORDS +
        r'\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    )

    m = re.search(numeric_pattern, q_lower)
    if m:
        d1 = parse_single_date(m.group(1))
        d2 = parse_single_date(m.group(2))
        if d1 and d2 and d1 <= d2:
            return d1, d2

    # ------------------------------------------------------
    # 4️⃣ SINGLE DATE FALLBACK
    # ------------------------------------------------------
    single = parse_single_date(q)
    if single:
        return single, single

    return None

def get_current_fy_year() -> int:
    today = datetime.today()
    return today.year if today.month >= 4 else today.year - 1

def get_last_day_of_month(year: int, month: int) -> int:
    """Safely return the last day of the given month/year."""
    # List of days in each month (index 0 unused)
    month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    
    if month == 2:
        # Check for leap year
        if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
            return 29
        else:
            return 28
    else:
        return month_days[month]

def parse_multi_month_date(q: str) -> List[Tuple[str, str]] | None:
    """
    Rules:
    - 'and', ','  → discrete months (ONLY those months)
    - 'to'        → continuous range (fill months in between)
    Financial Year: April–March
    """

    print("parse_multi_month_date=============================in function=====")

    if not q or not isinstance(q, str):
        return None

    q_lower = q.lower().strip()

    # -------------------------------------------------
    # 🚫 HARD BLOCKS (wrong intent)
    # -------------------------------------------------

    # Exact date present → handled by date parser
    if (
        re.search(r'\b\d{1,2}(st|nd|rd|th)?\b', q_lower)
        and re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', q_lower)
    ):
        return None

    # Quarter intent → handled by quarter parser
    if re.search(r'\bq[1-4]\b|quarter', q_lower):
        return None

    # Exact date range like "15 sep to 30 oct"
    if re.search(
        r'\b\d{1,2}\b.*\b(to|till|until|through|-|–|—)\b.*\b\d{1,2}\b',
        q_lower
    ):
        return None

    # Continuous month range with relative year → handled elsewhere
    if (
        ' to ' in q_lower
        and re.search(r'\b(last|previous|this|current|next)\s+year\b', q_lower)
    ):
        return None

    # Non-"to" range words should not enter here
    if re.search(r'\b(till|until|through|-|–|—)\b', q_lower) and ' to ' not in q_lower:
        return None

    # -------------------------------------------------
    # Detect continuous vs discrete
    # -------------------------------------------------
    is_continuous = ' to ' in q_lower

    # -------------------------------------------------
    # Relative year handling (ONLY for discrete months)
    # -------------------------------------------------
    fy_shift = 0
    if re.search(r'\b(last|previous)\s+year\b', q_lower):
        fy_shift = -1
    elif re.search(r'\b(next)\s+year\b', q_lower):
        fy_shift = 1

    # -------------------------------------------------
    # 1️⃣ YEAR-ONLY DETECTION
    # -------------------------------------------------
    year_pattern = r'\b(19\d{2}|20\d{2})\b'
    years = [int(y) for y in re.findall(year_pattern, q_lower)]

    month_check = re.search(
        r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec',
        q_lower
    )

    if years and not month_check:
        years = sorted(set(years))
        ranges = []

        if is_continuous:
            start_year = years[0]
            end_year = years[-1]
            ranges.append(
                (f"01-04-{start_year}", f"31-03-{end_year + 1}")
            )
        else:
            for y in years:
                ranges.append(
                    (f"01-04-{y}", f"31-03-{y + 1}")
                )

        return ranges

    # -------------------------------------------------
    # 2️⃣ MONTH LOGIC
    # -------------------------------------------------

    q_normalized = re.sub(r'\s+and\s+|\s*,\s*', ' ', q_lower)

    month_pattern = (
        r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
        r'nov(?:ember)?|dec(?:ember)?)'
    )

    month_matches = re.findall(month_pattern, q_normalized)
    if not month_matches:
        return None

    # Explicit year mentions
    year_pattern = r'\b(20\d{2}|19\d{2}|\d{2})\b'
    year_mentions = re.findall(year_pattern, q_lower)

    explicit_years = []
    for y in year_mentions:
        yi = int(y)
        if len(y) == 2:
            yi += 2000
        explicit_years.append(yi)

    default_year = get_current_fy_year() + fy_shift

    month_years = []

    for i, month_name in enumerate(month_matches):
        month_num = extract_month_by_name(month_name)
        if not month_num:
            continue

        if explicit_years:
            year = explicit_years[min(i, len(explicit_years) - 1)]
        else:
            year = default_year + 1 if month_num < 4 else default_year

        month_years.append((year, month_num))

    if not month_years:
        return None

    month_years = sorted(set(month_years))

    # -------------------------------------------------
    # 3️⃣ BUILD RESULT
    # -------------------------------------------------
    ranges: List[Tuple[str, str]] = []

    if is_continuous:
        start_year, start_month = month_years[0]
        end_year, end_month = month_years[-1]

        start_date = f"01-{start_month:02d}-{start_year}"
        last_day = get_last_day_of_month(end_year, end_month)
        end_date = f"{last_day:02d}-{end_month:02d}-{end_year}"

        ranges.append((start_date, end_date))
    else:
        for year, month in month_years:
            last_day = get_last_day_of_month(year, month)
            start_date = f"01-{month:02d}-{year}"
            end_date = f"{last_day:02d}-{month:02d}-{year}"
            ranges.append((start_date, end_date))

    return ranges

def parse_multi_year_date(q: str) -> List[Tuple[str, str]] | None:
    """
    Rules:
    - 'and', ','  → discrete years
    - 'to'        → continuous range
    Financial Year: April–March
    """

    print("parse_multi_year_date=============================in function=====")

    if not q or not isinstance(q, str):
        return None

    q_lower = q.lower().strip()

    # -------------------------------------------------
    # 🚫 HARD BLOCKS
    # -------------------------------------------------

    # If month present → handled by month parser
    if re.search(
        r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec',
        q_lower
    ):
        return None

    # Quarter intent
    if re.search(r'\bq[1-4]\b|quarter', q_lower):
        return None

    # -------------------------------------------------
    # Detect continuous vs discrete
    # -------------------------------------------------
    is_continuous = ' to ' in q_lower

    # -------------------------------------------------
    # Extract Years
    # -------------------------------------------------
    year_pattern = r'\b(19\d{2}|20\d{2})\b'
    years = [int(y) for y in re.findall(year_pattern, q_lower)]

    if not years:
        return None

    years = sorted(set(years))

    ranges: List[Tuple[str, str]] = []

    # -------------------------------------------------
    # Build Result
    # -------------------------------------------------

    if is_continuous:
        start_year = years[0]
        end_year = years[-1]

        start_date = f"01-04-{start_year}"
        end_date = f"31-03-{end_year + 1}"

        ranges.append((start_date, end_date))

    else:
        for y in years:
            start_date = f"01-04-{y}"
            end_date = f"31-03-{y + 1}"
            ranges.append((start_date, end_date))

    return ranges



def month_range_with_year(q: str):
    """
    Supports:
      - apr-may
      - apr to may
      - apr & may
      - apr-may 2024
      - oct-feb last year
    Financial Year: April–March
    """

    if not q or not isinstance(q, str):
        return None

    q = q.lower().strip()

    # -----------------------------
    # Month regex (same as yours)
    # -----------------------------
    month_regex = (
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )

    RANGE_REGEX = r"(?:to|till|until|thru|through|or|[-–—&])"
    YEAR_REGEX  = r"(19\d{2}|20\d{2}|\d{2})"

    # -----------------------------
    # Relative year detection
    # -----------------------------
    is_last_year = bool(re.search(r"\b(last|previous)\s+year\b", q))
    is_this_year = bool(re.search(r"\b(this|current)\s+year\b", q))

    # -----------------------------
    # Main regex
    # -----------------------------
    m = re.search(
        rf"\b{month_regex}\b\s*{RANGE_REGEX}\s*\b{month_regex}\b(?:\s+{YEAR_REGEX})?",
        q
    )

    print(m,'===========================================')

    if not m:
        return None

    start_month_str = m.group(1)
    end_month_str   = m.group(2)
    year_str        = m.group(3)

    # -----------------------------
    # Month normalization
    # -----------------------------
    month_map = {
        'jan': 1, 'january': 1,
        'feb': 2, 'february': 2,
        'mar': 3, 'march': 3,
        'apr': 4, 'april': 4,
        'may': 5,
        'jun': 6, 'june': 6,
        'jul': 7, 'july': 7,
        'aug': 8, 'august': 8,
        'sep': 9, 'sept': 9, 'september': 9,
        'oct':10, 'october':10,
        'nov':11, 'november':11,
        'dec':12, 'december':12,
    }

    def norm(m):
        return month_map.get(m[:3])

    m1 = norm(start_month_str)
    m2 = norm(end_month_str)

    if not m1 or not m2:
        return None

    # -----------------------------
    # Financial year resolution
    # -----------------------------
    today = datetime.today()
    current_fy = today.year if today.month >= 4 else today.year - 1

    if year_str:
        fy = int(year_str)
        if fy < 100:
            fy += 2000
    elif is_last_year:
        fy = current_fy - 1
    else:
        fy = current_fy

    # -----------------------------
    # Assign years per month (FY aware)
    # -----------------------------
    y1 = fy if m1 >= 4 else fy + 1
    y2 = fy if m2 >= 4 else fy + 1

    if m2 < m1:
        y2 += 1

    # -----------------------------
    # Build dates
    # -----------------------------
    start_date = f"01-{m1:02d}-{y1}"
    end_day = monthrange(y2, m2)[1]
    end_date = f"{end_day:02d}-{m2:02d}-{y2}"

    return {
        "start_date": start_date,
        "end_date": end_date
    }

def extract_month_by_name(text):
    months = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }
    for word in text.split():
        if word in months:
            return months[word]
    return None

def detect_last_n_days(question: str):
    text = question.lower().strip()

    # --------------------------------------------
    # 1. LAST YEAR / PREVIOUS FY (Highest Priority)
    # --------------------------------------------
    last_year_patterns = [
        r"last\s+year\b",
        r"previous\s+year\b",
        r"last\s+financial\s+year\b",
        r"last\s+fy\b",
        r"previous\s+fy\b",
        r"last\s+f\.?y\.?\b"
    ]

    multi_year_patterns = [
        (r"last\s+(\d+)\s+years?", "years_number"),
        (r"last\s+(one|two|three|four|five)\s+years?", "years_word"),
    ]

    today = datetime.today()
    current_fy = today.year if today.month >= 4 else today.year - 1
    current_q = get_fy_quarter(today.month)

    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5
    }

    for pattern in last_year_patterns:

        if re.search(pattern, text):
            last_fy = current_fy - 1
            start_date = f"01-04-{last_fy}"
            end_date = f"31-03-{last_fy + 1}"

            return {
                "type": "last_financial_year",
                "fy": last_fy,
                "label": f"Last Financial Year (FY{last_fy})",
                "start_date": start_date,
                "end_date": end_date
            }
    
    for pattern, ptype in multi_year_patterns:
        match = re.search(pattern, text)
        if match:
            # Convert word to number
            if ptype == "years_number":
                n = int(match.group(1))
            else:
                n = word_to_num[match.group(1)]

            # Current FY
            today = datetime.today()
            current_fy = today.year if today.month >= 4 else today.year - 1

            # Last N full FY ranges
            start_fy = current_fy - n   # N financial years back
            end_fy = current_fy - 1     # Last completed FY

            start_date = f"01-04-{start_fy}"
            end_date = f"31-03-{end_fy + 1}"

            return {
                "type": "last_n_financial_years",
                "years": n,
                "label": f"Last {n} Financial Years (FY{start_fy}–FY{end_fy})",
                "start_date": start_date,
                "end_date": end_date
            }

    # --------------------------------------------
    # 2. Keywords Patterns
    # --------------------------------------------
    patterns = [
        (r"last\s+(\d+)\s+days?", "days"),
        (r"past\s+(\d+)\s+days?", "days"),

        (r"last\s+(\d+)\s+months?", "months"),
        (r"last\s+(one|two|three|four|five|six)\s+months?", "months_word"),

        (r"last\s+1\s+month\b", "month_single"),
        (r"last\s+one\s+month\b", "month_single"),
        (r"last\s+month\b", "month_single"),

        (r"last\s+week\b", "week"),
        (r"past\s+week\b", "week"),

        (r"last\s+quarter\b", "quarter"),
        (r"past\s+quarter\b", "quarter"),

        (r"last\s+(\d+)\s+quarters?", "quarters_number"),
        (r"last\s+(one|two|three|four)\s+quarters?", "quarters_word"),
    ]

    word_to_num = {
        "one": 1, "two": 2, "three": 3,
        "four": 4, "five": 5, "six": 6
    }

    # Helper: compute last N full quarters
    def compute_last_n_quarters(n):
        end_q = current_q - 1
        end_fy = current_fy

        if end_q <= 0:
            end_q = 4
            end_fy -= 1

        start_q = end_q - (n - 1)
        start_fy = end_fy

        while start_q <= 0:
            start_q += 4
            start_fy -= 1

        q_dates = {
            1: ("01-04", "30-06"),
            2: ("01-07", "30-09"),
            3: ("01-10", "31-12"),
            4: ("01-01", "31-03")
        }

        start_day, start_month = q_dates[start_q][0].split("-")
        end_day, end_month = q_dates[end_q][1].split("-")

        start_date = f"{start_day}-{start_month}-{start_fy}"
        end_date = f"{end_day}-{end_month}-{end_fy}"

        return {
            "type": "last_n_quarters",
            "label": f"Last {n} Quarters (Q{start_q} FY{start_fy} → Q{end_q} FY{end_fy})",
            "start_date": start_date,
            "end_date": end_date
        }

    # --------------------------------------------
    # 3. PATTERN PROCESS LOOP
    # --------------------------------------------
    for pattern, ptype in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        # --- Last Month (full previous month) ---
        if ptype == "month_single":
            first_of_this_month = today.replace(day=1)
            last_day_prev = first_of_this_month - timedelta(days=1)
            first_day_prev = last_day_prev.replace(day=1)

            return {
                "type": "last_full_month",
                "label": f"Last Month ({first_day_prev.strftime('%b %Y')})",
                "start_date": first_day_prev.strftime("%d-%m-%Y"),
                "end_date": last_day_prev.strftime("%d-%m-%Y")
            }

        # --- Last Week (Mon–Sun) ---
        if ptype == "week":
            days_back = today.weekday() + 7
            last_monday = today - timedelta(days=days_back)
            last_sunday = last_monday + timedelta(days=6)

            return {
                "type": "last_full_week",
                "label": "Last Week (Mon–Sun)",
                "start_date": last_monday.strftime("%d-%m-%Y"),
                "end_date": last_sunday.strftime("%d-%m-%Y")
            }

        # --- Last N Quarters (number) ---
        if ptype == "quarters_number":
            return compute_last_n_quarters(int(match.group(1)))

        # --- Last N Quarters (word) ---
        if ptype == "quarters_word":
            return compute_last_n_quarters(word_to_num.get(match.group(1), 1))

        # --- Last Quarter ---
        if ptype == "quarter":
            return compute_last_n_quarters(1)

        # --- Last N Full Months (word) ---
        if ptype == "months_word":
            n = word_to_num.get(match.group(1), 1)

            first_of_this_month = today.replace(day=1)
            end_date_dt = first_of_this_month - timedelta(days=1)
            start_date_dt = end_date_dt.replace(day=1)

            for _ in range(n - 1):
                start_date_dt = (start_date_dt.replace(day=1) - timedelta(days=1)).replace(day=1)

            return {
                "type": "last_n_full_months",
                "months": n,
                "label": f"Last {n} Months (Full Months)",
                "start_date": start_date_dt.strftime("%d-%m-%Y"),
                "end_date": end_date_dt.strftime("%d-%m-%Y")
            }

        # --- Last N Full Months (numeric) ---
        if ptype == "months":
            n = int(match.group(1))

            first_of_this_month = today.replace(day=1)
            end_date_dt = first_of_this_month - timedelta(days=1)
            start_date_dt = end_date_dt.replace(day=1)

            for _ in range(n - 1):
                start_date_dt = (start_date_dt.replace(day=1) - timedelta(days=1)).replace(day=1)

            return {
                "type": "last_n_full_months",
                "months": n,
                "label": f"Last {n} Months (Full Months)",
                "start_date": start_date_dt.strftime("%d-%m-%Y"),
                "end_date": end_date_dt.strftime("%d-%m-%Y")
            }

        # --- Last N Days ---
        if ptype == "days":
            days = int(match.group(1))
            start_date = (today - timedelta(days=days - 1)).strftime("%d-%m-%Y")
            end_date = today.strftime("%d-%m-%Y")

            return {
                "type": "last_n_days",
                "days": days,
                "label": f"Last {days} Days",
                "start_date": start_date,
                "end_date": end_date
            }

    return None


def detect_this_period(question: str):
    text = question.lower().strip()
    today = datetime.today()
    print('Detecting this period --------------------')
    # Determine current FY
    current_fy = today.year if today.month >= 4 else today.year - 1

    # Helper: Get current quarter
    current_q = get_fy_quarter(today.month)



    # ===================================================================
    # 1. THIS MONTH
    # ===================================================================
    if re.search(r"\bthis\s+month\b|\bcurrent\s+month\b", text):
        start = today.replace(day=1).strftime("%d-%m-%Y")
        end = today.strftime("%d-%m-%Y")
        return {
            "type": "this_month",
            "label": f"This Month (MTD) – {today.strftime('%b %Y')}",
            "start_date": start,
            "end_date": end
        }

    # ===================================================================
    # 2. THIS QUARTER
    # ===================================================================
    if re.search(r"\bthis\s+quarter\b|\bcurrent\s+quarter\b|\bqtd\b", text):
        if current_q == 1:
            start = f"01-04-{current_fy}"
            end = f"30-06-{current_fy}"
        elif current_q == 2:
            start = f"01-07-{current_fy}"
            end = f"30-09-{current_fy}"
        elif current_q == 3:
            start = f"01-10-{current_fy}"
            end = f"31-12-{current_fy}"
        else:  # current_q == 4
            start = f"01-01-{current_fy + 1}"
            end = f"31-03-{current_fy + 1}"

        return {
            "type": "this_quarter",
            "label": f"This Quarter (Q{current_q} FY{current_fy})",
            "start_date": start,
            "end_date": end
        }
    # ===================================================================
    # 3. THIS YEAR / THIS FY
    # ===================================================================
    if re.search(r"\bthis\s+year\b|\bthis\s+fy\b|\bcurrent\s+year\b|\bcurrent\s+fy\b", text):
        start = f"01-04-{current_fy}"
        end = today.strftime("%d-%m-%Y")
        return {
            "type": "this_fy",
            "label": f"This Financial Year (FY{current_fy} YTD)",
            "start_date": start,
            "end_date": end
        }

    # ===================================================================
    # 4. THIS WEEK (Monday to Today)
    # ===================================================================
    if re.search(r"\bthis\s+week\b|\bcurrent\s+week\b", text):
        monday = today - timedelta(days=today.weekday())
        start = monday.strftime("%d-%m-%Y")
        end = today.strftime("%d-%m-%Y")
        return {
            "type": "this_week",
            "label": "This Week (Mon–Today)",
            "start_date": start,
            "end_date": end
        }
    return None

def detect_year_range_logic(question: str):
    import re

    text = question.lower().strip()

    year_range_patterns = [
        r"(20\d{2})\s*(to|and|-|–)\s*(20\d{2})",            # 2022 to 2024, 2022-2024
        r"fy\s*(20\d{2})\s*(to|-|–)\s*fy\s*(20\d{2})",      # FY 2022 to FY 2024
        r"fy\s*(\d{2})\s*(to|-|–)\s*fy\s*(\d{2})",          # FY22 to FY24
        r"fy\s*(\d{2})\s*(to|-|–)\s*(\d{2})",               # FY22-24 (common)
    ]

    print(year_range_patterns,'----------------------------')
    for pattern in year_range_patterns:
        match = re.search(pattern, text)
        if match:
            y1, _, y2 = match.groups()

            # Convert 2-digit year → 4-digit
            if len(y1) == 2:
                y1 = "20" + y1
            if len(y2) == 2:
                y2 = "20" + y2

            y1 = int(y1)
            y2 = int(y2)

            start_year = min(y1, y2)
            end_year = max(y1, y2)

            start_date = f"01-04-{start_year}"
            end_date = f"31-03-{end_year + 1}"

            return {
                "type": "year_range",
                "label": f"FY {start_year} to FY {end_year}",
                "start_date": start_date,
                "end_date": end_date
            }

    return None

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
NON_ADDITIVE_MARKERS = ["%", ":"]

def _is_additive_key(key: str) -> bool:
    """
    Decide whether a column/metric should be summed.
    """
    return not any(marker in key for marker in NON_ADDITIVE_MARKERS)

def calculate_master_totals(data: Any) -> Dict[str, Union[int, float]]:
    """
    MASTER aggregation logic:
    - Column-wise totals
    - Sums ALL additive numeric fields
    - Ignores % and ratio fields
    - Works for wrapped response shapes
    """

    # 🔥 UNWRAP if response contains "data"
    if isinstance(data, dict) and "data" in data:
        rows = data["data"]
    elif isinstance(data, list):
        rows = data
    else:
        return {}

    totals: Dict[str, float] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        for key, value in row.items():

            # Skip percentage / ratio fields
            if "pct" in key.lower() or "%" in key.lower():
                continue

            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value

    # Clean formatting
    final_totals = {}
    for k, v in totals.items():
        if float(v).is_integer():
            final_totals[k] = int(v)
        else:
            final_totals[k] = round(v, 2)

    return final_totals

def month_year_range_parser(q: str):
    """
    Supports:
        - april 2022 - april 2025
        - april 2022 to april 2025
        - april 2022 & april 2025
        - april 2022 through april 2025
    
    Returns:
        {
            "start_date": "DD-MM-YYYY",
            "end_date": "DD-MM-YYYY"
        }
    """

    if not q or not isinstance(q, str):
        return None

    q = q.lower().strip()

    month_regex = (
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )

    year_regex = r"(19\d{2}|20\d{2})"
    # range_regex = r"(?:to|till|until|thru|through|and|[-–—&])"

    range_regex = r"(?:\s*(?:to|till|until|thru|through|and|-|–|—|&)\s*)"

    # pattern = rf"\b{month_regex}\s+{year_regex}\b\s*{range_regex}\s*\b{month_regex}\s+{year_regex}\b"
    pattern = rf"\b{month_regex}\s+{year_regex}{range_regex}{month_regex}\s+{year_regex}\b"
    match = re.search(pattern, q)

    if not match:
        return None

    start_month_str = match.group(1)
    start_year_str  = match.group(2)
    end_month_str   = match.group(3)
    end_year_str    = match.group(4)

    month_map = {
        'jan': 1, 'january': 1,
        'feb': 2, 'february': 2,
        'mar': 3, 'march': 3,
        'apr': 4, 'april': 4,
        'may': 5,
        'jun': 6, 'june': 6,
        'jul': 7, 'july': 7,
        'aug': 8, 'august': 8,
        'sep': 9, 'sept': 9, 'september': 9,
        'oct':10, 'october':10,
        'nov':11, 'november':11,
        'dec':12, 'december':12,
    }

    m1 = month_map.get(start_month_str[:3])
    m2 = month_map.get(end_month_str[:3])

    if not m1 or not m2:
        return None

    y1 = int(start_year_str)
    y2 = int(end_year_str)

    start_date = f"01-{m1:02d}-{y1}"
    end_day = monthrange(y2, m2)[1]
    end_date = f"{end_day:02d}-{m2:02d}-{y2}"

    return {
        "start_date": start_date,
        "end_date": end_date
    }



# --------------------------------------------
# Date Parsing Logic
# --------------------------------------------
def parse_dates_from_question(question: str):
    q = normalize(question)

    start_date, end_date = None, None
    print("=========================================================")

    # -------------------------------------------------------
    # 1️⃣ Detect Quarter (q1, q2, q3, q4)
    # -------------------------------------------------------
    if "q1" in q or "quarter 1" in q:
        year = extract_year_from_text(q) or get_current_fy()
        return f"01-04-{year}", f"30-06-{year}"

    if "q2" in q or "quarter 2" in q:
        year = extract_year_from_text(q) or get_current_fy()
        return f"01-07-{year}", f"30-09-{year}"

    if "q3" in q or "quarter 3" in q:
        year = extract_year_from_text(q) or get_current_fy()
        return f"01-10-{year}", f"31-12-{year}"

    if "q4" in q or "quarter 4" in q:
        year = extract_year_from_text(q) or get_current_fy()
        year = year + 1
        return f"01-01-{year}", f"31-03-{year}"
    
    # -------------------------------------------------------
    # 3️⃣ between date like "5 june to 10 june 2024"
    # -------------------------------------------------------
    date_pair = parse_single_or_range_date(q)
    if date_pair:
        d1, d2 = date_pair
        return d1.strftime("%d-%m-%Y"), d2.strftime("%d-%m-%Y")
    
    # # -------------------------------------------------------
    # # 6️⃣ Detect explicit date range: "april 2023 to june 2024"
    # # -------------------------------------------------------

    range_data  = month_year_range_parser(q)
    print(range_data,'========range_data============')
    if range_data:
        return range_data

    # -------------------------------------------------------
    # 2️⃣ Detect explicit date range: "april 2024 to june 2024"
    # -------------------------------------------------------
    month_range = month_range_with_year(q)
    if month_range:
        return month_range["start_date"],month_range["end_date"]
    
    print("===========================hello=================================")

    # # -------------------------------------------------------
    # # 6️⃣ Month range WITHOUT year (april to september)
    # # -------------------------------------------------------
    # month_range_wy = month_range_without_year(q)
    # if month_range_wy:
    #     return month_range_wy['start_date'],month_range_wy['end_date']
    
    # -------------------------------------------------------
    # 4️⃣ Single Month WITH year
    # -------------------------------------------------------
    month = extract_month_by_name(q)
    year = extract_year_from_text(q)

    if month and year:
        start_date = f"01-{month:02d}-{year}"
        last_day = monthrange(year, month)[1]
        end_date = f"{last_day:02d}-{month:02d}-{year}"
        return start_date, end_date

    # -------------------------------------------------------
    # 5️⃣ Single Month WITHOUT year (FY logic)
    # -------------------------------------------------------
    if month and not year:
        fy = get_current_fy()
        year = fy if month >= 4 else fy + 1
        start_date = f"01-{month:02d}-{year}"
        last_day = monthrange(year, month)[1]
        end_date = f"{last_day:02d}-{month:02d}-{year}"
        return start_date, end_date
    
    
    # -------------------------------------------------------
    # Additional check for "last n days"
    # -------------------------------------------------------
    last_n_days = detect_last_n_days(question)
    if last_n_days:
        return last_n_days["start_date"], last_n_days["end_date"]
    
    # -------------------------------------------------------
    # Additional check for "this period"
    # -------------------------------------------------------
    this_period = detect_this_period(question)
    if this_period:
        return this_period["start_date"], this_period["end_date"]


    # -------------------------------------------------------
    # 7️⃣ FY detection
    # -------------------------------------------------------
    if "fy" in q or "financial year" in q or "f y" in q:
        year = extract_year_from_text(q)
        if year:
            return f"01-04-{year}", f"31-03-{year+1}"
    
    year_range = detect_year_range_logic(question)
    if year_range:
        print("===========================================")
        return year_range["start_date"], year_range["end_date"]

    # Standalone year → interpret as FY
    year = extract_year_from_text(q)
    if year:
        return f"01-04-{year}", f"31-03-{year+1}"
 
    # -------------------------------------------------------
    # 8️⃣ Default → current FY
    # -------------------------------------------------------
    fy = get_current_fy()
    return f"01-04-{fy}", f"31-03-{fy+1}"

def detect_zero_null_patterns(question: str) -> Dict[str, Any]:
    """
    Detect patterns for zero/null value questions
    
    Examples:
    - "Are there users with targets but no actuals"
    - "Show users with zero actual"
    - "Users without any actual performance"
    - "Find users having targets but actual is zero"
    - "List users where actual is null"
    """
    
    q_lower = question.lower()
    
    zero_null_pattern = {
        "has_zero_null": False,
        "field": None,  # Which field is zero/null (actual, target, etc.)
        "condition": None,  # The condition type
        "inverse_field": None  # The field that should NOT be zero (e.g., target)
    }
    
    # Patterns for "no", "zero", "null", "without", "missing"

    zero_indicators = [
        r'no(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',
        r'null(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',

        r'without(?:\s+\w+){0,3}\s+(?:any\s+)?(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',
        r'missing(?:\s+\w+){0,3}\s+(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)',

        r'(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)\s+'
        r'(?:is|are|equals?)\s+(?:zero|0|null)',

        r'(actual|target|leads|appointments|completion|booked|resolved|sr|achieved|service request)\s+'
        r'(?:is|are)\s+(?:not\s+present|absent|empty)',
    ]

    
    for pattern in zero_indicators:
        match = re.search(pattern, q_lower)
        if match:
            field = match.group(1)
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = field
            zero_null_pattern["condition"] = "equals_zero"
            break

    is_zero = ["zero", "no ", "without ", "haven't ", "didn't ", "not any"]
    has_zero = any(z in q_lower for z in is_zero)
    if has_zero:

        field_detected = False
        # Special handling: "zero actual of sr"
        if  not field_detected and ("book" in q_lower or "booking" in q_lower or "booked" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "booked"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True
            
        
        if not field_detected and ("complete" in q_lower or "completion" in q_lower or "completed" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "completion"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True
        
        if not field_detected and ("resolve" in q_lower or "resolved" in q_lower or "resolution" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "resolved"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True
        
        if not field_detected and ("qualif" in q_lower or "ql" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "qualified"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True

        if not field_detected and ("leads" in q_lower or "lead" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "lead"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True
        
        if not field_detected and ("target" in q_lower):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "target"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True

        if not field_detected and any(z in q_lower for z in ["sr","service","case","service request","service requests"]):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "sr"
            zero_null_pattern["condition"] = "equals_zero"
            field_detected = True

        if not field_detected and any(z in q_lower for z in ["appintment","appintments"]):
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = "appointment"
            zero_null_pattern["condition"] = "equals_zero" 
            field_detected = True

    
    # Patterns for "but" constructions: "with X but no Y"
    # This means: X > 0 AND Y = 0
    but_patterns = [
        r'with\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|null)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)',
        r'having\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|null)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)',
        r'(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)\s+but(?:\s+\w+){0,3}\s+(?:no|not|zero|without|missing)(?:\s+\w+){0,3}\s+(targets?|actuals?|leads?|appointments?|completion|booked|resolved|sr)'
    ]
    
    for pattern in but_patterns:
        match = re.search(pattern, q_lower)
        if match:
            inverse_field = match.group(1).rstrip('s')  # Remove plural 's'
            zero_field = match.group(2).rstrip('s')
            
            zero_null_pattern["has_zero_null"] = True
            zero_null_pattern["field"] = zero_field
            zero_null_pattern["inverse_field"] = inverse_field
            zero_null_pattern["condition"] = "inverse_zero"
            break
    
    return zero_null_pattern



def resolve_user_from_tokens(query: str, df: pd.DataFrame) -> List[str]:
    """
    Resolve multiple users mentioned in a query using token overlap scoring.
    Returns list of matched user names.
    """

    if "user_name" not in df.columns:
        return []

    tokens = re.findall(r"[A-Za-z]+", query.lower())
    if not tokens:
        return []

    resolved = []

    for user in df["user_name"].dropna().unique():
        user_tokens = user.lower().split()

        # Count overlaps
        overlap = sum(1 for t in tokens if t in user_tokens)

        # Require at least one strong signal (first name or last name)
        if overlap >= 1:
            resolved.append((user, overlap))

    # Sort by confidence
    resolved.sort(key=lambda x: x[1], reverse=True)

    # Return only names
    return [u for u, _ in resolved if u != 'Service Request Queue']

def make_json_safe(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    else:
        return obj
    
def parse_question_intent(query: str) -> dict:
    q = query.lower()

    intent = {
        "metric": None,
        "action": "show",
        "aggregation": None,
        "limit": None,
        "threshold": None,
        "condition": None,
        "raw_query": q,
        "zero_filters": [],
    }

    # Detect metric
    for metric, meta in DATA_DICTIONARY.items():
        if any(k in q for k in meta["keywords"]):
            intent["metric"] = metric
            break

    intent["zero_null"] = detect_zero_null_patterns(q)

    # Ranking
    if any(w in q for w in ["top", "highest", "best", "most"]):
        intent["action"] = "top"
        intent["aggregation"] = "max"

    elif any(w in q for w in ["bottom", "lowest", "worst", "least"]):
        intent["action"] = "bottom"
        intent["aggregation"] = "min"

    # Average
    elif any(w in q for w in ["average", "avg", "mean"]):
        intent["aggregation"] = "avg"

    # Compare
    elif any(w in q for w in ["compare", "vs", "target"]):
        intent["action"] = "compare"

    # Rate / percentage
    if any(w in q for w in ["rate", "percentage", "%","achievement"]) :
        intent["aggregation"] = "rate"
    # gap
    if any(w in q for w in ["gap", "difference", "diff", "variance", "delta"]):
        intent["aggregation"] = "gap"

    # Top / Bottom N
    match = re.search(r"\b(top|bottom)\s+(\d+)", q)
    if match:
        intent["limit"] = int(match.group(2))
    elif intent["action"] in ["top", "bottom"]:
        intent["limit"] = 10  # default FIX

    # Threshold extraction (50%, above 30%, etc.)
    threshold_match = re.search(r"(\d+)\s*%", q)
    if threshold_match:
        intent["threshold"] = int(threshold_match.group(1))

    intent["condition"] = parse_advanced_conditions(q)

    return intent

def parse_advanced_conditions(q: str) -> dict | None:

    left = "actual"
    right = "target"

    if "target" in q and "actual" in q:
        if q.index("target") < q.index("actual"):
            left, right = "target", "actual"
        else:
            left, right = "actual", "target"
            
    if any(w in q for w in [
        "less than or equal",
        "less than equal",
        "at most",
        "no more than",
        "up to",
        "did not exceed",
        "not exceeded",
        "not exceed",
        "within target",
        "not achieved",
        "not achieve"
        "not met",
        "not hit",
        "not reached"
    ]):
        return {"op": "<=", "left": left, "right": right}
    
    if any(w in q for w in [
        "achieved",
        "met",
        "hit",
        "reached"
    ]):
        return {"op": ">=", "left": left, "right": right}

    if any(w in q for w in ["exceeded", "greater than", "above","highest"]):
        return {"op": ">", "left": left, "right": right}

    if any(w in q for w in ["failed", "below", "less than",'under',"missed","lowest"]):
        return {"op": "<", "left": left, "right": right}

    if any(w in q for w in ["equal", "exactly"]):
        return {"op": "=", "left": left, "right": right}

    return None


# -------------------------------------------------
# Metric-specific column resolver
# -------------------------------------------------
def resolve_columns(metric: str, raw_q: str):
    mappings = {
        "qualified_leads": {
            "actual": "ql_actual",
            "target": "ql_target",
            "rate": "ql_rate",
            "total":"total_leads",
            "rate_type": "achievement",
            "label": "QL Achievement Rate (%)"
        },
        "appointments": {
            "booked_actual": "appt_booked_actual",
            "booked_target": "appt_booked_target",
            "completion_actual": "appt_completion_actual",
            "completion_target": "appt_completion_target",
            "completion_base": "appt_booked_actual"  # used for rate
            
        },
        "service_requests": {
            "actual": "resolved_actual",
            "target": "sr_target",
            "rate": "sr_rate",
            "total":"total_sr",
            "rate_type": "achievement",
            "label": "Service Request Resolution Rate (%)",
        }
    }

    metric_map = mappings.get(metric, {})

    # If appointment → decide booked vs completion
    if metric == "appointments":
        q_lower = raw_q.lower()
        if any(w in q_lower for w in ["booked", "book", "booking"]):
            return {
                "actual": metric_map["booked_actual"],
                "target": metric_map["booked_target"],
                "rate_type": "achievement",
                "rate": "booked_rate",
                "total":"total_activities",
                "label": "Appointment Booking Achievement (%)"
            }
        else:
            return {
                "actual": metric_map["completion_actual"],
                "target": metric_map["completion_target"],
                "base": metric_map["completion_base"],
                "rate": "completion_rate",
                "rate_type": "efficiency",
                "total":"total_activities",
                "label": "Appointment Completion Rate (%)"
                
            }

    return metric_map

def resolve_output_columns(metric: str, raw_q: str, available_cols: list) -> list:
    """
    Determine EXACTLY which columns to return based on what the user asked for.
    Uses resolve_columns() for precise metric-aware column names.

    Intent detection:
    - only 'actual'      → actual column only
    - only 'target'      → target column only
    - only 'total'       → total column only
    - 'rate'/%/achieve   → actual + rate col (computed)
    - 'gap'/'difference' → actual + target + gap col (computed)
    - 'actual vs target' → actual + target
    - generic/ambiguous  → actual + target (safest default)

    user_name is ALWAYS included.
    Any extra computed columns (gap, rate, achievement_pct) passed in
    available_cols are preserved when relevant.
    """
    q = raw_q.lower()

    # Get the precise column names for this metric
    cols = resolve_columns(metric, q)

    actual_col  = cols.get("actual")
    target_col  = cols.get("target")
    total_col   = cols.get("total")

    # Intent flags
    wants_actual   = any(w in q for w in ["actual"])
    wants_target   = any(w in q for w in ["target"])
    wants_total    = any(w in q for w in ["total", "count"])
    wants_rate     = any(w in q for w in ["rate", "%", "percentage", "achievement", "achieve"])
    wants_gap      = any(w in q for w in ["gap", "difference", "diff", "variance", "delta"])
    wants_compare  = any(w in q for w in ["compare", "vs", "versus"])

    # Computed columns that may already be in available_cols
    computed_rate_cols = [c for c in available_cols if c.endswith("_rate") or c.endswith("_achievement_pct") or c.endswith("_pct")]
    computed_gap_cols  = [c for c in available_cols if c == "gap"]

    # Always anchor with user_name
    selected = ["user_name"] if "user_name" in available_cols else []

    # --- Strict single-intent cases ---
    if wants_actual and not wants_target and not wants_compare and not wants_rate and not wants_gap:
        if actual_col and actual_col in available_cols:
            selected.append(actual_col)

    elif wants_target and not wants_actual and not wants_compare and not wants_rate and not wants_gap:
        if target_col and target_col in available_cols:
            selected.append(target_col)

    elif wants_total and not wants_actual and not wants_target and not wants_compare and not wants_rate and not wants_gap:
        if total_col and total_col in available_cols:
            selected.append(total_col)

    elif wants_rate and not wants_gap:
        # rate queries: actual + target + computed rate col
        if actual_col and actual_col in available_cols:
            selected.append(actual_col)
        if target_col and target_col in available_cols:
            selected.append(target_col)
        selected += computed_rate_cols

    elif wants_gap and not wants_rate:
        # gap queries: actual + target + gap col
        if actual_col and actual_col in available_cols:
            selected.append(actual_col)
        if target_col and target_col in available_cols:
            selected.append(target_col)
        selected += computed_gap_cols

    elif wants_compare or (wants_actual and wants_target):
        # explicit comparison: actual + target + any achievement pct
        if actual_col and actual_col in available_cols:
            selected.append(actual_col)
        if target_col and target_col in available_cols:
            selected.append(target_col)
        # selected += computed_rate_cols

    else:
        # Generic / ambiguous → actual + target (safest, cleanest default)
        if actual_col and actual_col in available_cols:
            selected.append(actual_col)
        if target_col and target_col in available_cols:
            selected.append(target_col)

    print(selected,wants_target)
    print(available_cols)
    # De-duplicate while preserving order
    seen = set()
    ordered = []
    for c in selected:
        if c not in seen and c in available_cols:
            seen.add(c)
            ordered.append(c)
    print(ordered)

    # Safety fallback: if only user_name (or nothing), return actual+target at minimum
    if len(ordered) <= 1:
        fallback = ["user_name"]
        if actual_col and actual_col in available_cols:
            fallback.append(actual_col)
        if target_col and target_col in available_cols:
            fallback.append(target_col)
        ordered = [c for c in fallback if c in available_cols] or available_cols

    return ordered


def apply_zero_null_filter(
    df: pd.DataFrame,
    zero_null: Dict[str, Any],
    cols: Dict[str, str],
    metric: str
) -> pd.DataFrame:
    """
    Apply zero/null filtering to the DataFrame
    
    Handles patterns like:
    - "users with targets but no actuals" → target > 0 AND actual = 0
    - "users with zero actual" → actual = 0
    - "users without any target" → target = 0
    """
    
    condition_type = zero_null.get("condition")
    field = zero_null.get("field")
    inverse_field = zero_null.get("inverse_field")
    
    if condition_type == "equals_zero":
        # Simple zero check: field = 0
        print(field, metric)
        col_name = normalize_field_name(field, metric)
        print(col_name,df.columns,'==========col_name========')
        if col_name in df.columns:
            df = df[df[col_name] == 0]
            print(f"Applied zero filter: {col_name} = 0")
    
    elif condition_type == "inverse_zero":
        # Complex pattern: inverse_field > 0 AND field = 0
        zero_col = normalize_field_name(field, metric)
        inverse_col = normalize_field_name(inverse_field, metric)
        
        if zero_col in df.columns and inverse_col in df.columns:
            df = df[(df[inverse_col] > 0) & (df[zero_col] == 0)]
            print(f"Applied inverse zero filter: {inverse_col} > 0 AND {zero_col} = 0")
    
    return df

def normalize_field_name(field: str, metric: str) -> str:
    """
    Normalize field names from natural language to database columns
    
    Examples:
    - "actual" + "qualified_leads" → "ql_actual"
    - "target" + "booked" → "appt_booked_target"
    """
    
    field_lower = field.lower().rstrip('s')  # Remove plural
    field_lower = field.lower().replace(' ','_')  # Remove plural
    print(field_lower,'=field_lower')
    print(metric,'=metric')
    
    # Map natural language to column prefixes
    field_map = {
        "actual": "_actual",
        "target": "_target",
        "lead": "total_leads",
        "qualified":"_actual",
        "appointment": "total_activities",
        "completion": "_actual",
        "booked": "_actual",
        "resolved": "_actual",
        "sr": "total_sr",
        'service_request':"total_sr"
    }
    
    # Metric-specific mappings
    if metric == "qualified_leads":
        if "actual" in field_lower or "qualified" in field_lower:
            return "ql_actual"
        elif "target" in field_lower:
            return "ql_target"
        elif "lead" in field_lower:
            return "total_leads"
    
    elif metric == "booked":
        if "actual" in field_lower or "booked" in field_lower:
            return "appt_booked_actual"
        elif "target" in field_lower:
            return "appt_booked_target"
        elif "appointment" in field_lower:
            return "total_activities"
    
    elif metric == "completion":
        if "actual" in field_lower or "completion" in field_lower:
            return "appt_completion_actual"
        elif "target" in field_lower:
            return "appt_completion_target"
        elif "appointment" in field_lower:
            return "total_activities"    
    
    elif metric == "service_requests":
        if "actual" in field_lower or "resolved" in field_lower:
            return "resolved_actual"
        elif "target" in field_lower:
            return "sr_target"
        elif "sr" in field_lower or "service_request" in field_lower:
            return "total_sr"
    
    # Fallback
    suffix = field_map.get(field_lower, "_actual")
    return f"{metric}{suffix}"


def resolve_question(parsed_data: dict, intent: dict):
    metric = intent.get("metric")
    action = intent.get("action")
    aggregation = intent.get("aggregation")
    raw_q = intent.get("raw_query", "").lower()
    zero_filters = intent.get("zero_filters", [])
    zero_null = intent.get("zero_null", {})

    if not metric:
        return {"response": "Please specify what you want to analyze."}

    config = DATA_DICTIONARY.get(metric)
    if not config:
        return {"response": "Metric not supported."}

    # -------------------------------------------------
    # Load dataframe
    # -------------------------------------------------
    report_key = config["report"]
    df = pd.DataFrame(parsed_data.get(report_key, {}).get("rows", []))

    if df.empty:
        return {"response": "No data available."}

    df.columns = [c.lower() for c in df.columns]

    required_cols = [c.lower() for c in config["columns"] if c.lower() in df.columns]
    base_cols = ["user_name"] + required_cols
    df = df[base_cols]
    
    users = resolve_user_from_tokens(raw_q,df)
    # print(users)
    if users:
        df = df[df["user_name"].isin(users)]
    else:
        df = df.copy()

    cols = resolve_columns(metric, raw_q)

    if zero_null and zero_null.get("has_zero_null"):
        if metric == "appointments":
            if any(w in raw_q for w in ["booked", "book", "booking"]) :
                sub_metric = "booked"
            else:
                sub_metric = "completion"
        elif metric == "qualified_leads":
            sub_metric = "qualified_leads"
        elif metric == "service_requests":
            sub_metric = "service_requests"
        df = apply_zero_null_filter(df, zero_null, cols, sub_metric)

    # -------------------------------------------------
    # RATE / ACHIEVEMENT LOGIC
    # -------------------------------------------------
    if aggregation == "rate":
        if not cols or "rate_type" not in cols:
            return {"response": "Rate calculation not supported for this metric."}

        rate_col = cols["rate"]
        rate_type = cols["rate_type"]

        if rate_type == "achievement":
            actual_col = cols["actual"]
            target_col = cols["target"]

            df = df[df[target_col] > 0]
            df[rate_col] = ((df[actual_col] / df[target_col]) * 100).round(2)

        elif rate_type == "efficiency":
            actual_col = cols["actual"]
            base_col = cols["base"]

            df = df[df[base_col] > 0]
            df[rate_col] = ((df[actual_col] / df[base_col]) * 100).round(2)

            df[rate_col] = df[rate_col].clip(upper=100)
        else:
            return {"response": "Unknown rate type."}

        # Threshold filtering
        if intent.get("threshold") is not None and intent.get("condition"):
            op = intent["condition"]["op"]
            threshold = intent["threshold"]

            if op == ">":
                df = df[df[rate_col] > threshold]
            elif op == "<":
                df = df[df[rate_col] < threshold]
            elif op == "=":
                df = df[df[rate_col] == threshold]
            elif op == ">=":
                df = df[df[rate_col] >= threshold]
            elif op == "<=":
                df = df[df[rate_col] <= threshold]

        # Top / Bottom
        if action in ["top", "bottom"]:
            limit = intent.get("limit", 5)
            df = df.sort_values(
                rate_col,
                ascending=(action == "bottom")
            ).head(limit)

        rate_output_cols = resolve_output_columns(metric, raw_q, df.columns.tolist())
        # always keep the rate column itself
        if rate_col in df.columns and rate_col not in rate_output_cols:
            rate_output_cols.append(rate_col)
        return {
            "metric": metric,
            "kpi": cols["label"],
            "count": len(df),
            "data": df.sort_values(rate_col, ascending=False)[rate_output_cols]
                    .to_dict(orient="records")
        }

    # -------------------------------------------------
    # Gap / Diffrence LOGIC
    # -------------------------------------------------
    if aggregation == "gap":
        if not cols:
            return {"response": "Rate calculation not supported for this metric."}

        actual_col = cols["actual"]
        target_col = cols["target"]

        # Exclude zero targets for rate logic
        df = df[df[target_col] > 0]
        df = df[df[actual_col] > 0]


        df["gap"] = (df[target_col] - df[actual_col]).abs().round(2)

        if intent.get("condition") is not None:
            op = intent["condition"]["op"]
            left_col = actual_col if intent["condition"]["left"] == "actual" else target_col
            right_col = target_col if intent["condition"]["right"] == "target" else actual_col

            if op == ">":
                df = df[df[left_col] > df[right_col]]
            elif op == "<":
                df = df[df[left_col] < df[right_col]]
            elif op == "=":
                df = df[df[left_col] == df[right_col]]
            elif op == ">=":
                df = df[df[left_col] >= df[right_col]]
            elif op == "<=":
                df = df[df[left_col] <= df[right_col]]


        # Top / Bottom
        if action in ["top", "bottom"]:
            limit = intent.get("limit", 5)
            df = df.sort_values(
                "gap",
                ascending=(action == "bottom")
            ).head(limit)

        gap_output_cols = resolve_output_columns(metric, raw_q, df.columns.tolist())
        # always keep the gap column itself
        if "gap" in df.columns and "gap" not in gap_output_cols:
            gap_output_cols.append("gap")
        return {
            "metric": metric,
            "kpi": cols["label"],
            "count": len(df),
            "data": df[gap_output_cols].to_dict(orient="records")
        }

    # -------------------------------------------------
    # TARGET vs ACTUAL (COMPARE)
    # -------------------------------------------------
    if action == "compare" and cols:
        actual_col = cols["actual"]
        target_col = cols["target"]
        df = df[df[target_col] > 0]
        achievement_pct = actual_col.replace("_actual","")
        achievement_pct = achievement_pct+"_achievement_pct"
        df[achievement_pct] = (
            df[actual_col] / df[target_col].replace(0, np.nan) * 100
        ).round(1)

        if intent.get("condition"):
            op = intent["condition"]["op"]
            left_col = actual_col if intent["condition"]["left"] == "actual" else target_col
            right_col = target_col if intent["condition"]["right"] == "target" else actual_col

            if op == ">":
                df = df[df[left_col] > df[right_col]]
            elif op == "<":
                df = df[df[left_col] < df[right_col]]
            elif op == "=":
                df = df[df[left_col] == df[right_col]]
            elif op == ">=":
                df = df[df[left_col] >= df[right_col]]
            elif op == "<=":
                df = df[df[left_col] <= df[right_col]]

        # print(df)
        compare_cols = resolve_output_columns(metric, raw_q, df.columns.tolist())
        # always keep the achievement pct column if it was just added
        # if achievement_pct in df.columns and achievement_pct not in compare_cols:
        #     compare_cols.append(achievement_pct)
        return {
            "metric": metric,
            "comparison": "actual vs target",
            "data": df[compare_cols].to_dict(orient="records")
        }

    # -------------------------------------------------
    # TOP / BOTTOM (NORMAL, NON-RATE)
    # -------------------------------------------------
    if action in ["top", "bottom"]:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        if "actual" in raw_q:
            sort_col = next((c for c in numeric_cols if c.endswith("_actual")))
        elif "target" in raw_q:
            sort_col = next((c for c in numeric_cols if c.endswith("_target")))
        else:
            sort_col = next(
                        (c for c in numeric_cols if c.endswith("_actual")),
                        numeric_cols[0]
                    )
        limit = intent.get("limit", 5)

        df = df.sort_values(
            sort_col,
            ascending=(action == "bottom")
        ).head(limit)

        return {
            "metric": metric,
            "sorted_by": sort_col,
            "limit": limit,
            "data": df[resolve_output_columns(metric, raw_q, df.columns.tolist())].to_dict(orient="records")
        }

    # -------------------------------------------------
    # AVERAGE
    # -------------------------------------------------
    if aggregation == "avg":
        return {
            "metric": metric,
            "average": df.select_dtypes(include="number")
                        .mean()
                        .round(2)
                        .to_dict()
        }
    
    if intent.get("condition") is not None:
        if not cols:
            return {"response": "Rate calculation not supported for this metric."}
        
        actual_col = cols["actual"]
        target_col = cols["target"]

        op = intent["condition"]["op"]
        if op == ">":
            df = df[df[actual_col] > df[target_col]]
        elif op == "<":
            df = df[df[actual_col] < df[target_col]]
        elif op == "=":
            df = df[df[actual_col] == df[target_col]]
        elif op == ">=":
            df = df[df[actual_col] >= df[target_col]]
        elif op == "<=":
            df = df[df[actual_col] <= df[target_col]]  

            
    # -------------------------------------------------
    # DEFAULT FALLBACK
    # -------------------------------------------------
    output_cols = resolve_output_columns(metric, raw_q, df.columns.tolist())
    df = df[output_cols]
    return {
        "metric": metric,
        "count": len(df),
        "data": df.to_dict(orient="records")
    }

def sort_funnel_by_numeric_desc(data: Any) -> Any:
    """
    Sort nested funnel dictionaries by numeric values in descending order.
    Works with various funnel output structures:
    - Dict[user, Dict[metric, value]] -> Sorts users by total/first numeric metric
    - Dict[project, Dict[user, Dict[metric, value]]] -> Sorts projects and users
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
                sorted_data[key] = sort_funnel_by_numeric_desc(data[key])
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
            
            return dict(sorted(data.items(), key=get_sort_key))
    return data


@app.post("/ask")
async def ask_performance_question(request: QueryRequest):
    token = get_salesforce_token()
    if not token:
        raise HTTPException(500, "Authentication failed")
    

    qoq_result = detect_qoq(request.query)
    if qoq_result:
        quarters = qoq_result["quarters"]
        analysis_type = qoq_result["type"]
        fy = qoq_result["fy"]
        all_quarter_results = []

        print(f"Detected {analysis_type.upper()} for FY{fy} → Running {len(quarters)} quarters")

        for qtr in quarters:
            print(qtr,'quarter details --------------------')
            start_date = qtr["start_date"]
            end_date = qtr["end_date"]
            label = qtr["quarter"]

            start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
            end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

            raw_data = fetch_combined_reports(start_date, end_date, token)
            if not raw_data:
                raise HTTPException(500, "Failed to fetch CRM data")

            parsed = parse_report_data(raw_data)
            intent = parse_question_intent(request.query)
            response = resolve_question(parsed, intent)

            result = {
                    "quarter": label,
                    "period": f"{start_date} to {end_date}",
                    "CRE_GRE": sort_funnel_by_numeric_desc(response)
                }
            all_quarter_results.append(result)

            # ✅ Extract only rows
            if isinstance(response, dict) and "data" in response:
                all_quarter_results.extend(response["data"])
            elif isinstance(response, list):
                all_quarter_results.extend(response)
            # Return structured QoQ or Quarter-wise response
        totals = calculate_master_totals(all_quarter_results)

        return {
            "status": "success",
            "analysis_type": analysis_type,
            "fy": f"FY{fy}",
            "totals":totals,
            "data": sort_funnel_by_numeric_desc(all_quarter_results)
        }
    elif (yoy_result := detect_yoy(request.query)):
        periods = yoy_result["periods"]
        all_year_results = []

        print(f"Running YoY analysis → {len(periods)} years")

        for period in periods:
            start_date = period["start_date"]
            end_date = period["end_date"]
            label = period["year"]
            start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
            end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

            raw_data = fetch_combined_reports(start_date, end_date, token)
            if not raw_data:
                raise HTTPException(500, "Failed to fetch CRM data")

            parsed = parse_report_data(raw_data)
            intent = parse_question_intent(request.query)
            response = resolve_question(parsed, intent)


            result = {
                    "quarter": label,
                    "period": f"{start_date} to {end_date}",
                    "funnel": sort_funnel_by_numeric_desc(response)
                }
            all_year_results.append(result)

            # ✅ Extract only rows
            if isinstance(response, dict) and "data" in response:
                all_year_results.extend(response["data"])
            elif isinstance(response, list):
                all_year_results.extend(response)

            # Return structured QoQ or Quarter-wise response
        totals = calculate_master_totals(all_year_results)
        return {
            "status": "success",
            "analysis_type": "year_on_year",
            "fy": "Last 3 completed financial years",
            "totals":totals,
            "data": sort_funnel_by_numeric_desc(all_year_results)
        }
    
    elif (mom_result := detect_mom(request.query)):
        periods = mom_result["periods"]
        all_month_results = []

        print(f"Running MoM analysis → {len(periods)} Months")

        for period in periods:
            start_date = period["start_date"]
            end_date = period["end_date"]
            label = period["label"]

            start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
            end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

            raw_data = fetch_combined_reports(start_date, end_date, token)
            if not raw_data:
                raise HTTPException(500, "Failed to fetch CRM data")

            parsed = parse_report_data(raw_data)
            intent = parse_question_intent(request.query)
            response = resolve_question(parsed, intent)

            
            all_month_results.append({
                "month": label,
                "period": period["period"],
                "funnel": sort_funnel_by_numeric_desc(response)
            })
            
            # ✅ Extract only rows
            if isinstance(response, dict) and "data" in response:
                all_month_results.extend(response["data"])
            elif isinstance(response, list):
                all_month_results.extend(response)

        totals = calculate_master_totals(all_month_results)
        return {
            "status": "success",
            "analysis_type": "month_on_month",
            "comparison": "Last 6 months + Current MTD",
            "totals":totals,
            "data": all_month_results  # May → Jun → Jul → Aug → Sep → Oct → Nov (MTD)
        }
    elif (mul_year := parse_multi_year_date(request.query)):

        filters_label = ""
        yearly_results = []

        intent = parse_question_intent(request.query)

        for start_str, end_str in mul_year:

            start_dt = datetime.strptime(start_str, "%d-%m-%Y")
            end_dt = datetime.strptime(end_str, "%d-%m-%Y")

            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")

            raw_data = fetch_combined_reports(start_date, end_date, token)
            if not raw_data:
                continue

            parsed = parse_report_data(raw_data)
            response = resolve_question(parsed, intent)

            # Extract rows
            if isinstance(response, dict) and "data" in response:
                rows = response["data"]
            elif isinstance(response, list):
                rows = response
            else:
                rows = []

            totals = calculate_master_totals(rows)

            # Create FY label
            fy_label = f"FY {start_dt.year}-{str(end_dt.year)[-2:]}"

            yearly_results.append({
                "label": fy_label,
                "totals": totals,
                "data": sort_funnel_by_numeric_desc(rows)
            })
        return {
            "status": "success",
            
            "filter": filters_label,
            "data": yearly_results
        }
    elif (mul_month := parse_multi_month_date(request.query)):

        filters_label = ""
        combined_response = []

        for start_str, end_str in mul_month:

            start_date = datetime.strptime(start_str, "%d-%m-%Y").strftime("%Y-%m-%d")
            end_date = datetime.strptime(end_str, "%d-%m-%Y").strftime("%Y-%m-%d")

            print(start_date,end_date)
            raw_data = fetch_combined_reports(start_date, end_date, token)
            if not raw_data:
                continue

            parsed = parse_report_data(raw_data)
            intent = parse_question_intent(request.query)
            response = resolve_question(parsed, intent)
            
            # ✅ Extract only rows
            if isinstance(response, dict) and "data" in response:
                combined_response.extend(response["data"])
            elif isinstance(response, list):
                combined_response.extend(response)
        totals = calculate_master_totals(combined_response)
        return {
            "status": "success",
            "filter": filters_label,
            "totals": totals,
            "data": sort_funnel_by_numeric_desc(combined_response)
        }
    else:

        start_date, end_date  = parse_dates_from_question(request.query)

        start_date = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")

        raw_data = fetch_combined_reports(start_date, end_date, token)
        if not raw_data:
            raise HTTPException(500, "Failed to fetch CRM data")
        
        parsed = parse_report_data(raw_data)
        
        intent = parse_question_intent(request.query)
        
        response = resolve_question(parsed, intent)

        # Extract rows
        if isinstance(response, dict) and "data" in response:
            rows = response["data"]
        elif isinstance(response, list):
            rows = response
        else:
            rows = []
        
        totals = calculate_master_totals(rows)
        return make_json_safe({
            "intent": intent,
            "period": {
                "start_date": start_date,
                "end_date": end_date
            },
            "totals":totals,
            "data": sort_funnel_by_numeric_desc(rows)
        })

# Run with: uvicorn your_file:app --reload