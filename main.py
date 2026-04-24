"""
🎮 AI Desktop Companion — Tsundere Anime Reactor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Main entry point. Orchestrates the capture → react loop.
Captures every CAPTURE_INTERVAL for context, speaks every REACT_INTERVAL.
TTS synthesis runs in a background thread for pipelined performance.
Visual novel sprite overlay displays emotion-driven expressions.

Runs 100% locally using llama3.2-vision via Ollama — no cloud API needed!

Usage:
    python main.py

Make sure Ollama is running with `llama3.2-vision` pulled!
"""
import sys
import os
import time
import threading
import ssl
import ctypes
import subprocess
import atexit

# ─── Force UTF-8 Console (Fixes cp1252 emoji crashes) ────────
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── DPI Awareness (Fixes positioning on scaled displays) ────
# Must be called BEFORE any GUI/Tk code runs.
# Without this, Tkinter sees 1536x864 instead of real 1920x1080
# on a 125%-scaled display, and the sprite lands in the wrong spot.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()    # Fallback (system DPI)
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────

# ─── SSL Workaround (Fixes CERTIFICATE_VERIFY_FAILED) ────────
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context  # type: ignore
# ─────────────────────────────────────────────────────────────

import config
import title_ocr
import context_resolver
from subtitle_ocr import SubtitleOCR
from context_memory import ContextMemory, get_time_context
from scene_reactor import analyze_and_react, answer_user_question, summarize_current_page
from tts_engine import init_tts, synthesize
from audio_player import play_audio, play_audio_blocking, stop as stop_audio
from sprite_overlay import SpriteOverlay
from system_info import get_system_context, get_enriched_context
from screen_capture import capture_screen, has_significant_change
from schedule_manager import ScheduleManager
import system_actions
from logger import Log, PINK, CYAN, YELLOW, DIM, BOLD, RESET

# Module loggers
log_main = Log("Main")
log_tts = Log("TTS")
log_ctx = Log("Context")


# ─── Pretty Console Output ───────────────────────────────────

BANNER = f"""
{PINK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
{PINK}  🎮  April — Desktop Companion  🎭{RESET}
{PINK}  ♡  "I-it's not like I want to watch you..."  ♡{RESET}
{PINK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
"""


# ─── Background TTS Worker ───────────────────────────────────

_overlay_ref: SpriteOverlay | None = None  # set in main() for mute checks

def _tts_worker(dialogue: str, action_type: str, overlay_ref=None):
    """
    Synthesize and play audio in a background thread.
    Subtitle sync: dialogue text appears ONLY when audio starts playing,
    and clears when audio finishes. No more early subtitle flash.
    """
    try:
        # Check mute state before synthesizing
        if _overlay_ref and _overlay_ref.muted:
            log_tts.info("Voice muted — showing subtitle as text-only")
            # Muted: show text immediately, hold for a reasonable time, then clear
            ov = overlay_ref or _overlay_ref
            if ov:
                ov.start_dialogue()
                import time as _time
                _time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                ov.end_dialogue()
            return

        log_tts.info(f"Synthesizing: \"{dialogue[:60]}...\"")
        with log_tts.timed("TTS synthesis"):
            audio_bytes, audio_format = synthesize(dialogue, action_type=action_type)
        if audio_bytes:
            # Re-check mute in case it changed during synthesis
            if _overlay_ref and _overlay_ref.muted:
                log_tts.info("Voice muted during synthesis — showing text-only")
                ov = overlay_ref or _overlay_ref
                if ov:
                    ov.start_dialogue()
                    import time as _time
                    _time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                    ov.end_dialogue()
                return
            size_kb = len(audio_bytes) / 1024
            log_tts.success(f"Got {audio_format.upper()} audio — {size_kb:.1f}KB")

            # ── Subtitle Sync: show text RIGHT BEFORE audio plays ──
            ov = overlay_ref or _overlay_ref
            if ov:
                ov.start_dialogue()

            log_tts.info("Playing audio...")
            with log_tts.timed("Audio playback"):
                play_audio_blocking(audio_bytes, audio_format)
            log_tts.success("Playback finished")

            # ── Subtitle Sync: clear text AFTER audio ends ──
            if ov:
                ov.end_dialogue()
        else:
            log_tts.error("Synthesis returned no audio — showing text-only")
            # Fallback: show subtitle anyway since there's no audio
            ov = overlay_ref or _overlay_ref
            if ov:
                ov.start_dialogue()
                import time as _time
                _time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                ov.end_dialogue()
    except Exception as e:
        log_tts.error("TTS worker crashed", exc=e)
        # Safety: try to clear any stuck subtitle
        try:
            ov = overlay_ref or _overlay_ref
            if ov:
                ov.end_dialogue()
        except Exception:
            pass


