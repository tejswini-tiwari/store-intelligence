# Event schema validation and emission.
# Single source of truth for event structure.

import json
import uuid
import os
import pathlib
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

OUTPUT_PATH = pathlib.Path("./data/output/events.jsonl")
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)


def validate_event(event: dict) -> tuple[bool, list[str]]:
    try:
        from app.models import StoreEvent
        StoreEvent(**event)
        return True, []
    except Exception as e:
        errors = []
        if hasattr(e, 'errors'):
            for err in e.errors():
                field = '.'.join(str(x) for x in err.get('loc', []))
                errors.append(f"{field}: {err.get('msg', str(err))}")
        else:
            errors.append(str(e))
        return False, errors


def emit_event(event: dict) -> bool:
    valid, errors = validate_event(event)
    if not valid:
        for err in errors:
            logging.error(f"Event validation failed: {err}")
        return False

    if 'event_id' not in event:
        event['event_id'] = str(uuid.uuid4())

    if isinstance(event.get('timestamp'), datetime):
        event['timestamp'] = event['timestamp'].isoformat().replace('+00:00', 'Z')

    with open(OUTPUT_PATH, 'a') as f:
        f.write(json.dumps(event) + '\n')

    api_url = os.getenv('API_URL')
    if api_url:
        try:
            resp = requests.post(
                f"{api_url}/events/ingest",
                json={"events": [event]},
                timeout=2
            )
            if not resp.ok:
                logging.warning(f"API ingest failed: {resp.status_code}")
        except Exception as e:
            logging.warning(f"API ingest error: {e}")

    ts = event.get('timestamp', '')
    if isinstance(ts, str) and 'T' in ts:
        ts = ts.split('T')[1].replace('Z', '')
        ts = ts[:8]
    else:
        ts = datetime.now().strftime('%H:%M:%S')

    event_type = event.get('event_type', 'UNKNOWN')
    visitor_id = event.get('visitor_id', '-')
    zone_id = event.get('zone_id', '-')
    conf = event.get('confidence', 0.0)

    print(f"[{ts}] {event_type} {visitor_id} zone={zone_id} conf={conf:.2f}")
    return True


def emit_batch(events: list[dict]) -> dict:
    emitted = 0
    failed = 0
    failed_events = []
    for ev in events:
        if emit_event(ev):
            emitted += 1
        else:
            failed += 1
            failed_events.append(ev)
    return {"emitted": emitted, "failed": failed, "failed_events": failed_events}