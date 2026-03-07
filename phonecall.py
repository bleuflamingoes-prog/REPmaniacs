"""
GOAL 4: Emergency Dispatch — reads gpt_classifier → sends SMS via Vonage
=========================================================================
Reads dispatch_team from gpt_classifier (written by your classifier code).
Sends SMS alerts via Vonage — only needs API key + secret, no application needed.

Install:
    pip install vonage requests python-dotenv

Setup:
    1. Sign up at dashboard.nexmo.com (free, no credit card)
    2. Copy API Key and API Secret from the dashboard homepage
    3. Fill in the CONFIG block below
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

import vonage

load_dotenv()

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────

# Vonage — only needs key + secret for SMS (no application required)
VONAGE_API_KEY    = os.getenv("VONAGE_API_KEY",    "22e77ff6")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET", "wZPVCwekUshCOm03")
VONAGE_FROM_NAME  = "EmergencyAlert"   # Sender name shown on SMS (max 11 chars)

# ── Who to SMS for each dispatch team ─────────────────────
# Replace with real Singapore +65 numbers
DISPATCH_CONTACTS = {
    "ambulance":   ["+6594510267"],   # next of kin — they call 995
    "police":      ["+6590299265"],   # next of kin — they call 999
    "fire":        ["+6589592135"],   # next of kin — they call 995
    "social_work": ["+6580232395"],   # next of kin / social worker
    "none":        [],                # no action needed
}

# ── SMS message for each dispatch team ────────────────────
DISPATCH_MESSAGES = {
    "ambulance": (
        "EMERGENCY ALERT 🚑\n"
        "An elderly needs MEDICAL help.\n"
        "Please dispatch ambulance now.\n"
    ),
    "police": (
        "EMERGENCY ALERT 🚓\n"
        "A safety/security concern has been detected at an elderly's home.\n"
        "Please dispatch officers immediately.\n"
    ),
    "fire": (
        "EMERGENCY ALERT 🚒\n"
        "A fire or gas hazard may have been detected.\n"
        "Please dispatch firefighters immediately.\n"
    ),
    "social_work": (
        "WELFARE ALERT 👤\n"
        "A non-urgent concern has been flagged for an elderly.\n"
        "Please check on them when possible.\n"
    ),
}

# ── ClickHouse (HTTP API — same style as your classifier) ──
CLICKHOUSE_HOST     = os.getenv("CLICKHOUSE_HOST",
                                "https://bzit6h15r0.asia-southeast1.gcp.clickhouse.cloud:8443")
CLICKHOUSE_USER     = os.getenv("CLICKHOUSE_USER",     "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "2ai.gMRWIooUB")

POLL_INTERVAL = 5   # seconds between ClickHouse checks


# ──────────────────────────────────────────────────────────
# CLICKHOUSE HELPERS (HTTP — matches your classifier style)
# ──────────────────────────────────────────────────────────

def ch_query(sql: str) -> str:
    resp = requests.post(
        CLICKHOUSE_HOST,
        data=sql,
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=30,   # was 10 — increased to 30s
    )
    resp.raise_for_status()
    return resp.text


def init_db() -> None:
    try:
        ch_query("""
            CREATE TABLE IF NOT EXISTS dispatched_log
            (
                event_id      String,
                dispatched_at DateTime,
                dispatch_team String,
                urgency_level String,
                numbers_sms   String,
                transcript    String,
                reason        String,
                source        String
            )
            ENGINE = MergeTree()
            ORDER BY (dispatched_at, event_id)
            TTL dispatched_at + INTERVAL 90 DAY
        """)
        print("[ClickHouse] dispatched_log table ready ✓")
    except Exception as e:
        print(f"[ClickHouse] ⚠  Could not create dispatched_log: {e}")
        print("[ClickHouse] Continuing without dispatch logging...")

def fetch_pending_dispatches(already_seen: set) -> list[dict]:
    """
    Read new rows from gpt_classifier that haven't been dispatched yet.
    Filters out 'none' dispatch team and already seen events.
    """
    sql = """
        SELECT
            event_id,
            transcript,
            urgency_level,
            confidence,
            dispatch_team,
            reason,
            source,
            alert_type,
            timestamp
        FROM gpt_classifier
        WHERE dispatch_team != 'none'
        ORDER BY timestamp DESC
        LIMIT 50
        FORMAT JSON
    """
    try:
        result = ch_query(sql)
        rows   = json.loads(result).get("data", [])
        # Filter out already-dispatched events
        return [r for r in rows if r["event_id"] not in already_seen]
    except Exception as e:
        print(f"[ClickHouse] ⚠  Fetch failed: {e}")
        return []


def log_dispatch(event_id: str, dispatch_team: str, urgency: str,
                 numbers: list, transcript: str, reason: str, source: str) -> None:
    """Save a record of what was dispatched for audit trail."""
    numbers_str = json.dumps(numbers).replace("'", "\\'")
    transcript_safe = transcript.replace("'", "\\'")[:500]
    reason_safe     = reason.replace("'", "\\'")

    sql = f"""
        INSERT INTO dispatched_log
            (event_id, dispatched_at, dispatch_team, urgency_level,
             numbers_sms, transcript, reason, source)
        VALUES (
            '{event_id}',
            '{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}',
            '{dispatch_team}',
            '{urgency}',
            '{numbers_str}',
            '{transcript_safe}',
            '{reason_safe}',
            '{source}'
        )
    """
    try:
        ch_query(sql)
    except Exception as e:
        print(f"[ClickHouse] ⚠  Log failed: {e}")


# ──────────────────────────────────────────────────────────
# VONAGE SMS
# ──────────────────────────────────────────────────────────

def send_sms(to_number: str, message: str) -> bool:
    """
    Send an SMS via Vonage.
    Only needs API key + secret — no application or private key required.

    to_number: full number with country code, e.g. '+6591234567'
    Returns True if sent successfully.
    """
    try:
        client = vonage.Client(
            key=VONAGE_API_KEY,
            secret=VONAGE_API_SECRET,
        )
        sms = vonage.Sms(client)

        response = sms.send_message({
            "from": VONAGE_FROM_NAME,
            "to":   to_number.replace("+", ""),  # Vonage wants no + prefix
            "text": message,
        })

        status = response["messages"][0]["status"]
        if status == "0":
            print(f"[Vonage] ✅  SMS sent to {to_number}")
            return True
        else:
            error = response["messages"][0].get("error-text", "unknown error")
            print(f"[Vonage] ⚠  SMS to {to_number} failed: {error}")
            return False

    except Exception as e:
        print(f"[Vonage] ⚠  Exception sending to {to_number}: {e}")
        return False


# ──────────────────────────────────────────────────────────
# DISPATCH ONE EVENT
# ──────────────────────────────────────────────────────────

# Icons for display
TEAM_ICONS = {
    "ambulance":   "🚑",
    "police":      "🚓",
    "fire":        "🚒",
    "social_work": "👤",
    "none":        "✅",
}

def dispatch(row: dict) -> None:
    """
    Handle one classified emergency from gpt_classifier:
      1. Get the right contacts + message
      2. Send SMS to each contact
      3. Log to dispatched_log in ClickHouse
    """
    event_id      = row["event_id"]
    dispatch_team = row["dispatch_team"]
    urgency       = row["urgency_level"]
    confidence    = float(row.get("confidence", 0))
    transcript    = row.get("transcript", "")
    reason        = row.get("reason", "")
    source        = row.get("source", "UNKNOWN")
    alert_type    = row.get("alert_type", "")
    icon          = TEAM_ICONS.get(dispatch_team, "🆘")

    print(f"\n{'='*60}")
    print(f"  {icon}  DISPATCHING — {dispatch_team.upper()}")
    print(f"  Event ID  : {event_id[:8]}…")
    print(f"  Source    : {source}  |  Alert: {alert_type}")
    print(f"  Urgency   : {urgency.upper()}  |  Confidence: {confidence:.0%}")
    print(f"  Reason    : {reason}")
    print(f"  Transcript: {transcript[:80]}")
    print(f"{'='*60}")

    contacts = DISPATCH_CONTACTS.get(dispatch_team, [])
    if not contacts:
        print(f"[Dispatch] No contacts for team '{dispatch_team}' — skipping")
        log_dispatch(event_id, dispatch_team, urgency, [], transcript, reason, source)
        return

    # Build SMS — base message + details
    base_message = DISPATCH_MESSAGES.get(dispatch_team, "EMERGENCY: Check on elderly person now.")
    detail_line  = f"Details: {reason}"
    if alert_type and alert_type != "voice":
        detail_line += f" ({alert_type})"
    full_message = base_message + detail_line

    numbers_sent = []
    for number in contacts:
        success = send_sms(number, full_message)
        if success:
            numbers_sent.append(number)
        time.sleep(1)

    log_dispatch(event_id, dispatch_team, urgency,
                 numbers_sent, transcript, reason, source)
    print(f"[Dispatch] ✓  SMS sent to {len(numbers_sent)} contact(s)")


# ──────────────────────────────────────────────────────────
# MAIN POLLING LOOP
# ──────────────────────────────────────────────────────────

def run_polling_loop() -> None:
    """
    Poll gpt_classifier every POLL_INTERVAL seconds.
    Sends SMS for any new classified emergencies.
    Mirrors the style of your existing classifier polling loop.
    Press Ctrl+C to stop.
    """
    print("\n" + "="*60)
    print("  🚨  GOAL 4 — Emergency Dispatch (Vonage SMS)")
    print(f"     Polling gpt_classifier every {POLL_INTERVAL}s")
    print(f"     Teams: ambulance 🚑 | police 🚓 | fire 🚒 | social_work 👤")
    print("     Press Ctrl+C to stop")
    print("="*60 + "\n")

    init_db()

    already_dispatched = set()  # prevent double-SMS in same session

    while True:
        try:
            pending = fetch_pending_dispatches(already_dispatched)

            if pending:
                print(f"[Dispatch] Found {len(pending)} new event(s) to dispatch")
                for row in pending:
                    dispatch(row)
                    already_dispatched.add(row["event_id"])
            else:
                print(f"  ✅  No new dispatches — watching…", end="\r")

        except KeyboardInterrupt:
            print("\n[Dispatch] Stopped.")
            break
        except Exception as e:
            print(f"[Dispatch] ⚠  Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_polling_loop()