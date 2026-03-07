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

POLL_INTERVAL_SEC   = 5   # How often to check ClickHouse for new transcripts

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

def fetch_new_transcripts(already_seen: set) -> list:
    """
    Reads from your teammate's speech_events table.
    Only returns rows we haven't classified yet.
    """
    sql = """
        SELECT event_id, transcript, confidence, language, audio_path, timestamp
        FROM speech_events
        ORDER BY timestamp DESC
        LIMIT 20
        FORMAT JSON
    """
    result = ch_query(sql)
    rows = json.loads(result).get("data", [])

    # Filter out ones we've already processed
    new_rows = [r for r in rows if r["event_id"] not in already_seen]
    return new_rows

def save_dispatch_result(event_id: str, transcript: str, urgency: str,
                          confidence: float, location: str, audio_path: str):
    """
    Saves the classified result into voice_emergency_logs
    so the frontend dashboard can display it.
    """
    sql = f"""
    INSERT INTO voice_emergency_logs
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
def classify_and_dispatch(transcript: str, location: str = "Unknown") -> dict:
    """Use GPT to classify urgency and decide which team to dispatch."""
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

# ── DISPATCH ACTION ───────────────────────────────────────────────────────────
def dispatch(event_id: str, transcript: str, location: str, result: dict, audio_path: str):
    team    = result.get("dispatch_team", "none")
    urgency = result.get("urgency_level", "low")
    conf    = result.get("confidence", 0)
    reason  = result.get("reason", "")
    action  = DISPATCH_TEAMS.get(team, "Unknown team")

    print(f"\n{'='*60}")
    print(f"  🆘 NEW EMERGENCY CLASSIFIED")
    print(f"  EVENT:      {event_id}")
    print(f"  TRANSCRIPT: {transcript[:80]}...")
    print(f"  URGENCY:    {urgency.upper()}  |  CONFIDENCE: {conf:.0%}")
    print(f"  REASON:     {reason}")
    print(f"  ACTION:     {action}")
    print(f"{'='*60}\n")

    # Save result back to voice_emergency_logs for the dashboard
    save_dispatch_result(event_id, transcript, urgency, conf, location, audio_path)

# ── POLLING LOOP ──────────────────────────────────────────────────────────────
def run_polling_loop():
    """
    Polls ClickHouse every 5 seconds for new transcripts from main.py.
    When a new one arrives, classifies it and saves the result.
    Press Ctrl+C to stop.
    """
    print("\n" + "="*60)
    print("🤖 CLASSIFIER RUNNING — polling for new transcripts...")
    print(f"   Checking every {POLL_INTERVAL_SEC} seconds. Press Ctrl+C to stop.")
    print("="*60 + "\n")

    already_seen = set()  # Tracks event_ids we've already classified

    while True:
        try:
            new_rows = fetch_new_transcripts(already_seen)

            if not new_rows:
                print(f"  ⏳ No new transcripts... (checking again in {POLL_INTERVAL_SEC}s)")
            else:
                for row in new_rows:
                    event_id   = row["event_id"]
                    transcript = row["transcript"]
                    audio_path = row.get("audio_path", "")

                    print(f"  📥 New transcript received: '{transcript[:60]}...'")

                    # Classify with GPT
                    result = classify_and_dispatch(transcript, location="Unknown")

                    # Dispatch and save to voice_emergency_logs
                    dispatch(event_id, transcript, "Unknown", result, audio_path)

                    # Mark as seen so we don't classify it again
                    already_seen.add(event_id)

        except Exception as e:
            print(f"  ❌ Error: {e}")

        time.sleep(POLL_INTERVAL_SEC)

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_polling_loop()

