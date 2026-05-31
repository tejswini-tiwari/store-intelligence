from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert
from app.database import get_db, EventRecord
from app.models import IngestResponse, StoreEvent
from pydantic import BaseModel, Field, field_validator
from typing import Annotated
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


class IngestPayload(BaseModel):
    events: list[dict] = Field(max_length=500)


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(
    payload: IngestPayload,
    db: AsyncSession = Depends(get_db)
):
    """
    Ingest batch of events with idempotency and partial success support.
    Malformed individual events are logged as errors, not 5xx.
    """

    ingested_count = 0
    skipped_count = 0
    error_list = []

    redis_client = await get_redis()

    for event_dict in payload.events:
        event_id = event_dict.get("event_id", "unknown")

        result = await db.execute(
            select(EventRecord.id).where(EventRecord.event_id == event_id)
        )
        if result.scalar_one_or_none() is not None:
            skipped_count += 1
            continue

        try:
            event = StoreEvent.model_validate(event_dict)
        except Exception as validation_error:
            error_list.append(f"event_id {event_id}: {str(validation_error)}")
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
                raw_metadata=event.metadata.model_dump(),
            )
            db.add(record)

            await db.flush()

            if redis_client:
                try:
                    channel = f"store:{event.store_id}:events"
                    await redis_client.publish(channel, event.model_dump_json())
                except Exception:
                    pass

            ingested_count += 1

        except Exception as e:
            err_str = str(e)
            if "UNIQUE constraint failed" in err_str or "IntegrityError" in e.__class__.__name__:
                skipped_count += 1
                continue
            else:
                error_list.append(f"event_id {event.event_id}: {err_str}")
                continue

    await db.commit()

    return IngestResponse(
        ingested=ingested_count,
        skipped=skipped_count,
        errors=error_list,
    )