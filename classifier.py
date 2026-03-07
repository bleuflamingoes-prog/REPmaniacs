import os
import json
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

# ── DISPATCH MATRIX ───────────────────────────────────────────────────────────
# (dispatch_team, urgency_level) → (number, action description)
DISPATCH_MATRIX = {
    ("ambulance", "high"):   ("995",             "🚑 EMERGENCY — Calling 995 for Emergency Ambulance"),
    ("ambulance", "medium"): ("1777",             "🚑 URGENT — Calling 1777 SCDF Non-Emergency Ambulance"),
    ("ambulance", "low"):    ("POLYCLINIC",       "🏥 LOW — Alerting nearest polyclinic for welfare check"),

    ("fire", "high"):        ("995",              "🚒 EMERGENCY — Calling 995 for SCDF Fire & Rescue"),
    ("fire", "medium"):      ("1777",             "🚒 URGENT — Calling 1777 SCDF Non-Emergency"),
    ("fire", "low"):         ("BLDG-MGMT",        "🏢 LOW — Alerting building management"),

    ("police", "high"):      ("999",              "🚓 EMERGENCY — Calling 999 for Police"),
    ("police", "medium"):    ("1800-255-0000",    "🚓 URGENT — Calling 1800-255-0000 Police Non-Emergency"),
    ("police", "low"):       ("COMMUNITY-SAFETY", "👮 LOW — Flagging to community safety officer"),

    ("social_work", "high"): ("1800-225-5227",   "👤 URGENT — Calling 1800-CALL-PAP for urgent welfare"),
    ("social_work", "medium"):("1800-225-5227",  "👤 WELFARE — Calling 1800-CALL-PAP for welfare check"),
    ("social_work", "low"):  ("FRIENDLY-VISIT",  "🤝 LOW — Scheduling friendly visitor programme"),

    ("none", "high"):        ("995",              "⚠️  HIGH but unclear — defaulting to 995"),
    ("none", "medium"):      ("1777",             "⚠️  MEDIUM but unclear — calling 1777"),
    ("none", "low"):         ("NO-ACTION",        "✅ No action required"),
}

def get_dispatch(team: str, urgency: str):
    key = (team, urgency)
    return DISPATCH_MATRIX.get(key, ("UNKNOWN", "⚠️ No matching dispatch rule found"))

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
                          confidence: float, location: str, audio_path: str,
                          dispatch_team: str, source: str, alert_type: str,
                          reason: str, dispatch_number: str, dispatch_action: str):
    sql = f"""
    INSERT INTO gpt_classifier
        (event_id, timestamp, caller_id, transcript, urgency_level, confidence,
         location, audio_url, dispatch_team, source, alert_type, reason,
         dispatch_number, dispatch_action)
    VALUES (
        '{event_id}',
        '{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}',
        'PAB_USER',
        '{transcript.replace("'", "''")}',
        '{urgency}',
        {confidence},
        '{location}',
        '{audio_path}',
        '{dispatch_team}',
        '{source}',
        '{alert_type}',
        '{reason.replace("'", "''")}',
        '{dispatch_number}',
        '{dispatch_action.replace("'", "''")}'
    )
    """
    ch_query(sql)

# ── GPT CLASSIFIER ────────────────────────────────────────────────────────────
CONFIDENCE_RUBRIC = """
Rate confidence (0.0-1.0) using these rules:

  AMBULANCE (medical):
    - High (0.85-1.0): clear physical symptoms — chest pain, fall, bleeding, unconscious
    - Medium (0.5-0.84): vague symptoms — dizzy, weak, feeling unwell
    - Low (0.0-0.49): unclear or possibly non-medical

  POLICE (security):
    - High (0.85-1.0): explicit threat, intruder, robbery mentioned
    - Medium (0.5-0.84): suspicious person, feeling unsafe
    - Low (0.0-0.49): vague fear or uncertainty

  FIRE/SCDF:
    - High (0.85-1.0): smoke, fire, gas smell explicitly mentioned
    - Medium (0.5-0.84): burning smell, something wrong in kitchen
    - Low (0.0-0.49): uncertain hazard

  SOCIAL WORK (welfare):
    - High (0.85-1.0): explicit loneliness, no food, caregiver needed urgently
    - Medium (0.5-0.84): general distress, not clearly physical
    - Low (0.0-0.49): unclear need
"""

