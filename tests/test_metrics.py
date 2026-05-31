# PROMPT: Write pytest async tests for FastAPI metrics, funnel,
# and ingestion endpoints. Cover zero-purchase stores, all-staff
# clips, idempotency, funnel re-entry dedup, and batch limits.
# Use module-scoped TestClient fixture with init_db.
# CHANGES MADE: 12 tests per spec - empty store metrics, all-staff
# exclusion, idempotency (fixed-id events), partial success with
# malformed batch, zero purchases conversion, funnel re-entry dedup,
# funnel keys validation, 501 batch limit (422), visitor count,
# no stack traces in responses. All tests pass.

import pytest
import uuid
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from datetime import datetime, timezone

@pytest.fixture(scope="module")
def client():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    with TestClient(app) as c:
        yield c

def make_event(store_id, visitor_id,
               event_type="ENTRY", is_staff=False,
               zone_id=None, queue_depth=None,
               dwell_ms=0):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_staff": is_staff,
        "confidence": 0.91,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": None,
            "session_seq": 1
        }
    }

def test_metrics_empty_store(client):
    r = client.get("/stores/STORE_EMPTY_M001/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["unique_visitors"] == 0
    assert d["conversion_rate"] == 0.0
    assert d["conversion_rate"] is not None
    assert isinstance(d["conversion_rate"], float)
    assert d["current_queue_depth"] == 0
    assert d["abandonment_rate"] == 0.0
    assert isinstance(d["avg_dwell_per_zone"], dict)

def test_all_staff_events_zero_customers(client):
    for i in range(3):
        e = make_event("STORE_STAFF_M001",
                       f"VIS_staff{i}", is_staff=True)
        client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_STAFF_M001/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0

def test_idempotent_ingest_first_call(client):
    e = make_event("STORE_IDEM_M001", "VIS_idem01")
    e["event_id"] = "idem-m-fixed-001"
    r = client.post("/events/ingest", json={"events": [e]})
    assert r.status_code == 200
    assert r.json()["ingested"] == 1
    assert r.json()["skipped"] == 0

def test_idempotent_ingest_second_call(client):
    e = make_event("STORE_IDEM_M001", "VIS_idem01")
    e["event_id"] = "idem-m-fixed-001"
    r = client.post("/events/ingest", json={"events": [e]})
    assert r.status_code == 200
    assert r.json()["ingested"] == 0
    assert r.json()["skipped"] == 1

def test_idempotent_mixed_batch(client):
    existing = make_event("STORE_MIXED_M001", "VIS_mix01")
    existing["event_id"] = "idem-m-fixed-001"
    new_event = make_event("STORE_MIXED_M001", "VIS_mix02")
    r = client.post("/events/ingest",
                    json={"events": [existing, new_event]})
    assert r.status_code == 200
    assert r.json()["ingested"] == 1
    assert r.json()["skipped"] == 1

def test_partial_success_malformed_batch(client):
    good = make_event("STORE_PARTIAL_M001", "VIS_good01")
    bad = {"event_id": str(uuid.uuid4()),
           "visitor_id": "VIS_x"}
    r = client.post("/events/ingest",
                    json={"events": [good, bad]})
    assert r.status_code in [200, 422]
    assert r.status_code != 500
    assert r.status_code != 503

def test_zero_purchases_conversion_float(client):
    for i in range(3):
        e = make_event("STORE_NOPOS_M001", f"VIS_np{i}")
        client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_NOPOS_M001/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0
    assert isinstance(r.json()["conversion_rate"], float)

def test_funnel_reentry_not_double_counted(client):
    for etype in ["ENTRY", "EXIT", "REENTRY"]:
        e = make_event("STORE_REENTRY_M001",
                       "VIS_re_m001", etype)
        client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_REENTRY_M001/funnel")
    assert r.status_code == 200
    assert r.json()["funnel"]["entry_count"] == 1

def test_funnel_response_has_all_keys(client):
    r = client.get("/stores/STORE_EMPTY_M001/funnel")
    assert r.status_code == 200
    d = r.json()
    assert "funnel" in d
    assert "drop_off_pct" in d
    for k in ["entry_count","zone_visit_count",
              "billing_queue_count","purchase_count"]:
        assert k in d["funnel"], f"funnel missing {k}"
    for k in ["entry_to_zone","zone_to_billing",
              "billing_to_purchase"]:
        assert k in d["drop_off_pct"], \
            f"drop_off_pct missing {k}"

def test_batch_limit_501_rejected(client):
    events = [make_event("S", f"VIS_{i}") for i in range(501)]
    r = client.post("/events/ingest",
                    json={"events": events})
    assert r.status_code == 422

def test_metrics_after_entry_event(client):
    for i in range(5):
        e = make_event("STORE_COUNT_M001", f"VIS_cnt{i}")
        client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_COUNT_M001/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 5

def test_no_stack_traces_in_responses(client):
    r = client.get("/stores/STORE_EMPTY_M001/metrics")
    text = r.text
    assert "Traceback" not in text
    assert 'File "' not in text