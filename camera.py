"""
GOAL 3: Camera → Pose Detection → Fall/Unresponsive Detection → ClickHouse
===========================================================================
Uses the NEW MediaPipe Tasks API (works with mediapipe 0.10.21+)

SETUP — run these two commands before starting:
    pip install mediapipe opencv-python clickhouse-driver python-dotenv
    python download_model.py     ← run this once to get the pose model file

Controls:
    Q — quit the camera window
"""

import os
import uuid
import json
import time
from datetime import datetime, timezone
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from clickhouse_driver import Client
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────

CH_HOST     = os.getenv("CH_HOST",     "bzit6h15r0.asia-southeast1.gcp.clickhouse.cloud")
CH_PORT     = int(os.getenv("CH_PORT", 9440))
CH_DB       = os.getenv("CH_DATABASE", "default")
CH_USER     = os.getenv("CH_USER",     "default")
CH_PASSWORD = os.getenv("CH_PASSWORD", "o2W9Zxcl1.p3x")

MODEL_PATH     = "pose_landmarker.task"  # downloaded by download_model.py
CAMERA_INDEX   = 0      # 0 = default laptop webcam
DISPLAY_WINDOW = True   # Show live feed with overlays
FPS_TARGET     = 10     # Process 10 frames/sec to save CPU

# Detection thresholds
FALL_ANGLE_THRESH = 30    # Body degrees from horizontal — below this = fallen
STILL_SECONDS     = 10   # Seconds without movement = unresponsive
MISSING_SECONDS   = 5    # Seconds no person in frame = missing
MOTION_THRESH     = 0.015 # Minimum movement to count as "moving"
COOLDOWN_SECONDS  = 15   # Min seconds between repeat alerts of same type


# ──────────────────────────────────────────────────────────
# MEDIAPIPE TASKS API SETUP
# ──────────────────────────────────────────────────────────

