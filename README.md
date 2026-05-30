# Store Intelligence Pipeline

## Setup
```bash
git clone <repo-url>
cd store-intelligence
cp .env.example .env
docker compose up -d
bash pipeline/run.sh --clips ./data/clips/
```

## Running Tests
```bash
docker compose exec api pytest --cov=app tests/
```

## Running Detection Pipeline
```bash
python pipeline/detect.py --video ./data/clips/store1_entry.mp4 --store-id STORE_BLR_002 --camera-id CAM_ENTRY_01
```

## Running Dashboard
```bash
docker compose up dashboard
```

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | / | Health check |
| POST | /events/ingest | Ingest batch of events |
| GET | /metrics | Get store metrics |
| GET | /funnel | Get conversion funnel |
| GET | /anomalies | Get anomaly alerts |
| GET | /health | Detailed health check |

## Project Structure
```
store-intelligence/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   ├── database.py
│   ├── ingestion.py
│   ├── metrics.py
│   ├── funnel.py
│   ├── anomalies.py
│   └── health.py
├── pipeline/
│   ├── __init__.py
│   ├── detect.py
│   ├── tracker.py
│   ├── emit.py
│   └── run.sh
├── tests/
│   ├── __init__.py
│   ├── test_pipeline.py
│   ├── test_metrics.py
│   └── test_anomalies.py
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
├── dashboard/
├── data/
│   ├── clips/
│   └── output/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```