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
  - Drag-to-reposition (sprite + chatbox move together)
  - Position persistence across sessions
  - Right-click context menu (reset pos, mute, click-through, quit)
  - Double-click sprite to toggle chat
  - System tray icon (show/hide, mute, quit)
  - Click-through mode (transparent to mouse events)
"""
import json
import os
import threading
import ctypes
import tkinter as tk
from PIL import Image as PILImage, ImageTk, ImageDraw

import config
from emotion_mapper import get_sprite_expression, get_sprite_filename

# Chroma key — this EXACT color becomes transparent via Windows API.
# Must NOT appear anywhere in the sprite art.
# Using a dark near-black that won't be in the anime sprites.
CHROMA_KEY = "#0D0E0F"
CHROMA_KEY_RGB = (13, 14, 15)

# Windows API constants for click-through
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


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

        # ─── QOL state ────────────────────────────────────────
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._user_positioned = False   # True once user drags, disables auto-pin
        self._user_x = 0               # user-chosen window X
        self._user_y = 0               # user-chosen window Y
        self._click_through = False     # click-through mode
        self._muted = False             # mute TTS audio
        self._hidden = False            # hidden via tray
        self._tray_icon = None          # pystray icon
        self._context_menu = None       # right-click popup
        self._mute_callback = None      # callback to notify main of mute state

    # ══════════════════════════════════════════════════════════
    #  Startup
    # ══════════════════════════════════════════════════════════

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

        # Load saved position or default to bottom-right
        saved = self._load_position()
        win_h = display_h + self._idle_text_h + 10

        if saved:
            win_x, win_y = saved
            # Validate: make sure at least part of the window is on-screen
            if (-self._win_w < win_x < self._screen_w and
                    -win_h < win_y < self._screen_h):
                self._user_positioned = True
                self._user_x = win_x
                self._user_y = win_y
            else:
                # Saved position is off-screen, fall back to default
                win_x = self._screen_w - self._win_w - config.SPRITE_MARGIN_RIGHT
                win_y = self._bottom_y - win_h
        else:
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

        # ─── Bind Drag Events ─────────────────────────────────
        self._sprite_label.bind("<ButtonPress-1>", self._on_drag_start)
        self._sprite_label.bind("<B1-Motion>", self._on_drag_motion)
        self._sprite_label.bind("<ButtonRelease-1>", self._on_drag_end)

        # Also allow dragging from the text frame / name label
        for widget in (self._text_frame, self._name_label):
            widget.bind("<ButtonPress-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_end)

        # ─── Double-click sprite to toggle chat ──────────────
        self._sprite_label.bind("<Double-Button-1>", self._toggle_chat)

        # ─── Right-click context menu ─────────────────────────
        self._build_context_menu()
        self._sprite_label.bind("<Button-3>", self._show_context_menu)
        self._text_frame.bind("<Button-3>", self._show_context_menu)
        self._name_label.bind("<Button-3>", self._show_context_menu)
        self._text_label.bind("<Button-3>", self._show_context_menu)

        # Set initial sprite to neutral — ALWAYS VISIBLE from the start
        self._set_sprite("Open")

        # Show immediately — VTuber mode, always visible
        self._root.deiconify()

        # Start system tray icon
        if config.TRAY_ENABLED:
            tray_thread = threading.Thread(target=self._start_tray, daemon=True)
            tray_thread.start()

        # Poll for updates from other threads
        self._root.after(80, self._check_pending)
        self._root.mainloop()

    # ══════════════════════════════════════════════════════════
    #  Drag-to-Reposition
    # ══════════════════════════════════════════════════════════

    def _on_drag_start(self, event):
        """Record the mouse offset within the window for drag calculation."""
        self._drag_start_x = event.x_root - self._root.winfo_x()
        self._drag_start_y = event.y_root - self._root.winfo_y()

    def _on_drag_motion(self, event):
        """Move the window to follow the mouse in real-time."""
        new_x = event.x_root - self._drag_start_x
        new_y = event.y_root - self._drag_start_y
        self._root.geometry(f"+{new_x}+{new_y}")

    def _on_drag_end(self, event):
        """Finalize position after drag, save it, and switch to user-positioned mode."""
        self._user_x = self._root.winfo_x()
        self._user_y = self._root.winfo_y()
        self._user_positioned = True
        self._win_x = self._user_x
        self._save_position()

    # ══════════════════════════════════════════════════════════
    #  Position Persistence
    # ══════════════════════════════════════════════════════════

    def _save_position(self):
        """Save current window position to a JSON file."""
        try:
            data = {"x": self._user_x, "y": self._user_y}
            with open(config.POSITION_SAVE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[Sprite] ✗ Failed to save position: {e}")

    def _load_position(self) -> tuple[int, int] | None:
        """Load saved window position from JSON. Returns (x, y) or None."""
        try:
            if os.path.exists(config.POSITION_SAVE_FILE):
                with open(config.POSITION_SAVE_FILE, "r") as f:
                    data = json.load(f)
                return (int(data["x"]), int(data["y"]))
        except Exception as e:
            print(f"[Sprite] ✗ Failed to load position: {e}")
        return None

    # ══════════════════════════════════════════════════════════
    #  Right-Click Context Menu
    # ══════════════════════════════════════════════════════════

    def _build_context_menu(self):
        """Create the right-click popup menu."""
        self._context_menu = tk.Menu(
            self._root,
            tearoff=0,
            bg="#1a1a2e",
            fg="#ff6b9d",
            activebackground="#2a1a3e",
            activeforeground="#FFD700",
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=1,
        )
        self._context_menu.add_command(
            label="💬  Ask me something...",
            command=lambda: self._toggle_chat(),
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="📌  Reset Position",
            command=self._reset_position,
        )
        self._context_menu.add_command(
            label="🔇  Mute Voice",
            command=self._toggle_mute,
        )
        self._context_menu.add_command(
            label="👻  Click-Through Mode",
            command=self._toggle_click_through,
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="🙈  Hide (use tray to show)",
            command=self._hide_window,
        )
        self._context_menu.add_command(
            label="❌  Quit April",
            command=self._quit_app,
        )

    def _show_context_menu(self, event):
        """Display the context menu at the mouse position."""
        if self._context_menu:
            # Update dynamic labels before showing
            mute_label = "🔊  Unmute Voice" if self._muted else "🔇  Mute Voice"
            self._context_menu.entryconfigure(3, label=mute_label)

            ct_label = "🖱️  Disable Click-Through" if self._click_through else "👻  Click-Through Mode"
            self._context_menu.entryconfigure(4, label=ct_label)

            try:
                self._context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._context_menu.grab_release()

    # ══════════════════════════════════════════════════════════
    #  Context Menu Actions
    # ══════════════════════════════════════════════════════════

    def _reset_position(self):
        """Snap back to default bottom-right position."""
        self._user_positioned = False
        win_h = self._root.winfo_height()
        win_x = self._screen_w - self._win_w - config.SPRITE_MARGIN_RIGHT
        win_y = self._bottom_y - win_h
        self._win_x = win_x
        self._user_x = win_x
        self._user_y = win_y
        self._root.geometry(f"{self._win_w}x{win_h}+{win_x}+{win_y}")
        # Delete saved position file
        try:
            if os.path.exists(config.POSITION_SAVE_FILE):
                os.remove(config.POSITION_SAVE_FILE)
        except Exception:
            pass

    def _toggle_mute(self):
        """Toggle TTS mute state."""
        self._muted = not self._muted
        state = "muted 🔇" if self._muted else "unmuted 🔊"
        print(f"[Sprite] Voice {state}")
        if self._mute_callback:
            self._mute_callback(self._muted)

    def _toggle_click_through(self):
        """Toggle click-through mode using Windows API."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            if self._click_through:
                # Disable click-through: remove WS_EX_TRANSPARENT
                new_style = current_style & ~WS_EX_TRANSPARENT
                self._click_through = False
                print("[Sprite] Click-through OFF — window is interactive")
            else:
                # Enable click-through: add WS_EX_TRANSPARENT
                new_style = current_style | WS_EX_TRANSPARENT | WS_EX_LAYERED
                self._click_through = True
                print("[Sprite] Click-through ON — clicks pass through April")
                # Auto-disable after 30 seconds so user doesn't get stuck
                self._root.after(30000, self._auto_disable_click_through)

            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        except Exception as e:
            print(f"[Sprite] ✗ Click-through toggle failed: {e}")

    def _auto_disable_click_through(self):
        """Safety: auto-disable click-through after timeout."""
        if self._click_through:
            self._toggle_click_through()
            if self._text_label:
                self._text_label.config(text="( click-through expired — I'm back! ♡ )")

    def _hide_window(self):
        """Hide the overlay window (can restore from tray)."""
        if self._root:
            self._root.withdraw()
            self._hidden = True

    def _unhide_window(self):
        """Show the overlay window again."""
        if self._root:
            self._root.deiconify()
            self._root.attributes("-topmost", True)
            self._hidden = False

    def _quit_app(self):
        """Clean shutdown from context menu."""
        self._save_position()
        self.stop()
        os._exit(0)

    # ══════════════════════════════════════════════════════════
    #  System Tray Icon
    # ══════════════════════════════════════════════════════════

    def _create_tray_image(self):
        """Generate a small pink heart icon for the system tray."""
        size = 64
        img = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw a pink filled circle as base
        draw.ellipse([4, 4, size - 4, size - 4], fill=(255, 107, 157, 255))

        # Draw "A" in the center
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("segoeui.ttf", 32)
        except Exception:
            font = ImageFont.load_default()

        # Center the text
        bbox = draw.textbbox((0, 0), "A", font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) // 2
        ty = (size - th) // 2 - 2
        draw.text((tx, ty), "A", fill=(255, 255, 255, 255), font=font)

        return img

    def _start_tray(self):
        """Start pystray system tray icon in its own thread."""
        try:
            import pystray
            from pystray import MenuItem, Menu

            icon_image = self._create_tray_image()

            def on_show_hide(icon, item):
                if self._hidden:
                    self._root.after(0, self._unhide_window)
                else:
                    self._root.after(0, self._hide_window)

            def on_mute(icon, item):
                self._root.after(0, self._toggle_mute)

            def on_reset(icon, item):
                self._root.after(0, self._reset_position)

            def on_quit(icon, item):
                self._save_position()
                icon.stop()
                self._root.after(0, self._quit_app)

            def is_muted(item):
                return self._muted

            menu = Menu(
                MenuItem(
                    "Show / Hide April",
                    on_show_hide,
                    default=True,  # double-click tray icon = show/hide
                ),
                Menu.SEPARATOR,
                MenuItem("Mute Voice", on_mute, checked=is_muted),
                MenuItem("Reset Position", on_reset),
                Menu.SEPARATOR,
                MenuItem("Quit April", on_quit),
            )

            self._tray_icon = pystray.Icon(
                "april_companion",
                icon_image,
                "April — Desktop Companion ♡",
                menu,
            )
            self._tray_icon.run()

        except ImportError:
            print("[Sprite] ⚠ pystray not installed — tray icon disabled")
            print("[Sprite]   Install with: pip install pystray")
        except Exception as e:
            print(f"[Sprite] ✗ Tray icon failed: {e}")

    # ══════════════════════════════════════════════════════════
    #  Sprite Loading & Display
    # ══════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════
    #  Update Polling & Application
    # ══════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════
    #  Window Resizing (respects user-chosen position)
    # ══════════════════════════════════════════════════════════

    def _resize_for_text(self):
        """Dynamically resize the window to fit the current dialogue text.
        If user-positioned: grows downward from the user's chosen spot.
        If default: grows upward so the bottom edge stays pinned to the taskbar."""
        if not self._root or not self._text_frame:
            return
        # Let Tk recalculate widget sizes
        self._root.update_idletasks()
        # Measure how tall the text frame actually needs to be
        text_h = self._text_frame.winfo_reqheight()
        # Total window height: sprite + text + padding
        new_win_h = self._display_h + text_h + 20

        if self._user_positioned:
            # User has placed April somewhere — keep top-left pinned, grow downward
            self._root.geometry(f"{self._win_w}x{new_win_h}+{self._user_x}+{self._user_y}")
        else:
            # Default mode — bottom-right pinned, grow upward
            new_y = self._bottom_y - new_win_h
            self._root.geometry(f"{self._win_w}x{new_win_h}+{self._win_x}+{new_y}")

    def _resize_for_idle(self):
        """Shrink window back to compact idle size when dialogue clears."""
        if not self._root:
            return
        self._root.update_idletasks()
        text_h = self._text_frame.winfo_reqheight()
        new_win_h = self._display_h + text_h + 20

        if self._user_positioned:
            self._root.geometry(f"{self._win_w}x{new_win_h}+{self._user_x}+{self._user_y}")
        else:
            new_y = self._bottom_y - new_win_h
            self._root.geometry(f"{self._win_w}x{new_win_h}+{self._win_x}+{new_y}")

    # ══════════════════════════════════════════════════════════
    #  Lip Sync Animation
    # ══════════════════════════════════════════════════════════

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

    @property
    def muted(self) -> bool:
        """Thread-safe: check if TTS is muted."""
        return self._muted

    def get_pending_question(self) -> str | None:
        """Thread-safe: retrieve and clear any pending user question."""
        q = self._pending_question
        if q is not None:
            self._pending_question = None
        return q

    def set_question_callback(self, callback):
        """Set a callback to be invoked when a user question is submitted."""
        self._question_callback = callback

    def set_mute_callback(self, callback):
        """Set a callback to be invoked when mute state changes."""
        self._mute_callback = callback

    def stop(self):
        """Stop the overlay."""
        self._running = False
        self._is_talking = False
        self._save_position()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
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
