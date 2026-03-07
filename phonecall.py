"""
GOAL 4: Emergency Dispatch — reads gpt_classifier → sends SMS via Vonage v4
============================================================================
Install:
    pip install vonage requests python-dotenv

Setup:
    1. Sign up at dashboard.nexmo.com (free, no credit card)
    2. Copy API Key and API Secret from the dashboard homepage
    3. Fill in CONFIG below
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

from vonage import Vonage, Auth
from vonage_sms import SmsMessage

load_dotenv()

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────

VONAGE_API_KEY    = os.getenv("VONAGE_API_KEY",    "22e77ff6")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET", "wZPVCwekUshCOm03")
VONAGE_FROM_NAME  = "EmergencyAlert"   # Sender name on SMS (max 11 chars)

# ── Who to SMS for each dispatch team ─────────────────────
# Replace with real Singapore +65 numbers (WITH + sign)
DISPATCH_CONTACTS = {
    "ambulance":   ["+6590299265"],
    "police":      ["+6590299265"],
    "fire":        ["+6590299265"],
    "social_work": ["+6590299265"],
    "none":        [],
}

# ── SMS message for each dispatch team ────────────────────
DISPATCH_MESSAGES = {
    "ambulance": (
        "EMERGENCY ALERT 🚑\n"
        "Your elderly family member needs MEDICAL help.\n"
        "Please call 995 immediately or go to them now.\n"
    ),
    "police": (
        "EMERGENCY ALERT 🚓\n"
        "A safety/security concern has been detected at your "
        "elderly family member's home.\n"
        "Please call 999 or check on them immediately.\n"
    ),
    "fire": (
        "EMERGENCY ALERT 🚒\n"
        "A fire or gas hazard may have been detected.\n"
        "Please call 995 (SCDF) immediately.\n"
    ),
    "social_work": (
        "WELFARE ALERT 👤\n"
        "A non-urgent concern has been flagged for your "
        "elderly family member.\n"
        "Please check on them when possible.\n"
    ),
}

# ── ClickHouse (HTTP API — same as your classifier) ───────
CLICKHOUSE_HOST     = os.getenv("CLICKHOUSE_HOST",
                                "https://bzit6h15r0.asia-southeast1.gcp.clickhouse.cloud:8443")
CLICKHOUSE_USER     = os.getenv("CLICKHOUSE_USER",     "default")
<<<<<<< HEAD
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "o2W9Zxcl1.p3x")
=======
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "o2W9Zxcl1.p3x")
>>>>>>> 7b88c96b6e620d1b9a15337eb265452730e34e20

POLL_INTERVAL = 5   # seconds between ClickHouse checks


# ──────────────────────────────────────────────────────────
<<<<<<< HEAD
# CLICKHOUSE HELPERS (HTTP — matches your classifier style
=======
# CLICKHOUSE HELPERS (HTTP — same style as your classifier)
>>>>>>> 7b88c96b6e620d1b9a15337eb265452730e34e20
# ──────────────────────────────────────────────────────────

def ch_query(sql: str) -> str:
    resp = requests.post(
        CLICKHOUSE_HOST,
        data=sql,
        auth=(CLICKHOUSE_USER, CLICKHOUSE_PASSWORD),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def init_db() -> None:
    """Create dispatched_log table for audit trail. Non-blocking on failure."""
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
    Filters out 'none' dispatch team and already seen event IDs.
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
        return [r for r in rows if r["event_id"] not in already_seen]
    except Exception as e:
        print(f"[ClickHouse] ⚠  Fetch failed: {e}")
        return []


def log_dispatch(event_id: str, dispatch_team: str, urgency: str,
                 numbers: list, transcript: str, reason: str, source: str) -> None:
    """Save dispatch record to ClickHouse for audit trail."""
    numbers_str     = json.dumps(numbers).replace("'", "\\'")
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
# VONAGE SMS  (v4 SDK)
# ──────────────────────────────────────────────────────────

def get_vonage_client() -> Vonage:
    """Create Vonage v4 client — only needs API key + secret for SMS."""
    return Vonage(Auth(
        api_key=VONAGE_API_KEY,
        api_secret=VONAGE_API_SECRET,
    ))


def send_sms(to_number: str, message: str) -> bool:
    """
    Send SMS via Vonage v4 SDK.
    to_number: full number with + e.g. '+6591234567'
    Returns True if sent successfully.
    """
    try:
        client = get_vonage_client()

        # Vonage v4 — SmsMessage from vonage_sms package
        sms_message = SmsMessage(
            to=to_number.replace("+", ""),  # Vonage wants no + prefix
            from_=VONAGE_FROM_NAME,
            text=message,
        )

        response = client.sms.send(sms_message)

        # v4 response: check first message status
        first = response.messages[0]
        if first.status == "0":
            print(f"[Vonage] ✅  SMS sent to {to_number}")
            return True
        else:
            print(f"[Vonage] ⚠  SMS to {to_number} failed: {first.error_text}")
            return False

    except Exception as e:
        print(f"[Vonage] ⚠  Exception sending to {to_number}: {e}")
        return False


# ──────────────────────────────────────────────────────────
# DISPATCH ONE EVENT
# ──────────────────────────────────────────────────────────

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
      1. Get the right contacts + message for the dispatch_team
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

    # Build SMS message — base + reason detail
    base_message = DISPATCH_MESSAGES.get(
        dispatch_team,
        "EMERGENCY: Check on elderly person now."
    )
    detail = f"Details: {reason}"
    if alert_type and alert_type != "voice":
        detail += f" ({alert_type})"
    full_message = base_message + detail

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
    Mirrors your classifier's polling loop style.
    Polls gpt_classifier every POLL_INTERVAL seconds.
    Sends SMS for any new classified emergencies.
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