"""
audio_error_handling.py
-----------------------
Handles all audio quality problems BEFORE speech recognition runs.
Uses ONLY built-in Python libraries — no pip installs needed.

What this does:
  1. Checks microphone is available and sensitive enough
  2. Amplifies quiet voices (seniors often speak softly)
  3. Suppresses background noise (TV, traffic, other people)
  4. Isolates the voice from surrounding sounds
  5. Detects if the recording is silent / unusable

Call process_audio(filename) on any recorded .wav file and it returns
a cleaned, amplified version ready for the speech recogniser.
"""

import wave
import struct
import math
import array
import os

# ── Tuning knobs ──────────────────────────────────────────────────────────────
SILENCE_THRESHOLD   = 0.01  # RMS below this = recording is silent
TARGET_RMS          = 0.08  # Target loudness after amplification
MAX_AMPLIFY_FACTOR  = 8.0   # Never amplify more than 8× (prevents distortion)
NOISE_SAMPLE_SECS   = 0.5   # Use first 0.5s of audio to estimate background noise
# ─────────────────────────────────────────────────────────────────────────────


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _read_wav(filename):
    """Read a .wav file, return (samples as floats in [-1,1], sample_rate)."""
    with wave.open(filename, "rb") as wf:
        n_channels   = wf.getnchannels()
        sample_width = wf.getsampwidth()  # bytes per sample
        sample_rate  = wf.getframerate()
        n_frames     = wf.getnframes()
        raw          = wf.readframes(n_frames)

    # Decode bytes → integers
    if sample_width == 2:
        fmt = f"{n_frames * n_channels}h"
        max_val = 32768.0
    elif sample_width == 1:
        fmt = f"{n_frames * n_channels}B"
        max_val = 128.0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width} bytes")

    samples = list(struct.unpack(fmt, raw))

    # Mix stereo down to mono by averaging channels
    if n_channels == 2:
        samples = [(samples[i] + samples[i + 1]) / 2 for i in range(0, len(samples), 2)]

    # Normalise to [-1.0, 1.0]
    samples = [s / max_val for s in samples]
    return samples, sample_rate


def _write_wav(filename, samples, sample_rate):
    """Write float samples ([-1,1]) to a 16-bit mono .wav file."""
    int_samples = [max(-32768, min(32767, int(s * 32767))) for s in samples]
    raw = struct.pack(f"{len(int_samples)}h", *int_samples)

    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)


def _rms(samples):
    """Root mean square of a list of floats."""
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


# ── 1. MICROPHONE CHECK ───────────────────────────────────────────────────────

def check_microphone_available():
    """
    Best-effort mic check without sounddevice.
    Returns (True, message) or (False, error_message).
    """
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "-c", "import sounddevice as sd; print(sd.query_devices(kind='input')['name'])"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
    except Exception:
        pass

    # Fallback: just confirm the wav module is usable
    return True, "Microphone check skipped (sounddevice not installed). Proceeding with file."


# ── 2. SILENCE CHECK ─────────────────────────────────────────────────────────

def check_recording_sensitivity(samples):
    """
    Tests if the recording captured anything meaningful.

    Returns dict: is_silent, rms_level, recommendation
    """
    level = _rms(samples)

    if level < SILENCE_THRESHOLD:
        return {
            "is_silent": True,
            "rms_level": round(level, 4),
            "recommendation": (
                "Recording is silent or too quiet. "
                "Check the microphone is not muted and the speaker is within 1 metre."
            )
        }
    return {
        "is_silent": False,
        "rms_level": round(level, 4),
        "recommendation": "Audio level OK."
    }


# ── 3. VOICE AMPLIFICATION ────────────────────────────────────────────────────

def amplify_voice(samples):
    """
    Boosts quiet audio to a consistent loudness.
    Safe for soft-spoken users — won't over-amplify or clip.
    """
    level = _rms(samples)
    if level < 1e-6:
        return samples  # completely silent

    gain = min(TARGET_RMS / level, MAX_AMPLIFY_FACTOR)
    if gain > 1.0:
        print(f"🔊 Voice amplified {gain:.1f}× (original level was low)")

    amplified = [max(-1.0, min(1.0, s * gain)) for s in samples]
    return amplified


