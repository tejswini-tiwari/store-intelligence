from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.database import get_db, EventRecord
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional

router = APIRouter()


class EventItem(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: Optional[str]
    dwell_ms: int
    is_staff: bool
    confidence: float


class EventsResponse(BaseModel):
    store_id: str
    events: list[EventItem]
    total: int
    page: int
    page_size: int


@router.get("/stores/{store_id}/events")
async def get_events(
    store_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event_type (e.g. ENTRY, ZONE_ENTER)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Results per page"),
    db: AsyncSession = Depends(get_db)
):
    """
    Return paginated event list for a store, newest first.

    - Filters is_staff=False
    - Filters by store_id
    - Supports optional event_type filter
    - Data scoped to METRIC_WINDOW (all | today | last24h)
    - Results ordered by timestamp DESC (newest first)
    - Uses cursor-style offset pagination
    """
    from app.timewindow import get_metric_time_filter

    start, end, date_label = get_metric_time_filter(store_id, db)

    time_filter = []
    if start is not None and end is not None:
        time_filter.append(EventRecord.timestamp >= start)
        time_filter.append(EventRecord.timestamp <= end)

    base_filter = [
        EventRecord.store_id == store_id,
        EventRecord.is_staff == False
    ]
    if event_type:
        base_filter.append(EventRecord.event_type == event_type)
    base_filter.extend(time_filter)

    count_result = await db.execute(
        select(EventRecord.id).where(and_(*base_filter))
    )
    total = len(count_result.all())

    offset = (page - 1) * page_size
    events_result = await db.execute(
        select(EventRecord).where(and_(*base_filter))
        .order_by(EventRecord.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = events_result.scalars().all()

    events = [
        EventItem(
            event_id=row.event_id,
            store_id=row.store_id,
            camera_id=row.camera_id,
            visitor_id=row.visitor_id,
            event_type=row.event_type,
            timestamp=row.timestamp,
            zone_id=row.zone_id,
            dwell_ms=row.dwell_ms,
            is_staff=row.is_staff,
            confidence=row.confidence
        )
        for row in rows
    ]

    return EventsResponse(
        store_id=store_id,
        events=events,
        total=total,
        page=page,
        page_size=page_size
    )