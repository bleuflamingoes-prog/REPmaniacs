import os
import json
import uuid
import time
import requests
from datetime import datetime
from openai import OpenAI

# ── CONFIG ────────────────────────────────────────────────────────────────────
CLICKHOUSE_HOST     = "https://bzit6h15r0.asia-southeast1.gcp.clickhouse.cloud:8443"
CLICKHOUSE_USER     = "default"
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "your_password_here")

OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "your_openai_key_here")
client              = OpenAI(api_key=OPENAI_API_KEY)

POLL_INTERVAL_SEC   = 5
CAMERA_ALERT_TYPES  = {"fall", "unresponsive"}  # Only these trigger dispatch

# ── DISPATCH TEAMS ────────────────────────────────────────────────────────────
DISPATCH_TEAMS = {
    "ambulance":   "🚑 Medical emergency — ambulance dispatched",
    "police":      "🚓 Security/safety threat — police dispatched",
    "fire":        "🚒 Fire/gas hazard — SCDF dispatched",
    "social_work": "👤 Non-urgent welfare concern — social worker notified",
    "none":        "✅ No action needed",
}

# ── CLICKHOUSE HELPERS ────────────────────────────────────────────────────────
def ch_query(sql: str):
    resp = requests.post(
        CLICKHOUSE_HOST,
        data=sql,
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.text

def fetch_new_voice_transcripts(already_seen: set) -> list:
    """Reads new rows from speech_events (audio team)."""
    sql = """
        SELECT event_id, transcript, confidence, language, audio_path, timestamp
        FROM speech_events
        ORDER BY timestamp DESC
        LIMIT 20
        FORMAT JSON
    """
    result = ch_query(sql)
    rows = json.loads(result).get("data", [])
    return [r for r in rows if r["event_id"] not in already_seen]

def fetch_new_camera_events(already_seen: set) -> list:
    """
    Reads new rows from camera_events.
    Only returns rows where alert_type is 'fall' or 'unresponsive'.
    """
    sql = """
        SELECT event_id, alert_type, confidence, pose_state, still_duration, notes, detected_at
        FROM camera_events
        WHERE alert_type IN ('fall', 'unresponsive')
        ORDER BY detected_at DESC
        LIMIT 20
        FORMAT JSON
    """
    result = ch_query(sql)
    rows = json.loads(result).get("data", [])
    return [r for r in rows if r["event_id"] not in already_seen]

def save_dispatch_result(event_id: str, transcript: str, urgency: str,
                          confidence: float, location: str, audio_path: str):
    """Saves the classified result into gpt_classifier for the dashboard."""
    sql = f"""
    INSERT INTO gpt_classifier
        (event_id, timestamp, caller_id, transcript, urgency_level, confidence, location, audio_url)
    VALUES (
        '{event_id}',
        '{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}',
        'PAB_USER',
        '{transcript.replace("'", "''")}',
        '{urgency}',
        {confidence},
        '{location}',
        '{audio_path}'
    )
    """
    ch_query(sql)

# ── GPT CLASSIFIER ────────────────────────────────────────────────────────────
def classify_voice(transcript: str, location: str = "Unknown") -> dict:
    """Classify a voice transcript using GPT."""
    prompt = f"""
You are an emergency dispatcher AI for elderly care in Singapore.

Analyse this voice transcript and return ONLY a JSON object with these fields:
- urgency_level: "high", "medium", or "low"
- confidence: float between 0 and 1
- dispatch_team: one of "ambulance", "police", "fire", "social_work", "none"
- reason: one sentence explanation

Transcript: "{transcript}"
Location: {location}

Return ONLY valid JSON, no other text.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def classify_camera(alert_type: str, pose_state: str, still_duration: float, notes: str) -> dict:
    """Classify a camera event using GPT."""
    prompt = f"""
You are an emergency dispatcher AI for elderly care in Singapore.

A camera has detected a physical alert. Analyse this and return ONLY a JSON object with:
- urgency_level: "high", "medium", or "low"
- confidence: float between 0 and 1
- dispatch_team: one of "ambulance", "police", "fire", "social_work", "none"
- reason: one sentence explanation

Alert type: "{alert_type}"
Pose state: "{pose_state}"
Still duration: {still_duration} seconds
Notes: "{notes}"

Return ONLY valid JSON, no other text.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ── DISPATCH ACTION ───────────────────────────────────────────────────────────
def dispatch(event_id: str, transcript: str, location: str, result: dict,
             audio_path: str = "", source: str = "VOICE"):
    team    = result.get("dispatch_team", "none")
    urgency = result.get("urgency_level", "low")
    conf    = result.get("confidence", 0)
    reason  = result.get("reason", "")
    action  = DISPATCH_TEAMS.get(team, "Unknown team")

    source_icon = "🎙️" if source == "VOICE" else "📷"

    print(f"\n{'='*60}")
    print(f"  🆘 NEW EMERGENCY — SOURCE: {source_icon} {source}")
    print(f"  EVENT:      {event_id}")
    print(f"  TRANSCRIPT: {transcript[:80]}")
    print(f"  URGENCY:    {urgency.upper()}  |  CONFIDENCE: {conf:.0%}")
    print(f"  REASON:     {reason}")
    print(f"  ACTION:     {action}")
    print(f"{'='*60}\n")

    save_dispatch_result(event_id, transcript, urgency, conf, location, audio_path)

# ── POLLING LOOP ──────────────────────────────────────────────────────────────
def run_polling_loop():
    print("\n" + "="*60)
    print("🤖 CLASSIFIER RUNNING — polling for new events...")
    print(f"   Checking every {POLL_INTERVAL_SEC} seconds. Press Ctrl+C to stop.")
    print(f"   Sources: 🎙️  speech_events  |  📷 camera_events (fall, unresponsive)")
    print("="*60 + "\n")

    seen_voice  = set()
    seen_camera = set()

    while True:
        try:
            # ── VOICE transcripts ─────────────────────────────────────────
            voice_rows = fetch_new_voice_transcripts(seen_voice)
            if not voice_rows:
                print(f"  🎙️  No new voice transcripts...")
            else:
                for row in voice_rows:
                    event_id   = row["event_id"]
                    transcript = row["transcript"]
                    audio_path = row.get("audio_path", "")
                    print(f"  🎙️  Voice received: '{transcript[:60]}...'")
                    result = classify_voice(transcript)
                    dispatch(event_id, transcript, "Unknown", result, audio_path, source="VOICE")
                    seen_voice.add(event_id)

            # ── CAMERA events ─────────────────────────────────────────────
            camera_rows = fetch_new_camera_events(seen_camera)
            if not camera_rows:
                print(f"  📷  No new camera alerts...")
            else:
                for row in camera_rows:
                    event_id      = row["event_id"]
                    alert_type    = row["alert_type"]
                    pose_state    = row.get("pose_state", "")
                    still_duration = row.get("still_duration", 0)
                    notes         = row.get("notes", "")
                    transcript    = f"[CAMERA] {alert_type} detected — pose: {pose_state}, still for {still_duration}s. {notes}"
                    print(f"  📷  Camera alert: {alert_type.upper()} detected!")
                    result = classify_camera(alert_type, pose_state, still_duration, notes)
                    dispatch(event_id, transcript, "Unknown", result, source="CAMERA")
                    seen_camera.add(event_id)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        time.sleep(POLL_INTERVAL_SEC)

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_polling_loop()

