#!/usr/bin/env python3
"""
Feed a CCTV clip to the running API for live detection.

This is a thin client over POST /pipeline/process. The API runs detection on
the clip in the background and streams the resulting events into the dashboard
in real time. The store_id you pass here is the store the events land under —
nothing is hardcoded.

Examples:
  # Upload a clip and analyse it as the entry camera of a brand-new store
  python pipeline/feed.py --video "data/clips/Store 1/entry.mp4" \
      --store-id ST1008 --camera-id CAM_ENTRY_01 --role entry

  # Point at a clip already inside the API container (no upload)
  python pipeline/feed.py --server-path /app/data/clips/Store\ 1/entry.mp4 \
      --store-id ST1008 --role entry --no-upload

  # Watch the job until it finishes
  python pipeline/feed.py --video clip.mp4 --store-id ST9001 --watch
"""

import argparse
import os
import sys
import time

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")


def main():
    parser = argparse.ArgumentParser(description="Feed a CCTV clip to the API for detection")
    parser.add_argument("--video", help="Path to a local clip to UPLOAD")
    parser.add_argument("--server-path", help="Path to a clip already on the server (no upload)")
    parser.add_argument("--store-id", required=True, help="Store this footage belongs to (you choose it)")
    parser.add_argument("--camera-id", default="CAM_ENTRY_01")
    parser.add_argument("--role", default="entry", choices=["entry", "billing", "floor", "exclude"])
    parser.add_argument("--clip-start", default=None, help="ISO-8601 clip start; defaults to now (UTC)")
    parser.add_argument("--watch", action="store_true", help="Poll job status until it finishes")
    args = parser.parse_args()

    data = {
        "store_id": args.store_id,
        "camera_id": args.camera_id,
        "role": args.role,
    }
    if args.clip_start:
        data["clip_start"] = args.clip_start

    files = None
    if args.video:
        if not os.path.isfile(args.video):
            print(f"ERROR: file not found: {args.video}", file=sys.stderr)
            sys.exit(1)
        files = {"video": (os.path.basename(args.video), open(args.video, "rb"), "video/mp4")}
    elif args.server_path:
        data["video_path"] = args.server_path
    else:
        print("ERROR: provide --video (upload) or --server-path", file=sys.stderr)
        sys.exit(1)

    print(f"Feeding clip to {API_URL}/pipeline/process  (store_id={args.store_id}, role={args.role})")
    resp = requests.post(f"{API_URL}/pipeline/process", data=data, files=files, timeout=30)

    if resp.status_code != 202:
        print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    job = resp.json()
    job_id = job["job_id"]
    print(f"Job accepted: {job_id} (status={job['status']})")
    print(f"Open the dashboard to watch events stream in for {args.store_id}.")

    if args.watch:
        print("Watching job…")
        while True:
            time.sleep(3)
            j = requests.get(f"{API_URL}/pipeline/jobs/{job_id}", timeout=10).json()
            print(f"  status={j['status']} events_emitted={j.get('events_emitted')}")
            if j["status"] in ("done", "failed"):
                if j["status"] == "failed":
                    print(f"  error: {j.get('error')}", file=sys.stderr)
                    sys.exit(1)
                break
    print("Done.")


if __name__ == "__main__":
    main()
