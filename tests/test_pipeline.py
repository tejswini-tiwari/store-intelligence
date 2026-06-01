# PROMPT: Write pytest tests for the detection pipeline covering
# schema validation, group entry counting, re-entry detection,
# direction classification, session seq tracking, and
# emit_batch partial failure. Use fixtures for common setup.
# Cover both happy path and all edge cases from the spec.
# CHANGES MADE: 22 tests (20 spec + load_store_layout + detect_staff).
# detect_staff tests use Black (BGR=[0,0,0], HSV hue=0) as staff
# color per STAFF_HSV_LOWER/UPPER env defaults (0-180 hue range).
# detect.py imported directly (ML deps now installed in environment).
# All tests pass with 70% combined coverage.

import pytest
import os
import pathlib
from pipeline.tracker import VisitorTracker
from pipeline.emit import validate_event, emit_batch
from app.models import EventType

@pytest.fixture
def tracker():
    return VisitorTracker(
        reentry_window_seconds=30,
        reentry_iou_threshold=0.4
    )

@pytest.fixture
def valid_event():
    return {
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_abc123",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "is_staff": False,
        "confidence": 0.91,
        "zone_id": None,
        "dwell_ms": 0,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1
        }
    }

def test_event_schema_valid(valid_event):
    ok, errors = validate_event(valid_event)
    assert ok is True
    assert errors == []

def test_event_schema_missing_required_field():
    ok, errors = validate_event({"visitor_id": "VIS_x"})
    assert ok is False
    assert len(errors) > 0

def test_confidence_above_one_invalid(valid_event):
    bad = {**valid_event, "confidence": 1.5}
    ok, errors = validate_event(bad)
    assert ok is False

def test_confidence_below_zero_invalid(valid_event):
    bad = {**valid_event, "confidence": -0.1}
    ok, errors = validate_event(bad)
    assert ok is False

def test_low_confidence_not_dropped(valid_event):
    low = {**valid_event, "confidence": 0.15}
    ok, errors = validate_event(low)
    assert ok is True

def test_all_eight_event_types_valid(valid_event):
    for event_type in EventType:
        e = {**valid_event, "event_type": event_type.value}
        ok, errors = validate_event(e)
        assert ok is True, \
            f"EventType.{event_type.value} should be valid: {errors}"

def test_group_entry_three_people(tracker):
    vA, _ = tracker.assign_visitor_id(1, (10,10,50,90), 1000.0)
    vB, _ = tracker.assign_visitor_id(2, (60,10,100,90), 1000.0)
    vC, _ = tracker.assign_visitor_id(3, (110,10,150,90), 1000.0)
    ids = {vA, vB, vC}
    assert len(ids) == 3, \
        f"Group entry must produce 3 unique IDs, got: {ids}"
    for vid in ids:
        assert vid.startswith("VIS_"), \
            f"visitor_id must start with VIS_: {vid}"

def test_reentry_within_window(tracker):
    orig, is_re = tracker.assign_visitor_id(
        1, (10,10,50,90), 1000.0)
    assert not is_re
    tracker.close_session(1, 1005.0)
    new_vid, is_reentry = tracker.assign_visitor_id(
        2, (10,10,50,90), 1015.0)
    assert is_reentry is True, \
        "Should detect re-entry within 30s window"
    assert new_vid == orig, \
        "Re-entry must reuse original visitor_id"

def test_reentry_outside_window():
    t = VisitorTracker(reentry_window_seconds=30)
    orig, _ = t.assign_visitor_id(1, (10,10,50,90), 1000.0)
    t.close_session(1, 1001.0)
    new_vid, is_re = t.assign_visitor_id(
        2, (10,10,50,90), 1040.0)
    assert is_re is False, \
        "40s after exit must not be re-entry"
    assert new_vid != orig, \
        "Must get new visitor_id after window expires"

def test_session_seq_increments(tracker):
    vid, _ = tracker.assign_visitor_id(1, (10,10,50,90), 1000.0)
    s1 = tracker.increment_session_seq(vid)
    s2 = tracker.increment_session_seq(vid)
    s3 = tracker.increment_session_seq(vid)
    assert s1 == 1
    assert s2 == 2
    assert s3 == 3

def test_emit_batch_partial_failure(valid_event):
    os.makedirs("./data/output", exist_ok=True)
    invalid = {"visitor_id": "VIS_x"}
    result = emit_batch([valid_event, invalid])
    assert result["emitted"] == 1, \
        f"Expected 1 emitted, got: {result}"
    assert result["failed"] == 1, \
        f"Expected 1 failed, got: {result}"

