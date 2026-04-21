"""
Screen capture module using mss.
Captures the primary monitor and returns a PIL Image.
Includes frame-difference detection for smart skipping.

Thread safety: each thread gets its own mss instance via threading.local(),
since mss uses thread-local Win32 DC handles internally.
"""
import threading

import json
import os
import numpy as np
from mss import mss
from PIL import Image, ImageDraw
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
    
    # Mask out April's own UI elements so she doesn't perceive herself
    img = _mask_companion_ui(img)
    
    log.debug(f"Captured {img.width}x{img.height} from monitor {config.MONITOR_INDEX}")
    return img


def _mask_companion_ui(img: Image.Image) -> Image.Image:
    """Read April's current window position and black out that area."""
    draw = ImageDraw.Draw(img)
    masked = False
    
    try:
        if os.path.exists(config.POSITION_SAVE_FILE):
            with open(config.POSITION_SAVE_FILE, "r") as f:
                pos = json.load(f)
            
            # Simple rectangle mask for the whole companion window
            # (x, y) are screen-relative; mss capture is also screen-relative for the monitor
            x, y = pos.get("x", 0), pos.get("y", 0)
            w, h = pos.get("w", 400), pos.get("h", 600)
            
            # Convert screen coords to relative monitor coords if needed
            # For primary monitor(index 1), mss usually matches screen coords
            # We'll use a safety margin
            draw.rectangle([x-5, y-5, x+w+5, y+h+5], fill="black")
            masked = True
    except Exception as e:
        log.warn(f"Failed to mask based on position file: {e}")

    # Fallback/Safety: Mask the bottom-right corner where she usually lives
    if not masked:
        w, h = img.size
        # Mask a generic 450x700 area in the bottom right
        draw.rectangle([w-500, h-750, w, h], fill="black")

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
