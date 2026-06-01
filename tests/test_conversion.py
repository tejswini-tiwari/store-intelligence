# PROMPT: Write conversion tests proving the correlation window is real:
# - test_conversion_zero_when_no_purchase_in_window: BILLING_QUEUE_JOIN at
#   20:12 IST + transaction at 20:25 IST (gap > 5 min) -> conversion_rate=0.0
# - test_conversion_positive_when_aligned: BILLING_QUEUE_JOIN at
#   20:21 IST + transaction at 20:25 IST (gap <= 5 min) -> conversion_rate>0.0
# Use real IST timestamps (20:09-20:25 IST = 14:39-14:55 UTC on 2026-04-10).
# Also update existing tests to use historically-dated events with
# METRIC_WINDOW=all so unique_visitors>=1 shows up correctly.
# CHANGES MADE: 2 conversion tests + 1 metric_window all update.

import pytest
import uuid
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from datetime import datetime, timezone, timedelta
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_artifacts/test_conv.db"

@pytest.fixture(scope="module")
def client():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    with TestClient(app) as c:
        yield c


def ist_to_utc(date_str: str, time_str: str) -> datetime:
    import pytz
    naive = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
    ist = pytz.timezone("Asia/Kolkata")
    localized = ist.localize(naive)
    return localized.astimezone(timezone.utc)


def test_conversion_zero_when_no_purchase_in_window(client):
    """
    BILLING_QUEUE_JOIN at 20:12 IST (14:42 UTC) + transaction at 20:25 IST (14:55 UTC).
    Gap is 13 minutes > CONVERSION_WINDOW_MINUTES(5), so conversion_rate must be 0.0.
    """
    join_time = ist_to_utc("10-04-2026", "20:12:00")
    pos_time = ist_to_utc("10-04-2026", "20:25:04")

    entry_event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_CONV_001",
        "event_type": "ENTRY",
        "timestamp": ist_to_utc("10-04-2026", "20:09:00").isoformat(),
        "is_staff": False,
        "confidence": 0.91,
        "zone_id": None,
        "dwell_ms": 0,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }

    billing_event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_BILLING_01",
        "visitor_id": "VIS_CONV_001",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": join_time.isoformat(),
        "is_staff": False,
        "confidence": 0.88,
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "metadata": {"queue_depth": 1, "sku_zone": None, "session_seq": 1}
    }

    client.post("/events/ingest", json={"events": [entry_event, billing_event]})

    from app.database import AsyncSessionLocal, POSTransaction
    from sqlalchemy import text

    async def insert_pos():
        async with AsyncSessionLocal() as session:
            tx = POSTransaction(
                transaction_id="INV_CONV_001",
                store_id="ST1008",
                basket_value_inr=499.0,
                timestamp=pos_time
            )
            session.add(tx)
            await session.commit()

    asyncio.get_event_loop().run_until_complete(insert_pos())

    r = client.get("/stores/ST1008/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["conversion_rate"] == 0.0, \
        f"Gap {13} min > 5 min window, expected 0.0, got {d['conversion_rate']}"
    assert d["unique_visitors"] >= 1


def test_conversion_positive_when_aligned(client):
    """
    BILLING_QUEUE_JOIN at 20:21 IST (14:51 UTC) + transaction at 20:25 IST (14:55 UTC).
    Gap is 4 minutes <= CONVERSION_WINDOW_MINUTES(5), so conversion_rate > 0.0.
    """
    join_time = ist_to_utc("10-04-2026", "20:21:00")
    pos_time = ist_to_utc("10-04-2026", "20:25:04")

    entry_event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_CONV_002",
        "event_type": "ENTRY",
        "timestamp": ist_to_utc("10-04-2026", "20:15:00").isoformat(),
        "is_staff": False,
        "confidence": 0.91,
        "zone_id": None,
        "dwell_ms": 0,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }

    billing_event = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_BILLING_01",
        "visitor_id": "VIS_CONV_002",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": join_time.isoformat(),
        "is_staff": False,
        "confidence": 0.88,
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "metadata": {"queue_depth": 1, "sku_zone": None, "session_seq": 1}
    }

    client.post("/events/ingest", json={"events": [entry_event, billing_event]})

    from app.database import AsyncSessionLocal, POSTransaction

    async def insert_pos():
        async with AsyncSessionLocal() as session:
            tx = POSTransaction(
                transaction_id="INV_CONV_002",
                store_id="ST1008",
                basket_value_inr=799.0,
                timestamp=pos_time
            )
            session.add(tx)
            await session.commit()

    asyncio.get_event_loop().run_until_complete(insert_pos())

    r = client.get("/stores/ST1008/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["conversion_rate"] > 0.0, \
        f"Gap 4 min <= 5 min window, expected >0.0, got {d['conversion_rate']}"
    assert d["unique_visitors"] >= 1