"""
clickhouse_store.py
-------------------
Saves transcription results to ClickHouse so Member 4 can query them.

ClickHouse is a fast database perfect for storing and searching logs of
speech events. Each press of the help button creates one row in the table.

HOW TO SET UP CLICKHOUSE (do this once):
  1. Install ClickHouse: https://clickhouse.com/docs/en/install
  2. Start it:  sudo systemctl start clickhouse-server
  3. The default connection is localhost:8123 with no password.
     If your setup is different, edit HOST / PORT / PASSWORD below.
"""

import clickhouse_connect
from datetime import datetime
import uuid

# ── Connection settings ──────────────────────────────────────────────────────
HOST     = "localhost"
PORT     = 8123
USERNAME = "default"
PASSWORD = ""          # Change this if you set a ClickHouse password
DATABASE = "default"
TABLE    = "speech_events"
# ─────────────────────────────────────────────────────────────────────────────

_client = None


def get_client():
    """Returns a connected ClickHouse client (creates connection if needed)."""
    global _client
    if _client is None:
        try:
            _client = clickhouse_connect.get_client(
                host=HOST,
                port=PORT,
                username=USERNAME,
                password=PASSWORD,
                database=DATABASE
            )
            print("✅ Connected to ClickHouse.")
        except Exception as e:
            print(f"❌ Cannot connect to ClickHouse: {e}")
            print("   Make sure ClickHouse is running: sudo systemctl start clickhouse-server")
            return None
    return _client


def setup_table():
    """
    Creates the speech_events table if it doesn't exist yet.
    Run this once when you first set up the project.
    """
    client = get_client()
    if client is None:
        return False

    try:
        client.command(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                event_id     String,
                timestamp    DateTime,
                language     String,
                transcript   String,
                confidence   Float32,
                button_type  String,
                audio_path   String,
                duration_sec Float32
            ) ENGINE = MergeTree()
            ORDER BY timestamp
        """)
        print(f"✅ Table '{TABLE}' is ready in ClickHouse.")
        return True

    except Exception as e:
        print(f"❌ Failed to create table: {e}")
        return False


def save_speech_event(transcript, language, confidence, button_type, audio_path, duration_sec=0.0):
    """
    Saves one speech event (one button press + transcription) to ClickHouse.

    Args:
        transcript:   The transcribed text from the audio
        language:     Language detected (e.g. "en-SG", "zh-CN")
        confidence:   Model confidence score (0.0 to 1.0)
        button_type:  "intentional" or "accidental"
        audio_path:   Path to the saved .wav file
        duration_sec: How long the recording was in seconds

    Returns:
        True if saved successfully, False if something went wrong.
    """
    client = get_client()
    if client is None:
        return False

    try:
        event_id = str(uuid.uuid4())
        timestamp = datetime.now()

        client.insert(
            TABLE,
            [[event_id, timestamp, language, transcript, float(confidence),
              button_type, audio_path, float(duration_sec)]],
            column_names=["event_id", "timestamp", "language", "transcript",
                          "confidence", "button_type", "audio_path", "duration_sec"]
        )

        print(f"💾 Saved to ClickHouse (event_id: {event_id[:8]}...)")
        return True

    except Exception as e:
        print(f"❌ Failed to save to ClickHouse: {e}")
        return False


def get_recent_events(limit=20):
    """
    Retrieves the most recent speech events from ClickHouse.
    Useful for Member 4 to check what's been captured.

    Args:
        limit: How many rows to return (default: 20)

    Returns:
        List of dicts, each representing one speech event.
    """
    client = get_client()
    if client is None:
        return []

    try:
        result = client.query(f"""
            SELECT event_id, timestamp, language, transcript,
                   confidence, button_type, duration_sec
            FROM {TABLE}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """)

        rows = []
        for row in result.result_rows:
            rows.append({
                "event_id":    row[0][:8] + "...",
                "timestamp":   str(row[1]),
                "language":    row[2],
                "transcript":  row[3],
                "confidence":  round(row[4], 2),
                "button_type": row[5],
                "duration":    f"{row[6]:.1f}s"
            })
        return rows

    except Exception as e:
        print(f"❌ Failed to fetch events: {e}")
        return []


def get_stats():
    """
    Returns summary statistics — useful for Member 4's analysis.
    Shows total events, language breakdown, accidental vs intentional.
    """
    client = get_client()
    if client is None:
        return

    try:
        result = client.query(f"""
            SELECT
                COUNT(*)                                        AS total_events,
                countIf(button_type = 'intentional')           AS intentional,
                countIf(button_type = 'accidental')            AS accidental,
                countIf(language LIKE '%en%')                  AS english_events,
                countIf(language LIKE '%zh%')                  AS chinese_events,
                AVG(confidence)                                AS avg_confidence
            FROM {TABLE}
        """)

        if result.result_rows:
            row = result.result_rows[0]
            print("\n📊 ClickHouse Statistics:")
            print(f"   Total events:        {row[0]}")
            print(f"   Intentional presses: {row[1]}")
            print(f"   Accidental presses:  {row[2]}")
            print(f"   English events:      {row[3]}")
            print(f"   Chinese events:      {row[4]}")
            print(f"   Avg confidence:      {row[5]:.2f}")

    except Exception as e:
        print(f"❌ Failed to get stats: {e}")
