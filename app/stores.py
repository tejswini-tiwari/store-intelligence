from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db, EventRecord

router = APIRouter()


@router.get("/stores")
async def list_stores(db: AsyncSession = Depends(get_db)):
    """
    Return every store_id the API has ever seen an event for, newest
    activity first. This is what the live dashboard polls to discover
    stores dynamically — store_ids are NOT hardcoded anywhere. Whoever
    ingests events (or feeds footage via /pipeline/process) defines the
    store_id, and it shows up here automatically.

    Response:
    {
      "stores": [
        {"store_id": "ST1008", "event_count": 1423,
         "last_event_at": "2026-04-10T14:43:10+00:00"}
      ]
    }
    """
    result = await db.execute(
        select(
            EventRecord.store_id,
            func.count(EventRecord.id),
            func.max(EventRecord.timestamp),
        ).group_by(EventRecord.store_id)
    )
    rows = result.all()

    stores = [
        {
            "store_id": store_id,
            "event_count": count,
            "last_event_at": max_ts.isoformat() if max_ts else None,
        }
        for store_id, count, max_ts in rows
    ]
    # Most recently active store first; None timestamps sink to the bottom.
    stores.sort(key=lambda s: s["last_event_at"] or "", reverse=True)

    return {"stores": stores}
