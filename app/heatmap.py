from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db, EventRecord
from app.models import HeatmapResponse
from app.timewindow import get_metric_time_filter
from datetime import datetime, timezone

router = APIRouter()

# Spec: flag data_confidence=False when fewer than this many sessions in window
_MIN_SESSIONS_FOR_CONFIDENCE = 20


@router.get("/stores/{store_id}/heatmap")
async def get_heatmap(
    store_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Return zone visit frequency and dwell heatmap data, normalised 0-100.

    Returns per-zone:
    - visit_count: raw number of ZONE_ENTER events
    - visit_score: visit_count normalised 0-100 (100 = busiest zone)
    - total_dwell_ms: sum of ZONE_DWELL dwell_ms values
    - avg_dwell_ms: mean dwell across ZONE_DWELL events
    - dwell_score: avg_dwell_ms normalised 0-100 (100 = longest dwell zone)

    data_confidence is False when unique sessions < 20 (low-data warning).
    All queries filter is_staff=False.
    Data is scoped to METRIC_WINDOW (all | today | last24h).
    """
    start, end, date_label = get_metric_time_filter(store_id, db)

    time_filter = []
    if start is not None and end is not None:
        time_filter.append(EventRecord.timestamp >= start)
        time_filter.append(EventRecord.timestamp <= end)

    enter_result = await db.execute(
        select(
            EventRecord.zone_id,
            func.count(EventRecord.id)
        ).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ZONE_ENTER",
                EventRecord.is_staff == False,
                EventRecord.zone_id.isnot(None),
                *time_filter
            )
        ).group_by(EventRecord.zone_id)
    )
    enter_rows = enter_result.all()

    dwell_result = await db.execute(
        select(
            EventRecord.zone_id,
            func.sum(EventRecord.dwell_ms),
            func.count(EventRecord.id)
        ).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ZONE_DWELL",
                EventRecord.is_staff == False,
                EventRecord.zone_id.isnot(None),
                *time_filter
            )
        ).group_by(EventRecord.zone_id)
    )
    dwell_rows = dwell_result.all()
    dwell_map = {row[0]: (row[1] or 0, row[2] or 0) for row in dwell_rows}

    # Count unique sessions to determine data_confidence
    session_result = await db.execute(
        select(func.count(func.distinct(EventRecord.visitor_id))).where(
            and_(
                EventRecord.store_id == store_id,
                EventRecord.event_type == "ENTRY",
                EventRecord.is_staff == False,
                *time_filter
            )
        )
    )
    session_count = session_result.scalar() or 0

    raw_zones = {}
    for zone_id, visit_count in enter_rows:
        if zone_id:
            total_dwell, dwell_count = dwell_map.get(zone_id, (0, 0))
            avg_dwell = int(total_dwell / dwell_count) if dwell_count > 0 else 0
            raw_zones[zone_id] = {
                "visit_count": visit_count,
                "total_dwell_ms": int(total_dwell),
                "avg_dwell_ms": avg_dwell,
            }

    # Normalise visit_count and avg_dwell_ms to 0-100 per spec
    max_visits = max((z["visit_count"] for z in raw_zones.values()), default=0)
    max_dwell = max((z["avg_dwell_ms"] for z in raw_zones.values()), default=0)

    zones = {}
    for zone_id, z in raw_zones.items():
        zones[zone_id] = {
            "visit_count": z["visit_count"],
            "visit_score": round(z["visit_count"] / max_visits * 100) if max_visits > 0 else 0,
            "total_dwell_ms": z["total_dwell_ms"],
            "avg_dwell_ms": z["avg_dwell_ms"],
            "dwell_score": round(z["avg_dwell_ms"] / max_dwell * 100) if max_dwell > 0 else 0,
        }

    computed_date = date_label
    if not computed_date:
        max_ts_result = await db.execute(
            select(func.max(EventRecord.timestamp)).where(
                EventRecord.store_id == store_id
            )
        )
        max_ts = max_ts_result.scalar()
        computed_date = max_ts.date().isoformat() if max_ts else datetime.now(timezone.utc).date().isoformat()

    return HeatmapResponse(
        store_id=store_id,
        date=computed_date,
        zones=zones,
        data_confidence=session_count >= _MIN_SESSIONS_FOR_CONFIDENCE,
        computed_at=datetime.now(timezone.utc).isoformat()
    )