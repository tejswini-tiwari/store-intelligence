# Re-ID and multi-object tracking logic.
# Assigns visitor_id tokens and manages session lifecycle.

import uuid
import time
from shapely.geometry import Point, Polygon


class VisitorTracker:
    def __init__(self, reentry_window_seconds: int = 30,
                 reentry_iou_threshold: float = 0.4):
        self.active_tracks = {}
        self.exited_tracks = {}
        self.reentry_window = reentry_window_seconds
        self.reentry_iou_threshold = reentry_iou_threshold

    def compute_iou(self, bbox1: tuple, bbox2: tuple) -> float:
        x1_1, y1_1, x2_1, y2_1 = bbox1
        x1_2, y1_2, x2_2, y2_2 = bbox2

        xi1 = max(x1_1, x1_2)
        yi1 = max(y1_1, y1_2)
        xi2 = min(x2_1, x2_2)
        yi2 = min(y2_1, y2_2)

        if xi2 <= xi1 or yi2 <= yi1:
            return 0.0

        inter_area = (xi2 - xi1) * (yi2 - yi1)
        bbox1_area = (x2_1 - x1_1) * (y2_1 - y1_1)
        bbox2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
        union_area = bbox1_area + bbox2_area - inter_area

        return inter_area / union_area if union_area > 0 else 0.0

    def assign_visitor_id(self, track_id: int, bbox: tuple,
                          current_time: float) -> tuple[str, bool]:
        if track_id in self.active_tracks:
            self.active_tracks[track_id]['bbox'] = bbox
            self.active_tracks[track_id]['last_seen'] = current_time
            return self.active_tracks[track_id]['visitor_id'], False

        for vid, exit_data in list(self.exited_tracks.items()):
            time_diff = current_time - exit_data['exit_time']
            if time_diff <= self.reentry_window:
                iou = self.compute_iou(bbox, exit_data['bbox'])
                if iou >= self.reentry_iou_threshold:
                    del self.exited_tracks[vid]
                    self.active_tracks[track_id] = {
                        'visitor_id': vid,
                        'bbox': bbox,
                        'last_seen': current_time,
                        'zone': None,
                        'zone_enter_time': None,
                        'session_seq': 0,
                        'is_active': True,
                        'billing_visited': False
                    }
                    return vid, True

        new_vid = 'VIS_' + uuid.uuid4().hex[:6]
        self.active_tracks[track_id] = {
            'visitor_id': new_vid,
            'bbox': bbox,
            'last_seen': current_time,
            'zone': None,
            'zone_enter_time': None,
            'session_seq': 0,
            'is_active': True,
            'billing_visited': False
        }
        return new_vid, False

    def close_session(self, track_id: int, current_time: float) -> str | None:
        if track_id not in self.active_tracks:
            return None
        visitor_id = self.active_tracks[track_id]['visitor_id']
        bbox = self.active_tracks[track_id]['bbox']
        billing_visited = self.active_tracks[track_id].get('billing_visited', False)
        del self.active_tracks[track_id]
        self.exited_tracks[visitor_id] = {
            'bbox': bbox,
            'exit_time': current_time,
            'billing_visited': billing_visited
        }
        return visitor_id

    def get_current_zone(self, centroid: tuple,
                         zone_polygons: dict) -> str | None:
        point = Point(centroid)
        best_zone = None
        best_area = float('inf')

        for zone_id, vertices in zone_polygons.items():
            poly = Polygon(vertices)
            if poly.contains(point):
                area = poly.area
                if area < best_area:
                    best_area = area
                    best_zone = zone_id

        return best_zone

    def update_zone(self, visitor_id: str, new_zone: str | None,
                    current_time: float) -> dict | None:
        track_id = None
        for tid, data in self.active_tracks.items():
            if data['visitor_id'] == visitor_id:
                track_id = tid
                break

        if track_id is None:
            return None

        track = self.active_tracks[track_id]
        old_zone = track.get('zone')
        events = []

        if old_zone is not None and new_zone is not None and old_zone != new_zone:
            if track.get('zone_enter_time') is not None:
                dwell_ms = int((current_time - track['zone_enter_time']) * 1000)
            else:
                dwell_ms = 0
            events.append({'emit': 'ZONE_EXIT', 'zone_id': old_zone, 'dwell_ms': dwell_ms})
            track['zone'] = new_zone
            track['zone_enter_time'] = current_time
            events.append({'emit': 'ZONE_ENTER', 'zone_id': new_zone, 'dwell_ms': 0})
        elif new_zone is None and old_zone is not None:
            if track.get('zone_enter_time') is not None:
                dwell_ms = int((current_time - track['zone_enter_time']) * 1000)
            else:
                dwell_ms = 0
            events.append({'emit': 'ZONE_EXIT', 'zone_id': old_zone, 'dwell_ms': dwell_ms})
            track['zone'] = None
            track['zone_enter_time'] = None
        elif new_zone is not None and old_zone is None:
            track['zone'] = new_zone
            track['zone_enter_time'] = current_time
            events.append({'emit': 'ZONE_ENTER', 'zone_id': new_zone, 'dwell_ms': 0})
        elif new_zone is not None and old_zone == new_zone:
            if track.get('zone_enter_time') is not None:
                elapsed = current_time - track['zone_enter_time']
                if elapsed >= 30:
                    dwell_ms = int(elapsed * 1000)
                    events.append({'emit': 'ZONE_DWELL', 'zone_id': new_zone, 'dwell_ms': dwell_ms})
                    track['zone_enter_time'] = current_time

        return events if events else None

    def increment_session_seq(self, visitor_id: str) -> int:
        for tid, data in self.active_tracks.items():
            if data['visitor_id'] == visitor_id:
                data['session_seq'] += 1
                return data['session_seq']
        return 1

    def cleanup_expired_exits(self, current_time: float) -> None:
        expired = [vid for vid, data in self.exited_tracks.items()
                   if current_time - data['exit_time'] > self.reentry_window]
        for vid in expired:
            del self.exited_tracks[vid]