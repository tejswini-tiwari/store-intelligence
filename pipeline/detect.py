# Main detection and tracking script.
# Processes CCTV clips using YOLOv8 and emits structured events.

import cv2
import numpy as np
import argparse
import pathlib
import logging
import json
import time
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from ultralytics import YOLO
from supervision import ByteTrack, Detections
from shapely.geometry import Point, Polygon
from pipeline.tracker import VisitorTracker
from pipeline.emit import emit_event


def load_model(model_path: str) -> YOLO:
    model = YOLO(model_path)
    logging.info(f"Loaded YOLO model from {model_path}")
    return model


def load_store_layout(layout_path: str, store_id: str, camera_id: str) -> dict:
    with open(layout_path, 'r') as f:
        layout = json.load(f)

    store_data = layout.get("stores", {}).get(store_id, {})
    camera_data = store_data.get("cameras", {}).get(camera_id, {})
    open_hours = store_data.get("open_hours", {"open": "09:00", "close": "21:00"})
    entry_threshold_y = camera_data.get("entry_threshold_y", 540)
    zone_polygons = camera_data.get("zone_polygons", {})

    return {
        "entry_threshold_y": entry_threshold_y,
        "zone_polygons": zone_polygons,
        "open_hours": open_hours
    }


def classify_direction(prev_centroid_y: float,
                       curr_centroid_y: float,
                       threshold_y: float) -> str:
    if prev_centroid_y < threshold_y and curr_centroid_y >= threshold_y:
        return "ENTRY"
    elif prev_centroid_y > threshold_y and curr_centroid_y <= threshold_y:
        return "EXIT"
    return None


def detect_staff(frame: np.ndarray, bbox: tuple) -> bool:
    x1, y1, x2, y2 = map(int, bbox)

    h, w = frame.shape[:2]
    if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        logging.warning(f"Bbox {bbox} out of frame bounds ({w}x{h})")
        return False

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    lower_str = os.getenv("STAFF_HSV_LOWER", "100,50,50")
    upper_str = os.getenv("STAFF_HSV_UPPER", "130,255,255")
    lower = tuple(map(int, lower_str.split(',')))
    upper = tuple(map(int, upper_str.split(',')))

    mask = cv2.inRange(hsv, lower, upper)
    mask_pixels = cv2.countNonZero(mask)
    bbox_area = (x2 - x1) * (y2 - y1)

    return mask_pixels > 0.15 * bbox_area


