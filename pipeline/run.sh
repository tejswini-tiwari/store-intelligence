#!/bin/bash
# Store Intelligence Pipeline Runner
# Reads data/cameras.json to get store_id, camera_id, role, clip_start per clip.
#
# Usage:
#   bash pipeline/run.sh
#   bash pipeline/run.sh --clips ./data/clips/
#
# Environment variables:
#   API_URL — defaults to http://localhost:8000
#   YOLO_MODEL — defaults to yolov8m.pt

set -e

CLIPS_DIR="${CLIPS_DIR:-./data/clips}"
CAMERAS_FILE="${CAMERAS_FILE:-./data/cameras.json}"
LAYOUT_FILE="${LAYOUT_FILE:-./data/store_layout.json}"
API_URL="${API_URL:-http://localhost:8000}"
MODEL="${YOLO_MODEL:-yolov8m.pt}"

echo "========================================"
echo "Store Intelligence Pipeline"
echo "========================================"
echo "Clips dir:    $CLIPS_DIR"
echo "Cameras file: $CAMERAS_FILE"
echo "Layout file:  $LAYOUT_FILE"
echo "API URL:      $API_URL"
echo "Model:        $MODEL"
echo ""

python3 -c "
import sys
import json
import os
import subprocess

cameras_file = sys.argv[1]
clips_dir = sys.argv[2]
model = sys.argv[3] if len(sys.argv) > 3 else 'yolov8m.pt'
layout_file = sys.argv[4] if len(sys.argv) > 4 else './data/store_layout.json'

with open(cameras_file, 'r') as f:
    cameras = json.load(f)

has_clips = False
for clip_name, config in cameras.items():
    clip_path = os.path.join(clips_dir, clip_name)
    if not os.path.isfile(clip_path):
        print(f'WARN: {clip_path} not found, skipping', file=sys.stderr)
        continue

    has_clips = True
    role = config.get('role', '')
    clip_start = config.get('clip_start', '')

    if role == 'exclude':
        print(f'SKIP: {clip_name} has role exclude -- no events emitted')
        continue

    store_id = config.get('store_id', '')
    camera_id = config.get('camera_id', '')

    print(f'Processing: {clip_name}')
    print(f'  Store:  {store_id}')
    print(f'  Camera: {camera_id}')
    print(f'  Role:   {role}')
    print(f'  Clip:   {clip_start}')

    result = subprocess.run([
        sys.executable, 'pipeline/detect.py',
        '--video', clip_path,
        '--store-id', store_id,
        '--camera-id', camera_id,
        '--role', role,
        '--clip-start', clip_start,
        '--model', model,
        '--layout', layout_file,
        '--log-level', 'INFO'
    ])
    if result.returncode != 0:
        print(f'ERROR: detect.py failed for {clip_name}', file=sys.stderr)
    else:
        print(f'Done: {clip_name}')

if not has_clips:
    print('WARN: No clips found in cameras.json exist in --clips dir', file=sys.stderr)
" "$CAMERAS_FILE" "$CLIPS_DIR" "$MODEL" "$LAYOUT_FILE"

echo ""
echo "========================================"
echo "Pipeline complete."
echo "========================================"