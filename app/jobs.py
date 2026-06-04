"""
Input ingestion for CCTV footage.

POST /pipeline/process lets a caller FEED a video clip (multipart upload or a
path to a clip already on disk) together with the store_id / camera_id / role
it belongs to. The API saves the clip, then runs pipeline/detect.py against it
in a background subprocess. detect.py emits events back into POST /events/ingest
(this same API), which persists them and publishes each event to Redis — so the
live dashboard updates in real time as detection progresses.

Key design points:
- store_id is supplied by the CALLER, never hardcoded. Whatever store_id you
  feed is the store the events land under and the store the dashboard shows.
- Detection runs out-of-process (subprocess) so heavy YOLO inference never
  blocks the API event loop.
- Job state is tracked in an in-memory registry. This is intentionally simple
  and single-instance; a multi-replica deployment would move it to Redis/DB.
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import os
import re
import sys
import uuid
import shutil
import structlog

logger = structlog.get_logger()

router = APIRouter()

# repo root = parent of the app/ package dir
REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = REPO_ROOT / "data" / "inbox"
INBOX_DIR.mkdir(parents=True, exist_ok=True)

VALID_ROLES = {"entry", "billing", "floor", "exclude"}

# In-memory job registry: job_id -> job dict
JOBS: dict[str, dict] = {}

_EVENTS_EMITTED_RE = re.compile(r"total events emitted:\s*(\d+)")


class JobResponse(BaseModel):
    job_id: str
    status: str
    store_id: str
    camera_id: str
    role: str
    clip_start: str
    video: str
    events_emitted: Optional[int] = None
    error: Optional[str] = None
    submitted_at: str
    finished_at: Optional[str] = None


def _job_public(job: dict) -> dict:
    """Strip internal fields before returning a job to the client."""
    return {k: v for k, v in job.items() if k != "_proc"}


async def _run_detection(job_id: str):
    """Background task: run detect.py against the job's clip and track status."""
    job = JOBS[job_id]
    job["status"] = "running"

    model = os.getenv("YOLO_MODEL", "yolov8m.pt")
    layout = os.getenv("LAYOUT_FILE", str(REPO_ROOT / "data" / "store_layout.json"))
    # detect.py emits to API_URL; default to this same API instance.
    api_url = os.getenv("API_URL", "http://localhost:8000")

    cmd = [
        sys.executable,
        str(REPO_ROOT / "pipeline" / "detect.py"),
        "--video", job["video"],
        "--store-id", job["store_id"],
        "--camera-id", job["camera_id"],
        "--role", job["role"],
        "--clip-start", job["clip_start"],
        "--model", model,
        "--layout", layout,
        "--log-level", "INFO",
    ]

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "API_URL": api_url}

    logger.info("detection job started", job_id=job_id, store_id=job["store_id"],
                camera_id=job["camera_id"], role=job["role"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace") if stdout else ""

        if proc.returncode == 0:
            job["status"] = "done"
            match = _EVENTS_EMITTED_RE.search(output)
            job["events_emitted"] = int(match.group(1)) if match else None
            logger.info("detection job done", job_id=job_id,
                        events_emitted=job["events_emitted"])
        else:
            job["status"] = "failed"
            job["error"] = f"detect.py exited with code {proc.returncode}"
            job["_log_tail"] = output[-2000:]
            logger.error("detection job failed", job_id=job_id,
                         returncode=proc.returncode)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        logger.error("detection job crashed", job_id=job_id, error=str(e))
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/pipeline/process", response_model=JobResponse, status_code=202)
async def process_footage(
    store_id: str = Form(..., description="Store this footage belongs to (you choose it)"),
    camera_id: str = Form("CAM_ENTRY_01", description="Camera identifier"),
    role: str = Form("entry", description="entry | billing | floor | exclude"),
    clip_start: Optional[str] = Form(
        None, description="ISO-8601 start time of the clip; defaults to now (UTC)"
    ),
    video: Optional[UploadFile] = File(
        None, description="CCTV clip to analyse (multipart upload)"
    ),
    video_path: Optional[str] = Form(
        None, description="Alternatively, path to a clip already on the server"
    ),
):
    """
    Feed a CCTV clip for detection. Returns 202 with a job_id immediately;
    detection runs in the background and events stream into the API + dashboard.

    Provide EITHER an uploaded `video` file OR a server-side `video_path`.
    """
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of {sorted(VALID_ROLES)}, got '{role}'",
        )

    job_id = uuid.uuid4().hex[:12]

    # Resolve the source clip.
    if video is not None and video.filename:
        safe_name = Path(video.filename).name
        dest = INBOX_DIR / f"{job_id}_{safe_name}"
        with open(dest, "wb") as f:
            shutil.copyfileobj(video.file, f)
        resolved_path = str(dest)
    elif video_path:
        p = Path(video_path)
        if not p.is_file():
            raise HTTPException(status_code=422, detail=f"video_path not found: {video_path}")
        resolved_path = str(p)
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either an uploaded 'video' file or a 'video_path'.",
        )

    if not clip_start:
        clip_start = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    job = {
        "job_id": job_id,
        "status": "queued",
        "store_id": store_id,
        "camera_id": camera_id,
        "role": role,
        "clip_start": clip_start,
        "video": resolved_path,
        "events_emitted": None,
        "error": None,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    JOBS[job_id] = job

    # Fire-and-forget: detection proceeds in the background.
    asyncio.create_task(_run_detection(job_id))

    return _job_public(job)


@router.get("/pipeline/jobs")
async def list_jobs():
    """List all detection jobs, newest first."""
    jobs = sorted(
        (_job_public(j) for j in JOBS.values()),
        key=lambda j: j["submitted_at"],
        reverse=True,
    )
    return {"jobs": jobs}


@router.get("/pipeline/jobs/{job_id}")
async def get_job(job_id: str):
    """Get the status of a single detection job."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return _job_public(job)
