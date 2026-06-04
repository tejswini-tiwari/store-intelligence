# Store Intelligence Pipeline

## What This Is

CCTV footage from retail stores is processed through a YOLOv8 detection pipeline to produce visitor events such as ENTRY, EXIT, ZONE_ENTER, ZONE_DWELL, and BILLING_QUEUE_JOIN. Events are ingested into a FastAPI REST API and surfaced in real time on a minimal web dashboard.

North star metric: Offline Store Conversion Rate, the percentage of visitors who enter the billing queue and complete a POS transaction.

## Quick Start

```bash
git clone https://github.com/tejswini-tiwari/store-intelligence
cd store-intelligence
cp .env.example .env
docker compose up --build
```

Open:

```text
API:        http://localhost:8000
API docs:   http://localhost:8000/docs
Dashboard:  http://localhost:8001
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected output:

```json
{
  "service": "healthy",
  "db_status": "connected",
  "last_event_per_store": {},
  "stale_feeds": [],
  "checked_at": "2026-..."
}
```

## Feeding Footage

You feed CCTV clips to the running API and watch detection results update on the dashboard in real time. The `store_id` is supplied by the caller; it is not hardcoded. A new store appears automatically once its first event lands.

The real CCTV clips are not committed to git. On another laptop, copy the clips into `data/clips/` before processing footage.

```bash
curl -X POST http://localhost:8000/pipeline/process \
  -F "video_path=./data/clips/Store 1/entry.mp4" \
  -F "store_id=ST1008" \
  -F "camera_id=CAM_ENTRY_01" \
  -F "role=entry" \
  -F "clip_start=2026-04-10T20:09:00+05:30"
```

PowerShell users can run the `curl` command on one line, or use PowerShell backticks instead of Bash `\` line continuations.

Track the background detection job:

```bash
curl http://localhost:8000/pipeline/jobs/<job_id>
```

You can also use the CLI client:

```bash
python pipeline/feed.py \
  --video "data/clips/Store 1/entry.mp4" \
  --store-id ST1008 \
  --camera-id CAM_ENTRY_01 \
  --role entry \
  --clip-start 2026-04-10T20:09:00+05:30 \
  --watch
```

The API saves uploaded clips, runs `pipeline/detect.py` in the background, and posts emitted events into `POST /events/ingest`. Events are persisted, published to Redis, and written to `data/output/events.jsonl`.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | Friendly API landing response |
| GET | /health | Service health and stale feed detection |
| GET | /stores | All store_ids seen so far |
| GET | /stores/{id}/events | Paginated event log |
| GET | /stores/{id}/metrics | Real-time store metrics |
| GET | /stores/{id}/funnel | Conversion funnel with drop-off percentage |
| GET | /stores/{id}/heatmap | Zone visit frequency heatmap |
| GET | /stores/{id}/anomalies | Active anomalies with severity |
| POST | /events/ingest | Ingest batches of events |
| POST | /pipeline/process | Feed a CCTV clip for live detection |
| GET | /pipeline/jobs | List detection jobs |
| GET | /pipeline/jobs/{id} | Detection job status |

## Example API Calls

```bash
curl http://localhost:8000/stores
curl http://localhost:8000/stores/ST1008/events
curl http://localhost:8000/stores/ST1008/metrics
curl http://localhost:8000/stores/ST1008/anomalies
curl http://localhost:8000/stores/ST1008/funnel
curl http://localhost:8000/stores/ST1008/heatmap
```

## Dashboard

The default Compose dashboard is a minimal web dashboard:

```text
http://localhost:8001
```

The older Rich terminal dashboard is still available for local use:

```bash
python dashboard/live.py
```

## Legacy Batch Pass

A one-shot pass over everything in `data/cameras.json` is behind the `batch` Compose profile, so a plain `docker compose up` does not run once and exit.

```bash
docker compose --profile batch up pipeline
```

## Running Tests

Inside Docker:

```bash
docker compose exec api pytest tests/ -v --cov=app --cov=pipeline --cov-report=term-missing
```

Or locally:

```bash
pip install -r requirements.txt
pytest tests/ -v --cov=app --cov=pipeline
```

Expected: tests pass with coverage greater than 70%.

## Project Structure

```text
store-intelligence/
|-- app/
|   |-- main.py        # FastAPI app, middleware, exception handler
|   |-- models.py      # Pydantic schemas
|   |-- database.py    # SQLAlchemy async ORM
|   |-- ingestion.py   # Idempotent batch ingest
|   |-- metrics.py     # Real-time store metrics
|   |-- funnel.py      # Conversion funnel
|   |-- anomalies.py   # Anomaly detection
|   |-- heatmap.py     # Zone heatmap
|   |-- events.py      # Paginated event list endpoint
|   |-- stores.py      # Dynamic store discovery
|   `-- health.py      # Health check endpoint
|-- dashboard/
|   |-- web.py         # Minimal web dashboard on port 8001
|   `-- live.py        # Optional Rich terminal dashboard
|-- pipeline/
|   |-- detect.py      # YOLOv8 + ByteTrack detection
|   |-- tracker.py     # Re-ID, zone tracking, session management
|   |-- emit.py        # Event validation and emission
|   `-- run.sh         # Optional batch pass
|-- tests/
|-- docs/
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
`-- .env.example
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | sqlite+aiosqlite:///./data/store.db | DB connection |
| REDIS_URL | redis://localhost:6379 | Redis connection |
| API_URL | http://localhost:8000 | API for pipeline/dashboard calls |
| YOLO_MODEL | yolov8m.pt | YOLOv8 model variant |
| STAFF_HSV_LOWER | 0,0,0 | HSV lower bound for staff uniform heuristic |
| STAFF_HSV_UPPER | 180,255,50 | HSV upper bound for staff uniform heuristic |
| REENTRY_WINDOW_SECONDS | 30 | Re-entry detection window |
| REENTRY_IOU_THRESHOLD | 0.4 | IoU threshold for re-entry match |
| METRIC_WINDOW | all | Metric time window: all, today, or last24h |

## Architecture

```text
CCTV clips
-> YOLOv8 detection
-> ByteTrack tracking
-> VisitorTracker / Re-ID
-> pipeline/emit.py
-> POST /events/ingest
-> SQLite/PostgreSQL + Redis
-> REST API
-> Web dashboard
```

See `docs/DESIGN.md` for full architecture documentation.
See `docs/CHOICES.md` for key technical decisions.
