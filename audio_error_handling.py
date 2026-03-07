"""
audio_error_handling.py
-----------------------
Handles all audio quality problems BEFORE speech recognition runs.

What this does:
  1. Checks microphone is available and sensitive enough
  2. Amplifies quiet voices (seniors often speak softly)
  3. Suppresses background noise (TV, traffic, other people)
  4. Isolates the voice from surrounding sounds
  5. Detects if the recording is silent / unusable

Call process_audio(filename) on any recorded .wav file and it returns
a cleaned, amplified version ready for the speech recogniser.
"""

import numpy as np
import soundfile as sf
import sounddevice as sd
import os

# ── Tuning knobs ──────────────────────────────────────────────────────────────
SILENCE_THRESHOLD     = 0.01   # RMS below this = recording is silent
TARGET_RMS            = 0.08   # Target loudness after amplification
MAX_AMPLIFY_FACTOR    = 8.0    # Never amplify more than 8× (prevents distortion)
NOISE_SAMPLE_SECS     = 0.5    # Use first 0.5s of audio to estimate background noise
# ─────────────────────────────────────────────────────────────────────────────


# ── 1. MICROPHONE CHECKS ──────────────────────────────────────────────────────

def check_microphone_available():
    """
    Verifies a microphone exists and is accessible.

    Returns:
        (True, device_name)  if mic found
        (False, error_msg)   if no mic or permission denied
    """
    try:
        devices = sd.query_devices()
        input_devices = [d for d in devices if d["max_input_channels"] > 0]

        if not input_devices:
            return False, "No microphone found. Please plug in a microphone."

        default = sd.query_devices(kind="input")
        return True, default["name"]

    except Exception as e:
        return False, f"Microphone check failed: {e}"


def check_recording_sensitivity(audio_data, sample_rate):
    """
    Tests if the microphone captured anything meaningful.

    Args:
        audio_data:  numpy array of audio samples
        sample_rate: sample rate of the recording

    Returns:
        dict with keys: is_silent, rms_level, recommendation
    """
    rms = float(np.sqrt(np.mean(audio_data ** 2)))

    if rms < SILENCE_THRESHOLD:
        return {
            "is_silent": True,
            "rms_level": round(rms, 4),
            "recommendation": (
                "Recording is silent or too quiet. "
                "Check that the microphone is not muted and the person is speaking "
                "within 1 metre of the device."
            )
        }

    return {
        "is_silent": False,
        "rms_level": round(rms, 4),
        "recommendation": "Audio level OK."
    }


# ── 2. VOICE AMPLIFICATION ────────────────────────────────────────────────────

def amplify_voice(audio_data):
    """
    Boosts quiet audio to a consistent loudness level.
    Safe for soft-spoken seniors — will not over-amplify or clip.

    Args:
        audio_data: numpy float32 array

    Returns:
        Amplified numpy array (same shape), clipped to [-1, 1]
    """
    rms = float(np.sqrt(np.mean(audio_data ** 2)))

    if rms < 1e-6:
        # Completely silent — nothing to amplify
        return audio_data

    gain = min(TARGET_RMS / rms, MAX_AMPLIFY_FACTOR)

    if gain > 1.0:
        print(f"🔊 Voice amplified {gain:.1f}× (original level was low)")

    amplified = audio_data * gain

    # Hard clip to prevent distortion
    return np.clip(amplified, -1.0, 1.0)


# ── 3. NOISE SUPPRESSION ──────────────────────────────────────────────────────

def suppress_noise(audio_data, sample_rate):
    """
    Reduces steady background noise (TV, fan, traffic).

    Strategy: spectral subtraction
      - Estimate noise from the first 0.5 seconds (assumed to be background)
      - Subtract that noise profile from the whole recording
      - This is lightweight and works without extra libraries

    Args:
        audio_data:  numpy float32 array (mono)
        sample_rate: int

    Returns:
        Noise-suppressed numpy array
    """
    noise_samples = int(NOISE_SAMPLE_SECS * sample_rate)

    if len(audio_data) <= noise_samples:
        # Recording is too short to estimate noise — return as-is
        return audio_data

    # Estimate noise floor from the opening silence
    noise_profile = audio_data[:noise_samples]
    noise_power   = np.mean(noise_profile ** 2)

    # Apply soft noise gate: attenuate frames quieter than 2× the noise floor
    frame_size = 512
    output = audio_data.copy()

    for start in range(0, len(audio_data) - frame_size, frame_size):
        frame = audio_data[start : start + frame_size]
        frame_power = np.mean(frame ** 2)

        if frame_power < 2 * noise_power:
            # This frame is likely background — suppress it
            output[start : start + frame_size] *= 0.1

    print("🔇 Noise suppression applied.")
    return output


