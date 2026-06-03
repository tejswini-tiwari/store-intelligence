# PROMPT: Write pytest tests for GET /stores/{id}/heatmap covering:
# - empty store returns empty zones dict with data_confidence=False
# - single zone with ZONE_ENTER and ZONE_DWELL events returns correct
#   visit_count and avg_dwell_ms
# - visit_score and dwell_score are normalised 0-100 (busiest zone = 100)
# - multiple zones: highest visit_count zone has visit_score=100
# - data_confidence=False when unique visitors < 20
# - data_confidence=True when unique visitors >= 20
# - staff events are excluded from zone counts
# CHANGES MADE: 7 tests per spec. Fixtures reuse test_metrics.py pattern
# (module-scoped TestClient + asyncio init_db). Normalisation verified by
# seeding two zones with 2:1 visit ratio and asserting score ratio.

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
    with TestClient(app) as c:
        yield c
    loop.close()


def _entry(store_id, visitor_id, ts, is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": "ENTRY",
        "timestamp": ts,
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.95,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


def _zone_enter(store_id, visitor_id, zone_id, ts, is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_FLOOR_01",
        "visitor_id": visitor_id,
        "event_type": "ZONE_ENTER",
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": zone_id, "session_seq": 2},
    }


def _zone_dwell(store_id, visitor_id, zone_id, dwell_ms, ts, is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_FLOOR_01",
        "visitor_id": visitor_id,
        "event_type": "ZONE_DWELL",
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.90,
        "metadata": {"queue_depth": None, "sku_zone": zone_id, "session_seq": 3},
    }


BASE_TS = "2025-06-01T10:00:00Z"
BASE_DT = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


class TestHeatmapEmpty:
    def test_empty_store_returns_empty_zones(self, client):
        store_id = f"STORE_HM_EMPTY_{uuid.uuid4().hex[:4]}"
        resp = client.get(f"/stores/{store_id}/heatmap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["store_id"] == store_id
        assert body["zones"] == {}
        assert body["data_confidence"] is False

    def test_response_has_required_fields(self, client):
        store_id = f"STORE_HM_FIELDS_{uuid.uuid4().hex[:4]}"
        resp = client.get(f"/stores/{store_id}/heatmap")
        assert resp.status_code == 200
        body = resp.json()
        for field in ("store_id", "date", "zones", "data_confidence", "computed_at"):
            assert field in body, f"Missing field: {field}"


class TestHeatmapSingleZone:
    def test_single_zone_visit_count_and_dwell(self, client):
        store_id = f"STORE_HM_ONE_{uuid.uuid4().hex[:4]}"
        vis = f"VIS_{uuid.uuid4().hex[:6]}"
        ts = BASE_TS
        events = [
            _entry(store_id, vis, ts),
            _zone_enter(store_id, vis, "SKINCARE", ts),
            _zone_dwell(store_id, vis, "SKINCARE", 45000, ts),
        ]
        client.post("/events/ingest", json={"events": events})

        resp = client.get(f"/stores/{store_id}/heatmap")
        assert resp.status_code == 200
        body = resp.json()
        assert "SKINCARE" in body["zones"]
        zone = body["zones"]["SKINCARE"]
        assert zone["visit_count"] == 1
        assert zone["avg_dwell_ms"] == 45000
        # Single zone must be normalised to 100
        assert zone["visit_score"] == 100
        assert zone["dwell_score"] == 100

    def test_staff_events_excluded(self, client):
        store_id = f"STORE_HM_STAFF_{uuid.uuid4().hex[:4]}"
        staff = f"VIS_{uuid.uuid4().hex[:6]}"
        ts = BASE_TS
        events = [
            _entry(store_id, staff, ts, is_staff=True),
            _zone_enter(store_id, staff, "SKINCARE", ts, is_staff=True),
            _zone_dwell(store_id, staff, "SKINCARE", 90000, ts, is_staff=True),
        ]
        client.post("/events/ingest", json={"events": events})

        resp = client.get(f"/stores/{store_id}/heatmap")
        body = resp.json()
        assert body["zones"] == {}


class TestHeatmapNormalisation:
    def test_busiest_zone_scores_100(self, client):
        store_id = f"STORE_HM_NORM_{uuid.uuid4().hex[:4]}"
        ts = BASE_TS
        # Zone A: 4 visitors, Zone B: 2 visitors → A should score 100, B should score 50
        for i in range(4):
            vis = f"VIS_{uuid.uuid4().hex[:6]}"
            client.post("/events/ingest", json={"events": [
                _entry(store_id, vis, ts),
                _zone_enter(store_id, vis, "ZONE_A", ts),
            ]})
        for i in range(2):
            vis = f"VIS_{uuid.uuid4().hex[:6]}"
            client.post("/events/ingest", json={"events": [
                _entry(store_id, vis, ts),
                _zone_enter(store_id, vis, "ZONE_B", ts),
            ]})

        resp = client.get(f"/stores/{store_id}/heatmap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["zones"]["ZONE_A"]["visit_score"] == 100
        assert body["zones"]["ZONE_B"]["visit_score"] == 50

    def test_scores_are_integers_in_0_100(self, client):
        store_id = f"STORE_HM_RANGE_{uuid.uuid4().hex[:4]}"
        ts = BASE_TS
        for zone in ("ZONE_X", "ZONE_Y", "ZONE_Z"):
            vis = f"VIS_{uuid.uuid4().hex[:6]}"
            client.post("/events/ingest", json={"events": [
                _entry(store_id, vis, ts),
                _zone_enter(store_id, vis, zone, ts),
            ]})

        resp = client.get(f"/stores/{store_id}/heatmap")
        body = resp.json()
        for zone_id, zone_data in body["zones"].items():
            assert 0 <= zone_data["visit_score"] <= 100, f"{zone_id} visit_score out of range"
            assert 0 <= zone_data["dwell_score"] <= 100, f"{zone_id} dwell_score out of range"


class TestHeatmapDataConfidence:
    def test_data_confidence_false_below_20_sessions(self, client):
        store_id = f"STORE_HM_LOWCONF_{uuid.uuid4().hex[:4]}"
        ts = BASE_TS
        for i in range(5):
            vis = f"VIS_{uuid.uuid4().hex[:6]}"
            client.post("/events/ingest", json={"events": [
                _entry(store_id, vis, ts),
                _zone_enter(store_id, vis, "ZONE_A", ts),
            ]})

        resp = client.get(f"/stores/{store_id}/heatmap")
        body = resp.json()
        assert body["data_confidence"] is False

    def test_data_confidence_true_at_20_sessions(self, client):
        store_id = f"STORE_HM_HICONF_{uuid.uuid4().hex[:4]}"
        ts = BASE_TS
        for i in range(20):
            vis = f"VIS_{uuid.uuid4().hex[:6]}"
            client.post("/events/ingest", json={"events": [
                _entry(store_id, vis, ts),
                _zone_enter(store_id, vis, "ZONE_A", ts),
            ]})

        resp = client.get(f"/stores/{store_id}/heatmap")
        body = resp.json()
        assert body["data_confidence"] is True
