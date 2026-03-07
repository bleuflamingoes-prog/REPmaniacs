"""
main.py
-------
THE MAIN ENTRY POINT — run this file to start the whole system.

What happens when you run this:
  1. System starts up and connects to ClickHouse
  2. Listens for SPACE BAR (simulating the help button)
  3. When button held 2s → records audio from mic (10 seconds)
  4. Sends audio to MERaLiON (A*STAR SG model) for transcription
  5. Saves transcript + metadata to ClickHouse for Member 4
  6. Repeat

HOW TO RUN:
  python main.py

HOW TO TEST (without waiting for button):
  python main.py --test

HOW TO SEE STORED DATA:
  python main.py --view
"""

import argparse
import time
import os
from datetime import datetime

from audio_capture     import record_audio
from asr_inference     import transcribe_audio
from clickhouse_store  import setup_table, save_speech_event, get_recent_events, get_stats
from button_handler    import ButtonHandler, start_keyboard_listener

# ── Settings ─────────────────────────────────────────────────────────────────
RECORDING_DURATION = 10       # Seconds to record after button press
AUDIO_OUTPUT_DIR   = "audio_recordings"   # Folder to save .wav files
# ─────────────────────────────────────────────────────────────────────────────


def ensure_audio_folder():
    """Creates the audio recordings folder if it doesn't exist."""
    if not os.path.exists(AUDIO_OUTPUT_DIR):
        os.makedirs(AUDIO_OUTPUT_DIR)
        print(f"📁 Created folder: {AUDIO_OUTPUT_DIR}/")


def handle_help_request():
    """
    This is the full pipeline that runs when someone presses the help button:
      record → transcribe → save to ClickHouse
    """
    print("\n" + "="*60)
    print("🆘 HELP REQUEST RECEIVED")
    print("="*60)

    # Step 1: Record audio
    ensure_audio_folder()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_filename = os.path.join(AUDIO_OUTPUT_DIR, f"help_{timestamp_str}.wav")

    audio_file = record_audio(
        duration_seconds=RECORDING_DURATION,
        output_filename=audio_filename
    )

    if audio_file is None:
        print("❌ Recording failed — could not process help request.")
        return

    # Step 2: Transcribe with MERaLiON (SG-tuned ASR model)
    # Pass language_hint="zh" if you want to force Mandarin detection
    # Leave as None for automatic language detection (recommended)
    result = transcribe_audio(audio_file, language_hint=None)

    transcript = result["transcript"]
    language   = result["language"]
    confidence = result["confidence"]

    if not transcript:
        print("⚠️  No speech detected in recording.")
        transcript = "[no speech detected]"

    # Step 3: Save to ClickHouse for Member 4
    saved = save_speech_event(
        transcript   = transcript,
        language     = language,
        confidence   = confidence,
        button_type  = "intentional",
        audio_path   = audio_file,
        duration_sec = RECORDING_DURATION
    )

    if saved:
        print("\n✅ Help request fully processed and stored!")
    else:
        print("\n⚠️  Transcribed but could not save to ClickHouse. Check connection.")

    print("="*60 + "\n")


def run_live_mode():
    """
    Live mode: waits for button presses and processes help requests.
    Press SPACE and hold 2 seconds to trigger. Press ESC to quit.
    """
    print("\n" + "="*60)
    print("🚀 HELP BUTTON SYSTEM — LIVE MODE")
    print("="*60)
    print("  Hold SPACE for 2 seconds  →  Help request (intentional)")
    print("  Quick tap of SPACE        →  Ignored (accidental)")
    print("  Press ESC                 →  Quit")
    print("="*60 + "\n")

    result = start_keyboard_listener(on_intentional_press=handle_help_request)

    if result is None:
        print("⚠️  Keyboard listener failed. Running in simulation mode instead.")
        run_test_mode()
        return

    handler, listener = result

    try:
        listener.join()  # Keep running until ESC is pressed
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")


def run_test_mode():
    """
    Test mode: simulates button presses to verify everything works.
    Runs three tests:
      - Accidental press (0.5s hold → should be ignored)
      - Intentional press (2.5s hold → should trigger recording)
    """
    print("\n" + "="*60)
    print("🧪 TEST MODE")
    print("="*60)

    # Test 1: Accidental press (should be ignored)
    print("\n[Test 1] Simulating ACCIDENTAL press (0.5s hold)...")
    handler = ButtonHandler(on_intentional_press=handle_help_request)
    handler.simulate_press(hold_duration=0.5)
    time.sleep(1)

    # Test 2: Intentional press (should trigger recording + transcription)
    print("\n[Test 2] Simulating INTENTIONAL press (2.5s hold)...")
    print("         (Will record audio, so make sure your mic is ready!)")
    time.sleep(1)
    handler2 = ButtonHandler(on_intentional_press=handle_help_request)
    handler2.simulate_press(hold_duration=2.5)

    # Wait for the recording + transcription to complete
    print("\n⏳ Waiting for processing to finish...")
    time.sleep(RECORDING_DURATION + 15)  # recording time + model inference time

    # Show what's in ClickHouse
    print("\n📋 Latest entries in ClickHouse:")
    events = get_recent_events(limit=5)
    if events:
        for event in events:
            print(f"\n  🕐 {event['timestamp']}")
            print(f"  🌐 Language:    {event['language']}")
            print(f"  📝 Transcript:  {event['transcript']}")
            print(f"  📊 Confidence:  {event['confidence']}")
            print(f"  🔘 Button type: {event['button_type']}")
    else:
        print("  (No events found — ClickHouse may not be running)")

    get_stats()


def view_stored_data():
    """Shows the most recent 20 events stored in ClickHouse."""
    print("\n📋 Most recent speech events in ClickHouse:\n")
    events = get_recent_events(limit=20)

    if not events:
        print("  No data found. Make sure ClickHouse is running and you've recorded something.")
        return

    for i, event in enumerate(events, 1):
        print(f"  [{i}] {event['timestamp']} | {event['language']}")
        print(f"       📝 {event['transcript']}")
        print(f"       🔘 {event['button_type']} | confidence: {event['confidence']} | {event['duration']}")
        print()

    get_stats()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SG Speech Help Button System")
    parser.add_argument("--test", action="store_true",
                        help="Run in test mode (simulates button presses)")
    parser.add_argument("--view", action="store_true",
                        help="View stored transcriptions in ClickHouse")
    args = parser.parse_args()

    # Always set up the database table first
    print("🔧 Setting up ClickHouse table...")
    setup_table()

    if args.view:
        view_stored_data()
    elif args.test:
        run_test_mode()
    else:
        run_live_mode()
