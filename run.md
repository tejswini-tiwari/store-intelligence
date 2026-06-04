# Run Instructions

## Prerequisites

- **Docker Desktop** must be running before any `docker compose` command
- **4 CCTV clips** must be present in `data/clips/` (already included)

## One-time Setup

```bash
# Build images (first time only — takes 3-5 minutes on a fast connection)
docker compose up --build
```

> If build hangs at `pip install`, check Docker Desktop is running and you have >4GB RAM allocated.

## Starting Services (after first build)

```bash
docker compose up
```

This starts:
- **API** at `http://localhost:8000` — FastAPI server
- **Redis** at `localhost:6379` — pub/sub for live events
- **Dashboard** — Rich terminal display (updates live)
- **PostgreSQL** — at `db:5432` (internal)

## Running the Batch Pipeline (optional)

Processes all clips in `data/clips/` once and exits:

```bash
docker compose --profile batch up pipeline
```

## Feeding Clips (Live Ingestion)

While services are running, process a clip on demand:

```bash
curl -X POST http://localhost:8000/pipeline/process \
  -F "video=@data/clips/Front_Gate.mp4" \
  -F "store_id=ST1008" \
  -F "camera_id=CAM_ENTRY_01" \
  -F "role=entry" \
  -F "clip_start=2026-04-10T14:39:00Z"
```

Or use the CLI client:

```bash
python pipeline/feed.py data/clips/Front_Gate.mp4 ST1008 CAM_ENTRY_01 entry 2026-04-10T14:39:00Z
```

## Viewing Results

| Endpoint | Description |
|---|---|
| `http://localhost:8000/stores` | All discovered stores |
| `http://localhost:8000/stores/ST1008/events` | Paginated event log |
| `http://localhost:8000/stores/ST1008/metrics` | Conversion, abandon rate, dwell |
| `http://localhost:8000/stores/ST1008/heatmap` | Zone visit frequency + dwell |
| `http://localhost:8000/health` | API health + feed summary |

## Stopping

```bash
docker compose down        # stop + keep data
docker compose down -v     # stop + wipe database
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `connection refused` on 8000 | API not ready — wait 10s after `Up` |
| Build hangs at ultralytics | Docker needs internet to download YOLO weights |
| Dashboard shows no data | Ensure `data/clips/` contains clips and cameras.json is mounted |
| `pipeline` exits immediately | Run `docker compose --profile batch up --build pipeline` to see logs |