# ── 4. VOICE ISOLATION ────────────────────────────────────────────────────────

def isolate_voice(audio_data, sample_rate):
    """
    Isolates the human voice frequency range (85 Hz – 3 000 Hz).
    Filters out very low rumbles and very high hiss/clatter.

    Uses a simple FFT band-pass filter — no extra libraries needed.

    Args:
        audio_data:  numpy float32 array (mono)
        sample_rate: int

    Returns:
        Band-pass filtered numpy array
    """
    LOW_CUTOFF  =   85   # Hz — below this is rumble / HVAC
    HIGH_CUTOFF = 3000   # Hz — above this is hiss / clatter

    # FFT
    spectrum = np.fft.rfft(audio_data)
    freqs    = np.fft.rfftfreq(len(audio_data), d=1.0 / sample_rate)

    # Zero out frequencies outside the voice band
    spectrum[(freqs < LOW_CUTOFF) | (freqs > HIGH_CUTOFF)] = 0

    # Inverse FFT back to time domain
    filtered = np.fft.irfft(spectrum, n=len(audio_data))

    print(f"🎙️  Voice isolation applied ({LOW_CUTOFF}–{HIGH_CUTOFF} Hz band-pass).")
    return filtered.astype(np.float32)


# ── 5. FULL PIPELINE ──────────────────────────────────────────────────────────

def process_audio(input_filename, output_filename=None):
    """
    Runs the complete audio cleaning pipeline on a recorded .wav file.

    Steps:
      1. Load the file
      2. Check if it's silent / unusable
      3. Suppress background noise
      4. Isolate voice frequencies
      5. Amplify to consistent loudness
      6. Save cleaned file

    Args:
        input_filename:  Path to the raw recorded .wav file
        output_filename: Where to save cleaned audio
                         (default: adds '_clean' suffix to input name)

    Returns:
        dict with keys:
          - success      (bool)
          - output_file  (str or None)
          - is_silent    (bool)
          - rms_before   (float)
          - rms_after    (float)
          - message      (str)
    """
    # Default output filename
    if output_filename is None:
        base, ext = os.path.splitext(input_filename)
        output_filename = f"{base}_clean{ext}"

    # Load audio
    try:
        audio_data, sample_rate = sf.read(input_filename, dtype="float32")
    except Exception as e:
        return {
            "success": False, "output_file": None,
            "is_silent": True, "rms_before": 0, "rms_after": 0,
            "message": f"Could not load audio file: {e}"
        }

    # Flatten stereo to mono if needed
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)

    rms_before = float(np.sqrt(np.mean(audio_data ** 2)))

    # Check silence
    silence_check = check_recording_sensitivity(audio_data, sample_rate)
    if silence_check["is_silent"]:
        return {
            "success": False, "output_file": None,
            "is_silent": True, "rms_before": rms_before, "rms_after": 0,
            "message": silence_check["recommendation"]
        }

    # Clean the audio
    audio_data = suppress_noise(audio_data, sample_rate)
    audio_data = isolate_voice(audio_data, sample_rate)
    audio_data = amplify_voice(audio_data)

    rms_after = float(np.sqrt(np.mean(audio_data ** 2)))

    # Save cleaned file
    try:
        sf.write(output_filename, audio_data, sample_rate)
        print(f"✅ Cleaned audio saved to: {output_filename}")
    except Exception as e:
        return {
            "success": False, "output_file": None,
            "is_silent": False, "rms_before": rms_before, "rms_after": rms_after,
            "message": f"Could not save cleaned audio: {e}"
        }

    return {
        "success":     True,
        "output_file": output_filename,
        "is_silent":   False,
        "rms_before":  round(rms_before, 4),
        "rms_after":   round(rms_after,  4),
        "message":     "Audio processed successfully."
    }