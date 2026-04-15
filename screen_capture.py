"""
Screen capture module using mss.
Captures the primary monitor and returns a PIL Image.
Includes frame-difference detection for smart skipping.

Thread safety: each thread gets its own mss instance via threading.local(),
since mss uses thread-local Win32 DC handles internally.
"""
import threading

import numpy as np
from mss import mss
from PIL import Image
import config
from logger import Log

log = Log("Capture")

_thread_local = threading.local()   # per-thread mss instances
_previous_frame = None
_frame_lock = threading.Lock()


def _get_sct():
    """Get or create a thread-local mss screenshot instance."""
    if not hasattr(_thread_local, "sct"):
        log.debug(f"Creating new mss instance for thread {threading.current_thread().name}")
        _thread_local.sct = mss()
    return _thread_local.sct


def capture_screen() -> Image.Image:
    """Capture the primary monitor and return as PIL Image."""
    sct = _get_sct()
    monitor = sct.monitors[config.MONITOR_INDEX]
    screenshot = sct.grab(monitor)
    img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    log.debug(f"Captured {img.width}x{img.height} from monitor {config.MONITOR_INDEX}")
    return img


def has_significant_change(current_image: Image.Image) -> bool:
    """
    Compare current frame to the previous one.
    Returns True if enough pixels changed to consider it 'activity'.
    Always returns True on the first frame.
    """
    global _previous_frame

    # Downscale for fast comparison (160x90 is plenty)
    small = current_image.resize((160, 90))
    current_arr = np.array(small, dtype=np.float32)

    with _frame_lock:
        if _previous_frame is None:
            _previous_frame = current_arr
            log.debug("First frame — always significant")
            return True

        # Mean absolute difference normalized to 0-1
        diff = np.mean(np.abs(current_arr - _previous_frame)) / 255.0
        _previous_frame = current_arr

    changed = diff >= config.FRAME_DIFF_THRESHOLD
    log.debug(f"Frame diff: {diff:.4f} (threshold={config.FRAME_DIFF_THRESHOLD}) — {'CHANGED' if changed else 'unchanged'}")
    return changed
