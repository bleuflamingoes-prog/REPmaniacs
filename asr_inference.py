"""
asr_inference.py
"""
import torch
import librosa
import numpy as np

_model = None
_processor = None

def load_model():
    global _model, _processor
    if _model is not None:
        return
    print("Loading MERaLiON model (first time may take a few minutes)...")
    try:
        from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
        model_name = "MERaLiON/MERaLiON-AudioLLM-Whisper-SEA-LION"
        _processor = AutoProcessor.from_pretrained(model_name)
        _model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_name,
            low_cpu_mem_usage=True,
            torch_dtype=torch.float32
        )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = _model.to(device)
        print("Model loaded on: " + device.upper())
    except Exception as e:
        print("MERaLiON failed, trying Whisper fallback: " + str(e))
        _load_whisper_fallback()

def _load_whisper_fallback():
    global _model, _processor
    from transformers import WhisperProcessor, WhisperForConditionalGeneration
    _processor = WhisperProcessor.from_pretrained("openai/whisper-large-v3")
    _model = WhisperForConditionalGeneration.from_pretrained(
        "openai/whisper-large-v3",
        torch_dtype=torch.float32
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    _model = _model.to(device)
    print("Whisper fallback loaded.")

def transcribe_audio(audio_filepath, language_hint=None):
    load_model()
    print("Transcribing: " + audio_filepath)
    try:
        audio_array, sample_rate = librosa.load(audio_filepath, sr=16000, mono=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        inputs = _processor(
            audio_array,
            sampling_rate=16000,
            return_tensors="pt"
        )
        input_features = inputs.input_features.to(device=device, dtype=torch.float32)
        forced_decoder_ids = None
        if language_hint:
            forced_decoder_ids = _processor.get_decoder_prompt_ids(
                language=language_hint,
                task="transcribe"
            )
        with torch.no_grad():
            generated_ids = _model.generate(
                input_features,
                forced_decoder_ids=forced_decoder_ids
            )
        transcript = _processor.batch_decode(
            generated_ids,
            skip_special_tokens=True
        )[0].strip()
        detected_language = _detect_language_label(transcript, language_hint)
        print("Transcript: " + transcript)
        print("Language: " + detected_language)
        return {
            "transcript": transcript,
            "language": detected_language,
            "confidence": 0.85
        }
    except Exception as e:
        print("Transcription failed: " + str(e))
        return {"transcript": "", "language": "unknown", "confidence": 0.0}

def _detect_language_label(transcript, language_hint):
    if language_hint == "zh":
        return "zh-CN (Mandarin)"
    if language_hint == "en":
        return "en-SG (Singapore English)"
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in transcript)
    if has_chinese:
        return "zh-CN (Mandarin / Code-switch)"
    return "en-SG (Singapore English)"