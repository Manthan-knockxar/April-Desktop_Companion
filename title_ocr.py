import re
import time
import win32gui
import win32process
import psutil
from logger import Log

log = Log("TitleOCR")

_reader = None

def init_ocr():
    """Initialize the EasyOCR reader singleton on CPU to save VRAM."""
    global _reader
    if _reader is not None:
        return
        
    try:
        import easyocr
        log.info("Initializing EasyOCR reader (CPU) — this may take a few seconds...")
        _reader = easyocr.Reader(['en'], gpu=False)
        log.success("EasyOCR reader initialized")
    except ImportError:
        log.error("easyocr not installed. OCR fallback will be disabled.")
    except Exception as e:
        log.error(f"Failed to initialize EasyOCR: {e}")

def get_active_window_info_win32() -> tuple[str, str]:
    """Primary fast-path using OS APIs to get title and process name."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd).strip()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                exe = psutil.Process(pid).name()
            except Exception:
                exe = "unknown.exe"
            return title, exe
        return "", "unknown.exe"
    except Exception:
        return "", "unknown.exe"

def get_active_window_title_ocr_fallback() -> str:
    """
    Fallback method: screenshots the top 40px of the screen and runs CPU OCR.
    Useful for games or media players that blank their window titles.
    """
    global _reader
    if _reader is None:
        return ""
        
    try:
        from PIL import Image
        import mss

        with mss.mss() as sct:
            # Grab the primary monitor
            monitor = sct.monitors[1]  # 0 is "all monitors", 1 is primary
            
            # Crop to the top 40 pixels (title bar region)
            title_box = {
                "top": monitor["top"],
                "left": monitor["left"],
                "width": monitor["width"],
                "height": min(40, monitor["height"])
            }
            
            # Capture and convert to PIL format
            sct_img = sct.grab(title_box)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            # Run OCR
            import numpy as np
            img_np = np.array(img)
            results = _reader.readtext(img_np, detail=0)  # detail=0 returns just the string list
            
            if not results:
                return ""
                
            # Join and clean whitespace
            raw_text = " ".join(results)
            clean_text = re.sub(r'\s+', ' ', raw_text).strip()
            return clean_text
            
    except Exception as e:
        log.error(f"OCR fallback failed silently: {e}")
        return ""


def ocr_screen_text(image) -> str:
    """
    Run OCR on a full screenshot (PIL Image) to extract visible text.
    
    This is used to augment the vision model's scene description with
    ACTUAL text from the screen, so April reacts to what's really written.
    
    Returns up to 300 chars of cleaned text, or empty string on failure.
    """
    global _reader
    if _reader is None:
        return ""
    
    try:
        import numpy as np
        img_np = np.array(image)
        
        # Run OCR — detail=0 returns just strings
        results = _reader.readtext(img_np, detail=0, paragraph=True)
        
        if not results:
            return ""
        
        # Join and clean
        raw_text = " ".join(results)
        clean_text = re.sub(r'\s+', ' ', raw_text).strip()
        
        # Cap at 300 chars to keep prompt lean
        if len(clean_text) > 300:
            clean_text = clean_text[:300] + "..."
        
        return clean_text
        
    except Exception as e:
        log.error(f"Screen OCR failed: {e}")
        return ""

if __name__ == "__main__":
    print("Testing Win32 Title Fetch (Fast Path)...")
    win32_title, win32_exe = get_active_window_info_win32()
    print(f"Result: '{win32_title}' ({win32_exe})")
    
    print("\nInitializing OCR...")
    init_ocr()
    
    print("\nTesting OCR Title Fetch (Fallback)...")
    start = time.perf_counter()
    ocr_title = get_active_window_title_ocr_fallback()
    elapsed = time.perf_counter() - start
    print(f"Result: '{ocr_title}' (took {elapsed:.2f}s)")
    
    print("\nDone. Switch windows and re-run to test different contexts.")
