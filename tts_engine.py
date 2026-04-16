"""
TTS engine — streamlined dual fallback:
  1. edge-tts (cloud, unlimited, fast, consistent English voice)
  2. VOICEVOX (local Japanese anime voices)
"""
import io
import json
import os
import re
import tempfile
import time

import soundfile as sf
import requests
import config
from logger import Log

log = Log("TTS-Eng")


def _clean_text_for_tts(text: str) -> str:
    """
    Clean dialogue text for TTS engines.
    Strips markdown formatting and non-ASCII characters that crash TTS.
    """
    original_len = len(text)
    # Remove asterisks (markdown bold/italic)
    text = text.replace('*', '')
    # Replace em dash and en dash with comma pause
    text = text.replace('—', ', ').replace('–', ', ')
    # Replace ellipsis character with dots
    text = text.replace('…', '...')
    # Remove any non-ASCII characters (emoji, CJK, symbols, etc.)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) != original_len:
        log.debug(f"Cleaned text: {original_len} → {len(text)} chars")
    return text


# ─── Kokoro Setup ────────────────────────────────────────────
_kokoro_pipeline = None
_voicevox_available = False

def _check_voicevox() -> bool:
    try:
        r = requests.get(f"{config.VOICEVOX_URL}/speakers", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def init_tts():
    """Initialize TTS engines, detect availability."""
    global _voicevox_available, _kokoro_pipeline

    if os.path.exists(config.KOKORO_MODEL_PATH) and os.path.exists(config.KOKORO_VOICES_PATH):
        try:
            from kokoro_onnx import Kokoro
            _kokoro_pipeline = Kokoro(config.KOKORO_MODEL_PATH, config.KOKORO_VOICES_PATH)
            log.success(f"Kokoro-ONNX ready — voice={config.KOKORO_VOICE}")
        except Exception as e:
            log.error(f"Kokoro initialization failed: {e}")
    else:
        log.warn("Kokoro models missing. Skipping offline init.")

    log.info(f"Checking VOICEVOX at {config.VOICEVOX_URL}...")
    _voicevox_available = _check_voicevox()
    if _voicevox_available:
        log.success(f"VOICEVOX detected — speaker={config.VOICEVOX_SPEAKER}")
    else:
        log.debug("VOICEVOX not found (optional)")

    log.success(f"edge-tts ready — voice={config.EDGE_TTS_VOICE}, rate={config.EDGE_TTS_RATE}")


def _speak_voicevox(text: str) -> bytes | None:
    """Generate audio via VOICEVOX. Returns WAV bytes."""
    try:
        log.debug(f"VOICEVOX: querying audio for {len(text)} chars...")
        with log.timed("VOICEVOX audio_query"):
            query_resp = requests.post(
                f"{config.VOICEVOX_URL}/audio_query",
                params={"text": text, "speaker": config.VOICEVOX_SPEAKER},
                timeout=10,
            )
            query_resp.raise_for_status()
        query_data = query_resp.json()

        query_data["speedScale"] = 1.15
        query_data["pitchScale"] = 0.03
        query_data["intonationScale"] = 1.5

        with log.timed("VOICEVOX synthesis"):
            synth_resp = requests.post(
                f"{config.VOICEVOX_URL}/synthesis",
                params={"speaker": config.VOICEVOX_SPEAKER},
                headers={"Content-Type": "application/json"},
                data=json.dumps(query_data),
                timeout=30,
            )
            synth_resp.raise_for_status()

        size_kb = len(synth_resp.content) / 1024
        log.success(f"VOICEVOX: got {size_kb:.1f}KB WAV")
        return synth_resp.content

    except Exception as e:
        log.error("VOICEVOX synthesis failed", exc=e)
        return None

def _speak_kokoro(text: str) -> bytes | None:
    """Generate audio via Kokoro-ONNX globally."""
    if not _kokoro_pipeline:
        return None
    try:
        with log.timed(f"Kokoro-ONNX synthesis"):
            samples, sample_rate = _kokoro_pipeline.create(
                text, voice=config.KOKORO_VOICE, speed=config.KOKORO_SPEED, lang="en-us"
            )
            
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format='WAV')
        audio_bytes = buf.getvalue()
        
        size_kb = len(audio_bytes) / 1024
        log.success(f"Kokoro-ONNX: got {size_kb:.1f}KB WAV")
        return audio_bytes
    except Exception as e:
        log.error("Kokoro synthesis failed", exc=e)
        return None


