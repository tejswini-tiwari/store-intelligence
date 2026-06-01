import cv2
import os
import json
from pathlib import Path

CLIPS_DIR = Path("./data/clips")
FRAMES_DIR = Path("./data/frames")
CAMERAS_FILE = Path("./data/cameras.json")

FRAMES_DIR.mkdir(parents=True, exist_ok=True)

with open(CAMERAS_FILE, "r") as f:
    cameras = json.load(f)

clips_found = False
for clip_name, config in cameras.items():
    clip_path = CLIPS_DIR / clip_name
    if not clip_path.exists():
        print(f"WARN: {clip_path} not found, skipping", flush=True)
        continue

    clips_found = True
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        print(f"WARN: Cannot open {clip_path}, skipping", flush=True)
        continue

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        fps = 15.0

    frame_idx = int(30 * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print(f"WARN: Cannot read frame at ~30s from {clip_path}, skipping", flush=True)
        continue

    out_path = FRAMES_DIR / f"{clip_name}.png"
    cv2.imwrite(str(out_path), frame)
    print(f"Saved: {out_path}", flush=True)

if not clips_found:
    print("WARN: No clips found in cameras.json exist in --clips dir", flush=True)