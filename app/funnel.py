from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, EventRecord, POSTransaction
from app.timewindow import get_metric_time_filter, get_conversion_window_minutes
from datetime import datetime, timezone, timedelta
from bisect import bisect_left

router = APIRouter()


async def _count_distinct_visitors(db, filters):
    """COUNT(DISTINCT visitor_id) pushed to the DB — no event rows leave SQL."""
    result = await db.execute(
        select(func.count(func.distinct(EventRecord.visitor_id))).where(and_(*filters))
    )
    return result.scalar() or 0


@router.get("/stores/{store_id}/funnel")
async def get_funnel(
    store_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Conversion funnel. SESSION is the unit — not raw events.
    All queries: is_staff=false, store_id=?, today UTC.

    STEP 1 — Build session map:
    Get all events for this store today where is_staff=false.
    Group by visitor_id.

    For re-entry handling:
    A visitor_id that has a REENTRY event counts as ONE unique
    visitor across the whole funnel. Do not count them twice.

    STEP 2 — Compute funnel stages:

    entry_count:
      Count distinct visitor_ids that have at least one ENTRY event.

    zone_visit_count:
      Count distinct visitor_ids that have at least one
      ZONE_ENTER event (any zone except BILLING).

    billing_queue_count:
      Count distinct visitor_ids that have at least one
      BILLING_QUEUE_JOIN event.

    purchase_count:
      Count distinct visitor_ids where visitor had a
      BILLING_QUEUE_JOIN and a matching POS transaction
      within CONVERSION_WINDOW_MINUTES minutes (same logic as conversion_rate).

    STEP 3 — Compute drop-off percentages:
    entry_to_zone_dropoff:
      (entry_count - zone_visit_count) / entry_count * 100
    zone_to_billing_dropoff:
      (zone_visit_count - billing_queue_count) / zone_visit_count * 100
    billing_to_purchase_dropoff:
      (billing_queue_count - purchase_count) / billing_queue_count * 100

    All dropoffs: return 0.0 if denominator is 0.
    Round all percentages to 2 decimal places.

    Return:
    {
        "store_id": store_id,
        "date": "2026-03-03",
        "funnel": {
            "entry_count": 0,
            "zone_visit_count": 0,
            "billing_queue_count": 0,
            "purchase_count": 0
        },
        "drop_off_pct": {
            "entry_to_zone": 0.0,
            "zone_to_billing": 0.0,
            "billing_to_purchase": 0.0
        },
        "computed_at": "<current UTC ISO timestamp>"
    }
    """
    start, end, date_label = get_metric_time_filter(store_id, db)
    conv_window = get_conversion_window_minutes()

    time_filter = []
    if start is not None and end is not None:
        time_filter.append(EventRecord.timestamp >= start)
        time_filter.append(EventRecord.timestamp <= end)

    base_filter = [EventRecord.store_id == store_id, EventRecord.is_staff == False, *time_filter]

    # STAGE 1-3 — COUNT(DISTINCT visitor_id) per stage runs entirely in SQL.
    # Same visitor_id across re-entries dedupes automatically (DISTINCT).
    entry_count = await _count_distinct_visitors(
        db, [*base_filter, EventRecord.event_type == "ENTRY"]
    )
    zone_visit_count = await _count_distinct_visitors(
        db,
        [
            *base_filter,
            EventRecord.event_type == "ZONE_ENTER",
            EventRecord.zone_id.isnot(None),
            EventRecord.zone_id != "",
            EventRecord.zone_id != "BILLING",
        ],
    )
    billing_queue_count = await _count_distinct_visitors(
        db, [*base_filter, EventRecord.event_type == "BILLING_QUEUE_JOIN"]
    )

    # STAGE 4 — purchase correlation. Only the billing-join subset and POS
    # timestamps leave SQL (a small fraction of total events). Correlate with
    # bisect over sorted POS timestamps: O(joins · log(pos)) instead of the
    # previous O(visitors · joins · pos) nested scan.
    purchase_count = 0
    if billing_queue_count > 0:
        joins_result = await db.execute(
            select(EventRecord.visitor_id, EventRecord.timestamp).where(
                and_(*base_filter, EventRecord.event_type == "BILLING_QUEUE_JOIN")
            )
        )
        billing_joins = joins_result.all()

        pos_filter = [POSTransaction.store_id == store_id]
        if start is not None and end is not None:
            pos_filter.append(POSTransaction.timestamp >= start)
            # widen upper bound by the window so a join near `end` still matches
            pos_filter.append(POSTransaction.timestamp <= end + timedelta(minutes=conv_window))
        pos_result = await db.execute(
            select(POSTransaction.timestamp).where(and_(*pos_filter))
        )
        pos_timestamps = sorted(row[0] for row in pos_result.all())

        window = timedelta(minutes=conv_window)
        purchase_visitors = set()
        for visitor_id, join_time in billing_joins:
            if visitor_id in purchase_visitors:
                continue
            idx = bisect_left(pos_timestamps, join_time)
            if idx < len(pos_timestamps) and pos_timestamps[idx] <= join_time + window:
                purchase_visitors.add(visitor_id)
        purchase_count = len(purchase_visitors)

    entry_to_zone_dropoff = ((entry_count - zone_visit_count) / entry_count * 100) if entry_count > 0 else 0.0
    zone_to_billing_dropoff = ((zone_visit_count - billing_queue_count) / zone_visit_count * 100) if zone_visit_count > 0 else 0.0
    billing_to_purchase_dropoff = ((billing_queue_count - purchase_count) / billing_queue_count * 100) if billing_queue_count > 0 else 0.0

    computed_date = date_label
    if not computed_date:
        max_ts_result = await db.execute(
            select(func.max(EventRecord.timestamp)).where(
                EventRecord.store_id == store_id
            )
        )
        max_ts = max_ts_result.scalar()
        computed_date = max_ts.date().isoformat() if max_ts else datetime.now(timezone.utc).date().isoformat()

    return {
        "store_id": store_id,
        "date": computed_date,
        "funnel": {
            "entry_count": entry_count,
            "zone_visit_count": zone_visit_count,
            "billing_queue_count": billing_queue_count,
            "purchase_count": purchase_count
        },
        "drop_off_pct": {
            "entry_to_zone": round(entry_to_zone_dropoff, 2),
            "zone_to_billing": round(zone_to_billing_dropoff, 2),
            "billing_to_purchase": round(billing_to_purchase_dropoff, 2)
        },
        "computed_at": datetime.now(timezone.utc).isoformat()
    }