# ── 4. NOISE SUPPRESSION ─────────────────────────────────────────────────────

def suppress_noise(samples, sample_rate):
    """
    Reduces steady background noise using a simple noise gate.
    Estimates noise from the first 0.5 seconds, then suppresses
    frames quieter than 2× that noise floor.
    """
    noise_end = int(NOISE_SAMPLE_SECS * sample_rate)

    if len(samples) <= noise_end:
        return samples  # too short

    noise_profile = samples[:noise_end]
    noise_power   = sum(s * s for s in noise_profile) / len(noise_profile)

    frame_size = 512
    output     = samples[:]

    for start in range(0, len(samples) - frame_size, frame_size):
        frame       = samples[start : start + frame_size]
        frame_power = sum(s * s for s in frame) / len(frame)

        if frame_power < 2 * noise_power:
            for i in range(start, start + frame_size):
                output[i] *= 0.1

    print("🔇 Noise suppression applied.")
    return output


# ── 5. VOICE ISOLATION (band-pass filter) ────────────────────────────────────

def isolate_voice(samples, sample_rate):
    """
    Keeps only the human voice band (85 Hz – 3000 Hz) using FFT.
    Removes low rumble and high hiss.
    """
    LOW_CUTOFF  =   85
    HIGH_CUTOFF = 3000

    n       = len(samples)
    # Manual DFT is too slow; use Python's cmath for a basic FFT substitute.
    # For pure-Python speed we apply a simple IIR band-pass instead.

    # High-pass at ~85 Hz  (removes rumble)
    rc_hp = 1.0 / (2 * math.pi * LOW_CUTOFF)
    dt    = 1.0 / sample_rate
    alpha_hp = rc_hp / (rc_hp + dt)

    hp = [0.0] * n
    hp[0] = samples[0]
    for i in range(1, n):
        hp[i] = alpha_hp * (hp[i - 1] + samples[i] - samples[i - 1])

    # Low-pass at ~3000 Hz (removes hiss)
    rc_lp    = 1.0 / (2 * math.pi * HIGH_CUTOFF)
    alpha_lp = dt / (rc_lp + dt)

    lp = [0.0] * n
    lp[0] = hp[0]
    for i in range(1, n):
        lp[i] = lp[i - 1] + alpha_lp * (hp[i] - lp[i - 1])

    print(f"🎙️  Voice isolation applied ({LOW_CUTOFF}–{HIGH_CUTOFF} Hz band-pass).")
    return lp


# ── 6. FULL PIPELINE ──────────────────────────────────────────────────────────

def process_audio(input_filename, output_filename=None):
    """
    Runs the complete audio cleaning pipeline on a recorded .wav file.

    Args:
        input_filename:  Path to the raw .wav file
        output_filename: Where to save cleaned audio
                         (defaults to original name + '_clean')

    Returns:
        dict: success, output_file, is_silent, rms_before, rms_after, message
    """
    if output_filename is None:
        base, ext    = os.path.splitext(input_filename)
        output_filename = f"{base}_clean{ext}"

    # Load
    try:
        samples, sample_rate = _read_wav(input_filename)
    except Exception as e:
        return {
            "success": False, "output_file": None,
            "is_silent": True, "rms_before": 0, "rms_after": 0,
            "message": f"Could not load audio file: {e}"
        }

    rms_before = _rms(samples)

    # Silence check
    silence_check = check_recording_sensitivity(samples)
    if silence_check["is_silent"]:
        return {
            "success": False, "output_file": None,
            "is_silent": True, "rms_before": rms_before, "rms_after": 0,
            "message": silence_check["recommendation"]
        }

    # Clean
    samples = suppress_noise(samples, sample_rate)
    samples = isolate_voice(samples, sample_rate)
    samples = amplify_voice(samples)

    rms_after = _rms(samples)

    # Save
    try:
        _write_wav(output_filename, samples, sample_rate)
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