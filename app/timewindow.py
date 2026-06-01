from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

METRIC_WINDOW = os.getenv("METRIC_WINDOW", "all").strip().lower()
CONVERSION_WINDOW_MINUTES = int(os.getenv("CONVERSION_WINDOW_MINUTES", "5"))


def get_metric_time_filter(store_id: str, db_session) -> tuple[Optional[datetime], Optional[datetime], str]:
    """
    Returns (start, end, date_label) based on METRIC_WINDOW env var.

    - "all": returns (None, None, date_label_from_max_event_ts)
    - "today": returns (midnight UTC today, now UTC, date_label=today's date)
    - "last24h": returns (now - 24h, now, date_label=today's date)

    date_label is based on max event timestamp for the store when window="all",
    otherwise based on start of the window period.
    """
    now = datetime.now(timezone.utc)

    if METRIC_WINDOW == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        date_label = start.date().isoformat()
        return start, end, date_label

    elif METRIC_WINDOW == "last24h":
        start = now - timedelta(hours=24)
        end = now
        date_label = now.date().isoformat()
        return start, end, date_label

    else:
        return None, None, ""


def get_conversion_window_minutes() -> int:
    return CONVERSION_WINDOW_MINUTES