def compute_clip_timestamp(clip_start_iso: str,
                           frame_number: int,
                           fps: float) -> str:
    clip_start = datetime.fromisoformat(clip_start_iso.replace('Z', '+00:00'))
    delta_seconds = frame_number / fps
    ts = clip_start.timestamp() + delta_seconds
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def process_video(video_path: str, store_id: str,
                  camera_id: str, role: str, model: YOLO,
                  layout: dict, clip_start_iso: str) -> None:
    if role == "exclude":
        logging.info(f"Camera {camera_id} has role 'exclude' — skipping")
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logging.error(f"Cannot open video: {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        fps = 15.0

    byte_tracker = ByteTrack(track_thresh=0.45, track_buffer=30, match_thresh=0.8)

    tracker = VisitorTracker(
        reentry_window_seconds=int(os.getenv("REENTRY_WINDOW_SECONDS", "30")),
        reentry_iou_threshold=float(os.getenv("REENTRY_IOU_THRESHOLD", "0.4"))
    )

    prev_centroids = {}
    frame_count = 0
    total_events = 0
    is_entry_cam = role == "entry"
    is_floor_cam = role == "floor"
    is_billing_cam = role == "billing"

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        timestamp = compute_clip_timestamp(clip_start_iso, frame_count, fps)

        results = model(frame, classes=[0], verbose=False)

        boxes_list = []
        scores_list = []
        for r in results:
            if r.boxes is None:
                continue
            boxes = r.boxes.xyxy.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy()
            for box, score, cls in zip(boxes, scores, classes):
                if int(cls) == 0:
                    boxes_list.append(box)
                    scores_list.append(float(score))

        if not boxes_list:
            if frame_count % 300 == 0:
                logging.info(f"Processed frame {frame_count}, no detections")
            continue

        boxes = np.array(boxes_list)
        scores = np.array(scores_list)
        detections = Detections(xyxy=boxes, confidence=scores)
        tracked = byte_tracker.update_with_detections(detections)

        for det in tracked:
            x1, y1, x2, y2 = det[0]
            track_id = det[4]
            confidence = det[5] if len(det) > 5 else det[2]

            if confidence < 0.45:
                logging.debug(f"Low confidence detection: {confidence:.2f} for track {track_id}")

            centroid = ((x1 + x2) / 2, (y1 + y2) / 2)
            is_staff = detect_staff(frame, (x1, y1, x2, y2))

            visitor_id, is_reentry = tracker.assign_visitor_id(
                track_id, (x1, y1, x2, y2), time.time())

            if is_reentry and is_entry_cam:
                emit_event({
                    "store_id": store_id,
                    "camera_id": camera_id,
                    "visitor_id": visitor_id,
                    "event_type": "REENTRY",
                    "timestamp": timestamp,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "zone_id": tracker.active_tracks.get(track_id, {}).get('zone')
                })
                total_events += 1

            if is_entry_cam and not is_floor_cam:
                prev_y = prev_centroids.get(track_id)
                if prev_y is not None:
                    direction = classify_direction(
                        prev_y, centroid[1], layout["entry_threshold_y"])
                    if direction == "ENTRY":
                        emit_event({
                            "store_id": store_id,
                            "camera_id": camera_id,
                            "visitor_id": visitor_id,
                            "event_type": "ENTRY",
                            "timestamp": timestamp,
                            "is_staff": is_staff,
                            "confidence": confidence,
                            "zone_id": tracker.active_tracks.get(track_id, {}).get('zone')
                        })
                        total_events += 1
                    elif direction == "EXIT":
                        visitor_id_closed = tracker.close_session(track_id, time.time())
                        emit_event({
                            "store_id": store_id,
                            "camera_id": camera_id,
                            "visitor_id": visitor_id_closed or visitor_id,
                            "event_type": "EXIT",
                            "timestamp": timestamp,
                            "is_staff": is_staff,
                            "confidence": confidence,
                            "zone_id": None
                        })
                        total_events += 1
                prev_centroids[track_id] = centroid[1]

            zone_events = tracker.update_zone(
                visitor_id,
                tracker.get_current_zone(centroid, layout["zone_polygons"]),
                time.time()
            )
            if zone_events:
                if isinstance(zone_events, list):
                    for ze in zone_events:
                        emit_event({
                            "store_id": store_id,
                            "camera_id": camera_id,
                            "visitor_id": visitor_id,
                            "event_type": ze["emit"],
                            "timestamp": timestamp,
                            "is_staff": is_staff,
                            "confidence": confidence,
                            "zone_id": ze.get("zone_id"),
                            "dwell_ms": ze.get("dwell_ms", 0)
                        })
                        total_events += 1
                else:
                    emit_event({
                        "store_id": store_id,
                        "camera_id": camera_id,
                        "visitor_id": visitor_id,
                        "event_type": zone_events["emit"],
                        "timestamp": timestamp,
                        "is_staff": is_staff,
                        "confidence": confidence,
                        "zone_id": zone_events.get("zone_id"),
                        "dwell_ms": zone_events.get("dwell_ms", 0)
                    })
                    total_events += 1

            current_zone = tracker.get_current_zone(centroid, layout["zone_polygons"])
            if is_billing_cam and current_zone and (current_zone.upper() == "BILLING" or "billing" in current_zone.lower()):
                queue_depth = sum(
                    1 for tid, data in tracker.active_tracks.items()
                    if tracker.get_current_zone(
                        ((data['bbox'][0] + data['bbox'][2]) / 2,
                         (data['bbox'][1] + data['bbox'][3]) / 2),
                        layout["zone_polygons"]
                    ) and
                    (tracker.get_current_zone(
                        ((data['bbox'][0] + data['bbox'][2]) / 2,
                         (data['bbox'][1] + data['bbox'][3]) / 2),
                        layout["zone_polygons"]
                    ).upper() == "BILLING" or
                    "billing" in tracker.get_current_zone(
                        ((data['bbox'][0] + data['bbox'][2]) / 2,
                         (data['bbox'][1] + data['bbox'][3]) / 2),
                        layout["zone_polygons"]
                    ).lower())
                )
                if queue_depth > 0:
                    emit_event({
                        "store_id": store_id,
                        "camera_id": camera_id,
                        "visitor_id": visitor_id,
                        "event_type": "BILLING_QUEUE_JOIN",
                        "timestamp": timestamp,
                        "is_staff": is_staff,
                        "confidence": confidence,
                        "zone_id": current_zone,
                        "metadata": {"queue_depth": queue_depth}
                    })
                    total_events += 1

        if frame_count % 100 == 0:
            tracker.cleanup_expired_exits(time.time())

        if frame_count % 300 == 0:
            logging.info(f"Processed frame {frame_count}, active tracks: {len(tracked)}")

    cap.release()
    logging.info(f"Total frames processed: {frame_count}, total events emitted: {total_events}")


def main():
    parser = argparse.ArgumentParser(description="Process CCTV clips for retail analytics")
    parser.add_argument("--video", required=True, help="Path to input video file")
    parser.add_argument("--store-id", required=True, help="Store identifier")
    parser.add_argument("--camera-id", required=True, help="Camera identifier")
    parser.add_argument("--model", default="yolov8m.pt", help="Path to YOLO model file")
    parser.add_argument("--layout", default="./data/store_layout.json", help="Path to store layout JSON")
    parser.add_argument("--role", required=True, choices=["entry", "billing", "floor", "exclude"],
                        help="Camera role: entry, billing, floor, exclude")
    parser.add_argument("--clip-start", default="2026-03-03T09:00:00Z", help="ISO timestamp for clip start")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"], help="Logging level")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    model = load_model(args.model)
    layout = load_store_layout(args.layout, args.store_id, args.camera_id)
    process_video(args.video, args.store_id, args.camera_id, args.role, model, layout, args.clip_start)


if __name__ == "__main__":
    main()