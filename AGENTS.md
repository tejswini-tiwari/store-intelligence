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

## Real Dataset (overrides earlier assumptions)

- Single real store: store_id = "ST1008", store_name "Brigade_Bangalore",
  city Bangalore. Use "ST1008" as the canonical store_id EVERYWHERE — in emitted
  events and in the POS loader. There are no other stores.

- 5 CCTV clips live in ./data/clips/ with descriptive filenames. All five cover
  the SAME real time window 20:09–20:13 IST on 2026-04-10, from different angles
  in the one store. Camera roles are defined in data/cameras.json, NOT parsed
  from the filename. The roles are:
    * Front_Gate.mp4                                  -> role "entry"   (CAM_ENTRY_01)
    * Cash_Counter_top_view.mp4                       -> role "billing" (CAM_BILLING_01)
    * Left_part_of_the_shop_with_cash_counter.mp4     -> role "floor"   (CAM_FLOOR_01)
    * Right_part_of_the_shop_with_cash_counter.mp4    -> role "floor"   (CAM_FLOOR_02)
    * Store_Room.mp4                                  -> role "exclude" (CAM_STOREROOM_01)

- Only the "entry" camera emits ENTRY/EXIT/REENTRY events (it is the sole
  visitor counter). Only the "billing" camera drives billing-queue events.
  "floor" cameras do zone tracking only. "exclude" cameras emit nothing.

- Camera FOV limitations (document these honestly; they constrain metrics):
    * The billing top-view camera sees only the person AT the counter, not the
      waiting line, so true queue_depth and BILLING_QUEUE_ABANDON cannot be
      reliably observed from this footage. Queue-depth logic still exists and is
      proven by tests, but will not spike on real footage.
    * The Store Room has no customers and is excluded from all customer metrics.

- Staff detection: staff wear BLACK t-shirts (not navy). The HSV colour range in
  .env / .env.example must target black:
      STAFF_HSV_LOWER=0,0,0
      STAFF_HSV_UPPER=180,255,50
  Note honestly that black-shirt detection is approximate (customers may also
  wear black, dim CCTV reads as low-value), so it is a heuristic baseline, not
  exact ground truth.

- Store layout is a floor-plan IMAGE (data/store_layout_source.xlsx for human
  reference), NOT machine-readable polygons. Zone polygons must be defined
  per-camera in PIXEL coordinates in data/store_layout.json, because each camera
  sees zones in perspective — they cannot be copied from the top-down floorplan.

- POS data is a real Purplle line-item sales export at data/pos_raw.csv (NOT the
  example schema). Columns include order_id, invoice_number, order_date
  (DD-MM-YYYY), order_time (HH:MM:SS local IST), store_id (ST1008), store_name,
  total_amount, plus many ignorable columns. A loader (pipeline/load_pos.py)
  groups line items by invoice_number into transactions, sums total_amount as
  basket value, and converts IST date+time to UTC. There is NO single timestamp
  or basket_value_inr column in the raw file.

- Real store zones (from the floorplan, use these zone_id names): EB_KOREAN,
  THE_FACE_SHOP, GOOD_VIBES, DERMDOC, MINIMALIST, AQUALOGICA, LAKME, ACCESSORIES,
  MAYBELLINE, FACES_CANADA, COLORBAR_SUGAR, SWISS_BEAUTY, RENEE_NYBAE,
  ALPS_GOODNESS, STREAX, FOH, NAIL_FRAGRANCE, MAKEUP_UNIT, BILLING (Cash Counter),
  PMU.

- Metric time window must be configurable via env METRIC_WINDOW
  (values: all | today | last24h), default "all", because ingested events are
  historically dated (2026-04-10) and a "today UTC" filter would wrongly return
  zeros when a reviewer runs this on another date.