import threading

CONFIDENCE_THRESHOLD = 0.45
MIN_WORD_COUNT = 2

NON_EMERGENCY_PHRASES = [
    "where is the toilet", "where's the toilet", "toilet", "bathroom", "restroom",
    "i need to pee", "bored", "boring", "testing", "test",
    "one two three", "1 2 3", "mic test", "is this working", "can you hear me",
    "too cold", "too hot", "air con", "aircon", "fan", "it's noisy", "too noisy",
    "i'm tired", "i am tired", "sleepy", "i want to sleep", "i want to go home",
    "i'm bored", "nothing to do", "i want my phone",
]

EMERGENCY_KEYWORDS = [
    "help", "emergency", "accident", "fire", "danger", "hurt", "injured",
    "bleeding", "pain", "attack", "trapped", "stuck", "cannot breathe",
    "can't breathe", "chest", "faint", "unconscious", "fall", "fell",
    "broke", "broken", "scared", "threatening", "knife", "weapon",
    "please", "urgent", "serious", "bad", "wrong", "problem",
    "police", "ambulance", "doctor", "nurse",
    "outside", "door", "someone", "people", "stranger", "dizzy", "unwell",
]

def check_audio_errors(transcript, confidence, attempt=1):
    """
    attempt=1 → first try, be strict
    attempt=2 → second try, if still can't speak → treat as emergency (possible stroke/distress)
    """

    no_speech   = not transcript or transcript.strip() == "" or transcript == "[no speech detected]"
    too_short   = len(transcript.strip().split()) < MIN_WORD_COUNT if transcript else True
    too_choppy  = confidence < CONFIDENCE_THRESHOLD

    # On second attempt — if still silent or choppy, escalate to emergency
    if attempt >= 2 and (no_speech or too_short or too_choppy):
        return {
            "has_error":        False,
            "error_type":       None,
            "message":          "Second attempt also unclear — escalating to emergency (possible stroke/distress).",
            "alert_text":       "Unable to verify speech. Sending help as a precaution.",
            "should_retry":     False,
            "force_emergency":  True
        }

    if no_speech:
        return {
            "has_error":       True,
            "error_type":      "no_speech",
            "message":         "WARNING: No speech detected. The recording was silent or too quiet.",
            "alert_text":      "We could not hear you. Please press the button again and speak clearly.",
            "should_retry":    True,
            "force_emergency": False
        }

    if too_short:
        return {
            "has_error":       True,
            "error_type":      "no_speech",
            "message":         "WARNING: Too short to understand. Please say more.",
            "alert_text":      "We could not understand you. Please press the button again and describe your situation.",
            "should_retry":    True,
            "force_emergency": False
        }

    if too_choppy:
        return {
            "has_error":       True,
            "error_type":      "choppy_audio",
            "message":         "WARNING: Audio was unclear (" + str(round(confidence * 100)) + "% confidence). Speak louder.",
            "alert_text":      "Your audio was unclear. Please press the button again and speak louder.",
            "should_retry":    True,
            "force_emergency": False
        }

    transcript_lower = transcript.lower()

    # Emergency keywords always override non-emergency check
    if any(kw in transcript_lower for kw in EMERGENCY_KEYWORDS):
        return {"has_error": False, "error_type": None, "message": None, "alert_text": None, "should_retry": False, "force_emergency": False}

    # Non-emergency misuse check (only on first attempt)
    if any(phrase in transcript_lower for phrase in NON_EMERGENCY_PHRASES):
        matched = [p for p in NON_EMERGENCY_PHRASES if p in transcript_lower][0]
        return {
            "has_error":       True,
            "error_type":      "non_emergency",
            "message":         "WARNING: Does not sound like an emergency (detected: '" + matched + "'). This button is for emergencies only.",
            "alert_text":      "This button is for emergencies only. Please only press it if you are in danger.",
            "should_retry":    False,
            "force_emergency": False
        }

    return {"has_error": False, "error_type": None, "message": None, "alert_text": None, "should_retry": False, "force_emergency": False}


def play_voice_alert(text):
    def _speak():
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 150)
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
        except Exception:
            try:
                import subprocess
                safe_text = text.replace("'", "")
                subprocess.run([
                    "powershell", "-Command",
                    f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('{safe_text}')"
                ], capture_output=True)
            except Exception as e:
                print("Voice alert could not play: " + str(e))

    threading.Thread(target=_speak, daemon=True).start()


def handle_audio_error(error_result):
    print("\n" + "!"*60)
    print(error_result["message"])
    print("!"*60)

    if error_result["alert_text"]:
        print("\nPlaying voice alert: " + error_result["alert_text"])
        play_voice_alert(error_result["alert_text"])

    if error_result["should_retry"]:
        print("\nPlease press the button again and try once more.")
    elif not error_result.get("force_emergency"):
        print("\nRequest not logged. Please use this button for emergencies only.")

    return error_result["should_retry"]