# ─── Background Context Worker (Silent) ──────────────────────

_accumulated_scenes: list[str] = []
_accumulated_lock = threading.Lock()
_question_event = threading.Event()  # signaled when a user question arrives
_scheduler: ScheduleManager | None = None  # global scheduler reference

def _context_worker():
    """
    Runs continuously in the background.
    Captures screen and tracks frame changes for the main loop.
    
    NOTE: Does NOT call the vision model anymore. In the two-stage pipeline,
    Stage 1 in the main loop already does vision. Running vision here too
    caused lock contention that doubled response times.
    """
    cycle_count = 0
    while True:
        cycle_count += 1
        try:
            with log_ctx.timed("Screen capture"):
                image = capture_screen()
            
            # Smart skip — check for visual change
            if config.SKIP_UNCHANGED_FRAMES and not has_significant_change(image):
                log_ctx.debug(f"Frame unchanged — skipping (cycle #{cycle_count})")
                time.sleep(config.CAPTURE_INTERVAL)
                continue
                
            log_ctx.debug(f"Frame changed (cycle #{cycle_count})")
                
        except Exception as e:
            log_ctx.error("Context worker error", exc=e)
            
        time.sleep(config.CAPTURE_INTERVAL)


# ─── Scene Similarity Check ──────────────────────────────────

def _scene_is_similar(scene: str, previous_scene: str, threshold: float = 0.5) -> bool:
    """Check if two scene descriptions share too many keywords."""
    if not previous_scene:
        return False

    ignore_words = {
        "the", "a", "an", "is", "are", "and", "with", "in", "on", "at",
        "their", "they", "user", "full", "visible", "nearby",
        "no", "not", "appears", "to", "be", "of", "has", "have",
        "several", "some", "there", "currently", "screen", "window",
        # Content-type words that inflate similarity when watching same content
        "watching", "anime", "video", "playing", "browsing", "streaming",
        "computer", "person", "show", "episode", "game", "called",
        "youtube", "chrome", "website", "browser",
    }

    def extract_keys(text):
        words = set()
        for word in text.lower().replace(",", "").replace(".", "").split():
            if word not in ignore_words and len(word) > 2:
                words.add(word)
        return words

    keys_a = extract_keys(scene)
    keys_b = extract_keys(previous_scene)

    if not keys_a or not keys_b:
        return False

    overlap = len(keys_a & keys_b)
    total = max(len(keys_a), len(keys_b))
    similarity = overlap / total if total > 0 else 0

    log_main.debug(f"Scene similarity: {similarity:.0%} (threshold={threshold:.0%}) — "
                   f"overlap={overlap}/{total} keywords")

    return similarity >= threshold


# ─── Main Loop (Spoken Reactions) ─────────────────────────────

