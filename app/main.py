# FastAPI application entrypoint.

from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
import uuid


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Store Intelligence API", lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok"}


# TODO: router from app.routers import ingestion, metrics, funnel, anomalies, health
# TODO: app.include_router(ingestion.router, prefix="/events", tags=["events"])
# TODO: app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
# TODO: app.include_router(funnel.router, prefix="/funnel", tags=["funnel"])
# TODO: app.include_router(anomalies.router, prefix="/anomalies", tags=["anomalies"])
# TODO: app.include_router(health.router, prefix="/health", tags=["health"])