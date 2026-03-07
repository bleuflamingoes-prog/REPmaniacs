"""
audio_capture.py
----------------
Records audio from your laptop's microphone.
No coding knowledge needed — just call record_audio() and it saves a .wav file.
"""

import sounddevice as sd
import soundfile as sfpy
import numpy as np

SAMPLE_RATE = 16000   # 16kHz is what Whisper expects
CHANNELS = 1          # Mono audio

def record_audio(duration_seconds=10, output_filename="recorded_audio.wav"):
    """
    Records audio from the default microphone.

    Args:
        duration_seconds: How many seconds to record (default: 10)
        output_filename:  Where to save the audio file (default: recorded_audio.wav)

    Returns:
        The filename of the saved audio, or None if something went wrong.
    """
    print(f"\n🎙️  Recording for {duration_seconds} seconds... Speak now!")

    try:
        audio_data = sd.rec(
            int(duration_seconds * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32"
        )
        sd.wait()  # Wait until recording is done

        sf.write(output_filename, audio_data, SAMPLE_RATE)
        print(f"✅ Recording saved to: {output_filename}")
        return output_filename

    except Exception as e:
        print(f"❌ Error recording audio: {e}")
        print("   Make sure your microphone is connected and not being used by another app.")
        return None


def list_microphones():
    """Prints all available audio input devices so you can pick the right one."""
    print("\n🎤 Available microphones on this computer:")
    devices = sd.query_devices()
    for i, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            print(f"   [{i}] {device['name']}")
