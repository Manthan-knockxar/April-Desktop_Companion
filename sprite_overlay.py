"""
Sprite overlay — Tkinter-based always-on-top transparent window that
displays a visual novel-style anime sprite in the bottom-right corner
with dialogue text in a frosted glass text box.

VTuber mode: sprite is ALWAYS visible with neutral expression.
Dialogue text appears and fades, but the sprite never hides.

Transparency: Uses a unique chroma key color (#0D0E0F) instead of
white, so white pixels in the sprite (shirt, socks, hair) are preserved.

Features:
  - Emotion-driven sprite switching (Noraneko Sabrina pack)
  - Lip-sync animation (toggles mouth open/closed during speech)
  - VN-style dialogue box with character name tab
  - Always-visible idle sprite (VTuber corner mode)
"""
import os
import threading
import tkinter as tk
from PIL import Image as PILImage, ImageTk

import config
from emotion_mapper import get_sprite_expression, get_sprite_filename

# Chroma key — this EXACT color becomes transparent via Windows API.
# Must NOT appear anywhere in the sprite art.
# Using a dark near-black that won't be in the anime sprites.
CHROMA_KEY = "#0D0E0F"
CHROMA_KEY_RGB = (13, 14, 15)


class SpriteOverlay:
    """Always-on-top transparent overlay with anime sprite + dialogue box."""

    def __init__(self):
        self._thread = None
        self._root = None
        self._sprite_label = None
        self._text_label = None
        self._name_label = None
        self._text_frame = None
        self._running = False

        # State
        self._current_expression = "Open"
        self._current_emotion = "neutral"
        self._current_dialogue = ""
        self._is_talking = False
        self._lip_sync_job = None
        self._hide_text_job = None

        # Sprite image cache {expression: ImageTk.PhotoImage}
        self._sprite_cache: dict[str, ImageTk.PhotoImage] = {}
        self._sprite_pil_cache: dict[str, PILImage.Image] = {}

        # Pending updates from other threads
        self._pending_update = None

        # Chat input state
        self._chat_entry = None
        self._chat_button = None
        self._chat_visible = False
        self._pending_question = None  # user question for main.py to pick up
        self._question_callback = None  # called when a question is submitted

    def start(self):
        """Start the overlay in a background thread."""
        if not config.SPRITE_ENABLED and not config.OVERLAY_ENABLED:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()

    def _run_tk(self):
        """Tkinter main loop (runs in its own thread)."""
        self._root = tk.Tk()
        self._root.title("April — Desktop Companion")
        self._root.overrideredirect(True)          # No title bar
        self._root.attributes("-topmost", True)     # Always on top
        self._root.attributes("-alpha", 0.95)       # Slight transparency
        self._root.configure(bg=CHROMA_KEY)

        # Make chroma key pixels transparent (Windows-specific)
        # This color (#0D0E0F) won't appear in the anime sprite art
        self._root.wm_attributes("-transparentcolor", CHROMA_KEY)

        # Pre-load all sprites
        self._preload_sprites()

        # Calculate sprite dimensions from first loaded image
        first_sprite = next(iter(self._sprite_pil_cache.values()), None)
        if first_sprite:
            sprite_w, sprite_h = first_sprite.size
            aspect = sprite_w / sprite_h
            display_h = config.SPRITE_HEIGHT
            display_w = int(display_h * aspect)
        else:
            display_w = 240
            display_h = config.SPRITE_HEIGHT

        # ─── Store layout dimensions for dynamic resizing ────
        self._display_h = display_h
        self._screen_w = self._root.winfo_screenwidth()
        self._screen_h = self._root.winfo_screenheight()
        self._win_w = max(display_w + 20, 380)  # wide enough for text wrapping
        self._idle_text_h = 120                  # enough for name + text + chat button
        self._bottom_y = self._screen_h - config.SPRITE_MARGIN_BOTTOM - 50  # bottom edge (above taskbar)

        # Initial window size (sprite + small idle text box)
        win_h = display_h + self._idle_text_h + 10
        win_x = self._screen_w - self._win_w - config.SPRITE_MARGIN_RIGHT
        win_y = self._bottom_y - win_h
        self._win_x = win_x

        self._root.geometry(f"{self._win_w}x{win_h}+{win_x}+{win_y}")

        # ─── Sprite Display ──────────────────────────────────
        self._sprite_label = tk.Label(
            self._root,
            bg=CHROMA_KEY,
            borderwidth=0,
        )
        self._sprite_label.pack(side="top", anchor="e", padx=0)

        # ─── Dialogue Box (VN-style) ─────────────────────────
        self._text_frame = tk.Frame(
            self._root,
            bg=config.OVERLAY_BG_COLOR,
            highlightbackground="#ff6b9d",
            highlightthickness=2,
            padx=12,
            pady=8,
        )
        self._text_frame.pack(side="bottom", fill="x", padx=5, pady=(0, 5))

        # Character name tab
        self._name_label = tk.Label(
            self._text_frame,
            text="  ♡ April  ",
            font=("Segoe UI", 10, "bold"),
            fg=config.OVERLAY_NAME_COLOR,
            bg="#2a1a3e",
            padx=6,
            pady=2,
        )
        self._name_label.pack(anchor="w", pady=(0, 3))

        # Dialogue text — dynamic height, auto-wraps
        self._text_label = tk.Label(
            self._text_frame,
            text="( watching... )",
            font=("Segoe UI", 12),
            fg=config.OVERLAY_TEXT_COLOR,
            bg=config.OVERLAY_BG_COLOR,
            wraplength=self._win_w - 60,
            justify="left",
            anchor="nw",
        )
        self._text_label.pack(fill="both", expand=True)

        # ─── Chat Input (hidden by default) ──────────────────
        self._chat_row = tk.Frame(
            self._text_frame,
            bg=config.OVERLAY_BG_COLOR,
        )
        # Don't pack yet — shown on toggle

        self._chat_entry = tk.Entry(
            self._chat_row,
            font=("Segoe UI", 11),
            fg="#ffffff",
            bg="#2a2a4e",
            insertbackground="#ff6b9d",
            highlightbackground="#ff6b9d",
            highlightthickness=1,
            relief="flat",
        )
        self._chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self._chat_entry.bind("<Return>", self._on_chat_submit)

        self._chat_send_btn = tk.Label(
            self._chat_row,
            text=" ➜ ",
            font=("Segoe UI", 12, "bold"),
            fg="#ff6b9d",
            bg="#2a2a4e",
            cursor="hand2",
            padx=4,
        )
        self._chat_send_btn.pack(side="right")
        self._chat_send_btn.bind("<Button-1>", self._on_chat_submit)

        # Chat toggle button (always visible)
        self._chat_button = tk.Label(
            self._text_frame,
            text="  💬 Ask me something...  ",
            font=("Segoe UI", 9),
            fg="#888899",
            bg="#1f1f3a",
            cursor="hand2",
            padx=4,
            pady=2,
        )
        self._chat_button.pack(anchor="w", pady=(4, 0))
        self._chat_button.bind("<Button-1>", self._toggle_chat)

        # Set initial sprite to neutral — ALWAYS VISIBLE from the start
        self._set_sprite("Open")

        # Show immediately — VTuber mode, always visible
        self._root.deiconify()

        # Poll for updates from other threads
        self._root.after(80, self._check_pending)
        self._root.mainloop()

    def _preload_sprites(self):
        """Load and resize all sprite PNGs into cache with proper chroma key compositing."""
        if not os.path.isdir(config.SPRITE_BASE_DIR):
            print(f"[Sprite] ✗ Sprite directory not found: {config.SPRITE_BASE_DIR}")
            return

        loaded = 0
        for fname in os.listdir(config.SPRITE_BASE_DIR):
            if not fname.endswith(".png"):
                continue
            path = os.path.join(config.SPRITE_BASE_DIR, fname)
            try:
                img = PILImage.open(path).convert("RGBA")

                # Resize to target height, preserve aspect
                aspect = img.width / img.height
                new_h = config.SPRITE_HEIGHT
                new_w = int(new_h * aspect)
                img = img.resize((new_w, new_h), PILImage.LANCZOS)

                # Composite onto chroma key background (NOT white!)
                # This preserves white pixels in the sprite (shirt, socks, etc.)
                bg = PILImage.new("RGBA", img.size, (*CHROMA_KEY_RGB, 255))
                composite = PILImage.alpha_composite(bg, img)

                # Extract expression key from filename
                # e.g., "Sabby_Casual_Frown_Blush.png" → "Frown_Blush"
                key = fname.replace("Sabby_Casual_", "").replace(".png", "")
                self._sprite_pil_cache[key] = composite
                loaded += 1

            except Exception as e:
                print(f"[Sprite] ✗ Failed to load {fname}: {e}")

        print(f"[Sprite] ✓ Loaded {loaded} sprite expressions")

    def _get_photo(self, expression: str) -> ImageTk.PhotoImage | None:
        """Get or create PhotoImage for an expression (must be called from Tk thread)."""
        if expression in self._sprite_cache:
            return self._sprite_cache[expression]

        pil_img = self._sprite_pil_cache.get(expression)
        if pil_img is None:
            # Fallback: try without blush, then default to "Open"
            base = expression.replace("_Blush", "")
            pil_img = self._sprite_pil_cache.get(base)
            if pil_img is None:
                pil_img = self._sprite_pil_cache.get("Open")
            if pil_img is None:
                return None

        photo = ImageTk.PhotoImage(pil_img)
        self._sprite_cache[expression] = photo
        return photo

    def _set_sprite(self, expression: str):
        """Update the displayed sprite expression."""
        if not self._sprite_label:
            return
        photo = self._get_photo(expression)
        if photo:
            self._sprite_label.configure(image=photo)
            self._sprite_label.image = photo  # prevent garbage collection
            self._current_expression = expression

    def _check_pending(self):
        """Poll for pending updates from other threads."""
        if self._pending_update is not None:
            update = self._pending_update
            self._pending_update = None
            self._apply_update(update)

        if self._running and self._root:
            self._root.after(80, self._check_pending)

    def _apply_update(self, update: dict):
        """Apply a pending update on the Tk thread."""
        emotion = update.get("emotion", "neutral")
        dialogue = update.get("dialogue", "")
        action_type = update.get("action_type", "commentary")
        emotional_intensity = update.get("emotional_intensity", "DEFAULT_TSUNDERE")

        self._current_emotion = emotion
        self._current_dialogue = dialogue

        # Cancel any pending text hide
        if self._hide_text_job:
            self._root.after_cancel(self._hide_text_job)
            self._hide_text_job = None

        # Cancel ongoing lip sync
        if self._lip_sync_job:
            self._root.after_cancel(self._lip_sync_job)
            self._lip_sync_job = None

        # Determine idle expression
        idle_expr = get_sprite_expression(
            emotion=emotion,
            action_type=action_type,
            emotional_intensity=emotional_intensity,
            dialogue=dialogue,
            is_talking=False,
        )

        # Set idle sprite first
        self._set_sprite(idle_expr)

        # Update dialogue text and resize window to fit
        if self._text_label and dialogue:
            self._text_label.config(text=dialogue)
            self._resize_for_text()

        # Start lip sync animation
        self._is_talking = True
        self._start_lip_sync(emotion, action_type, emotional_intensity, dialogue)

        # Schedule text clear (but sprite stays visible with neutral expression)
        self._hide_text_job = self._root.after(
            config.OVERLAY_DISPLAY_SECONDS * 1000,
            self._on_text_timeout,
        )

    def _resize_for_text(self):
        """Dynamically resize the window to fit the current dialogue text.
        Grows upward so the bottom edge stays pinned to the taskbar."""
        if not self._root or not self._text_frame:
            return
        # Let Tk recalculate widget sizes
        self._root.update_idletasks()
        # Measure how tall the text frame actually needs to be
        text_h = self._text_frame.winfo_reqheight()
        # Total window height: sprite + text + padding
        new_win_h = self._display_h + text_h + 20
        # Keep bottom edge pinned, grow upward
        new_y = self._bottom_y - new_win_h
        self._root.geometry(f"{self._win_w}x{new_win_h}+{self._win_x}+{new_y}")

    def _resize_for_idle(self):
        """Shrink window back to compact idle size when dialogue clears."""
        if not self._root:
            return
        self._root.update_idletasks()
        text_h = self._text_frame.winfo_reqheight()
        new_win_h = self._display_h + text_h + 20
        new_y = self._bottom_y - new_win_h
        self._root.geometry(f"{self._win_w}x{new_win_h}+{self._win_x}+{new_y}")

    def _start_lip_sync(self, emotion: str, action_type: str,
                        emotional_intensity: str, dialogue: str):
        """Animate lip sync by toggling between idle/talking expressions."""
        if not self._is_talking or not self._root:
            return

        # Calculate how many toggles based on dialogue length
        # ~6 chars per toggle at 150ms each
        total_toggles = max(4, len(dialogue) // 6)
        self._lip_sync_count = 0
        self._lip_sync_total = total_toggles
        self._lip_sync_params = (emotion, action_type, emotional_intensity, dialogue)

        self._do_lip_sync_frame()

    def _do_lip_sync_frame(self):
        """Single frame of lip sync animation."""
        if not self._is_talking or not self._root:
            return

        if self._lip_sync_count >= self._lip_sync_total:
            # Done talking — settle on idle expression
            self._is_talking = False
            emotion, action_type, emotional_intensity, dialogue = self._lip_sync_params
            idle_expr = get_sprite_expression(
                emotion=emotion,
                action_type=action_type,
                emotional_intensity=emotional_intensity,
                dialogue=dialogue,
                is_talking=False,
            )
            self._set_sprite(idle_expr)
            return

        # Toggle between talking and idle
        emotion, action_type, emotional_intensity, dialogue = self._lip_sync_params
        is_mouth_open = (self._lip_sync_count % 2 == 0)

        expr = get_sprite_expression(
            emotion=emotion,
            action_type=action_type,
            emotional_intensity=emotional_intensity,
            dialogue=dialogue,
            is_talking=is_mouth_open,
        )
        self._set_sprite(expr)

        self._lip_sync_count += 1
        self._lip_sync_job = self._root.after(
            config.SPRITE_LIP_SYNC_MS,
            self._do_lip_sync_frame,
        )

    def _on_text_timeout(self):
        """Called when dialogue display time expires. Clears text, shrinks window, resets sprite."""
        self._is_talking = False
        if self._lip_sync_job:
            self._root.after_cancel(self._lip_sync_job)
            self._lip_sync_job = None

        # Clear the dialogue text and shrink window back
        if self._text_label:
            self._text_label.config(text="( watching... )")
            self._resize_for_idle()

        # Reset sprite to neutral idle expression — sprite stays visible!
        self._set_sprite("Open")

    # ─── Public API (thread-safe) ─────────────────────────────

    def show(self, dialogue: str, emotion: str = "neutral",
             action_type: str = "commentary",
             emotional_intensity: str = "DEFAULT_TSUNDERE"):
        """
        Thread-safe method to display a reaction with the correct sprite.
        Called from the main loop or TTS worker thread.
        """
        if not self._running:
            return
        self._pending_update = {
            "emotion": emotion,
            "dialogue": dialogue,
            "action_type": action_type,
            "emotional_intensity": emotional_intensity,
        }

    def get_pending_question(self) -> str | None:
        """Thread-safe: retrieve and clear any pending user question."""
        q = self._pending_question
        if q is not None:
            self._pending_question = None
        return q

    def set_question_callback(self, callback):
        """Set a callback to be invoked when a user question is submitted."""
        self._question_callback = callback

    def stop(self):
        """Stop the overlay."""
        self._running = False
        self._is_talking = False
        if self._root:
            try:
                self._root.quit()
            except Exception:
                pass

    # ─── Chat Input Handlers ─────────────────────────────────

    def _toggle_chat(self, event=None):
        """Show/hide the chat input field."""
        if self._chat_visible:
            self._chat_row.pack_forget()
            self._chat_visible = False
            self._chat_button.config(text="  💬 Ask me something...  ")
        else:
            self._chat_row.pack(fill="x", pady=(4, 0))
            self._chat_visible = True
            self._chat_entry.focus_set()
            self._chat_button.config(text="  ✖ Close  ")
        self._resize_for_text()

    def _on_chat_submit(self, event=None):
        """Handle Enter key or send button click."""
        if not self._chat_entry:
            return
        question = self._chat_entry.get().strip()
        if not question:
            return

        # Store question for main.py to pick up
        self._pending_question = question
        
        # Signal the main loop to wake up and process the question
        if self._question_callback:
            self._question_callback()

        # Clear entry and hide chat
        self._chat_entry.delete(0, tk.END)
        self._chat_row.pack_forget()
        self._chat_visible = False
        self._chat_button.config(text="  💬 Ask me something...  ")

        # Show "thinking" feedback
        if self._text_label:
            self._text_label.config(text="Hmm, let me look... 💭")
            self._resize_for_text()
