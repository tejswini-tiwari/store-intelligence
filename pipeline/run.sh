#!/bin/bash
# Store Intelligence Pipeline Runner
# Processes all CCTV clips and feeds events to the API
#
# Usage:
#   bash pipeline/run.sh
#   bash pipeline/run.sh --clips ./data/clips/ \
#        --layout ./data/store_layout.json
#
# Environment variables:
#   API_URL — defaults to http://localhost:8000
#   YOLO_MODEL — defaults to yolov8m.pt

set -e

CLIPS_DIR="${CLIPS_DIR:-./data/clips}"
LAYOUT_FILE="${LAYOUT_FILE:-./data/store_layout.json}"
API_URL="${API_URL:-http://localhost:8000}"
MODEL="${YOLO_MODEL:-yolov8m.pt}"

echo "========================================"
echo "Store Intelligence Pipeline"
echo "========================================"
echo "Clips dir:   $CLIPS_DIR"
echo "Layout file: $LAYOUT_FILE"
echo "API URL:     $API_URL"
echo "Model:       $MODEL"
echo ""

# Wait for API to be ready
echo "Waiting for API to be ready..."
for i in $(seq 1 30); do
    if curl -sf "$API_URL/health" > /dev/null 2>&1; then
        echo "API is ready."
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: API not ready after 30 seconds"
        exit 1
    fi
    sleep 1
done

# Process each clip
CLIP_COUNT=0
for clip in "$CLIPS_DIR"/*.mp4; do
    if [ ! -f "$clip" ]; then
        echo "No .mp4 clips found in $CLIPS_DIR"
        break
    fi

    # Extract store_id and camera_id from filename
    # Expected format: STORE_BLR_002_CAM_ENTRY_01.mp4
    FILENAME=$(basename "$clip" .mp4)
    STORE_ID=$(echo "$FILENAME" | cut -d_ -f1-3)
    CAMERA_ID=$(echo "$FILENAME" | cut -d_ -f4-)

    echo "Processing: $FILENAME"
    echo "  Store:  $STORE_ID"
    echo "  Camera: $CAMERA_ID"

    python pipeline/detect.py \
        --video "$clip" \
        --store-id "$STORE_ID" \
        --camera-id "$CAMERA_ID" \
        --model "$MODEL" \
        --layout "$LAYOUT_FILE" \
        --log-level INFO

    CLIP_COUNT=$((CLIP_COUNT + 1))
    echo "  Done."
    echo ""
done

echo "========================================"
echo "Pipeline complete. Processed $CLIP_COUNT clips."
echo "Events written to: ./data/output/events.jsonl"
echo "========================================"