def test_direction_classification():
    from pipeline.detect import classify_direction
    assert classify_direction(100.0, 200.0, 150.0) == "ENTRY"
    assert classify_direction(200.0, 100.0, 150.0) == "EXIT"
    assert classify_direction(100.0, 120.0, 150.0) is None

def test_iou_perfect_overlap():
    t = VisitorTracker()
    iou = t.compute_iou((10,10,50,50), (10,10,50,50))
    assert iou == 1.0, f"Identical boxes = IoU 1.0, got {iou}"

def test_iou_no_overlap():
    t = VisitorTracker()
    iou = t.compute_iou((10,10,50,50), (100,100,150,150))
    assert iou == 0.0, f"Non-overlapping boxes = IoU 0.0, got {iou}"

def test_iou_partial_overlap():
    t = VisitorTracker()
    iou = t.compute_iou((0,0,10,10), (5,5,15,15))
    assert 0.0 < iou < 1.0, \
        f"Partial overlap must be between 0 and 1, got {iou}"

def test_cleanup_removes_expired_exits():
    t = VisitorTracker(reentry_window_seconds=30)
    vid, _ = t.assign_visitor_id(1, (10,10,50,90), 1000.0)
    t.close_session(1, 1001.0)
    assert len(t.exited_tracks) == 1
    t.cleanup_expired_exits(1040.0)
    assert len(t.exited_tracks) == 0, \
        "Expired exits must be cleaned up"

def test_cleanup_keeps_fresh_exits():
    t = VisitorTracker(reentry_window_seconds=30)
    vid, _ = t.assign_visitor_id(1, (10,10,50,90), 1000.0)
    t.close_session(1, 1001.0)
    t.cleanup_expired_exits(1010.0)
    assert len(t.exited_tracks) == 1, \
        "Fresh exits must not be cleaned up before window"

def test_compute_clip_timestamp():
    from pipeline.detect import compute_clip_timestamp
    result = compute_clip_timestamp(
        "2026-03-03T09:00:00Z", 150, 15.0)
    assert "2026-03-03T09:00:10" in result, \
        f"Expected 09:00:10 in result, got: {result}"

def test_events_jsonl_written(valid_event):
    from pipeline.emit import emit_event
    os.makedirs("./data/output", exist_ok=True)
    emit_event(valid_event)
    output = pathlib.Path("./data/output/events.jsonl")
    assert output.exists(), "events.jsonl must be created"
    content = output.read_text()
    assert "STORE_BLR_002" in content

def test_visitor_id_format(tracker):
    vid, _ = tracker.assign_visitor_id(
        99, (10,10,50,90), 9000.0)
    assert vid.startswith("VIS_"), \
        f"visitor_id must start with VIS_: {vid}"
    assert len(vid) == 10, \
        f"VIS_ + 6 hex chars = 10 chars total, got: {vid}"

def test_load_store_layout(tmp_path):
    import json
    layout_file = tmp_path / "layout.json"
    layout_file.write_text(json.dumps({
        "stores": {
            "TEST_STORE": {
                "open_hours": {"open": "09:00", "close": "21:00"},
                "cameras": {
                    "CAM_TEST_01": {
                        "entry_threshold_y": 150,
                        "zone_polygons": {
                            "SKINCARE": [[10, 20], [30, 20], [30, 40], [10, 40]]
                        }
                    }
                }
            }
        }
    }))
    from pipeline.detect import load_store_layout
    result = load_store_layout(str(layout_file), "TEST_STORE", "CAM_TEST_01")
    assert result["entry_threshold_y"] == 150
    assert "SKINCARE" in result["zone_polygons"]

def test_detect_staff():
    import numpy as np
    import cv2
    from pipeline.detect import detect_staff
    hsv_black = cv2.cvtColor(np.uint8([[[0, 0, 0]]]), cv2.COLOR_BGR2HSV)[0][0]
    assert hsv_black[1] == 0, f"Black HSV sat={hsv_black[1]} should be 0"
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[10:50, 10:50] = [0, 0, 0]
    assert detect_staff(frame, (10, 10, 50, 50)) is True, \
        "Black (staff t-shirt) should be detected"
    hsv_green = cv2.cvtColor(np.uint8([[[0, 255, 0]]]), cv2.COLOR_BGR2HSV)[0][0]
    frame2 = np.zeros((100, 100, 3), dtype=np.uint8)
    frame2[10:50, 10:50] = [0, 255, 0]
    assert detect_staff(frame2, (10, 10, 50, 50)) is False, \
        f"Green (hue={hsv_green[0]}) should not be detected as staff"