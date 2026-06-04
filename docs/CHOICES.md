# Architecture Choices

## Decision 1: Detection Model Selection

### Options Considered

Option A — YOLOv8m (chosen):
  - Real-time capable at 15fps on CPU/GPU
  - Person detection (class 0) out of the box
  - ultralytics library: single pip install
  - ByteTrack integration via supervision library
  - Pre-trained on COCO — no fine-tuning needed for persons

Option B — RT-DETR:
  - Transformer-based, higher accuracy on small objects
  - Slower inference — borderline for real-time at 15fps on CPU
  - Less mature ecosystem than YOLOv8 for tracking integration

Option C — MediaPipe Person Detection:
  - Designed for mobile/edge, very fast
  - Less accurate on partial occlusions
  - Limited to single-person or small group scenarios
  - No native ByteTrack integration

Option D — GPT-4V / Claude Vision (VLM):
  - Could classify zones, detect staff, describe scenes
  - API latency: 2-5 seconds per frame — unusable for video
  - Cost: ~$0.01 per frame × 15fps × 1200s = $180 per clip
  - Evaluated for zone classification only (not frame-by-frame)

### What AI Suggested

The AI suggested starting with YOLOv8n (nano) for speed and
upgrading to YOLOv8m if accuracy was insufficient. It also
suggested evaluating RT-DETR for the partial occlusion cases
in the billing clip specifically.

### What I Chose and Why

YOLOv8m. The medium variant is the right balance point:
nano sacrifices too much accuracy on partial occlusions and
group entries (2-4 people simultaneously), which are explicitly
listed as challenge edge cases. The extra inference time vs
nano is acceptable given the 15fps clip rate. RT-DETR was
rejected because ByteTrack integration adds friction and
the accuracy improvement for this use case is marginal.

### Trade-offs

Accuracy vs speed: YOLOv8m at ~20ms/frame on GPU (or ~120ms
on CPU) leaves headroom for tracking and zone logic at 15fps.
If deployed on edge hardware (Jetson Nano), quantized YOLOv8n
would be the right choice with a minor accuracy penalty.

### VLM Usage

Evaluated GPT-4V for staff detection and zone classification.
Result: too slow for frame-by-frame use. Used it instead for
a one-time prompt to generate the HSV colour range configuration
format for staff uniform detection. Prompt was:
"Given a retail staff uniform that is black, what HSV
range in OpenCV (0-180 hue scale) would reliably detect it
while excluding dark customer clothing?"
The output (H: 100-130, S: 50-255, V: 50-255) became the
default in .env.example. This is the right use of a VLM:
one-time configuration generation, not per-frame inference.

## Decision 2: Event Schema Design

### Options Considered

Option A — Flat JSON with all fields at top level:
  All metadata fields (queue_depth, sku_zone, session_seq)
  at the root level of the event object.

Option B — Nested metadata object (chosen):
  Core fields at root. Optional analytics fields in a
  metadata sub-object. Null fields allowed.

Option C — Event type-specific schemas:
  Separate schema per EventType — ENTRY events have
  different fields than BILLING_QUEUE_JOIN events.

### What AI Suggested

The AI initially suggested Option C (type-specific schemas)
arguing it would give stronger type safety per event type.
It also suggested making confidence optional with a default
of 1.0 for events where confidence is not applicable.

### What I Chose and Why

Option B — nested metadata. Reasons:

1. Single schema means single validation path in emit.py.
   Type-specific schemas would require a discriminated union
   and significantly more complex validation logic.

2. The metadata sub-object cleanly separates the fields that
   are always present (event identity, timestamp, visitor_id)
   from the fields that are conditionally relevant
   (queue_depth only for billing events, sku_zone for zone
   events). Null fields are explicit about absence.

3. Overrode the AI on confidence: confidence must always be
   present and never default to 1.0. A default of 1.0 would
   misrepresent uncertain detections as certain. The spec
   explicitly says do not suppress low-confidence events —
   the corollary is: always record the actual confidence.

