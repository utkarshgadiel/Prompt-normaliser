"""One business clock for planning; independent of the hosting machine's TZ."""
from datetime import datetime, timedelta, timezone

# India does not observe daylight saving time. No OS tzdata dependency needed.
BUSINESS_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def business_today():
    return datetime.now(BUSINESS_TZ).date()