def main():
    print(BANNER)

    # Validate Ollama connectivity and model availability
    try:
        import ollama
        models = ollama.list()
        model_names = [m.model for m in models.models] if models.models else []
        
        # Check vision model (Stage 1)
        found_vision = any(config.OLLAMA_MODEL in name for name in model_names)
        if not found_vision:
            log_main.error(f"Vision model '{config.OLLAMA_MODEL}' not found in Ollama!")
            log_main.info(f"Available models: {model_names if model_names else 'none'}")
            print(f"  Run: {BOLD}ollama pull {config.OLLAMA_MODEL}{RESET}")
            sys.exit(1)
        log_main.success(f"Ollama connected — vision model '{config.OLLAMA_MODEL}' ready")
        
        # Check text model (Stage 2)
        found_text = any(config.OLLAMA_TEXT_MODEL in name for name in model_names)
        if not found_text:
            log_main.error(f"Text model '{config.OLLAMA_TEXT_MODEL}' not found in Ollama!")
            print(f"  Run: {BOLD}ollama pull {config.OLLAMA_TEXT_MODEL}{RESET}")
            sys.exit(1)
        log_main.success(f"Text model '{config.OLLAMA_TEXT_MODEL}' ready (Stage 2)")
        
        # Warm-up inference to pre-load model weights (avoids 30s cold start)
        log_main.info("Warming up models (first inference)...")
        try:
            ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": "Say hi in 3 words."}],
                options={"num_predict": 10, "num_ctx": 256},
            )
            ollama.chat(
                model=config.OLLAMA_TEXT_MODEL,
                messages=[{"role": "user", "content": "Say hi in 3 words."}],
                options={"num_predict": 10, "num_ctx": 256},
            )
            log_main.success("Both models warmed up")
        except Exception:
            log_main.warn("Warm-up failed (non-fatal) — first reaction may be slow")
    except Exception as e:
        log_main.error(f"Cannot connect to Ollama at {config.OLLAMA_BASE_URL}")
        log_main.error(f"  Error: {e}")
        print(f"  Make sure Ollama is running: {BOLD}ollama serve{RESET}")
        sys.exit(1)

    # Start RVC Sidecar if enabled
    rvc_process = None
    if getattr(config, "RVC_ENABLED", False):
        log_main.info("Starting RVC Sidecar (Python 3.10)...")
        sidecar_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rvc_sidecar")
        venv_python = os.path.join(sidecar_dir, "venv", "Scripts", "python.exe")
        try:
            if os.path.exists(venv_python):
                # Redirect logs to files for debugging
                log_out = open(os.path.join(sidecar_dir, "sidecar_stdout.log"), "a")
                log_err = open(os.path.join(sidecar_dir, "sidecar_stderr.log"), "a")
                
                # Pass config to sidecar via env vars
                sidecar_env = os.environ.copy()
                sidecar_env["RVC_F0_CHANGE"] = str(getattr(config, "RVC_PITCH_SHIFT", 0))
                sidecar_env["RVC_INDEX_RATE"] = str(getattr(config, "RVC_INDEX_RATE", 0.75))
                sidecar_env["RVC_PROTECT"] = str(getattr(config, "RVC_PROTECT", 0.33))
                sidecar_env["RVC_RMS_MIX"] = str(getattr(config, "RVC_RMS_MIX", 0.25))
                sidecar_env["RVC_FILTER_RADIUS"] = str(getattr(config, "RVC_FILTER_RADIUS", 3))

                rvc_process = subprocess.Popen(
                    [venv_python, "server.py"],
                    cwd=sidecar_dir,
                    stdout=log_out,
                    stderr=log_err,
                    env=sidecar_env,
                )
                atexit.register(lambda: rvc_process.terminate())
                # Wait for the heavy PyTorch model to load into GPU (poll health endpoint)
                log_main.debug("Waiting for RVC sidecar to warm up...")
                import requests
                for _ in range(15):
                    try:
                        resp = requests.get(f"{config.RVC_SIDECAR_URL}/health", timeout=2)
                        if resp.status_code == 200:
                            break
                    except Exception:
                        pass
                    time.sleep(2)
            else:
                log_main.warn("RVC enabled but Python 3.10 venv not found. Run setup_rvc.ps1 first.")
        except Exception as e:
            log_main.error(f"Failed to start RVC Sidecar: {e}")

    # Initialize systems
    log_main.info("Initializing TTS engine...")
    init_tts()
    
    # Phase 10: Initialize EasyOCR singleton
    log_main.info("Initializing OCR (CPU) - this takes a few seconds...")
    title_ocr.init_ocr()

    # Initialize sprite overlay — always visible from the start
    global _overlay_ref
    overlay = None
    if config.SPRITE_ENABLED or config.OVERLAY_ENABLED:
        log_main.info("Initializing sprite overlay...")
        overlay = SpriteOverlay()
        overlay.start()
        overlay.set_question_callback(lambda: _question_event.set())
        _overlay_ref = overlay  # expose for TTS mute checks
        tray_status = "+ tray icon" if config.TRAY_ENABLED else "no tray"
        log_main.success(f"Sprite overlay started (draggable, {tray_status})")

    memory = ContextMemory()
    subtitle_thread = SubtitleOCR(memory)

    # Initialize schedule manager
    global _scheduler
    scheduler = ScheduleManager()
    scheduler.start()
    _scheduler = scheduler
    log_main.success("Schedule manager started (timers, Pomodoro, reminders)")

    # Log configuration
    log_main.info(f"Config: capture_interval={config.CAPTURE_INTERVAL}s, react_interval={config.REACT_INTERVAL}s")
    log_main.info(f"Config: vision={config.OLLAMA_MODEL}, text={config.OLLAMA_TEXT_MODEL}, gpu_layers={config.OLLAMA_NUM_GPU}")
    log_main.info(f"Config: frame_diff_threshold={config.FRAME_DIFF_THRESHOLD}, "
                  f"downscale={config.DOWNSCALE_WIDTH}x{config.DOWNSCALE_HEIGHT}")
    log_main.success("Ready! Watching your screen — reacts to anything (100% local)")
    print(f"{DIM}  Press Ctrl+C to stop{RESET}\n")
    
    # Start background context gathering thread
    context_thread = threading.Thread(target=_context_worker, daemon=True)
    context_thread.start()
    log_main.info("Background context thread started")

    reaction_count = 0
    consecutive_failures = 0

    try:
        while True:
            reaction_count += 1
            
            # ── Step 1: Capture screenshot for reaction ──
            try:
                with log_main.timed("Screen capture for reaction"):
                    image = capture_screen()
            except Exception as e:
                log_main.error("Screen capture failed — skipping cycle", exc=e)
                time.sleep(config.REACT_INTERVAL)
                continue

            # ── Step 2: Full reaction cycle ──
            log_main.info(f"═══ Reaction #{reaction_count} ═══")

            # Start OCR immediately — runs on CPU in parallel with everything below
            ocr_result = {"text": ""}
            def _run_ocr():
                try:
                    ocr_result["text"] = title_ocr.ocr_screen_text(image)
                except Exception as e:
                    log_main.debug(f"OCR failed (non-fatal): {e}")
            
            ocr_thread = threading.Thread(target=_run_ocr, daemon=True)
            ocr_thread.start()

            # Get thread-safe copy of accumulated scenes (do NOT clear yet)
            with _accumulated_lock:
                current_scenes = list(_accumulated_scenes)
            log_main.debug(f"Consumed {len(current_scenes)} accumulated scenes")

            # Gather real-time system context (open apps, CPU, battery, etc.)
            with log_main.timed("System context gathering"):
                system_context, enriched_window = get_enriched_context()

            # Phase 10: Perception
            title_text, process_name = title_ocr.get_active_window_info_win32()
            if not title_text:
                title_text = title_ocr.get_active_window_title_ocr_fallback()
                
            context_label = context_resolver.resolve(title_text, process_name)
            
            # Subtitle daemon management
            if context_label.category == "video":
                subtitle_thread.start()
            else:
                subtitle_thread.stop()
                
            # Phase 10: State Updates
            memory.update_activity(context_label.intent, context_label.specific_context)
            
            # April 2.0: Tick productivity tracker
            memory.tick_productivity(context_label.intent)
            
            novelty_flag = context_label.specific_context not in memory.seen_contexts
            if novelty_flag:
                memory.seen_contexts.add(context_label.specific_context)
                
            time_context = get_time_context()
            
            from context_resolver import APRIL_OPINIONS
            personality_note = APRIL_OPINIONS.get(process_name, "") # Note: In actual implementation, match against opinions heuristic. Let's just pass empty for now unless matched later.
            
            callback_flag = memory.should_callback(context_label.intent)

            log_main.info(f"🎯 Activity: {context_label.specific_context} (Intent: {context_label.intent})")
            log_main.info(f"Memory state: affection={memory.affection}, streak={memory.roast_streak}, "
                          f"duration={memory.get_activity_duration()}m, total={memory.total_interactions}, "
                          f"focus={memory.focus_score:.0%}")

            # April 2.0: Proactive feature checks
            break_severity = scheduler.check_break_needed(
                context_label.intent, memory.get_activity_duration()
            )
            is_distraction = scheduler.check_distraction(context_label.intent)
            action_cancelled = memory.consume_action_cancelled()
            schedule_status = scheduler.get_status_summary()

            # Determine if April should announce a pending action
            pending_action = None
            if is_distraction and scheduler.focus_violations >= 3:
                pending_action = "pause your media"
            elif break_severity == "demand":
                pending_action = "pause your media to get your attention"

            # Boredom suppression check
            is_similar_now = memory.similar_scene_streak >= 3
            if not memory.should_react(action_type=memory.last_reaction_label, scene_is_similar=is_similar_now):
                log_main.debug("Skipping reaction — boredom suppression active")
                time.sleep(config.REACT_INTERVAL)
                continue

            # Wait for OCR to finish (started right after capture — should be done by now)
            ocr_thread.join(timeout=10)
            if ocr_result["text"]:
                log_main.info(f"📝 OCR: '{ocr_result['text'][:80]}...'")

            result = analyze_and_react(
                image=image,
                context_label=context_label,
                novelty_flag=novelty_flag,
                time_context=time_context,
                personality_note=personality_note,
                callback_flag=callback_flag,
                emotional_intensity=memory.get_emotional_intensity(),
                system_context=system_context,
                subtitle_buffer=list(memory.subtitle_buffer),
                session_narrative=memory.get_context_summary(),
                break_severity=break_severity,
                is_distraction=is_distraction,
                action_cancelled=action_cancelled,
                pending_action=pending_action,
                schedule_status=schedule_status,
                screen_text=ocr_result["text"],
            )

            if result is None:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    backoff = min(60, 15 * consecutive_failures)
                    log_main.warn(f"Multiple failures ({consecutive_failures}) — backing off {backoff}s")
                    time.sleep(backoff)
                else:
                    log_main.warn(f"Reaction failed (failure #{consecutive_failures}) — "
                                  f"waiting {config.REACT_INTERVAL}s")
                    time.sleep(config.REACT_INTERVAL)
                continue

            consecutive_failures = 0
            
            # Reaction succeeded, safe to clear the copied context buffer
            with _accumulated_lock:
                _accumulated_scenes.clear()

            scene = result["scene"]
            dialogue = result["dialogue"]
            emotion = result.get("emotion", "neutral")
            action_type = result.get("action_type", "commentary")

            # Check scene similarity for boredom tracking
            similar = _scene_is_similar(scene, memory.last_scene_description)
            memory.update_boredom(similar)

            # Log results
            log_main.reaction("👁️", f"{CYAN}Scene: {scene}{RESET}")
            log_main.reaction("🎭", f"{PINK}Emotion: {emotion} | Action: {action_type}{RESET}")
            if memory.similar_scene_streak >= 2:
                log_main.reaction("😤", f"{YELLOW}Boredom level: {memory.similar_scene_streak}{RESET}")

            # Update memory
            memory.add_event(action_type, action_type, scene)
            memory.set_last_reaction(action_type, scene)

            # ── Step 3: Display sprite + dialogue (overlay) ──
            print(f"\n  {PINK}{BOLD}💬 \"{dialogue}\"{RESET}")
            print(f"  {DIM}   Affection: {memory.affection} | Streak: {memory.roast_streak} | Mood: {memory.get_emotional_intensity()}{RESET}\n")

            # Show sprite overlay with emotion-driven expression
            if overlay:
                overlay.show(
                    dialogue=dialogue,
                    emotion=emotion,
                    action_type=action_type,
                    emotional_intensity=memory.get_emotional_intensity(),
                )
                log_main.debug(f"Sprite updated: emotion={emotion}")

            # Fire-and-forget TTS in background thread (passes overlay for subtitle sync)
            tts_thread = threading.Thread(
                target=_tts_worker,
                args=(dialogue, action_type),
                kwargs={"overlay_ref": overlay},
                daemon=True,
            )
            tts_thread.start()
            log_main.debug("TTS thread dispatched")

            # April 2.0: Execute pending action after TTS starts
            # The action was already announced in the dialogue — now we do the countdown
            if pending_action:
                log_main.info(f"Action announced: '{pending_action}' — starting {config.ACTION_ANNOUNCE_DELAY}s countdown")
                action_key = _resolve_action_key(pending_action)
                if action_key:
                    system_actions.execute_action_async(action_key, overlay)

            # Wait for the next reaction cycle, but poll for user questions + schedule events
            log_main.debug(f"Waiting {config.REACT_INTERVAL}s until next reaction (polling for questions)...")
            _wait_and_poll(overlay, memory, scheduler)

    except KeyboardInterrupt:
        print(f"\n{PINK}♡ B-bye... it's not like I'll miss watching your screen or anything! ♡{RESET}\n")
        if overlay:
            overlay.stop()
        if scheduler:
            scheduler.stop()
        stop_audio()
        sys.exit(0)