4. session_seq in metadata gives downstream consumers the
   ability to reconstruct the ordered event sequence per
   visitor without sorting by timestamp (which has clock
   skew risk in distributed setups).

### Trade-offs

The nested metadata approach means some fields are always
present but usually null (e.g. queue_depth on ENTRY events).
This wastes a small amount of storage but keeps the ingestion
and query paths simple. At 40 stores × ~100 events/hour the
storage overhead is negligible.

## Decision 3: API Storage Architecture

### Options Considered

Option A — SQLite (dev) + PostgreSQL (prod) via SQLAlchemy:
  Same ORM models, swap DATABASE_URL env var. Zero dev setup.

Option B — TimescaleDB:
  PostgreSQL extension with automatic time partitioning.
  Fast range queries on timestamp. Hypertable per store.

Option C — InfluxDB:
  Native time-series database. Line protocol for writes.
  Flux query language. Separate ecosystem from SQLAlchemy.

Option D — Redis only:
  Store events in Redis sorted sets (score = timestamp).
  Fast real-time queries. No persistence guarantees.

### What AI Suggested

The AI recommended TimescaleDB given the time-series nature
of the event data, noting that automatic partitioning would
make the /metrics and /anomalies queries significantly faster
at scale. It also suggested Redis as a read-through cache
for the /metrics endpoint.

### What I Chose and Why

Option A — SQLAlchemy with SQLite/PostgreSQL. Reasons:

1. Event volume does not justify TimescaleDB complexity.
   5 stores × ~100 events/hour = 500 events/hour = 12,000
   events/day. A standard PostgreSQL table with a composite
   index on (store_id, timestamp, event_type) handles this
   trivially — query times under 10ms.

2. TimescaleDB requires a separate Docker image and
   extension setup. For a challenge submission where
   docker compose up must work with zero manual steps,
   this is unnecessary friction.

3. SQLAlchemy async models work identically for SQLite
   (dev/test) and PostgreSQL (prod) via DATABASE_URL.
   This gives fast test cycles (SQLite, in-memory possible)
   and production readiness (PostgreSQL) from the same code.

4. Partially followed the AI suggestion: Redis IS used,
   but as a pub/sub channel for the live dashboard, not
   as a cache. Caching /metrics would risk serving stale
   data — the spec requires real-time, not cached.

### Trade-offs

The original /funnel implementation loaded every event for a
store/day into Python and ran an O(visitors × billing_joins ×
pos) nested loop for purchase correlation — memory and CPU both
scaled with raw event volume, which is exactly what breaks at 40
live stores. This has since been rewritten: each funnel stage is
a SQL `COUNT(DISTINCT visitor_id)` that executes in the database
(no event rows shipped to the app), and purchase correlation
fetches only the small billing-join subset and bisects sorted POS
timestamps — O(joins × log(pos)). Re-entry dedup is now inherent
in DISTINCT rather than hand-rolled.

The next bottleneck at scale is no longer memory but repeated
COUNT(DISTINCT) scans on every funnel/metrics request. The
production answer is a per-session aggregate table updated on each
ingest batch, so reads become single-row lookups. I deliberately
did not build that for the challenge dataset (it adds write-path
complexity and a migration) but the SQL-aggregation step makes
the endpoint correct and bounded today, and the materialised-view
path is a clean follow-on.

### A note on the live-ingestion API choice

A second API-architecture decision sits alongside storage: how
footage enters the system. `POST /pipeline/process` accepts an
uploaded clip and runs detect.py in an out-of-process subprocess,
rather than blocking the request thread or standing up a
Celery/broker worker. The full options-considered / what-AI-
suggested / what-I-chose write-up for this lives in
DESIGN.md → AI-Assisted Decisions #3. The store_id is supplied by
the caller on every request, so no store identifier is hardcoded
anywhere and a brand-new store appears automatically via
`GET /stores` once its first event lands.