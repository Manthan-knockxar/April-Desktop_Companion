import time
import threading
from difflib import SequenceMatcher
from logger import Log

log = Log("SubOCR")

class SubtitleOCR:
    def __init__(self, context_memory):
        self.context_memory = context_memory
        self.stop_event = threading.Event()
        self.thread = None
        self.last_subtitle = ""

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        log.info("Starting Subtitle OCR daemon thread")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            log.info("Stopping Subtitle OCR daemon thread")
            self.stop_event.set()
            self.thread.join(timeout=2.0)

    def _run_loop(self):
        try:
            import mss
            from PIL import Image
            import numpy as np
            import title_ocr
            
            # Wait until phase 1 OCR is initialized if it wasn't already
            title_ocr.init_ocr()
            reader = title_ocr._reader
            if not reader:
                log.error("Cannot run subtitles: EasyOCR failed to load.")
                return

            with mss.mss() as sct:
                monitor = sct.monitors[1]  # primary monitor
                target_height = monitor["height"]
                crop_y = int(target_height * 0.85)  # Bottom 15%
                
                bbox = {
                    "top": monitor["top"] + crop_y,
                    "left": monitor["left"],
                    "width": monitor["width"],
                    "height": target_height - crop_y
                }

                while not self.stop_event.is_set():
                    # Check every 3 seconds
                    time.sleep(3.0)
                    if self.stop_event.is_set():
                        break

                    sct_img = sct.grab(bbox)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    img_np = np.array(img)
                    
                    results = reader.readtext(img_np, detail=0)
                    if not results:
                        continue
                        
                    new_text = " ".join(results).strip()
                    if not new_text or len(new_text) < 3:
                        continue
                        
                    # Calculate similarity > 80%
                    similarity = SequenceMatcher(None, self.last_subtitle.lower(), new_text.lower()).ratio()
                    if similarity < 0.80:
                        log.debug(f"Cap: {new_text} (sim={similarity:.2f})")
                        self.last_subtitle = new_text
                        self.context_memory.subtitle_buffer.append(new_text)
                        
        except Exception as e:
            log.error(f"Subtitle OCR daemon crashed: {e}")
            
if __name__ == "__main__":
    # Mock context memory
    class MockMem:
        def __init__(self):
            self.subtitle_buffer = []
    
    m = MockMem()
    ocr = SubtitleOCR(m)
    ocr.start()
    print("Capturing for 10 seconds. Play a video with subtitles...")
    time.sleep(10)
    ocr.stop()
    print("Buffer:", m.subtitle_buffer)