def _resolve_action_key(pending_action: str) -> str | None:
    """Map a pending action description to an ACTION_REGISTRY key."""
    action_lower = pending_action.lower()
    if "pause" in action_lower:
        return "pause_media"
    if "play" in action_lower or "resume" in action_lower:
        return "play_media"
    if "mute" in action_lower:
        return "mute_volume"
    if "volume down" in action_lower or "turn down" in action_lower:
        return "volume_down"
    if "volume up" in action_lower or "turn up" in action_lower:
        return "volume_up"
    if "next" in action_lower or "skip" in action_lower:
        return "next_track"
    return None


def _wait_and_poll(overlay, memory, scheduler=None):
    """
    Wait for REACT_INTERVAL seconds, using an Event for efficient sleep.
    If a question comes in (via the event), process it immediately,
    then reset the timer so the next auto-reaction doesn't fire right after.
    Also polls schedule manager for fired events.
    """
    remaining = config.REACT_INTERVAL
    while remaining > 0:
        _question_event.clear()
        # Wait efficiently — wakes up on question event OR timeout
        triggered = _question_event.wait(timeout=min(remaining, 1.0))
        remaining -= 1.0
        
        # Check for user questions
        if triggered and overlay:
            question = overlay.get_pending_question()
            if question:
                _handle_user_question(question, overlay, memory, scheduler)
                # Reset timer after answering
                remaining = config.REACT_INTERVAL
                continue

        # Check for schedule events
        if scheduler:
            for label, callback in scheduler.get_pending_callbacks():
                log_main.info(f"⏰ Schedule event: {label}")
                try:
                    callback()
                except Exception as e:
                    log_main.error(f"Schedule callback failed: {label}", exc=e)

        # Check if an action was cancelled (set cancel memory for next reaction)
        if overlay and overlay.action_cancelled:
            overlay.clear_action_cancel()
            memory.record_action_cancelled("an action")
            log_main.info("User cancelled a pending action — will be annoyed next reaction")

        if remaining <= 0:
            break


