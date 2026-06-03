#!/usr/bin/env python3
"""
Loader for data/sample_eventsbe42122.jsonl.
Converts the third-party event schema to our internal StoreEvent schema,
then POSTs to /events/ingest.

Event type mapping:
  entry          -> ENTRY
  exit           -> EXIT
  zone_entered   -> ZONE_ENTER
  zone_exited    -> ZONE_EXIT
  queue_completed-> BILLING_QUEUE_JOIN (queue served = billing completed)
  queue_abandoned-> BILLING_QUEUE_ABANDON

Field mapping:
  id_token        -> visitor_id
  store_code      -> store_id (store_1076 -> ST1076, or from store_id field if present)
  camera_id       -> camera_id
  event_timestamp / event_time -> timestamp (UTC)
  is_staff        -> is_staff
  gender_pred     -> metadata.gender_pred
  age_pred        -> metadata.age_pred
  zone_id         -> zone_id (for zone events)
  track_id        -> metadata.track_id
  queue_join_ts   -> metadata.queue_join_ts
  queue_served_ts -> metadata.queue_served_ts
  queue_exit_ts   -> metadata.queue_exit_ts
  wait_seconds    -> metadata.wait_seconds
  queue_position_at_join -> metadata.queue_position_at_join
  abandoned       -> metadata.abandoned (true/false)

Usage:
  python pipeline/load_events.py --file data/sample_eventsbe42122.jsonl
"""

import argparse
import asyncio
import json
import sys
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

API_URL = os.getenv("API_URL", "http://localhost:8000")


def map_event_type(raw_type: str) -> str:
    mapping = {
        "entry": "ENTRY",
        "exit": "EXIT",
        "zone_entered": "ZONE_ENTER",
        "zone_exited": "ZONE_EXIT",
        "queue_completed": "BILLING_QUEUE_JOIN",
        "queue_abandoned": "BILLING_QUEUE_ABANDON",
    }
    return mapping.get(raw_type.lower(), raw_type.upper())


def convert_event(raw: dict) -> dict:
    event_type = map_event_type(raw.get("event_type", ""))

    store_code = raw.get("store_code", "")
    if raw.get("store_id"):
        store_id = raw["store_id"]
    elif store_code:
        store_id = store_code.upper().replace("STORE_", "ST")
    else:
        store_id = "UNKNOWN"

    if event_type == "BILLING_QUEUE_JOIN":
        timestamp_str = raw.get("queue_join_ts") or raw.get("event_timestamp")
    elif event_type in ("ZONE_ENTER", "ZONE_EXIT"):
        timestamp_str = raw.get("event_time") or raw.get("event_timestamp")
    else:
        timestamp_str = raw.get("event_timestamp")

    if timestamp_str:
        if "+" in timestamp_str or timestamp_str.endswith("Z"):
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(timestamp_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        timestamp = ts.isoformat().replace("+00:00", "Z")
    else:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    visitor_id = str(raw.get("id_token") or raw.get("track_id") or uuid.uuid4().hex[:6])
    if not visitor_id.startswith("VIS_"):
        visitor_id = f"VIS_{visitor_id}"

    metadata = {
        "queue_depth": raw.get("queue_position_at_join"),
        "sku_zone": raw.get("zone_name"),
        "session_seq": 0,
    }

    if raw.get("gender_pred"):
        metadata["gender_pred"] = raw["gender_pred"]
    if raw.get("age_pred"):
        metadata["age_pred"] = raw["age_pred"]
    if raw.get("track_id"):
        metadata["track_id"] = raw["track_id"]
    if raw.get("queue_join_ts"):
        metadata["queue_join_ts"] = raw["queue_join_ts"]
    if raw.get("queue_served_ts"):
        metadata["queue_served_ts"] = raw["queue_served_ts"]
    if raw.get("queue_exit_ts"):
        metadata["queue_exit_ts"] = raw["queue_exit_ts"]
    if raw.get("wait_seconds"):
        metadata["wait_seconds"] = raw["wait_seconds"]
    if raw.get("abandoned") is not None:
        metadata["abandoned"] = raw["abandoned"]
    if raw.get("zone_id"):
        metadata["zone_id_raw"] = raw["zone_id"]
    if raw.get("zone_type"):
        metadata["zone_type"] = raw["zone_type"]

    return {
        "event_id": raw.get("queue_event_id") or str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": raw.get("camera_id", "UNKNOWN"),
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": raw.get("zone_id"),
        "dwell_ms": 0,
        "is_staff": raw.get("is_staff", False),
        "confidence": 0.9,
        "metadata": metadata,
    }


async def load_events(file_path: str, batch_size: int = 100) -> dict:
    with open(file_path, "r") as f:
        lines = f.readlines()

    total = len(lines)
    ingested = 0
    skipped = 0
    errors = 0
    error_list = []

    for i in range(0, total, batch_size):
        batch_lines = lines[i : i + batch_size]
        batch = []
        for line in batch_lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                converted = convert_event(raw)
                batch.append(converted)
            except Exception as e:
                errors += 1
                error_list.append(f"JSON parse error: {e}")

        if not batch:
            continue

        try:
            resp = requests.post(
                f"{API_URL}/events/ingest",
                json={"events": batch},
                timeout=10,
            )
            if resp.ok:
                result = resp.json()
                ingested += result.get("ingested", 0)
                skipped += result.get("skipped", 0)
                error_list.extend(result.get("errors", []))
            else:
                errors += len(batch)
                error_list.append(f"API error {resp.status_code}: {resp.text}")
        except Exception as e:
            errors += len(batch)
            error_list.append(f"Connection error: {e}")

        print(f"  Processed {min(i + batch_size, total)}/{total} events")

    return {
        "total": total,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "error_list": error_list[:20],
    }


def main():
    parser = argparse.ArgumentParser(description="Load sample events JSONL into the API")
    parser.add_argument("--file", required=True, help="Path to sample_eventsbe42122.jsonl")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for ingestion")
    args = parser.parse_args()

    print(f"Loading events from: {args.file}")
    print(f"API URL: {API_URL}")
    print("")

    result = asyncio.run(load_events(args.file, args.batch_size))

    print("")
    print(f"Total events in file: {result['total']}")
    print(f"Ingested: {result['ingested']}")
    print(f"Skipped (duplicates): {result['skipped']}")
    print(f"Errors: {result['errors']}")
    if result["error_list"]:
        print("")
        print("First few errors:")
        for e in result["error_list"][:5]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()