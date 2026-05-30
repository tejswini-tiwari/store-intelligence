from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, check_db_health, EventRecord
from datetime import datetime, timezone, timedelta

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    FULL IMPLEMENTATION:

    1. db_status: call check_db_health() from database.py
       "connected" if True, "unavailable" if False

    2. last_event_per_store: dict[str, str]
       SELECT store_id, MAX(timestamp) FROM events GROUP BY store_id
       Return as {"STORE_BLR_002": "2026-03-03T14:22:10Z", ...}
       Empty dict if no events yet.

    3. stale_feeds: list[str]
       For each store_id in last_event_per_store:
       If (now - last_event_timestamp) > 10 minutes → add to stale list
       Return list of store_ids with stale feeds.
       Empty list if all feeds are fresh.

    4. service status:
       "healthy" if db_status = "connected" AND stale_feeds is empty
       "degraded" if db_status = "connected" BUT stale_feeds not empty
       "down" if db_status = "unavailable"

    5. If db_status = "unavailable":
       Return HTTP 503 with body:
       {
           "service": "down",
           "db_status": "unavailable",
           "error": "Database connection failed",
           "last_event_per_store": {},
           "stale_feeds": []
       }

    6. Normal response:
    {
        "service": "healthy" | "degraded",
        "db_status": "connected",
        "last_event_per_store": {...},
        "stale_feeds": [...],
        "checked_at": "<current UTC ISO timestamp>"
    }
    """
    db_healthy = await check_db_health()

    if not db_healthy:
        return JSONResponse(
            status_code=503,
            content={
                "service": "down",
                "db_status": "unavailable",
                "error": "Database connection failed",
                "last_event_per_store": {},
                "stale_feeds": []
            }
        )

    last_event_result = await db.execute(
        select(EventRecord.store_id, func.max(EventRecord.timestamp)).group_by(EventRecord.store_id)
    )
    last_event_rows = last_event_result.all()

    last_event_per_store = {}
    for store_id, max_ts in last_event_rows:
        if max_ts:
            last_event_per_store[store_id] = max_ts.isoformat()

    now = datetime.now(timezone.utc)
    stale_feeds = []
    for store_id, last_ts_str in last_event_per_store.items():
        last_ts = datetime.fromisoformat(last_ts_str).replace(tzinfo=timezone.utc)
        if (now - last_ts) > timedelta(minutes=10):
            stale_feeds.append(store_id)

    if stale_feeds:
        service_status = "degraded"
    else:
        service_status = "healthy"

    return {
        "service": service_status,
        "db_status": "connected",
        "last_event_per_store": last_event_per_store,
        "stale_feeds": stale_feeds,
        "checked_at": now.isoformat()
    }