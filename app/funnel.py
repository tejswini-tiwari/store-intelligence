from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, EventRecord, POSTransaction
from datetime import datetime, timezone, timedelta

router = APIRouter()


def today_utc_range():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


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
      within 5 minutes (same logic as conversion_rate).

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
    start, end = today_utc_range()
    today_date = start.date().isoformat()

    events_result = await db.execute(
        select(EventRecord.visitor_id, EventRecord.event_type, EventRecord.timestamp, EventRecord.zone_id).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False,
                EventRecord.timestamp >= start,
                EventRecord.timestamp <= end
            )
        )
    )
    events = events_result.all()

    visitor_events = {}
    for visitor_id, event_type, timestamp, zone_id in events:
        if visitor_id not in visitor_events:
            visitor_events[visitor_id] = []
        visitor_events[visitor_id].append((event_type, timestamp, zone_id))

    reentry_visitors = set()
    for visitor_id, evts in visitor_events.items():
        event_types = [e[0] for e in evts]
        if "REENTRY" in event_types:
            reentry_visitors.add(visitor_id)

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

    entry_visitors = set()
    zone_visitors = set()
    billing_visitors = set()
    purchase_visitors = set()

    for visitor_id, evts in visitor_events.items():
        event_types = [e[0] for e in evts]
        timestamps = [e[1] for e in evts]
        zone_ids = [e[2] for e in evts]

        if "ENTRY" in event_types:
            entry_visitors.add(visitor_id)

        has_zone_enter = False
        for event_type, ts, zone_id in evts:
            if event_type == "ZONE_ENTER" and zone_id and zone_id != "BILLING":
                has_zone_enter = True
                break
        if has_zone_enter:
            zone_visitors.add(visitor_id)

        billing_join_timestamps = []
        for event_type, ts, zone_id in evts:
            if event_type == "BILLING_QUEUE_JOIN":
                billing_join_timestamps.append(ts)

        if billing_join_timestamps:
            billing_visitors.add(visitor_id)

            for join_time in billing_join_timestamps:
                for pos_time in pos_timestamps:
                    if join_time <= pos_time <= join_time + timedelta(minutes=5):
                        purchase_visitors.add(visitor_id)
                        break

    entry_count = len(entry_visitors)
    zone_visit_count = len(zone_visitors)
    billing_queue_count = len(billing_visitors)
    purchase_count = len(purchase_visitors)

    entry_to_zone_dropoff = ((entry_count - zone_visit_count) / entry_count * 100) if entry_count > 0 else 0.0
    zone_to_billing_dropoff = ((zone_visit_count - billing_queue_count) / zone_visit_count * 100) if zone_visit_count > 0 else 0.0
    billing_to_purchase_dropoff = ((billing_queue_count - purchase_count) / billing_queue_count * 100) if billing_queue_count > 0 else 0.0

    return {
        "store_id": store_id,
        "date": today_date,
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