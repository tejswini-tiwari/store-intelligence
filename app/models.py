# Pydantic models for Store Intelligence API.

from enum import Enum
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import uuid


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class IngestRequest(BaseModel):
    events: list[StoreEvent] = Field(max_length=500)


class IngestResponse(BaseModel):
    ingested: int
    skipped: int
    errors: list[str]


class AnomalyItem(BaseModel):
    anomaly_type: str
    severity: Severity
    store_id: str
    zone_id: Optional[str]
    detected_at: datetime
    suggested_action: str
    details: dict = {}


class MetricsResponse(BaseModel):
    # fields to be added in metrics.py session
    pass


class FunnelResponse(BaseModel):
    # fields to be added in metrics.py session
    pass


class HeatmapResponse(BaseModel):
    # fields to be added in metrics.py session
    pass