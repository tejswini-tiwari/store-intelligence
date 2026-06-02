from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, EventRecord, POSTransaction
from app.models import AnomalyItem, Severity
from app.timewindow import get_metric_time_filter, get_conversion_window_minutes, METRIC_WINDOW
from datetime import datetime, timezone, timedelta
import logging

router = APIRouter()


async def get_conversion_rate_for_range(db: AsyncSession, store_id: str,
                                        date_start: datetime, date_end: datetime,
                                        conv_window: int) -> float:
    unique_visitors_result = await db.execute(
        select(func.count(func.distinct(EventRecord.visitor_id))).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ENTRY",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= date_start,
                EventRecord.timestamp <= date_end
            )
        )
    )
    unique_visitors = unique_visitors_result.scalar() or 0

    if unique_visitors == 0:
        return 0.0

    billing_joins_result = await db.execute(
        select(EventRecord.visitor_id, EventRecord.timestamp).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= date_start,
                EventRecord.timestamp <= date_end
            )
        )
    )
    billing_joins = billing_joins_result.all()

    pos_transactions_result = await db.execute(
        select(POSTransaction.timestamp).where(
            and_(
                POSTransaction.store_id == store_id,
                POSTransaction.timestamp >= date_start,
                POSTransaction.timestamp <= date_end
            )
        )
    )
    pos_timestamps = [row[0] for row in pos_transactions_result.all()]

    converted_visitors = set()
    for visitor_id, join_time in billing_joins:
        for pos_time in pos_timestamps:
            if join_time <= pos_time <= join_time + timedelta(minutes=conv_window):
                converted_visitors.add(visitor_id)
                break

    return len(converted_visitors) / unique_visitors if unique_visitors > 0 else 0.0


