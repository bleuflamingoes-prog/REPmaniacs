import argparse
import time
import os
from datetime import datetime

from audio_capture      import record_audio
from asr_inference      import transcribe_audio
from clickhouse_store   import setup_table, save_speech_event, get_recent_events, get_stats
from button_handler     import ButtonHandler, start_keyboard_listener
from audio_error_handler_2nd_ed import check_audio_errors, handle_audio_error, play_voice_alert

RECORDING_DURATION = 10
AUDIO_OUTPUT_DIR   = "audio_recordings"


def ensure_audio_folder():
    if not os.path.exists(AUDIO_OUTPUT_DIR):
        os.makedirs(AUDIO_OUTPUT_DIR)
        print("Created folder: " + AUDIO_OUTPUT_DIR)

def handle_help_request(attempt=1):
    print("\n" + "="*60)
    print("HELP REQUEST RECEIVED (attempt " + str(attempt) + ")")
    print("="*60)

    ensure_audio_folder()
    timestamp_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_filename = os.path.join(AUDIO_OUTPUT_DIR, f"help_{timestamp_str}.wav")

    audio_file = record_audio(duration_seconds=RECORDING_DURATION, output_filename=audio_filename)

    if audio_file is None:
        print("Recording failed - microphone may not be connected.")
        play_voice_alert("Recording failed. Please check your microphone and try again.")
        return

    result     = transcribe_audio(audio_file, language_hint=None)
    transcript = result["transcript"]
    language   = result["language"]
    confidence = result["confidence"]

    print("\nChecking audio quality and content...")
    error_result = check_audio_errors(transcript, confidence, attempt=attempt)

    if error_result.get("force_emergency"):
        # Second attempt still unclear — send help anyway (possible stroke/distress)
        print("\n🚨 ESCALATING TO EMERGENCY — person may be unable to speak (stroke/distress)")
        play_voice_alert("Unable to verify speech. Sending help as a precaution.")
        save_speech_event(
            transcript   = transcript or "[unable to speak]",
            language     = language,
            confidence   = confidence,
            button_type  = "escalated_emergency",
            audio_path   = audio_file,
            duration_sec = RECORDING_DURATION
        )
        print("="*60 + "\n")
        return

    if error_result["has_error"]:
        handle_audio_error(error_result)
        save_speech_event(
            transcript   = transcript or "[error]",
            language     = language,
            confidence   = confidence,
            button_type  = "error:" + error_result["error_type"],
            audio_path   = audio_file,
            duration_sec = RECORDING_DURATION
        )
        # If should retry, immediately trigger second attempt
        if error_result["should_retry"] and attempt == 1:
            print("\nStarting second attempt automatically...")
            handle_help_request(attempt=2)
        print("="*60 + "\n")
        return

    print("\nValid emergency request detected!")
    play_voice_alert("Help is on the way. Your request has been received.")

    saved = save_speech_event(
        transcript   = transcript,
        language     = language,
        confidence   = confidence,
        button_type  = "intentional",
        audio_path   = audio_file,
        duration_sec = RECORDING_DURATION
    )

    if saved:
        print("Saved to ClickHouse successfully!")
    else:
        print("Could not save to ClickHouse - check connection.")

    print("="*60 + "\n")

def run_live_mode():
    print("\n" + "="*60)
    print("HELP BUTTON SYSTEM - LIVE MODE")
    print("="*60)
    print("  Hold SPACE for 2 seconds  ->  Help request")
    print("  Quick tap of SPACE        ->  Ignored (accidental)")
    print("  Press ESC                 ->  Quit")
    print("="*60 + "\n")

    result = start_keyboard_listener(on_intentional_press=handle_help_request)

    # if result is None:
    #     print("Keyboard listener failed. Running test mode instead.")
    #     run_test_mode()
    #     return

    handler, listener = result
    try:
        listener.join()
    except KeyboardInterrupt:
        print("\nShutting down...")

# def run_test_mode():
#     print("\n" + "="*60)
#     print("TEST MODE - Testing all error scenarios")
#     print("="*60)

#     # Test 1: No speech
#     print("\n[Test 1] Simulating NO SPEECH...")
#     result = check_audio_errors("", 0.9)
#     handle_audio_error(result)
#     time.sleep(3)

#     # Test 2: Choppy audio
#     print("\n[Test 2] Simulating CHOPPY AUDIO...")
#     result = check_audio_errors("help me please", 0.2)
#     handle_audio_error(result)
#     time.sleep(3)

#     # Test 3: Non-emergency
#     print("\n[Test 3] Simulating NON-EMERGENCY...")
#     result = check_audio_errors("I am hungry where is the toilet", 0.9)
#     handle_audio_error(result)
#     time.sleep(3)

#     # Test 4: Valid emergency
#     print("\n[Test 4] Simulating VALID EMERGENCY...")
#     result = check_audio_errors("Help there are people outside my door I am scared", 0.9)
#     if not result["has_error"]:
#         print("Valid emergency - would trigger full recording pipeline.")
#         play_voice_alert("Help is on the way. Your request has been received.")
#     time.sleep(3)

#     # Test 5: Real mic press
#     print("\n[Test 5] Real button press - speak into mic when recording starts!")
#     time.sleep(1)
#     handler = ButtonHandler(on_intentional_press=handle_help_request)
#     handler.simulate_press(hold_duration=2.5)

#     print("\nWaiting for processing to finish...")
#     time.sleep(RECORDING_DURATION + 20)

#     print("\nLatest entries in ClickHouse:")
#     events = get_recent_events(limit=5)
#     if events:
#         for event in events:
#             print("\n  " + event["timestamp"])
#             print("  Language:    " + event["language"])
#             print("  Transcript:  " + event["transcript"])
#             print("  Button type: " + event["button_type"])
#     else:
#         print("  (No ClickHouse data - that's ok if ClickHouse isn't running yet)")

    get_stats()

def view_stored_data():
    print("\nMost recent speech events in ClickHouse:\n")
    events = get_recent_events(limit=20)

    if not events:
        print("No data found. Make sure ClickHouse is running.")
        return

    for i, event in enumerate(events, 1):
        print(f"  [{i}] {event['timestamp']} | {event['language']}")
        print(f"       {event['transcript']}")
        print(f"       {event['button_type']} | confidence: {event['confidence']} | {event['duration']}")
        print()

    get_stats()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SG Speech Help Button System")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    parser.add_argument("--view", action="store_true", help="View stored data")
    args = parser.parse_args()

    print("Setting up ClickHouse table...")
    setup_table()

    run_live_mode()

    if args.view:
        view_stored_data()
    # elif args.test:
    #     run_test_mode()
    # else:
    #     run_live_mode()