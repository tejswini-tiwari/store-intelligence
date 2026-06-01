from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, EventRecord, POSTransaction
from app.timewindow import get_metric_time_filter, get_conversion_window_minutes
from datetime import datetime, timezone, timedelta

router = APIRouter()


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

    pos_time_filter = []
    if start is not None and end is not None:
        pos_time_filter.append(POSTransaction.timestamp >= start)
        pos_time_filter.append(POSTransaction.timestamp <= end)

    all_event_filter = [EventRecord.store_id == store_id, EventRecord.is_staff == False]
    all_event_filter.extend(time_filter)

    events_result = await db.execute(
        select(EventRecord.visitor_id, EventRecord.event_type, EventRecord.timestamp, EventRecord.zone_id).where(
            and_(*all_event_filter)
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

    pos_filter = [POSTransaction.store_id == store_id]
    if pos_time_filter:
        pos_filter.extend(pos_time_filter)
    pos_transactions_result = await db.execute(
        select(POSTransaction.timestamp).where(and_(*pos_filter))
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
                    if join_time <= pos_time <= join_time + timedelta(minutes=conv_window):
                        purchase_visitors.add(visitor_id)
                        break

    entry_count = len(entry_visitors)
    zone_visit_count = len(zone_visitors)
    billing_queue_count = len(billing_visitors)
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