def _handle_user_question(question: str, overlay, memory, scheduler=None):
    """Process a direct user question via the chat input.
    
    April 2.0: Now supports command parsing for:
    - 'summarize' / 'tldr' → page summarization
    - 'set timer' / 'remind me' → schedule a timer/reminder
    - 'pause' / 'play' / 'mute' → media control (with announce-and-act)
    - 'pomodoro' / 'focus' → start/stop Pomodoro
    - 'cancel timer' → cancel active timer
    - 'daily report' / 'how did i do' → productivity report
    - Otherwise → standard Q&A
    """
    log_main.reaction("💬", f"{PINK}User asked: \"{question}\"{RESET}")
    q_lower = question.lower().strip()

    # ─── Command: Summarize / TL;DR ──────────────────────────
    if any(kw in q_lower for kw in ["summarize", "tldr", "tl;dr", "summary", "what does this say", "what's on screen"]):
        log_main.info("Command detected: Summarize current page")
        try:
            image = capture_screen()
            system_context = get_system_context()
            result = summarize_current_page(image, system_context)
            if result:
                _speak_result(result, overlay, memory)
                return
        except Exception as e:
            log_main.error("Summarization failed", exc=e)
        if overlay:
            overlay.show("Tch, I couldn't read that page properly. Try scrolling up.", "angry", "roast")
            overlay.start_dialogue()
            time.sleep(config.OVERLAY_DISPLAY_SECONDS)
            overlay.end_dialogue()
        return

    # ─── Command: Set Timer / Reminder ────────────────────────
    if any(kw in q_lower for kw in ["set timer", "remind me", "set a timer", "set an alarm", "reminder"]):
        if scheduler:
            minutes = _extract_minutes(q_lower)
            if minutes and minutes > 0:
                def _on_timer_fire():
                    if overlay:
                        overlay.show(
                            f"⏰ Hey! Your {minutes:.0f}-minute timer is up!",
                            "angry", "concerned",
                        )
                        overlay.start_dialogue()
                scheduler.set_timer(f"User timer ({minutes:.0f}m)", minutes, _on_timer_fire)
                if overlay:
                    overlay.show(f"Fine, I set a {minutes:.0f}-minute timer. Don't blame me when it goes off.", "smug", "commentary")
                    overlay.start_dialogue()
                    time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                    overlay.end_dialogue()
                return
            else:
                if overlay:
                    overlay.show("How many minutes? Say something like 'set timer 30 minutes'.", "confused", "commentary")
                    overlay.start_dialogue()
                    time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                    overlay.end_dialogue()
                return
        return

    # ─── Command: Pomodoro ────────────────────────────────────
    if any(kw in q_lower for kw in ["pomodoro", "focus mode", "start focus", "focus session"]):
        if scheduler:
            if "stop" in q_lower or "cancel" in q_lower:
                if scheduler.stop_pomodoro():
                    if overlay:
                        overlay.show("Fine, Pomodoro cancelled. Your lack of discipline is noted.", "disappointed", "commentary")
                        overlay.start_dialogue()
                        time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                        overlay.end_dialogue()
                return
            
            work_mins = _extract_minutes(q_lower) or config.POMODORO_DEFAULT_WORK
            
            def _on_work_end():
                if overlay:
                    overlay.show("Time's up! Take a break... and I mean actually step away from the screen.", "concerned", "concerned")
                    overlay.start_dialogue()
            
            def _on_break_end():
                if overlay:
                    overlay.show("Break's over! Get back to work, slacker.", "angry", "roast")
                    overlay.start_dialogue()
            
            scheduler.start_pomodoro(
                work_mins=work_mins,
                on_work_end=_on_work_end,
                on_break_end=_on_break_end,
            )
            if overlay:
                overlay.show(
                    f"Pomodoro started! {work_mins:.0f} minutes of focus. I'm watching you.",
                    "smug", "commentary",
                )
                overlay.start_dialogue()
                time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                overlay.end_dialogue()
            return
        return

    # ─── Command: Media Control ───────────────────────────────
    if any(kw in q_lower for kw in ["pause", "play", "resume", "mute", "next track", "skip", "volume"]):
        action_key = _resolve_action_key(q_lower)
        if action_key:
            action_desc = system_actions.ACTION_REGISTRY[action_key][1]
            if overlay:
                overlay.show(
                    f"Hmph, fine. I'm going to {action_desc}.",
                    "smug", "commentary",
                )
                overlay.start_dialogue()
            # Execute with countdown (announce was just shown)
            system_actions.execute_action_async(action_key, overlay)
            return
        return

    # ─── Command: Cancel Timer ────────────────────────────────
    if any(kw in q_lower for kw in ["cancel timer", "stop timer", "cancel reminder"]):
        if scheduler:
            timers = scheduler.get_active_timers()
            if timers:
                # Cancel the most recent user timer
                for label_str in timers:
                    label = label_str.split(" (in")[0]  # strip the time part
                    if "User timer" in label or "Reminder" in label:
                        scheduler.cancel_timer(label)
                        if overlay:
                            overlay.show("Timer cancelled. You're welcome.", "neutral", "commentary")
                            overlay.start_dialogue()
                            time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                            overlay.end_dialogue()
                        return
            if overlay:
                overlay.show("There are no active timers to cancel.", "neutral", "commentary")
                overlay.start_dialogue()
                time.sleep(config.OVERLAY_DISPLAY_SECONDS)
                overlay.end_dialogue()
            return
        return

    # ─── Command: Daily Report ────────────────────────────────
    if any(kw in q_lower for kw in ["daily report", "how did i do", "productivity", "focus score", "report"]):
        report = memory.get_daily_report()
        log_main.info(f"Daily Report:\n{report}")
        # Save to file
        import os
        report_dir = os.path.join("training_data", "daily_reports")
        os.makedirs(report_dir, exist_ok=True)
        from datetime import datetime
        report_file = os.path.join(report_dir, f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        # Show summary
        score_pct = f"{memory.focus_score:.0%}"
        if overlay:
            overlay.show(
                f"Your focus score today: {score_pct}. Productive: {memory.productive_minutes:.0f}min, Distracted: {memory.unproductive_minutes:.0f}min. Report saved.",
                "smug", "commentary",
            )
            overlay.start_dialogue()
            time.sleep(config.OVERLAY_DISPLAY_SECONDS)
            overlay.end_dialogue()
        return

    # ─── Default: Standard Q&A ────────────────────────────────
    try:
        with log_main.timed("Screenshot for Q&A"):
            image = capture_screen()
    except Exception as e:
        log_main.error("Screen capture failed for Q&A", exc=e)
        if overlay:
            overlay.show("Ugh, I can't even see your screen right now! Try again.", "angry", "roast")
        return

    with log_main.timed("System context for Q&A"):
        system_context = get_system_context()

    result = answer_user_question(
        question=question,
        image=image,
        system_context=system_context,
        context_summary=memory.get_context_summary(),
    )

    if result is None:
        log_main.error("Q&A failed — API unavailable")
        if overlay:
            overlay.show(
                "Tch, the API is being difficult right now. Ask me later.",
                "angry", "roast",
            )
        return

    _speak_result(result, overlay, memory)


def _speak_result(result: dict, overlay, memory):
    """Display + speak a result dict (shared by Q&A and summarize)."""
    dialogue = result["dialogue"]
    emotion = result.get("emotion", "neutral")
    action_type = result.get("action_type", "commentary")

    print(f"\n  {CYAN}{BOLD}💬 [Q&A] \"{dialogue}\"{RESET}")

    if overlay:
        overlay.show(
            dialogue=dialogue,
            emotion=emotion,
            action_type=action_type,
            emotional_intensity=memory.get_emotional_intensity(),
        )

    tts_thread = threading.Thread(
        target=_tts_worker,
        args=(dialogue, action_type),
        kwargs={"overlay_ref": overlay},
        daemon=True,
    )
    tts_thread.start()
    log_main.debug("Q&A TTS thread dispatched")


def _extract_minutes(text: str) -> float | None:
    """Extract a number of minutes from a natural language string."""
    import re
    # "30 minutes", "30 mins", "30 min", just "30"
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:minute|min|m\b)', text)
    if m:
        return float(m.group(1))
    # "1 hour", "2 hours"
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:hour|hr|h\b)', text)
    if m:
        return float(m.group(1)) * 60
    # Just a bare number
    m = re.search(r'(\d+)', text)
    if m:
        val = float(m.group(1))
        if val > 0:
            return val
    return None


if __name__ == "__main__":
    main()