def classify_voice(transcript: str, location: str = "Unknown") -> dict:
    prompt = f"""
You are an emergency dispatcher AI for elderly care in Singapore.

Analyse this voice transcript and return ONLY a JSON object with:
- urgency_level: "high", "medium", or "low"
- confidence: float between 0 and 1
- dispatch_team: one of "ambulance", "police", "fire", "social_work", "none"
- reason: one sentence explanation

{CONFIDENCE_RUBRIC}

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
    prompt = f"""
You are an emergency dispatcher AI for elderly care in Singapore.

A camera detected a physical alert. Return ONLY a JSON object with:
- urgency_level: "high", "medium", or "low"
- confidence: float between 0 and 1
- dispatch_team: one of "ambulance", "police", "fire", "social_work", "none"
- reason: one sentence explanation

{CONFIDENCE_RUBRIC}

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
             audio_path: str = "", source: str = "VOICE", alert_type: str = ""):
    team    = result.get("dispatch_team", "none")
    urgency = result.get("urgency_level", "low")
    conf    = result.get("confidence", 0)
    reason  = result.get("reason", "")

    dispatch_number, dispatch_action = get_dispatch(team, urgency)
    source_icon = "🎙️" if source == "VOICE" else "📷"

    print(f"\n{'='*60}")
    print(f"  🆘 NEW EMERGENCY — SOURCE: {source_icon} {source}")
    print(f"  EVENT:       {event_id}")
    print(f"  TRANSCRIPT:  {transcript[:80]}")
    print(f"  ALERT TYPE:  {alert_type if alert_type else 'voice'}")
    print(f"  URGENCY:     {urgency.upper()}  |  CONFIDENCE: {conf:.0%}")
    print(f"  TEAM:        {team.upper()}")
    print(f"  REASON:      {reason}")
    print(f"  ── DISPATCH ──────────────────────────────────────")
    print(f"  NUMBER:      {dispatch_number}")
    print(f"  ACTION:      {dispatch_action}")
    print(f"{'='*60}\n")

    # TODO: plug in real calls here e.g. Twilio to dial dispatch_number

    save_dispatch_result(
        event_id=event_id,
        transcript=transcript,
        urgency=urgency,
        confidence=conf,
        location=location,
        audio_path=audio_path,
        dispatch_team=team,
        source=source,
        alert_type=alert_type,
        reason=reason,
        dispatch_number=dispatch_number,
        dispatch_action=dispatch_action,
    )

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
            # ── VOICE ─────────────────────────────────────────────────────
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
                    dispatch(event_id, transcript, "Unknown", result,
                             audio_path=audio_path, source="VOICE", alert_type="voice")
                    seen_voice.add(event_id)

            # ── CAMERA ────────────────────────────────────────────────────
            camera_rows = fetch_new_camera_events(seen_camera)
            if not camera_rows:
                print(f"  📷  No new camera alerts...")
            else:
                for row in camera_rows:
                    event_id       = row["event_id"]
                    alert_type     = row["alert_type"]
                    pose_state     = row.get("pose_state", "")
                    still_duration = row.get("still_duration", 0)
                    notes          = row.get("notes", "")
                    transcript     = f"[CAMERA] {alert_type} detected — pose: {pose_state}, still for {still_duration}s. {notes}"
                    print(f"  📷  Camera alert: {alert_type.upper()} detected!")
                    result = classify_camera(alert_type, pose_state, still_duration, notes)
                    dispatch(event_id, transcript, "Unknown", result,
                             source="CAMERA", alert_type=alert_type)
                    seen_camera.add(event_id)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        time.sleep(POLL_INTERVAL_SEC)

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_polling_loop()

