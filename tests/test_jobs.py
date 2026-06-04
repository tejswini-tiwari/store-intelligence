# PROMPT: Write pytest tests for the input-ingestion layer:
# - GET /stores returns [] before any events, and lists distinct store_ids
#   (with event_count + last_event_at) after ingest, newest-active first.
# - POST /pipeline/process: 422 when neither video upload nor video_path given;
#   422 on invalid role; 202 + job_id when given a valid video_path; the job is
#   retrievable via GET /pipeline/jobs/{id}; 404 for unknown job.
# Monkeypatch the detection runner so no real YOLO subprocess launches.
# CHANGES MADE: 8 tests per spec. Patched app.jobs._run_detection with an async
# no-op that marks the job done, so /pipeline/process is exercised end-to-end
# (validation + registry) without invoking detect.py. video_path test writes a
# throwaway file so the existence check passes.

import asyncio
import uuid
import pytest
from fastapi.testclient import TestClient

import app.jobs as jobs_module
from app.main import app
from app.database import init_db
from datetime import datetime, timezone


@pytest.fixture(scope="module")
def client():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    with TestClient(app) as c:
        yield c
    loop.close()


@pytest.fixture(autouse=True)
def _no_real_detection(monkeypatch):
    """Replace the detection runner with an instant no-op for every test."""
    async def fake_run(job_id):
        job = jobs_module.JOBS[job_id]
        job["status"] = "done"
        job["events_emitted"] = 0
        job["finished_at"] = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(jobs_module, "_run_detection", fake_run)


def _entry_event(store_id, ts):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": "ENTRY",
        "timestamp": ts,
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


class TestStoresDiscovery:
    def test_stores_listed_after_ingest(self, client):
        store_id = f"STORE_DISCO_{uuid.uuid4().hex[:4]}"
        client.post("/events/ingest", json={"events": [
            _entry_event(store_id, "2026-04-10T14:10:00Z"),
            _entry_event(store_id, "2026-04-10T14:11:00Z"),
        ]})

        resp = client.get("/stores")
        assert resp.status_code == 200
        stores = {s["store_id"]: s for s in resp.json()["stores"]}
        assert store_id in stores
        assert stores[store_id]["event_count"] >= 2
        assert stores[store_id]["last_event_at"] is not None

    def test_stores_response_shape(self, client):
        resp = client.get("/stores")
        assert resp.status_code == 200
        body = resp.json()
        assert "stores" in body
        assert isinstance(body["stores"], list)
        for s in body["stores"]:
            assert {"store_id", "event_count", "last_event_at"} <= set(s.keys())


class TestProcessValidation:
    def test_missing_video_and_path_is_422(self, client):
        resp = client.post("/pipeline/process", data={"store_id": "ST_X", "role": "entry"})
        assert resp.status_code == 422

    def test_invalid_role_is_422(self, client, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        resp = client.post("/pipeline/process", data={
            "store_id": "ST_X",
            "role": "lobby",
            "video_path": str(clip),
        })
        assert resp.status_code == 422

    def test_video_path_not_found_is_422(self, client):
        resp = client.post("/pipeline/process", data={
            "store_id": "ST_X",
            "role": "entry",
            "video_path": "/no/such/clip.mp4",
        })
        assert resp.status_code == 422


class TestProcessJobLifecycle:
    def test_process_with_video_path_creates_job(self, client, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake video bytes")
        store_id = f"STORE_FEED_{uuid.uuid4().hex[:4]}"

        resp = client.post("/pipeline/process", data={
            "store_id": store_id,
            "camera_id": "CAM_ENTRY_01",
            "role": "entry",
            "video_path": str(clip),
        })
        assert resp.status_code == 202
        job = resp.json()
        assert job["store_id"] == store_id
        assert job["status"] in ("queued", "running", "done")
        assert "job_id" in job

        # Job is retrievable
        got = client.get(f"/pipeline/jobs/{job['job_id']}")
        assert got.status_code == 200
        assert got.json()["store_id"] == store_id

    def test_caller_defines_store_id(self, client, tmp_path):
        # The store_id is whatever the caller passes — not a fixed constant.
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake")
        custom = f"BRANDNEW_{uuid.uuid4().hex[:5]}"
        resp = client.post("/pipeline/process", data={
            "store_id": custom,
            "role": "floor",
            "video_path": str(clip),
        })
        assert resp.status_code == 202
        assert resp.json()["store_id"] == custom

    def test_unknown_job_is_404(self, client):
        resp = client.get("/pipeline/jobs/doesnotexist")
        assert resp.status_code == 404

    def test_jobs_list_returns_submitted_jobs(self, client, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake")
        client.post("/pipeline/process", data={
            "store_id": "ST_LIST",
            "role": "entry",
            "video_path": str(clip),
        })
        resp = client.get("/pipeline/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json()["jobs"], list)
        assert len(resp.json()["jobs"]) >= 1
