# Store Intelligence Pipeline — Architecture Design

## System Overview

The Store Intelligence Pipeline transforms raw CCTV footage from retail stores into actionable visitor analytics. CCTV clips (MP4, 1080p, 15fps) flow through a detection layer powered by YOLOv8m for person detection and ByteTrack for multi-object tracking, producing structured visitor events that are ingested into a FastAPI REST API and surfaced in real-time on a live Rich terminal dashboard. The north star metric driving the entire system is Offline Store Conversion Rate — the percentage of visitors who enter the billing queue and complete a POS transaction, which is the definitive measure of retail footfall monetization.

## Component Diagram

```
   Feed footage (you choose the store_id)
   pipeline/feed.py  or  curl -F video=@clip.mp4
              │
              ▼
┌─────────────────────────┐     ┌──────────────────┐
│  Intelligence API       │     │  Detection Layer │
│  POST /pipeline/process │────▶│  pipeline/       │
│  saves clip to          │ bg  │  detect.py       │
│  data/inbox, spawns ────┼────▶│  tracker.py      │
│  detection subprocess   │     │  emit.py         │
└─────────────────────────┘     └────────┬─────────┘
              ▲                           │ POST /events/ingest
              │ GET /stores, /metrics …   ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Live Dashboard │◀────│  Redis Pub/Sub   │◀────│  Intelligence   │
│  dashboard/     │     │  store:{id}:     │     │  API + DB       │
│  live.py        │     │  events          │     │  app/ FastAPI   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

Two ways footage enters: (a) **live** — `POST /pipeline/process` accepts an
uploaded clip and runs detection in a background subprocess; (b) **batch** —
`pipeline/run.sh` over `data/cameras.json`, now behind the `batch` compose
profile. Both paths funnel through the same `POST /events/ingest`.

## Data Flow

The journey of ONE visitor event from frame to dashboard:

Step 0 — Input: a caller feeds a clip to POST /pipeline/process
  with a chosen store_id; the API spawns detect.py against it.
Step 1 — Frame capture: YOLOv8 detects person bounding box
  in frame N of the entry camera clip.
Step 2 — Tracking: ByteTrack assigns track_id. VisitorTracker
  maps track_id → visitor_id (VIS_xxxxxx). Checks exited_tracks
  for re-entry using IoU + 30s window.
Step 3 — Event emission: emit.py validates against StoreEvent
  Pydantic model, writes to events.jsonl, POSTs to API.
Step 4 — Ingestion: POST /events/ingest deduplicates by
  event_id (idempotent), stores in SQLite/PostgreSQL via
  SQLAlchemy async ORM, publishes to Redis channel.
Step 5 — Query: GET /stores/{id}/metrics queries EventRecord
  table, filters is_staff=False, computes conversion rate by
  correlating BILLING_QUEUE_JOIN timestamps with POS transactions
  within a 5-minute window.
Step 6 — Dashboard: Redis subscriber receives event, updates
  Rich terminal layout with live metric values.

## Key Design Decisions

Decision 1 — SQLite in dev, PostgreSQL in prod:
  The same SQLAlchemy async models work with both via a
  DATABASE_URL environment variable swap. SQLite is chosen
  for development because it requires zero setup, stores data
  in a single file, and can be wiped completely between test
  runs with a single delete. PostgreSQL is selected for
  production because it handles concurrent writes from multiple
  camera feeds without write-locking the entire database — a
  critical requirement when processing 5 simultaneous store feeds.

Decision 2 — Idempotent ingest by event_id:
  This matters because the detection pipeline may crash mid-clip
  and be replayed, or events may be POSTed twice via network
  retry logic. Without idempotency, visitor counts would inflate
  and corrupt the conversion rate metric. The implementation uses
  INSERT OR IGNORE on SQLite (via UNIQUE constraint on event_id)
  and ON CONFLICT DO NOTHING on PostgreSQL via SQLAlchemy's
  on_conflict_do_nothing() — both guaranteeing at-least-once
  delivery with exactly-once semantics for deduplication.

Decision 3 — Re-ID using IoU + time window:
  The 30-second window and 0.4 IoU threshold were chosen to
  handle the dominant re-entry pattern: a visitor who steps
  outside briefly (smokes, takes a call) and returns. A full
  OSNet Re-ID model was evaluated and rejected because it adds
  GPU dependency and ~200ms of latency per frame on CPU, making
  real-time processing at 15fps impossible. The trajectory-based
  IoU approach runs in microseconds and handles the common case
  correctly. Stores with GPU budget would benefit from OSNet.

Decision 4 — Staff detection via HSV colour masking:
  Staff uniforms are detected using HSV colour range masking
  (configurable via STAFF_HSV_LOWER and STAFF_HSV_UPPER env
  vars) because this is the most reliable signal available
  without training data. Staff wear black t-shirts, which produce
  a stable HSV signature (hue=0, sat=0, value 50-255) that is
  robust to lighting changes within a store. The configuration
  is tunable per-store without code changes, and the approach
  adds zero inference latency.

Decision 5 — Confidence never suppressed:
  All detections are emitted regardless of confidence score.
  The confidence field is recorded verbatim so downstream
  consumers can apply their own filtering thresholds. Silently
  dropping low-confidence detections would cause systematic
  undercounting, which directly corrupts the conversion rate
  — the north star metric. The spec explicitly requires this
  behaviour.

## Live Input Ingestion

Footage enters the system at runtime, not only through a one-shot batch script.
`POST /pipeline/process` accepts a CCTV clip — either a multipart upload or a
`video_path` already on the server — together with the `store_id`, `camera_id`,
`role`, and `clip_start` the caller chooses. The API persists the clip to
`data/inbox/`, then launches `pipeline/detect.py` in a background subprocess
(`asyncio.create_subprocess_exec`) so heavy YOLO inference never blocks the event
loop. detect.py emits events back into `POST /events/ingest` on the same API,
which stores them and publishes each one to Redis (`store:{id}:events`). The Rich
dashboard is subscribed to that channel, so metrics update live as detection runs.

Two deliberate properties:
- **The caller defines `store_id`.** Nothing is hardcoded. A store_id the system
  has never seen appears automatically once its first event is ingested.
- **The dashboard discovers stores dynamically** via `GET /stores` (distinct
  store_ids with event counts and last-seen timestamps), refreshed every few
  seconds. An optional `STORE_IDS` env var pins/filters the view.

Detection job state lives in an in-memory registry (`GET /pipeline/jobs/{id}`),
which is intentionally single-instance; a multi-replica deployment would move it
to Redis or the database. The legacy batch pass (`pipeline/run.sh`) still exists
but is gated behind the `batch` compose profile so `docker compose up` no longer
runs-once-and-exits.

## API Design

- `POST /pipeline/process` — Feed a clip for background detection; returns 202 + job_id. Events stream into ingest + Redis. Key constraint: store_id supplied by caller, detection runs out-of-process.

- `GET /stores` — All store_ids the API has seen, newest-active first. Powers dynamic store discovery in the dashboard. Edge case: empty list before any events.

- `GET /health` — Returns 200 with `service: healthy`; `service: degraded` plus a populated `stale_feeds` list when any store has not reported in >10 minutes. Key constraint: detects stale camera feeds without flapping to 503 on historical data.

- `GET /stores/{store_id}/metrics` — Returns aggregated metrics (unique_visitors, conversion_rate, avg_dwell, queue_depth, abandonment_rate). Excludes is_staff=true. Key constraint: 5-minute window correlation between BILLING_QUEUE_JOIN and POS transaction.

- `GET /stores/{store_id}/funnel` — Entry → Zone → Billing → Purchase counts with drop-off %. Each stage is a SQL `COUNT(DISTINCT visitor_id)`; purchase correlation fetches only billing-join rows and bisects sorted POS timestamps. Key constraint: session is the unit; re-entries dedupe via the same visitor_id.

- `GET /stores/{store_id}/heatmap` — Per-zone visit_count and avg_dwell, each normalised 0–100 (busiest zone = 100). Key constraint: data_confidence=false when fewer than 20 sessions in the window.

- `GET /stores/{store_id}/events` — Returns paginated event list for a store, newest first. Supports ?event_type filter. Key constraint: cursor-based pagination for large result sets.

- `POST /events/ingest` — Ingests a batch of up to 500 events, idempotent by event_id, partial success on malformed events. Publishes each accepted event to Redis. Key constraint: deduplication must work across concurrent requests.

- `GET /stores/{store_id}/anomalies` — Returns active anomalies for a store. Suppresses CONVERSION_DROP when fewer than 7 days of historical data exist. Key constraint: 7-day rolling average requires history.

## Production Considerations

- `docker compose up` starts all services (API, Redis, pipeline worker, dashboard) with zero manual steps. Health checks on the API service ensure dependent services wait correctly.

- Every API request includes a `trace_id` in the structured JSON logging, enabling request correlation across services. Logs are JSON-lines formatted for log aggregation compatibility.

- The global exception handler catches all unhandled exceptions and returns structured JSON errors with error code, message, and request_id — never raw stack traces that leak internal path or dependency information.

- The `/health` endpoint checks each store's `last_event_at` timestamp and reports `service: degraded` with a populated `stale_feeds` list when any store has missed reporting for more than 10 minutes. It returns 503 only when the database itself is unreachable — a stale feed on historical data must not pin the container health check to a permanent 503.

- Heavy YOLO inference triggered by `POST /pipeline/process` runs in a background subprocess (`asyncio.create_subprocess_exec`), never on the request thread, so the API stays responsive to metric and health queries while detection is in flight.

- The `/funnel` endpoint computes each stage as a SQL `COUNT(DISTINCT visitor_id)` rather than loading all daily events into memory, so its cost scales with the number of distinct visitors, not raw event volume — important at 40 live stores.

- Test coverage exceeds 70% with async route bodies traced correctly via `concurrency = thread` and `greenlet` in .coveragerc, ensuring async FastAPI handlers are properly covered by pytest-asyncio.

## AI-Assisted Decisions

### 1. Re-ID Architecture

What I asked: How to implement visitor re-identification
  without GPU-dependent Re-ID models for a 15fps 1080p stream.

What the AI suggested: Use OSNet from torchreid — extract
  appearance embeddings per crop, compare cosine similarity
  across frames.

What I chose: IoU + time window approach. Reason: OSNet adds
  ~200ms per frame latency on CPU, making real-time processing
  impossible at 15fps. The IoU approach runs in microseconds
  and handles the dominant re-entry pattern (brief exit and
  return) correctly. For a production system with GPU budget,
  OSNet would be the right choice.

What I overrode: The AI's suggestion. Documented here per spec.

### 2. Event Schema — confidence field handling

What I asked: Should low-confidence detections be filtered
  before emitting events to reduce noise?

What the AI suggested: Add a confidence threshold (e.g. 0.45)
  and drop detections below it to keep the event stream clean.

What I chose: Emit all detections with their actual confidence
  value recorded. Reason: dropping detections silently causes
  undercounting. The spec explicitly states "do not suppress
  low-conf events." Downstream consumers (metrics, funnel) can
  apply their own threshold if needed.

What I overrode: The AI's suggestion. This is the right call
  for accuracy of the conversion rate metric.

### 3. Live Ingestion — how detection should run behind the API

What I asked: For the `POST /pipeline/process` upload endpoint,
  how should detection actually execute so the API can accept
  footage and still serve metrics/health while a clip is being
  analysed?

What the AI suggested: Run detection inside a FastAPI
  BackgroundTask in the same process, or push jobs onto a
  Celery/RQ worker backed by a message broker.

What I chose: Spawn detect.py as an out-of-process subprocess
  via asyncio.create_subprocess_exec, tracked in an in-memory
  job registry. Reason: a BackgroundTask runs in the same event
  loop and a multi-minute YOLO job would starve metric and health
  requests; Celery/RQ adds a broker, a worker image, and
  serialization plumbing that is unjustified for a single-node
  challenge submission. A subprocess gives true isolation (a
  crashing clip cannot take down the API) with zero new
  infrastructure, and reuses the exact CLI the batch path already
  uses — one detection code path, two entry points.

What I followed / overrode: Followed the AI on "don't block the
  request thread"; overrode the specific mechanism. I documented
  in Known Limitations that the in-memory registry is
  single-instance — the Celery/broker design the AI proposed is
  the right answer once detection must scale across replicas.

## Known Limitations

1. Cross-camera deduplication not implemented — a person
   visible in both entry and main floor cameras may be
   counted twice if their visitor_id differs across cameras.

2. Staff detection relies on uniform colour — stores with
   non-uniform staff attire or colour overlap with customer
   clothing will produce false positives/negatives. The current
   heuristic detects black t-shirts (HSV hue=0, sat=0, value 50-255).
   Dim CCTV lighting may cause under-detection; customers wearing
   black may be misclassified as staff. This is an approximation,
   not ground truth.

3. Billing camera field-of-view (FOV) is limited: the top-view
   camera at the billing counter only sees the person directly
   at the counter, NOT the waiting queue behind them. This means:
   - True queue_depth (waiting line length) cannot be measured
   - BILLING_QUEUE_ABANDON detection is incomplete — only visitors
     who approach the counter and then leave are captured; customers
     who abandon while waiting in line are not visible to this camera
   - The queue_depth metric reflects only current counter presence,
     not overall billing interest

4. The 7-day rolling average for CONVERSION_DROP anomaly
   requires 7 days of historical data — the anomaly is
   suppressed when historical data is insufficient.

5. Re-entry window of 30s is configurable but not adaptive —
   a customer who spends >30s outside (e.g. takes a call)
   will be counted as a new visitor.

6. The detection job registry behind POST /pipeline/process is
   in-memory and therefore single-instance: job status is lost on
   restart and is not shared across API replicas. This is fine for
   one node; horizontal scaling would move the registry to Redis
   or the database and detection onto a dedicated worker pool.