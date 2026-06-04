# Store Intelligence Pipeline

## What This Is

CCTV footage from retail stores is processed through a YOLOv8 detection pipeline to produce visitor events (ENTRY, EXIT, ZONE_ENTER, ZONE_DWELL, BILLING_QUEUE_JOIN, etc.) which are ingested into a FastAPI REST API and surfaced in real-time on a live Rich terminal dashboard. North star metric: Offline Store Conversion Rate — the percentage of visitors who enter the billing queue and complete a POS transaction.

## Quick Start (5 commands)

```bash
git clone https://github.com/tejswini-tiwari/Purplle-Tech-Hackathon-26-Solution
cd store-intelligence
cp .env.example .env
docker compose up -d
curl http://localhost:8000/health
open http://localhost:8000/docs on browser to check every API 
```

Expected output from health check:
```json
{
  "service": "healthy",
  "db_status": "connected",
  "last_event_per_store": {},
  "stale_feeds": [],
  "checked_at": "2026-..."
}
```

## Feeding Footage (live, on-demand)

You feed CCTV clips to the running API and watch detection results stream onto
the dashboard in real time. **The store_id is whatever you pass in** — it is not
hardcoded, and a brand-new store appears on the dashboard automatically once its
first event lands.

```bash
# After `docker compose up -d`, feed a clip with the CLI client:
python pipeline/feed.py \
  --video "data/clips/Store 1/entry.mp4" \
  --store-id ST1008 --camera-id CAM_ENTRY_01 --role entry --watch

# Or call the endpoint directly (multipart upload):
curl -X POST http://localhost:8000/pipeline/process \
  -F store_id=ST1008 -F camera_id=CAM_ENTRY_01 -F role=entry \
  -F video=@"data/clips/Store 1/entry.mp4"

# Track the background detection job:
curl http://localhost:8000/pipeline/jobs/<job_id>
```

The API saves the clip, runs `pipeline/detect.py` in the background, and the
emitted events flow into `POST /events/ingest` (persisted + published to Redis),
so the live dashboard updates as detection progresses. Events are also written to
`data/output/events.jsonl`.

### Running detection directly (no API)

```bash
pip install -r requirements.txt
python pipeline/detect.py \
  --video "data/clips/Store 1/entry.mp4" \
  --store-id ST1008 --camera-id CAM_ENTRY_01 --role entry \
  --clip-start 2026-04-10T20:09:00+05:30 \
  --layout data/store_layout.json
```

### Legacy batch pass (optional)

A one-shot pass over everything in `data/cameras.json`. It is behind the `batch`
compose profile so a plain `docker compose up` does not run-once-and-exit:

```bash
docker compose --profile batch up pipeline   # in Docker
bash pipeline/run.sh                          # or locally
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /pipeline/process | Feed a CCTV clip (upload or path) for live detection; returns 202 + job_id |
| GET | /pipeline/jobs/{id} | Detection job status (queued/running/done/failed) |
| GET | /stores | All store_ids seen so far (dashboard discovers stores from this) |
| POST | /events/ingest | Ingest batches of up to 500 events |
| GET | /stores/{id}/metrics | Real-time store metrics |
| GET | /stores/{id}/funnel | Conversion funnel with drop-off % |
| GET | /stores/{id}/heatmap | Zone visit frequency heatmap (normalised 0–100) |
| GET | /stores/{id}/anomalies | Active anomalies with severity |
| GET | /health | Service health and stale feed detection |

## Example API Calls

```bash
# Ingest an event
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events": [{"store_id": "STORE_BLR_002", ...}]}'

# Get metrics
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# Get active anomalies
curl http://localhost:8000/stores/STORE_BLR_002/anomalies

# Get conversion funnel
curl http://localhost:8000/stores/STORE_BLR_002/funnel
```

## Running the Live Dashboard

```bash
# Terminal dashboard (requires Redis running)
docker compose up dashboard

# Or run locally
python dashboard/live.py
```

## Running Tests

```bash
# Inside Docker
docker compose exec api pytest tests/ -v \
  --cov=app --cov=pipeline --cov-report=term-missing

# Or locally
pip install -r requirements.txt
pytest tests/ -v --cov=app --cov=pipeline
```

Expected: 43 tests pass, coverage >= 70%

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py      # YOLOv8 + ByteTrack detection
│   ├── tracker.py     # Re-ID, zone tracking, session mgmt
│   ├── emit.py        # Event validation and emission
│   └── run.sh         # Process all clips → events
├── app/
│   ├── main.py        # FastAPI app, middleware, exception handler
│   ├── models.py      # Pydantic schemas (StoreEvent, all 8 types)
│   ├── database.py    # SQLAlchemy async ORM
│   ├── ingestion.py   # Idempotent batch ingest
│   ├── metrics.py     # Real-time store metrics
│   ├── funnel.py      # Conversion funnel
│   ├── anomalies.py   # Anomaly detection
│   ├── heatmap.py     # Zone heatmap (0-100 normalised scores)
│   ├── events.py      # Paginated event list endpoint
│   └── health.py      # Health check endpoint
├── dashboard/
│   └── live.py        # Rich terminal live dashboard
├── tests/
│   ├── test_pipeline.py   # 22 pipeline tests
│   ├── test_metrics.py    # 12 API tests
│   ├── test_anomalies.py  # 9 anomaly tests
│   ├── test_heatmap.py    # heatmap normalisation + edge cases
│   ├── test_conversion.py # 5-min window correlation tests
│   └── test_pos_loader.py # IST→UTC conversion tests
├── docs/
│   ├── DESIGN.md      # Architecture and AI decisions
│   └── CHOICES.md     # 3 key decisions with rationale
├── docker-compose.yml # api + db + redis + dashboard
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | sqlite+aiosqlite:///./data/store.db | DB connection |
| REDIS_URL | redis://localhost:6379 | Redis connection |
| API_URL | http://localhost:8000 | API for pipeline to post events |
| YOLO_MODEL | yolov8m.pt | YOLOv8 model variant |
| STAFF_HSV_LOWER | 0,0,0 | HSV lower bound for staff uniform (black) |
| STAFF_HSV_UPPER | 180,255,50 | HSV upper bound for staff uniform (black) |
| REENTRY_WINDOW_SECONDS | 30 | Re-entry detection window |
| REENTRY_IOU_THRESHOLD | 0.4 | IoU threshold for re-entry match |

## Architecture

CCTV Clips → YOLOv8 Detection → ByteTrack Tracking
→ VisitorTracker (Re-ID) → emit.py → events.jsonl
→ POST /events/ingest → SQLite/PostgreSQL
→ GET /metrics, /funnel, /anomalies
→ Redis pub/sub → Live Dashboard

See docs/DESIGN.md for full architecture documentation.
See docs/CHOICES.md for key technical decisions.