@router.get("/stores/{store_id}/anomalies")
async def get_anomalies(
    store_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Detect and return active anomalies.
    Never return null — return empty list if none.

    Check these 3 anomaly types:

    1. BILLING_QUEUE_SPIKE:
       Get latest queue_depth from BILLING_QUEUE_JOIN events
       in the last 10 minutes for this store.
       If queue_depth > 10 -> CRITICAL
       If queue_depth > 5 -> WARN
       If queue_depth <= 5 -> no anomaly
       suggested_action: "Deploy additional billing staff immediately"
       details: {"current_queue_depth": N}

    2. CONVERSION_DROP:
       Today's conversion_rate (reuse logic from metrics.py).
       7-day rolling average: compute conversion_rate for each
       of the past 7 days, take the mean.
       If today < 7day_avg * 0.5 -> CRITICAL
       If today < 7day_avg * 0.8 -> WARN
       Only emit if 7day_avg > 0 (skip if no historical data).
       suggested_action:
         "Review floor staff positioning and zone signage"
       details: {
         "today_rate": X,
         "seven_day_avg": Y,
         "drop_pct": Z
       }

    3. DEAD_ZONE (one anomaly per dead zone found):
       For each zone_id that appears in events for this store:
       Check if there are zero ZONE_ENTER events in the
       last 30 minutes for that zone.
       If yes -> INFO anomaly
       suggested_action:
         f"Check camera feed and foot traffic for zone {zone_id}"
       details: {"zone_id": zone_id, "minutes_since_activity": N}

       Skip this check for zones that have never had any events
       (only flag zones that WERE active but went silent).

    Return list of AnomalyItem objects (from app/models.py).
    Each must have: anomaly_type, severity, store_id,
                    zone_id (null for non-zone anomalies),
                    detected_at (current UTC),
                    suggested_action, details
    """
    anomalies = []
    now = datetime.now(timezone.utc)
    conv_window = get_conversion_window_minutes()

    time_window_start, time_window_end = None, None
    if METRIC_WINDOW in ("today", "last24h"):
        time_window_start, time_window_end, _ = await get_metric_time_filter(store_id, db)

    ref_now_result = await db.execute(
        select(func.max(EventRecord.timestamp)).where(
            EventRecord.store_id == store_id
        )
    )
    ref_now = ref_now_result.scalar() or now
    ten_minutes_ago = ref_now - timedelta(minutes=10)
    thirty_minutes_ago = ref_now - timedelta(minutes=30)

    latest_queue_result = await db.execute(
        select(EventRecord.queue_depth, EventRecord.timestamp).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= ten_minutes_ago,
                EventRecord.timestamp <= ref_now
            )
        ).order_by(EventRecord.timestamp.desc()).limit(1)
    )
    latest_queue_row = latest_queue_result.first()

    if latest_queue_row:
        queue_depth = latest_queue_row[0]
        if queue_depth is not None:
            if queue_depth > 10:
                anomalies.append(AnomalyItem(
                    anomaly_type="BILLING_QUEUE_SPIKE",
                    severity=Severity.CRITICAL,
                    store_id=store_id,
                    zone_id=None,
                    detected_at=ref_now,
                    suggested_action="Deploy additional billing staff immediately",
                    details={"current_queue_depth": queue_depth}
                ))
            elif queue_depth > 5:
                anomalies.append(AnomalyItem(
                    anomaly_type="BILLING_QUEUE_SPIKE",
                    severity=Severity.WARN,
                    store_id=store_id,
                    zone_id=None,
                    detected_at=ref_now,
                    suggested_action="Deploy additional billing staff immediately",
                    details={"current_queue_depth": queue_depth}
                ))

    start, end = None, None
    if METRIC_WINDOW in ("today", "last24h"):
        start, end, _ = await get_metric_time_filter(store_id, db)
        today_rate = await get_conversion_rate_for_range(db, store_id, start, end, conv_window)

        rates_7day = []
        for i in range(1, 8):
            day_start = start - timedelta(days=i)
            day_end = end - timedelta(days=i)
            rate = await get_conversion_rate_for_range(db, store_id, day_start, day_end, conv_window)
            rates_7day.append(rate)

        valid_rates = [r for r in rates_7day if r > 0]
        if valid_rates:
            seven_day_avg = sum(valid_rates) / len(valid_rates)
            if today_rate < seven_day_avg * 0.5:
                drop_pct = ((seven_day_avg - today_rate) / seven_day_avg * 100) if seven_day_avg > 0 else 0
                anomalies.append(AnomalyItem(
                    anomaly_type="CONVERSION_DROP",
                    severity=Severity.CRITICAL,
                    store_id=store_id,
                    zone_id=None,
                    detected_at=ref_now,
                    suggested_action="Review floor staff positioning and zone signage",
                    details={
                        "today_rate": round(today_rate, 4),
                        "seven_day_avg": round(seven_day_avg, 4),
                        "drop_pct": round(drop_pct, 2)
                    }
                ))
            elif today_rate < seven_day_avg * 0.8:
                drop_pct = ((seven_day_avg - today_rate) / seven_day_avg * 100) if seven_day_avg > 0 else 0
                anomalies.append(AnomalyItem(
                    anomaly_type="CONVERSION_DROP",
                    severity=Severity.WARN,
                    store_id=store_id,
                    zone_id=None,
                    detected_at=ref_now,
                    suggested_action="Review floor staff positioning and zone signage",
                    details={
                        "today_rate": round(today_rate, 4),
                        "seven_day_avg": round(seven_day_avg, 4),
                        "drop_pct": round(drop_pct, 2)
                    }
                ))

    zone_activity_result = await db.execute(
        select(EventRecord.zone_id, func.count(EventRecord.id)).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ZONE_ENTER",
                EventRecord.is_staff == False,
                EventRecord.timestamp >= thirty_minutes_ago,
                EventRecord.timestamp <= ref_now
            )
        ).group_by(EventRecord.zone_id)
    )
    active_zones_in_window = {row[0] for row in zone_activity_result.all() if row[0]}

    all_zones_result = await db.execute(
        select(EventRecord.zone_id).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ZONE_ENTER",
                EventRecord.is_staff == False,
                EventRecord.zone_id.isnot(None)
            )
        )
    )
    all_zones = {row[0] for row in all_zones_result.all() if row[0]}

    for zone_id in all_zones:
        if zone_id != "BILLING" and zone_id not in active_zones_in_window:
            inactive_since_result = await db.execute(
                select(func.max(EventRecord.timestamp)).where(
                    and_(
                        EventRecord.store_id == store_id,
                        EventRecord.event_type == "ZONE_ENTER",
                        EventRecord.zone_id == zone_id
                    )
                )
            )
            last_activity = inactive_since_result.scalar()
            minutes_since = int((ref_now - last_activity).total_seconds() / 60) if last_activity else 30
            anomalies.append(AnomalyItem(
                anomaly_type="DEAD_ZONE",
                severity=Severity.INFO,
                store_id=store_id,
                zone_id=zone_id,
                detected_at=ref_now,
                suggested_action=f"Check camera feed and foot traffic for zone {zone_id}",
                details={"zone_id": zone_id, "minutes_since_activity": minutes_since}
            ))

    return anomalies