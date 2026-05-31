# PROMPT: Write pytest tests for anomaly detection covering
# queue spike severity thresholds, dead zone detection after
# silence, anomaly structure validation, and empty store case.
# CHANGES MADE: 9 tests per spec - empty store list, queue spike
# WARN (>5-10) and CRITICAL (>10), no spike <=5, dead zone after
# 45min silence (INFO), no dead zone for recent activity, anomaly
# structure fields validation, health endpoint, null safety across
# multiple stores. All tests pass.

import pytest
import uuid
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from datetime import datetime, timezone, timedelta

@pytest.fixture(scope="module")
def client():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    with TestClient(app) as c:
        yield c

def make_billing_event(store_id, queue_depth,
                        minutes_ago=0):
    ts = (datetime.now(timezone.utc)
          - timedelta(minutes=minutes_ago))
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_BILLING_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": ts.isoformat(),
        "is_staff": False,
        "confidence": 0.88,
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": None,
            "session_seq": 1
        }
    }

def make_zone_event(store_id, zone_id, minutes_ago=0):
    ts = (datetime.now(timezone.utc)
          - timedelta(minutes=minutes_ago))
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_FLOOR_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": "ZONE_ENTER",
        "timestamp": ts.isoformat(),
        "is_staff": False,
        "confidence": 0.85,
        "zone_id": zone_id,
        "dwell_ms": 0,
        "metadata": {
            "queue_depth": None,
            "sku_zone": zone_id,
            "session_seq": 1
        }
    }

def test_empty_store_returns_empty_list(client):
    r = client.get("/stores/STORE_ANON_EMPTY_A01/anomalies")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) == 0

def test_queue_spike_warn_threshold(client):
    e = make_billing_event("STORE_QWARN_A01",
                            queue_depth=7, minutes_ago=0)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_QWARN_A01/anomalies")
    assert r.status_code == 200
    spikes = [a for a in r.json()
              if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) > 0, "Expected BILLING_QUEUE_SPIKE anomaly"
    assert spikes[0]["severity"] == "WARN"
    assert len(spikes[0]["suggested_action"]) > 0

def test_queue_spike_critical_threshold(client):
    e = make_billing_event("STORE_QCRIT_A01",
                            queue_depth=12, minutes_ago=0)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_QCRIT_A01/anomalies")
    spikes = [a for a in r.json()
              if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) > 0
    assert spikes[0]["severity"] == "CRITICAL"

def test_no_spike_below_threshold(client):
    e = make_billing_event("STORE_QLOW_A01",
                            queue_depth=3, minutes_ago=0)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_QLOW_A01/anomalies")
    spikes = [a for a in r.json()
              if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) == 0, \
        f"queue_depth=3 must not trigger spike: {spikes}"

def test_dead_zone_after_45min_silence(client):
    e = make_zone_event("STORE_DEAD_A01",
                         "SKINCARE", minutes_ago=45)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_DEAD_A01/anomalies")
    dead = [a for a in r.json()
            if a["anomaly_type"] == "DEAD_ZONE"]
    assert len(dead) > 0, "Expected DEAD_ZONE anomaly"
    assert dead[0]["severity"] == "INFO"
    assert dead[0]["zone_id"] == "SKINCARE"
    assert len(dead[0]["suggested_action"]) > 0

def test_no_dead_zone_for_recent_activity(client):
    e = make_zone_event("STORE_ACTIVE_A01",
                         "FRAGRANCE", minutes_ago=5)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_ACTIVE_A01/anomalies")
    dead = [a for a in r.json()
            if a["anomaly_type"] == "DEAD_ZONE"
            and a.get("zone_id") == "FRAGRANCE"]
    assert len(dead) == 0, \
        "Recently active zone must not be flagged dead"

def test_anomaly_item_required_fields(client):
    e = make_billing_event("STORE_STRUCT_A01",
                            queue_depth=8, minutes_ago=0)
    client.post("/events/ingest", json={"events": [e]})
    r = client.get("/stores/STORE_STRUCT_A01/anomalies")
    assert r.status_code == 200
    for item in r.json():
        assert "anomaly_type" in item
        assert "severity" in item
        assert "suggested_action" in item
        assert "detected_at" in item
        assert item["severity"] in ["INFO","WARN","CRITICAL"]
        assert len(item["suggested_action"]) > 0

def test_health_endpoint_structure(client):
    r = client.get("/health")
    assert r.status_code in [200, 503]
    d = r.json()
    assert "service" in d
    assert "db_status" in d
    assert d["service"] in ["healthy","degraded","down"]
    if d["service"] != "down":
        assert "last_event_per_store" in d
        assert "stale_feeds" in d
        assert isinstance(d["stale_feeds"], list)

def test_anomalies_never_returns_null(client):
    for store in ["STORE_NULL_A01","STORE_NULL_A02",
                  "STORE_NULL_A03"]:
        r = client.get(f"/stores/{store}/anomalies")
        assert r.status_code == 200
        assert r.json() is not None
        assert isinstance(r.json(), list)