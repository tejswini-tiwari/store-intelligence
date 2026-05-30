from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert
from app.database import get_db, EventRecord
from app.models import IngestRequest, IngestResponse, StoreEvent
import redis.asyncio as aioredis
import json, os, logging

router = APIRouter()

_redis_client = None


async def get_redis():
    """
    Return async Redis client using REDIS_URL env var.
    If Redis is unavailable return None — never crash ingest
    because Redis is optional (used only for live dashboard).
    """
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            try:
                _redis_client = aioredis.from_url(redis_url)
                await _redis_client.ping()
            except Exception:
                _redis_client = None
    return _redis_client


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(
    payload: IngestRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    FULL IMPLEMENTATION:

    1. For each event in payload.events:

       a. Check if event_id already exists in DB:
          SELECT id FROM events WHERE event_id = ?
          If exists → add to skipped count, continue

       b. Try to insert EventRecord:
          Map StoreEvent fields to EventRecord columns.
          For metadata fields:
            queue_depth = event.metadata.queue_depth
            sku_zone = event.metadata.sku_zone
            session_seq = event.metadata.session_seq
            raw_metadata = event.metadata.model_dump()

          On IntegrityError (duplicate event_id race condition):
            → skip silently, add to skipped count

          On any other exception:
            → add to errors list as
              f"event_id {event.event_id}: {str(e)}"
            → continue processing remaining events

       c. If inserted successfully:
          → publish to Redis channel store:{store_id}:events
            payload: event.model_dump_json()
          → add to ingested count

    2. await db.commit() after processing all events

    3. Return IngestResponse(
         ingested=ingested_count,
         skipped=skipped_count,
         errors=error_list
       )

    IMPORTANT: This endpoint must NEVER return 5xx for
    malformed individual events. Partial success is correct.
    Only return 5xx if the entire DB is unavailable.
    """
    ingested_count = 0
    skipped_count = 0
    error_list = []

    redis = await get_redis()

    for event in payload.events:
        result = await db.execute(
            select(EventRecord.id).where(EventRecord.event_id == event.event_id)
        )
        if result.scalar_one_or_none() is not None:
            skipped_count += 1
            continue

        try:
            record = EventRecord(
                event_id=event.event_id,
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                event_type=event.event_type.value,
                timestamp=event.timestamp,
                zone_id=event.zone_id,
                dwell_ms=event.dwell_ms,
                is_staff=event.is_staff,
                confidence=event.confidence,
                queue_depth=event.metadata.queue_depth,
                sku_zone=event.metadata.sku_zone,
                session_seq=event.metadata.session_seq,
                raw_metadata=event.metadata.model_dump()
            )
            db.add(record)

            await db.flush()

            if redis:
                try:
                    channel = f"store:{event.store_id}:events"
                    await redis.publish(channel, event.model_dump_json())
                except Exception:
                    pass

            ingested_count += 1

        except Exception as e:
            if "UNIQUE constraint failed" in str(e) or "IntegrityError" in str(e.__class__.__name__):
                skipped_count += 1
                continue
            else:
                error_list.append(f"event_id {event.event_id}: {str(e)}")
                continue

    await db.commit()

    return IngestResponse(
        ingested=ingested_count,
        skipped=skipped_count,
        errors=error_list
    )