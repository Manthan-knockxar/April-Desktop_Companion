"""
Audio player — non-blocking playback with interrupt support.
Handles both WAV (VOICEVOX) and MP3 (edge-tts) formats.
"""
import io
import os
import tempfile
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf
from logger import Log

log = Log("Audio")

_current_stream = None
_lock = threading.Lock()


def _cleanup_temp(path: str | None):
    """Safely remove a temporary file."""
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def play_audio(audio_bytes: bytes, audio_format: str):
    """
    Play audio bytes in a non-blocking thread.
    Interrupts any currently playing audio.
    """
    if not audio_bytes:
        log.warn("play_audio called with empty bytes — skipping")
        return

    log.debug(f"Queuing {len(audio_bytes) / 1024:.1f}KB {audio_format.upper()} for playback")
    thread = threading.Thread(
        target=_play_worker,
        args=(audio_bytes, audio_format),
        daemon=True,
    )
    thread.start()


def _play_worker(audio_bytes: bytes, audio_format: str):
    """Worker thread for audio playback."""
    global _current_stream

    try:
        if audio_format == "wav":
            # WAV: read directly from bytes
            log.debug("Decoding WAV from memory...")
            data, samplerate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
            tmp_path = None
        elif audio_format == "mp3":
            # MP3: write to unique temp file, then read with soundfile
            tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", prefix="april_")
            tmp_path = tmp_fd.name
            tmp_fd.write(audio_bytes)
            tmp_fd.close()
            log.debug(f"Wrote MP3 to temp: {tmp_path}")

            try:
                data, samplerate = sf.read(tmp_path, dtype="float32")
            except Exception as e:
                log.error(f"soundfile can't decode MP3: {e}")
                _cleanup_temp(tmp_path)
                return
        else:
            log.error(f"Unknown audio format: {audio_format}")
            return

        duration = len(data) / samplerate
        channels = data.shape[1] if data.ndim > 1 else 1
        log.debug(f"Audio decoded: {samplerate}Hz, {channels}ch, {duration:.1f}s")

        # Ensure stereo
        if data.ndim == 1:
            data = np.column_stack([data, data])

        # Single lock acquisition for the entire stop-then-play sequence
        with _lock:
            # Stop any current playback
            if _current_stream is not None and _current_stream.active:
                log.debug("Interrupting previous playback")
                _current_stream.stop()
                _current_stream.close()
                _current_stream = None

            _current_stream = sd.OutputStream(
                samplerate=samplerate,
                channels=data.shape[1],
                dtype="float32",
            )
            _current_stream.start()
            log.info(f"▶ Playing {duration:.1f}s audio @ {samplerate}Hz")
            _current_stream.write(data)
            _current_stream.stop()
            _current_stream.close()
            _current_stream = None
            log.debug("Playback completed, stream closed")
            _cleanup_temp(tmp_path)

    except Exception as e:
        log.error("Playback error", exc=e)


def stop():
    """Public method to stop current playback."""
    with _lock:
        global _current_stream
        if _current_stream is not None and _current_stream.active:
            log.info("Stopping playback (manual stop)")
            _current_stream.stop()
            _current_stream.close()
            _current_stream = None
