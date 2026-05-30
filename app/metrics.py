from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, case
from app.database import get_db, EventRecord, POSTransaction
from app.models import MetricsResponse
from datetime import datetime, timezone, timedelta
import logging

router = APIRouter()


def today_utc_range():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


@router.get("/stores/{store_id}/metrics")
async def get_metrics(
    store_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Return real-time metrics for today (UTC midnight to now).
    ALL queries must filter is_staff = false.

    Compute these fields:

    1. unique_visitors: int
       COUNT DISTINCT visitor_id WHERE event_type = 'ENTRY'
       AND is_staff = false AND store_id = ? AND date = today

    2. conversion_rate: float
       Logic:
       - Get all BILLING_QUEUE_JOIN events for today
         (is_staff=false) with their timestamps
       - Get all POS transactions for today from POSTransaction table
       - For each billing event: check if there is a POS transaction
         for same store_id within the NEXT 5 minutes
       - converted_visitors = distinct visitor_ids that had
         at least one such match
       - conversion_rate = converted_visitors / unique_visitors
       - If unique_visitors = 0 → return 0.0 (never divide by zero)
       - If no POS data exists → return 0.0 (not null, not error)

    3. avg_dwell_per_zone: dict[str, float]
       For each zone_id: AVG(dwell_ms) from ZONE_DWELL events
       WHERE is_staff=false AND store_id=? AND date=today
       Return as {"SKINCARE": 45200.0, "BILLING": 12300.0, ...}
       Empty dict if no dwell events.

    4. current_queue_depth: int
       Latest metadata queue_depth value from
       BILLING_QUEUE_JOIN events for this store today.
       Return 0 if no such events.

    5. abandonment_rate: float
       COUNT(BILLING_QUEUE_ABANDON) / COUNT(BILLING_QUEUE_JOIN)
       for this store today, is_staff=false.
       Return 0.0 if no queue joins.

    Return as JSON dict with these exact keys:
    {
        "store_id": store_id,
        "date": "2026-03-03",
        "unique_visitors": 0,
        "conversion_rate": 0.0,
        "avg_dwell_per_zone": {},
        "current_queue_depth": 0,
        "abandonment_rate": 0.0,
        "computed_at": "<current UTC ISO timestamp>"
    }
    """
    start, end = today_utc_range()
    today_date = start.date().isoformat()

    unique_visitors_result = await db.execute(
        select(func.count(func.distinct(EventRecord.visitor_id))).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ENTRY",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        )
    )
    unique_visitors = unique_visitors_result.scalar() or 0

    billing_joins_result = await db.execute(
        select(EventRecord.visitor_id, EventRecord.timestamp).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        )
    )
    billing_joins = billing_joins_result.all()

    pos_transactions_result = await db.execute(
        select(POSTransaction.timestamp).where(
            and_(
                POSTransaction.store_id == store_id,
                POSTransaction.timestamp >= start,
                POSTransaction.timestamp <= end
            )
        )
    )
    pos_timestamps = [row[0] for row in pos_transactions_result.all()]

    converted_visitors = set()
    for visitor_id, join_time in billing_joins:
        for pos_time in pos_timestamps:
            if join_time <= pos_time <= join_time + timedelta(minutes=5):
                converted_visitors.add(visitor_id)
                break

    conversion_rate = len(converted_visitors) / unique_visitors if unique_visitors > 0 else 0.0

    dwell_result = await db.execute(
        select(EventRecord.zone_id, func.avg(EventRecord.dwell_ms)).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ZONE_DWELL",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end,
                EventRecord.zone_id.isnot(None)
            )
        ).group_by(EventRecord.zone_id)
    )
    dwell_rows = dwell_result.all()
    avg_dwell_per_zone = {row[0]: row[1] for row in dwell_rows} if dwell_rows else {}

    latest_queue_result = await db.execute(
        select(EventRecord.queue_depth).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        ).order_by(EventRecord.timestamp.desc()).limit(1)
    )
    latest_queue = latest_queue_result.scalar() or 0

    abandon_count_result = await db.execute(
        select(func.count(EventRecord.id)).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_ABANDON",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        )
    )
    abandon_count = abandon_count_result.scalar() or 0

    join_count_result = await db.execute(
        select(func.count(EventRecord.id)).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        )
    )
    join_count = join_count_result.scalar() or 0

    abandonment_rate = abandon_count / join_count if join_count > 0 else 0.0

    return {
        "store_id": store_id,
        "date": today_date,
        "unique_visitors": unique_visitors,
        "conversion_rate": round(conversion_rate, 4),
        "avg_dwell_per_zone": {k: round(v, 2) for k, v in avg_dwell_per_zone.items()},
        "current_queue_depth": latest_queue,
        "abandonment_rate": round(abandonment_rate, 4),
        "computed_at": datetime.now(timezone.utc).isoformat()
    }