def load_pose_detector():
    """
    Load the MediaPipe Pose Landmarker using the new Tasks API.
    Requires pose_landmarker.task file — run download_model.py first.
    """
    if not os.path.exists(MODEL_PATH):
        print(f"[Camera] ⚠  Model file '{MODEL_PATH}' not found!")
        print("         Run:  python download_model.py")
        exit(1)

    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.PoseLandmarkerOptions(
        base_options=base_options,
        output_segmentation_masks=False,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    detector = mp_vision.PoseLandmarker.create_from_options(options)
    print("[Camera] Pose model loaded ✓")
    return detector


# Landmark indices we care about (same as old API)
L_SHOULDER = 11
R_SHOULDER = 12
L_HIP      = 23
R_HIP      = 24


# ──────────────────────────────────────────────────────────
# CLICKHOUSE
# ──────────────────────────────────────────────────────────

def get_client() -> Client:
    return Client(
        host=CH_HOST, port=CH_PORT,
        database=CH_DB, user=CH_USER, password=CH_PASSWORD,
        secure=True
    )


def init_db() -> None:
    """
    Creates camera_events table.
    Goal 4 JOINs this with voice_emergency_logs on event_id.
    """
    client = get_client()
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS camera_events
            (
                event_id       String,
                detected_at    DateTime,
                alert_type     String,
                confidence     Float32,
                pose_state     String,
                still_duration Float32,
                notes          String,
                status         String
            )
            ENGINE = MergeTree()
            ORDER BY (detected_at, event_id)
            TTL detected_at + INTERVAL 30 DAY
        """)
        print("[ClickHouse] camera_events table ready ✓")
    except Exception as e:
        print(f"[ClickHouse] ⚠  init failed: {e}")
    finally:
        client.disconnect()


def store_alert(alert: dict) -> None:
    """Save a camera alert row to ClickHouse."""
    client = get_client()
    try:
        client.execute(
            """
            INSERT INTO camera_events
                (event_id, detected_at, alert_type, confidence,
                 pose_state, still_duration, notes, status)
            VALUES
            """,
            [{
                "event_id":       alert["event_id"],
                "detected_at":    datetime.now(timezone.utc).replace(tzinfo=None),
                "alert_type":     alert["alert_type"],
                "confidence":     alert["confidence"],
                "pose_state":     json.dumps(alert["pose_state"]),
                "still_duration": alert["still_duration"],
                "notes":          alert["notes"],
                "status":         "pending",
            }]
        )
        print(f"[ClickHouse] Stored {alert['alert_type']} alert {alert['event_id'][:8]}… ✓")
    except Exception as e:
        print(f"[ClickHouse] ⚠  Store failed: {e}")
    finally:
        client.disconnect()


# ──────────────────────────────────────────────────────────
# POSE ANALYSIS
# ──────────────────────────────────────────────────────────

def compute_body_angle(landmarks) -> float | None:
    """
    Angle of spine from vertical (0=upright, 90=horizontal/fallen).
    Uses shoulder midpoint vs hip midpoint.
    """
    try:
        ls = landmarks[L_SHOULDER]
        rs = landmarks[R_SHOULDER]
        lh = landmarks[L_HIP]
        rh = landmarks[R_HIP]

        # Need reasonable visibility
        if min(ls.visibility, rs.visibility, lh.visibility, rh.visibility) < 0.3:
            return None

        shoulder_mid = ((ls.x + rs.x) / 2, (ls.y + rs.y) / 2)
        hip_mid      = ((lh.x + rh.x) / 2, (lh.y + rh.y) / 2)

        dx = shoulder_mid[0] - hip_mid[0]
        dy = shoulder_mid[1] - hip_mid[1]

        # Angle from vertical: 0=upright, 90=horizontal
        angle = abs(90 - abs(np.degrees(np.arctan2(abs(dy), abs(dx)))))
        return angle

    except Exception:
        return None


def compute_movement(prev_landmarks, curr_landmarks) -> float:
    """Average movement of all landmarks between two frames (0.0–1.0)."""
    if prev_landmarks is None:
        return 1.0

    total, count = 0.0, 0
    for i in range(min(len(prev_landmarks), len(curr_landmarks))):
        p = prev_landmarks[i]
        c = curr_landmarks[i]
        if p.visibility > 0.3 and c.visibility > 0.3:
            total += np.sqrt((c.x - p.x) ** 2 + (c.y - p.y) ** 2)
            count += 1

    return (total / count) if count > 0 else 0.0


# ──────────────────────────────────────────────────────────
# ALERT STATE TRACKER
# ──────────────────────────────────────────────────────────

class AlertTracker:
    """Tracks alert state over time to avoid false positives and spam."""

    def __init__(self):
        self.last_alert_time  = {}
        self.still_since      = None
        self.missing_since    = None
        self.fall_since       = None
        self.prev_landmarks   = None
        self.movement_history = deque(maxlen=10)

    def can_alert(self, alert_type: str) -> bool:
        last = self.last_alert_time.get(alert_type, 0)
        return (time.time() - last) >= COOLDOWN_SECONDS

    def record_alert(self, alert_type: str):
        self.last_alert_time[alert_type] = time.time()

    def update(self, landmarks, frame_time: float) -> dict | None:
        """
        Process one frame. Returns alert dict if something needs storing,
        otherwise returns None.
        """

        # ── No person in frame ────────────────────────────────────
        if landmarks is None:
            self.fall_since  = None
            self.still_since = None
            self.prev_landmarks = None
            self.movement_history.clear()

            if self.missing_since is None:
                self.missing_since = frame_time
            elif (frame_time - self.missing_since) >= MISSING_SECONDS:
                if self.can_alert("missing"):
                    self.record_alert("missing")
                    dur = frame_time - self.missing_since
                    return self._make_alert(
                        alert_type     = "missing",
                        confidence     = 0.9,
                        still_duration = dur,
                        notes          = f"No person detected for {dur:.0f}s — may have collapsed out of frame",
                        pose_state     = {}
                    )
            return None

        # Person detected — reset missing timer
        self.missing_since = None

        # ── Movement and angle ────────────────────────────────────
        movement     = compute_movement(self.prev_landmarks, landmarks)
        body_angle   = compute_body_angle(landmarks)
        self.movement_history.append(movement)
        avg_movement = float(np.mean(self.movement_history))

        pose_state = {
            "body_angle_deg": round(body_angle, 1) if body_angle is not None else None,
            "movement_score": round(avg_movement, 4),
            "is_moving":      avg_movement > MOTION_THRESH,
        }

        # ── Fall detection ────────────────────────────────────────
        # body_angle close to 90 = horizontal = fallen
        if body_angle is not None and body_angle >= (90 - FALL_ANGLE_THRESH):
            if self.fall_since is None:
                self.fall_since = frame_time
            elif (frame_time - self.fall_since) >= 1.0:   # sustained 1 second
                if self.can_alert("fall"):
                    self.record_alert("fall")
                    self.prev_landmarks = landmarks
                    return self._make_alert(
                        alert_type     = "fall",
                        confidence     = round(min((body_angle / 90.0) * 1.2, 1.0), 2),
                        still_duration = frame_time - self.fall_since,
                        notes          = f"Person appears horizontal — body angle {body_angle:.0f}° from vertical",
                        pose_state     = pose_state
                    )
        else:
            self.fall_since = None

        # ── Unresponsive detection ────────────────────────────────
        if avg_movement < MOTION_THRESH:
            if self.still_since is None:
                self.still_since = frame_time
            elif (frame_time - self.still_since) >= STILL_SECONDS:
                if self.can_alert("unresponsive"):
                    self.record_alert("unresponsive")
                    dur = frame_time - self.still_since
                    self.prev_landmarks = landmarks
                    return self._make_alert(
                        alert_type     = "unresponsive",
                        confidence     = round(min(dur / 30.0, 1.0), 2),
                        still_duration = dur,
                        notes          = f"Person has not moved for {dur:.0f}s",
                        pose_state     = pose_state
                    )
        else:
            self.still_since = None

        self.prev_landmarks = landmarks
        return None

    def _make_alert(self, alert_type, confidence, still_duration, notes, pose_state):
        return {
            "event_id":       str(uuid.uuid4()),
            "alert_type":     alert_type,
            "confidence":     confidence,
            "still_duration": still_duration,
            "notes":          notes,
            "pose_state":     pose_state,
        }


# ──────────────────────────────────────────────────────────
# DISPLAY OVERLAY
# ──────────────────────────────────────────────────────────

ALERT_COLOURS = {
    "fall":         (0,   0,   255),   # Red
    "unresponsive": (0,   140, 255),   # Orange
    "missing":      (0,   255, 255),   # Yellow
    "normal":       (0,   220, 0),     # Green
}

ALERT_ICONS = {
    "fall":         "!! FALL DETECTED !!",
    "unresponsive": "!! UNRESPONSIVE !!",
    "missing":      "!! PERSON MISSING !!",
    "normal":       "MONITORING...",
}

ALERT_SUBTEXTS = {
    "fall":         "Person may be on the ground",
    "unresponsive": "No movement detected",
    "missing":      "No person in frame",
    "normal":       "",
}


def draw_skeleton(frame, landmarks):
    """Draw pose skeleton manually using the new Tasks API landmarks."""
    if not landmarks:
        return

    h, w = frame.shape[:2]

    connections = [
        (11, 12), (11, 13), (13, 15),
        (12, 14), (14, 16),
        (11, 23), (12, 24),
        (23, 24),
        (23, 25), (25, 27),
        (24, 26), (26, 28),
    ]

    points = {}
    for i, lm in enumerate(landmarks):
        if lm.visibility > 0.3:
            px = int(lm.x * w)
            py = int(lm.y * h)
            points[i] = (px, py)
            cv2.circle(frame, (px, py), 4, (0, 255, 0), -1)

    for a, b in connections:
        if a in points and b in points:
            cv2.line(frame, points[a], points[b], (0, 180, 0), 2)


def draw_overlay(frame, alert_type, pose_state, still_duration):
    """
    Draw full-featured alert overlay:
      - Top status bar (always visible)
      - Big centred alert box with flashing text (emergencies only)
      - Bottom info strip with angle / motion / still time
      - Flashing coloured border (emergencies only)
    """
    h, w   = frame.shape[:2]
    colour = ALERT_COLOURS.get(alert_type, (255, 255, 255))
    is_emergency = alert_type in ("fall", "unresponsive", "missing")
    flash  = int(time.time() * 2) % 2 == 0   # toggles every 0.5s

    # ── 1. Top status bar ─────────────────────────────────────────
    cv2.rectangle(frame, (0, 0), (w, 55), (20, 20, 20), -1)
    status_text = f"STATUS: {alert_type.upper()}"
    cv2.putText(frame, status_text,
                (12, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.1, colour, 2)

    # Confidence badge on the right of the top bar
    conf = pose_state.get("movement_score", 0)
    badge = f"Motion: {conf:.4f}"
    cv2.putText(frame, badge,
                (w - 230, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 1)

    # ── 2. Big centred ALERT box (emergencies only) ───────────────
    if is_emergency:
        # Semi-transparent dark background behind alert box
        overlay = frame.copy()
        box_x1, box_y1 = w // 8, h // 3
        box_x2, box_y2 = w - w // 8, h // 3 + 130
        cv2.rectangle(overlay, (box_x1, box_y1), (box_x2, box_y2), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

        # Coloured border around alert box
        border_thickness = 4 if flash else 2
        cv2.rectangle(frame, (box_x1, box_y1), (box_x2, box_y2),
                      colour, border_thickness)

        # Main alert text — flashes on/off
        icon_text = ALERT_ICONS.get(alert_type, "!! ALERT !!")
        if flash:
            text_size = cv2.getTextSize(icon_text, cv2.FONT_HERSHEY_DUPLEX, 1.1, 2)[0]
            text_x    = (w - text_size[0]) // 2
            cv2.putText(frame, icon_text,
                        (text_x, box_y1 + 50),
                        cv2.FONT_HERSHEY_DUPLEX, 1.1, colour, 2)

        # Subtext (always visible inside box)
        subtext = ALERT_SUBTEXTS.get(alert_type, "")
        sub_size = cv2.getTextSize(subtext, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 1)[0]
        sub_x    = (w - sub_size[0]) // 2
        cv2.putText(frame, subtext,
                    (sub_x, box_y1 + 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1)

        # Still/missing duration countdown
        dur_text = f"Duration: {still_duration:.0f}s"
        dur_size = cv2.getTextSize(dur_text, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 1)[0]
        dur_x    = (w - dur_size[0]) // 2
        cv2.putText(frame, dur_text,
                    (dur_x, box_y1 + 115),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, colour, 1)

    # ── 3. Bottom info strip ──────────────────────────────────────
    cv2.rectangle(frame, (0, h - 45), (w, h), (20, 20, 20), -1)
    angle     = pose_state.get("body_angle_deg")
    angle_str = f"{angle:.0f}" if angle is not None else "--"
    info = (f"Body angle: {angle_str}deg   |   "
            f"Still: {still_duration:.0f}s   |   "
            f"Press Q to quit")
    cv2.putText(frame, info,
                (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (160, 160, 160), 1)

    # ── 4. Flashing full-frame border (emergencies only) ─────────
    if is_emergency:
        border = 7 if flash else 2
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), colour, border)


# ──────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────

def run():
    detector = load_pose_detector()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[Camera] ⚠  Cannot open camera {CAMERA_INDEX}")
        print("         Try changing CAMERA_INDEX to 1 or 2")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    tracker            = AlertTracker()
    frame_delay        = 1.0 / FPS_TARGET
    last_process       = 0
    current_alert_type = "normal"
    current_pose_state = {}
    current_still      = 0.0

    print("[Camera] 📷  Monitoring started — press Q to quit")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[Camera] ⚠  Frame read failed")
            break

        now = time.time()

        if (now - last_process) >= frame_delay:
            last_process = now

            # Convert BGR → RGB for MediaPipe
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Run pose detection using new Tasks API
            mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            results   = detector.detect(mp_image)
            landmarks = results.pose_landmarks[0] if results.pose_landmarks else None

            # Draw skeleton
            if landmarks and DISPLAY_WINDOW:
                draw_skeleton(frame, landmarks)

            # Run alert logic
            alert = tracker.update(landmarks, now)

            if alert:
                current_alert_type = alert["alert_type"]
                current_pose_state = alert["pose_state"]
                current_still      = alert["still_duration"]

                print(f"\n[Camera] 🚨  ALERT: {alert['alert_type'].upper()}")
                print(f"         {alert['notes']}")
                print(f"         Confidence: {alert['confidence']:.0%}")
                store_alert(alert)

            elif landmarks is not None:
                current_alert_type = "normal"
                current_pose_state = {
                    "body_angle_deg": compute_body_angle(landmarks),
                    "movement_score": float(np.mean(tracker.movement_history))
                                      if tracker.movement_history else 0.0,
                }
                current_still = 0.0

        if DISPLAY_WINDOW:
            draw_overlay(frame, current_alert_type, current_pose_state, current_still)
            cv2.imshow("Goal 3 — Elder Safety Monitor", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[Camera] Quit.")
                break

    cap.release()
    cv2.destroyAllWindows()
    detector.close()


if __name__ == "__main__":
    init_db()
    run()