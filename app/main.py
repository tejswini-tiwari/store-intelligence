from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import structlog, uuid, time, logging, os

from app.database import init_db, engine

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB. Shutdown: dispose engine."""
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """
    Log every request with:
    - trace_id: new uuid4 per request
    - store_id: extracted from path if present
      (path like /stores/STORE_BLR_002/metrics → STORE_BLR_002)
    - endpoint: request.url.path
    - method: request.method
    - latency_ms: time taken in milliseconds
    - status_code: response status
    - event_count: only for POST /events/ingest,
      extract from request body if possible

    Use structlog.get_logger().info() for structured JSON logs.
    Store trace_id in request.state.trace_id for use in handlers.
    """
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id

    path = request.url.path
    store_id = None
    if "/stores/" in path:
        parts = path.split("/stores/")
        if len(parts) > 1:
            store_id = parts[1].split("/")[0]

    start_time = time.time()
    response = await call_next(request)
    latency_ms = (time.time() - start_time) * 1000

    event_count = None
    if request.method == "POST" and path == "/events/ingest":
        try:
            body = await request.body()
            if body:
                import json
                data = json.loads(body)
                event_count = len(data.get("events", []))
        except Exception:
            pass

    log_data = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": path,
        "method": request.method,
        "latency_ms": round(latency_ms, 2),
        "status_code": response.status_code,
    }
    if event_count is not None:
        log_data["event_count"] = event_count

    logger.info("request completed", **log_data)

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch ALL unhandled exceptions.
    Return HTTP 503 if it looks like a DB error
    (check if "database" or "connection" in str(exc).lower())
    Return HTTP 500 for everything else.

    Response body always:
    {
        "error": "internal_error" | "service_unavailable",
        "trace_id": request.state.trace_id,
        "message": "An unexpected error occurred"
    }
    NEVER include the actual exception message or traceback.
    Log the full exception internally with structlog.
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error("unhandled exception", trace_id=trace_id, exc=str(exc))

    exc_lower = str(exc).lower()
    if "database" in exc_lower or "connection" in exc_lower:
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "trace_id": trace_id,
                "message": "An unexpected error occurred"
            }
        )

    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "trace_id": trace_id,
            "message": "An unexpected error occurred"
        }
    )


from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router
from app.heatmap import router as heatmap_router
from app.events import router as events_router
from app.stores import router as stores_router
from app.jobs import router as jobs_router

app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(anomalies_router)
app.include_router(health_router)
app.include_router(heatmap_router)
app.include_router(events_router)
app.include_router(stores_router)
app.include_router(jobs_router)