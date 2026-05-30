# AGENTS.md — Store Intelligence Pipeline

## Project Goal
Build a full CCTV → Events → REST API pipeline for Apex Retail.
North star metric: Offline Store Conversion Rate.

## Tech Stack (do not deviate without asking)
- Detection: Python, ultralytics YOLOv8, ByteTrack, torchreid (OSNet)
- API: Python 3.11, FastAPI, SQLite (dev) / PostgreSQL (prod), Redis for pub/sub
- Container: docker-compose.yml, all services start with `docker compose up`
- Dashboard: Rich terminal library (Python)
- Testing: pytest, coverage >70%

## File Structure
Follow /store-intelligence/ layout from problem statement exactly.

## Key Constraints
- Every event MUST match the JSON schema in pipeline/emit.py
- visitor_id must be unique per visit session (not per person globally)
- Staff (is_staff=true) MUST be excluded from all customer metrics
- POST /events/ingest must be idempotent by event_id
- No raw stack traces in API responses — always structured JSON errors

## Event Types
ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL (every 30s),
BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY