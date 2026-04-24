"""
Central configuration for the AI Desktop Companion — April.
Now uses LOCAL llama3.2-vision via Ollama (no cloud API needed).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Ollama (Local LLM) ─────────────────────────────────────
# Two-Stage Pipeline:
#   Stage 1 (Perception): OLLAMA_MODEL (vision) — tiny prompt + screenshot → scene description
#   Stage 2 (Personality): OLLAMA_TEXT_MODEL (text-only) — scene + full personality → tsundere reaction
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2-vision:latest")        # Vision model (Stage 1)
OLLAMA_TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "llama3.2:3b")          # Fast text model (Stage 2)
OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU", "33"))  # Vision model layers — full GPU offload
OLLAMA_TEXT_NUM_GPU = -1                                  # Full GPU offload for 3B text model

# Inference settings — tuned for RTX 4060 Mobile (8GB VRAM)
OLLAMA_VISION_TEMPERATURE = 0.3       # conservative for scene analysis (Stage 1)
OLLAMA_REACT_TEMPERATURE = 0.75       # creative for tsundere dialogue (Stage 2)
OLLAMA_TIMEOUT = 120                  # local inference can be slower than cloud

# ─── Screen Capture ──────────────────────────────────────────
CAPTURE_INTERVAL = 5          # seconds between background context captures
REACT_INTERVAL = 5            # seconds between spoken reactions
MONITOR_INDEX = 1             # 1 = primary monitor (mss uses 1-indexed)

# ─── Frame Change Detection (Smart Skip) ─────────────────────
FRAME_DIFF_THRESHOLD = 0.03   # minimum % pixel change to consider "activity"
SKIP_UNCHANGED_FRAMES = True  # skip inference if frame barely changed

# ─── Image Downscale (Performance) ───────────────────────────
DOWNSCALE_WIDTH = 768         # resize screenshots before sending to model
DOWNSCALE_HEIGHT = 432        # (saves VRAM, faster inference — 768x432 balances quality vs speed)

# ─── VOICEVOX TTS (Fallback) ─────────────────────────────────
VOICEVOX_URL = "http://127.0.0.1:50021"
VOICEVOX_SPEAKER = 1          # speaker ID (0=四国めたん, 1=ずんだもん, etc.)

# ─── Kokoro TTS (Primary - Offline, Expressive) ──────────────
KOKORO_MODEL_PATH = "models/kokoro/kokoro-v0_19.onnx"
KOKORO_VOICES_PATH = "models/kokoro/voices-anime.bin.npz"
KOKORO_VOICE = "af_tsundere"            # CUSTOM community blend
KOKORO_SPEED = 1.15                     # faster for energetic tsundere vibe

# ─── Edge-TTS (Fallback) ─────────────────────────────────────
EDGE_TTS_VOICE = "en-US-AnaNeural"     # Cute, young female anime-style voice
EDGE_TTS_RATE = "+10%"                 # slightly faster and peppier
EDGE_TTS_PITCH = "+15Hz"               # pitch up slightly for a cuter tone

# ─── Character / Emotion ─────────────────────────────────────
AFFECTION_START = 0
AFFECTION_MAX = 10
AFFECTION_MIN = -10
ESCALATION_THRESHOLD = 3     # repeated roasts before max anger

# ─── Context Memory ──────────────────────────────────────────
MEMORY_WINDOW = 10            # number of recent events to remember

# ─── Sprite Overlay ──────────────────────────────────────────
SPRITE_ENABLED = True
SPRITE_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sprites", "NoranekoGames_Sabrina_BasePack", "Casual",
)
SPRITE_HEIGHT = 420           # display height in pixels (width auto-scaled)
SPRITE_MARGIN_RIGHT = 20      # pixels from right edge
SPRITE_MARGIN_BOTTOM = 20     # pixels from bottom edge
SPRITE_LIP_SYNC_MS = 150      # milliseconds between lip-sync frame toggles

# ─── Overlay (Subtitle + Sprite) ─────────────────────────────
OVERLAY_ENABLED = True
OVERLAY_FONT_SIZE = 18
OVERLAY_DISPLAY_SECONDS = 8
OVERLAY_BG_COLOR = "#1a1a2e"
OVERLAY_TEXT_COLOR = "#ff6b9d"
OVERLAY_NAME_COLOR = "#FFD700"  # gold for character name tab

# ─── QOL / UX ────────────────────────────────────────────────
POSITION_SAVE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "april_position.json",
)
TRAY_ENABLED = True               # system tray icon for show/hide/quit

# ─── RVC Voice Conversion ────────────────────────────────────
RVC_ENABLED = True
RVC_SIDECAR_URL = "http://127.0.0.1:5055"
RVC_PITCH_SHIFT = 18              # semitone pitch shift (0 for Ironmouse)
RVC_PITCH_SHIFT = 12              # Reduced to 12 (one octave) for a cuter, more "human" tone
RVC_INDEX_RATE = 0.45             # Lowered for a much smoother, flowing vocal quality
RVC_PROTECT = 0.5                 # Keeps the voice clear and breathy
RVC_RMS_MIX = 0.15                # Low value makes the volume flow more softly and cutely
RVC_FILTER_RADIUS = 3             # Standard filtering for natural pitch transitions
RVC_TIMEOUT = 30                  # seconds to wait for conversion

# ─── April 2.0: Proactive Features ───────────────────────────
ACTION_ANNOUNCE_DELAY = 5         # seconds April waits after announcing before acting
CANCEL_HOTKEY = "ctrl+shift+x"   # key combo to cancel a pending announced action

# ─── Pomodoro / Productivity ─────────────────────────────────
POMODORO_DEFAULT_WORK = 25        # default Pomodoro work duration (minutes)
POMODORO_DEFAULT_BREAK = 5        # default Pomodoro break duration (minutes)
BREAK_WARNING_MINUTES = 90        # coding duration before break warning
BREAK_DEMAND_MINUTES = 120        # coding duration before aggressive break demand
DISTRACTION_COOLDOWN = 30         # seconds before re-warning about distractions

# ─── Feature Flags ───────────────────────────────────────────
PROACTIVE_BUG_DETECTION = True    # April offers fixes when she sees errors
PROACTIVE_BREAK_REMINDERS = True  # April warns about long coding sessions
PROACTIVE_MEDIA_CONTROL = True    # April can pause/play media