async def _speak_edge_tts_async(text: str) -> bytes | None:
    """Generate audio via edge-tts. Returns MP3 bytes."""
    try:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice=config.EDGE_TTS_VOICE,
            rate=config.EDGE_TTS_RATE,
            pitch=getattr(config, "EDGE_TTS_PITCH", "+0Hz"),
        )

        tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", prefix="april_tts_")
        tmp_path = tmp_fd.name
        tmp_fd.close()
        await communicate.save(tmp_path)

        # Verify file has content
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 100:
            size = os.path.getsize(tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
            log.debug(f"edge-tts: saved {size / 1024:.1f}KB MP3 to temp file")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return data
        else:
            log.warn("edge-tts: generated file is empty or too small")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return None

    except Exception as e:
        log.error("edge-tts async failed", exc=e)
        return None


def _speak_edge_tts(text: str) -> bytes | None:
    """Sync wrapper for edge-tts with retry. Uses asyncio.run() for clean lifecycle."""
    import asyncio
    for attempt in range(3):
        try:
            log.debug(f"edge-tts: attempt {attempt + 1}/3")
            with log.timed(f"edge-tts synthesis (attempt {attempt + 1})"):
                result = asyncio.run(_speak_edge_tts_async(text))
            if result:
                return result
            if attempt < 2:
                log.warn(f"edge-tts: empty result, retrying (attempt {attempt + 2}/3)...")
                time.sleep(0.5)
        except Exception as e:
            log.error(f"edge-tts: attempt {attempt + 1}/3 failed", exc=e)
    log.error("edge-tts: all 3 attempts failed")
    return None


def synthesize(text: str, action_type: str = "commentary") -> tuple[bytes | None, str]:
    """
    Convert text to audio. Priority:
      1. Kokoro-ONNX (primary — 100% offline, expressive anime companion)
      2. edge-tts (cloud fallback)
      3. VOICEVOX (local Japanese anime voice)
    Returns (audio_bytes, format) where format is 'wav' or 'mp3'.
    """
    # Clean text for TTS (strip emoji, markdown, special chars)
    text = _clean_text_for_tts(text)
    if not text:
        log.warn("No text left after cleaning — skipping synthesis")
        return None, ""

    log.info(f"Synthesizing {len(text)} chars — action={action_type}")
    log.debug(f"Text: \"{text[:100]}{'...' if len(text) > 100 else ''}\"")

    # 1. Try Kokoro-ONNX (100% Offline, ultra-fast)
    if _kokoro_pipeline:
        log.info("Trying Kokoro-ONNX (primary)...")
        audio = _speak_kokoro(text)
        if audio:
            return audio, "wav"

    # 2. Try edge-tts (fallback)
    log.info("Trying edge-tts (fallback)...")
    audio = _speak_edge_tts(text)
    if audio:
        log.success(f"edge-tts succeeded — {len(audio) / 1024:.1f}KB MP3")
        return audio, "mp3"

    # 3. Try VOICEVOX (local Japanese anime voice)
    if _voicevox_available:
        log.info("Trying VOICEVOX (fallback)...")
        audio = _speak_voicevox(text)
        if audio:
            log.success(f"VOICEVOX succeeded — {len(audio) / 1024:.1f}KB WAV")
            return audio, "wav"

    log.error("All TTS engines failed — no audio produced")
    return None, ""
