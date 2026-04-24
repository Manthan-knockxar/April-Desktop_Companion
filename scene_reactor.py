"""
Scene Reactor — Two-Stage Pipeline Architecture.

Stage 1 (Perception): Send screenshot to llama3.2-vision with a tiny prompt.
    → Returns a factual scene description in ~3-5 seconds.
Stage 2 (Personality): Feed that description + full tsundere personality prompt
    to the fast llama3.2:3b text model.
    → Returns structured <<<FIELD>>> reaction in ~1-2 seconds.

Total turnaround: ~5-8 seconds vs 25-35 seconds with single-stage.
"""
import io
import time
import re
import threading
import json
import os
from datetime import datetime
import ollama
from PIL import Image

import config
from logger import Log
from comeback_templates import get_comeback_style, get_vocabulary_constraint
from context_resolver import ContextLabel
from knowledge_base import match_knowledge, format_knowledge_for_prompt
from personality import personality

log = Log("Reactor")

# ─── Threading Lock for Ollama ───────────────────────────────
_ollama_lock = threading.Lock()

# ─── Recent dialogue memory (anti-repetition) ────────────────
_recent_dialogues: list[str] = []
MAX_RECENT = 12

# ─── Error Detection Keywords ─────────────────────────────────
_ERROR_KEYWORDS = [
    "traceback", "error", "exception", "failed", "syntaxerror",
    "typeerror", "valueerror", "keyerror", "indexerror", "nameerror",
    "attributeerror", "importerror", "runtimeerror", "connectionerror",
    "filenotfounderror", "build failed", "compilation error",
    "segmentation fault", "stack trace", "undefined", "null pointer",
    "cannot read", "is not defined", "module not found",
]

# ─── Emotion Aliases (model outputs → valid sprite emotions) ──
_EMOTION_ALIASES = {
    "annoyed": "angry",
    "irritated": "angry",
    "frustrated": "angry",
    "furious": "angry",
    "curious": "happy",
    "interested": "happy",
    "intrigued": "happy",
    "amused": "smug",
    "bored": "disappointed",
    "tired": "disappointed",
    "concerned": "worried",
    "anxious": "worried",
    "nervous": "flustered",
    "embarrassed": "flustered",
    "shy": "flustered",
    "proud": "smug",
    "satisfied": "smug",
    "sad": "disappointed",
    "confused": "flustered",
}

# ─── Third-Person Phrases (rejection triggers) ───────────────
_THIRD_PERSON_PHRASES = [
    "the user ", "the user's ", "the user is", "the user has",
    "the user was", "the user seems", "the user appears",
    "the user might", "the user could", "the user should",
    "it seems like the user", "it appears the user",
    "the activity is", "the time is",
]

# ─── Banned Openers (prevent repetitive starts) ──────────────
_BANNED_OPENER_PATTERNS = [
    "it's thursday", "another thursday", "thursday morning",
    "it's monday", "it's tuesday", "it's wednesday", "it's friday",
    "it's saturday", "it's sunday",
    "your screen is currently", "the image shows",
    "the screenshot shows", "the code is",
]

def _detect_error_context(scene_description: str) -> bool:
    """Check if the scene description suggests an error/bug is visible."""
    scene_lower = scene_description.lower()
    return any(kw in scene_lower for kw in _ERROR_KEYWORDS)

def _build_anti_repeat_section() -> str:
    if not _recent_dialogues:
        return "(No recent lines yet)"
    lines = []
    for d in _recent_dialogues[-6:]:
        words = d.split()[:8]
        lines.append(f"  - {' '.join(words)}...")
    return "\n".join(lines)

def _get_recent_openers() -> set[str]:
    """Extract first 5 words of recent dialogues for opener diversity check."""
    openers = set()
    for d in _recent_dialogues[-6:]:
        opener = " ".join(d.lower().split()[:5])
        if opener:
            openers.add(opener)
    return openers


# ─── Training Data Logger ──────────────────────────────────────
TRAINING_DIR = "training_data"
os.makedirs(TRAINING_DIR, exist_ok=True)

def _log_training_data(scene: str, reaction: dict):
    try:
        log_file = os.path.join(TRAINING_DIR, "reaction_logs.jsonl")
        entry = {
            "timestamp": datetime.now().isoformat(),
            "scene_input": scene,
            "reaction_output": reaction
        }
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.error(f"Failed to log training data: {e}")



def _downscale_image(image: Image.Image) -> Image.Image:
    """Downscale screenshot for faster local inference. Saves VRAM."""
    target_w = config.DOWNSCALE_WIDTH
    target_h = config.DOWNSCALE_HEIGHT
    if image.width > target_w or image.height > target_h:
        log.debug(f"Downscaling {image.width}x{image.height} → {target_w}x{target_h}")
        image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return image


def _pil_to_bytes(image: Image.Image) -> bytes:
    """Convert PIL Image to JPEG bytes for Ollama vision input."""
    buf = io.BytesIO()
    if image.mode == "RGBA":
        image = image.convert("RGB")
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _call_ollama(prompt: str, image: Image.Image | None = None,
                 temperature: float = 0.7, max_tokens: int = 400,
                 tag: str = "Ollama", model_override: str | None = None) -> str | None:
    """
    Unified call to Ollama local model with retry logic.
    Supports text-only and multimodal (image) requests.
    """
    messages = []
    if image is not None:
        image_bytes = _pil_to_bytes(image)
        messages.append({
            "role": "user",
            "content": prompt,
            "images": [image_bytes],
        })
    else:
        messages.append({
            "role": "user",
            "content": prompt,
        })

    model_to_use = model_override if model_override else config.OLLAMA_MODEL
    gpu_layers = getattr(config, "OLLAMA_TEXT_NUM_GPU", -1) if model_override else config.OLLAMA_NUM_GPU

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"[{tag}] Calling {model_to_use} (attempt {attempt}/{max_retries})")
            
            with log.timed(f"[{tag}] Waiting for Ollama lock"):
                acquired = _ollama_lock.acquire(timeout=90)
            if not acquired:
                log.error(f"[{tag}] Ollama lock acquisition timed out (90s) — skipping")
                return None
            try:
                with log.timed(f"[{tag}] Local inference"):
                    response = ollama.chat(
                        model=model_to_use,
                        messages=messages,
                        options={
                            "temperature": temperature,
                            "num_predict": max_tokens,
                            "num_ctx": 1024 if image is not None else 2048,  # vision needs less context
                            "num_gpu": gpu_layers,
                        },
                    )
            finally:
                _ollama_lock.release()
                
            text = response["message"]["content"].strip()
            log.debug(f"[{tag}] Response ({len(text)} chars):\n    {text[:300]}")
            return text

        except ollama.ResponseError as e:
            log.error(f"[{tag}] Ollama response error (attempt {attempt}): {e}")
            if attempt < max_retries:
                time.sleep(2)
        except Exception as e:
            error_type = type(e).__name__
            log.error(f"[{tag}] {error_type}: {str(e)[:200]}")
            if attempt < max_retries:
                time.sleep(3)

    return None


# ═══════════════════════════════════════════════════════════════
# STAGE 1: PERCEPTION (Vision Model — tiny prompt, fast)
# ═══════════════════════════════════════════════════════════════

def _stage1_perceive(image: Image.Image, context_label: ContextLabel) -> str | None:
    """
    Send the screenshot to the vision model with a minimal prompt.
    Returns a short factual scene description.
    """
    image = _downscale_image(image)
    
    prompt = f"""You are observing the user's screen. 
Context hint: {context_label.specific_context}
Do NOT just state the application name (like "YouTube" or "VSCode"), since the system already knows that.
Instead, focus heavily on the SPECIFIC content visible on the screen.
For example, if it's a video, describe the video content, characters, or text. If it's a game, describe what's happening.
Ignore any large black rectangles (those are your own interface).
Start directly with your description. Keep it factual and under 40 words."""

    return _call_ollama(
        prompt=prompt,
        image=image,
        temperature=config.OLLAMA_VISION_TEMPERATURE,
        max_tokens=80,
        tag="Vision",
    )


# ═══════════════════════════════════════════════════════════════
# STAGE 2: PERSONALITY (Text Model — rich prompt, no image)
# ═══════════════════════════════════════════════════════════════

def _parse_response(text: str, context_label: ContextLabel) -> dict | None:
    """
    Flexible parser with quality enforcement.
    
    Post-processing pipeline:
      1. Extract emotion/action/thought/reaction from <<<TAGS>>>
      2. Apply emotion aliases (annoyed→angry, curious→happy, etc.)
      3. Strip action label prefixes from dialogue
      4. Reject third-person narration ("the user...")
      5. Reject thought=dialogue duplication
      6. Truncate overly long dialogues
      7. Check opener diversity
      8. Verify "you"/"your" presence
    """
    emotion = "neutral"
    action_type = "commentary"
    reaction = ""
    thought = ""
    
    valid_emotions = {"neutral", "angry", "happy", "smug", "flustered", "disappointed", "worried"}
    all_known_emotions = valid_emotions | set(_EMOTION_ALIASES.keys())
    valid_actions = {"commentary", "roast", "impressed", "concerned", "bored", "curious"}
    
    text_lower = text.lower()

    # ── Step 1: Extract emotion ──
    m_emo = re.search(r'<<<EMOTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_emo:
        emo_text = m_emo.group(1).strip().lower()
        # Try exact match first, then aliases
        emo_match = re.search(r'\b(' + '|'.join(all_known_emotions) + r')\b', emo_text)
        if emo_match:
            raw_emo = emo_match.group(1)
            emotion = _EMOTION_ALIASES.get(raw_emo, raw_emo)
    else:
        for emo in all_known_emotions:
            if f'<<<{emo.upper()}>>>' in text or f'<<<{emo.capitalize()}>>>' in text:
                emotion = _EMOTION_ALIASES.get(emo, emo)
                break

    # Ensure emotion is valid (in case alias mapping missed)
    if emotion not in valid_emotions:
        emotion = _EMOTION_ALIASES.get(emotion, "neutral")

    # ── Step 2: Extract action ──
    m_act = re.search(r'<<<ACTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_act:
        act_text = m_act.group(1).strip().lower()
        act_match = re.search(r'\b(' + '|'.join(valid_actions) + r')\b', act_text)
        if act_match:
            action_type = act_match.group(1)
    else:
        for act in valid_actions:
            if f'<<<{act.upper()}>>>' in text or f'<<<{act.capitalize()}>>>' in text:
                action_type = act
                break
        act_scan = re.search(r'\b(' + '|'.join(valid_actions) + r')\b', text_lower)
        if act_scan:
            action_type = act_scan.group(1)

    # ── Step 3: Extract thought (for logging + dedup check) ──
    m_thought = re.search(r'<<<THOUGHT>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_thought:
        thought = m_thought.group(1).strip()

    # ── Step 4: Extract reaction text ──
    m_react = re.search(r'<<<REACTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_react:
        reaction = m_react.group(1).strip()
    else:
        cleaned = re.sub(r'<<<[^>]+>>>', '\n', text)
        lines = []
        for line in cleaned.split('\n'):
            stripped = line.strip()
            if stripped.lower() in valid_emotions or stripped.lower() in valid_actions:
                continue
            if stripped.lower() in all_known_emotions:
                continue
            if len(stripped) > 10:
                lines.append(stripped)
        if lines:
            reaction = max(lines, key=len)
    
    # ══════════════════════════════════════════════════════════
    # POST-PROCESSING QUALITY PIPELINE
    # ══════════════════════════════════════════════════════════
    
    # Clean up basics
    reaction = reaction.replace('**', '').replace('"', '').strip()
    
    # Fix 5: Strip action label prefixes ("Commentary: ...", "roast: ...")
    for label in list(valid_actions) + ["reaction"]:
        if reaction.lower().startswith(f"{label}:"):
            reaction = reaction[len(label) + 1:].strip()
        elif reaction.lower().startswith(f"{label} :"):
            reaction = reaction[len(label) + 2:].strip()
    # Strip leading colons/dashes
    reaction = re.sub(r'^[:\-–—]\s*', '', reaction).strip()
    
    if not reaction:
        log.warn("Parse failed — no reaction text found")
        return None

    # Fix 1: Reject third-person narration
    reaction_lower = reaction.lower()
    has_third_person = any(phrase in reaction_lower for phrase in _THIRD_PERSON_PHRASES)
    if has_third_person:
        log.warn(f"Rejected third-person dialogue: '{reaction[:80]}...'")
        # Try to salvage: if there's a second sentence, use it
        sentences = re.split(r'[.!?]+\s+', reaction)
        salvaged = None
        for s in sentences[1:]:
            s = s.strip()
            s_lower = s.lower()
            if len(s) > 15 and not any(p in s_lower for p in _THIRD_PERSON_PHRASES):
                salvaged = s
                break
        if salvaged:
            reaction = salvaged
            log.info(f"Salvaged sentence: '{reaction[:60]}'")
        else:
            return None  # Total reject — will retry

    # Fix 2: Reject thought=dialogue duplication
    if thought and reaction:
        # Compare first 40 chars (normalized)
        t_norm = thought.lower().strip()[:40]
        r_norm = reaction.lower().strip()[:40]
        if t_norm and r_norm and (t_norm == r_norm or t_norm in r_norm or r_norm in t_norm):
            log.warn(f"Rejected thought-as-dialogue duplication")
            return None

    # Fix 4: Check banned openers
    reaction_opener = reaction.lower().strip()
    for banned in _BANNED_OPENER_PATTERNS:
        if reaction_opener.startswith(banned):
            log.warn(f"Rejected banned opener: '{banned}'")
            return None

    # Fix 7: Opener diversity check
    new_opener = " ".join(reaction.lower().split()[:5])
    recent_openers = _get_recent_openers()
    if new_opener in recent_openers:
        log.warn(f"Rejected duplicate opener: '{new_opener}'")
        return None

    # Fix 6: Truncate overly long dialogues to ~2 sentences
    words = reaction.split()
    if len(words) > 45:
        sentences = re.split(r'([.!?]+\s+)', reaction)
        truncated = ""
        for i in range(0, len(sentences), 2):  # pairs of (sentence, delimiter)
            chunk = sentences[i]
            if i + 1 < len(sentences):
                chunk += sentences[i + 1]
            if len((truncated + chunk).split()) > 40:
                break
            truncated += chunk
        if truncated.strip():
            reaction = truncated.strip()
            log.debug(f"Truncated dialogue from {len(words)} to {len(reaction.split())} words")

    # Fix 8: "You"/"your" enforcement — soft check (warn, don't reject)
    if "you" not in reaction.lower():
        log.debug("Dialogue missing 'you/your' — may lack direct address")

    # Final length check
    words = reaction.split()
    if len(words) < 5 or len(words) > 80:
        log.warn(f"Parse failed — word count {len(words)} out of bounds")
        return None
    
    # Normalize confused → flustered
    if emotion == "confused":
        emotion = "flustered"

    return {
        "scene": "",
        "thought": thought,
        "emotion": emotion,
        "action_type": action_type,
        "dialogue": reaction,
    }


def _stage2_react(
    scene_description: str,
    context_label: ContextLabel,
    novelty_flag: bool,
    time_context: dict,
    personality_note: str,
    callback_flag: bool,
    emotional_intensity: str,
    system_context: str = "",
    subtitle_buffer: list[str] | None = None,
    session_narrative: str = "",
    error_detected: bool = False,
    break_severity: str | None = None,
    is_distraction: bool = False,
    action_cancelled: tuple[bool, str] = (False, ""),
    pending_action: str | None = None,
    schedule_status: str = "",
    screen_text: str = "",
    knowledge_context: str = "",
    personality_brief: str = "",
) -> dict | None:
    """
    Send the scene description + full personality prompt to the fast text model.
    Returns a structured reaction with emotion, action, and dialogue.
    
    Now supports:
    - error_detected: inject bug analysis instruction
    - break_severity: inject break warning/demand
    - is_distraction: inject distraction callout (Pomodoro)
    - action_cancelled: inject annoyance about cancelled action
    - pending_action: inject action announcement into dialogue
    - schedule_status: active timers/pomodoro info
    """
    time_str = time_context.get("time_string", "Unknown time")
    
    subs_str = ""
    if context_label.category == "video" and subtitle_buffer:
        subs_str = "\nDIALOGUE ON SCREEN:\n" + "\n".join(f"- {line}" for line in subtitle_buffer)

    callback_str = ""
    if callback_flag:
        callback_str = "\nThe user was doing this earlier and left. They just came back to it."

    style_hint = get_comeback_style(emotional_intensity, is_curious=novelty_flag)
    vocab_hint = get_vocabulary_constraint(emotional_intensity)

    curious_rule = ""
    if novelty_flag:
        curious_rule = "\n- CURIOSITY: You've never seen this before. ACTION must be 'curious'. Ask what it is."

    # ── April 2.0: Dynamic context injections ──
    error_rule = ""
    if error_detected:
        error_rule = """\n- BUG DETECTED: You can see an error/traceback on screen. React to the specific error you see.
  Mention what the error likely is and give a quick hint about the fix, but wrapped in your sassy tone.
  Example: 'Oh great, a TypeError. You're passing a string where it wants an int, genius.'"""

    break_rule = ""
    if break_severity == "warning":
        break_rule = "\n- BREAK WARNING: The user has been coding for over 90 minutes straight. Express concern. Tell them to take a break, but reluctantly like you care."
    elif break_severity == "demand":
        break_rule = "\n- BREAK DEMAND: The user has been coding for over 2 HOURS without stopping. Be AGGRESSIVE. Demand they take a break NOW. You're genuinely worried but express it through anger."

    distraction_rule = ""
    if is_distraction:
        distraction_rule = "\n- FOCUS VIOLATION: The user is supposed to be working (Pomodoro active) but they switched to something unproductive. Call them out HARD. They're wasting their own focus session."

    cancelled_rule = ""
    was_cancelled, cancelled_label = action_cancelled
    if was_cancelled:
        cancelled_rule = f"\n- ACTION CANCELLED: You just tried to {cancelled_label} but the user pressed Ctrl+Shift+X to cancel it. Be ANNOYED that they stopped you. You were trying to help!"

    action_rule = ""
    if pending_action:
        action_rule = f"""\n- PENDING ACTION: You are about to {pending_action}. Work this into your dialogue NATURALLY.
  Don't say 'I will now execute action X'. Instead, say something like 'Hmph, I'm {pending_action} whether you like it or not!'
  The action is part of your reaction, not a separate announcement."""

    schedule_note = ""
    if schedule_status:
        schedule_note = f"\nSCHEDULE: {schedule_status}"

    # OCR text injection — actual text from the screen
    screen_text_section = ""
    if screen_text:
        screen_text_section = f"\nACTUAL TEXT ON SCREEN (from OCR — this is what's REALLY written): {screen_text}"

    prompt = f"""You are April, a tsundere anime girl living on a user's desktop. You are sharp-tongued, dramatic, and secretly caring.

{personality_brief}

CRITICAL RULES:
- ALWAYS talk directly TO the user using "you" and "your". NEVER say "the user" or narrate in third person.
- Your REACTION must be DIFFERENT from your THOUGHT. The thought is your inner reasoning. The reaction is what you SAY OUT LOUD.
- You MUST mention at least ONE specific detail from the screen (a name, title, color, filename, text snippet). Generic reactions are FORBIDDEN.
- Do NOT start with the day of the week ("It's Thursday...", "Another Monday..."). Jump straight into your reaction.
- Do NOT prefix your reaction with labels like "Commentary:" or "Roast:".
- Stick to your PERSONALITY BIASES mentioned in your brief.
NOTE: You are a desktop companion. If there's a black box in your vision, that's just your own UI being masked out—ignore it.

WHAT YOU SEE: {scene_description}{screen_text_section}
TIME: {time_str}
ACTIVITY: {context_label.specific_context}
{personality_note}{subs_str}{callback_str}{schedule_note}{knowledge_context}

STYLE: {style_hint}
VOCAB: {vocab_hint}

DO NOT REPEAT THESE RECENT LINES:
{_build_anti_repeat_section()}

SPECIAL RULES:{error_rule}{break_rule}{distraction_rule}{cancelled_rule}{action_rule}{curious_rule}

OUTPUT FORMAT — use these EXACT delimiters, nothing else:
<<<THOUGHT>>> 1-2 sentences of INTERNAL reasoning about what you see. This is private — the user won't hear this.
<<<EMOTION>>> one word: neutral/angry/happy/smug/flustered/disappointed/worried
<<<ACTION>>> one word: commentary/roast/impressed/concerned/bored/curious
<<<REACTION>>> 15-35 words. What you SAY OUT LOUD to the user. Must use "you"/"your". Must reference something specific on screen. Be sassy."""

    # Retry loop: quality pipeline may reject low-quality outputs
    max_quality_retries = 2
    for quality_attempt in range(1, max_quality_retries + 1):
        temp = config.OLLAMA_REACT_TEMPERATURE
        if quality_attempt > 1:
            temp = min(temp + 0.15, 1.0)  # Bump temperature on retry
            log.info(f"[React] Quality retry #{quality_attempt} (temp={temp:.2f})")

        text = _call_ollama(
            prompt=prompt.strip(),
            image=None,
            temperature=temp,
            max_tokens=220,
            tag="React",
            model_override=config.OLLAMA_TEXT_MODEL,
        )

        if not text:
            return None

        result = _parse_response(text, context_label)
        if result is not None:
            return result
        
        if quality_attempt < max_quality_retries:
            log.warn(f"[React] Quality filter rejected output — retrying ({quality_attempt}/{max_quality_retries})")

    log.warn("[React] All quality retries exhausted — no usable reaction")
    return None


# ═══════════════════════════════════════════════════════════════
# PUBLIC API (called by main.py)
# ═══════════════════════════════════════════════════════════════

def analyze_and_react(
    image: Image.Image,
    context_label: ContextLabel,
    novelty_flag: bool,
    time_context: dict,
    personality_note: str,
    callback_flag: bool,
    emotional_intensity: str,
    system_context: str = "",
    subtitle_buffer: list[str] | None = None,
    session_narrative: str = "",
    error_detected: bool = False,
    break_severity: str | None = None,
    is_distraction: bool = False,
    action_cancelled: tuple[bool, str] = (False, ""),
    pending_action: str | None = None,
    schedule_status: str = "",
    screen_text: str = "",
) -> dict | None:
    """
    Two-Stage Pipeline:
      Stage 1: Vision model describes the screen (~3-5s)
      Stage 2: Text model generates tsundere reaction (~1-2s)
    """
    total_start = time.time()

    # ── Stage 1: Perception ──
    log.info("═══ Stage 1: Perception (Vision) ═══")
    scene_description = _stage1_perceive(image, context_label)
    
    if not scene_description:
        log.warn("Stage 1 failed — no scene description from vision model")
        return None
    
    log.success(f"Scene: {scene_description[:120]}")

    # Auto-detect errors if feature is enabled
    if config.PROACTIVE_BUG_DETECTION and not error_detected:
        error_detected = _detect_error_context(scene_description)
        if error_detected:
            log.info("🐛 Error detected in scene — activating bug analysis mode")

    # Also check OCR text for errors
    if config.PROACTIVE_BUG_DETECTION and not error_detected and screen_text:
        error_detected = _detect_error_context(screen_text)
        if error_detected:
            log.info("🐛 Error detected in OCR text — activating bug analysis mode")

    # Match domain knowledge from OCR text + scene
    combined_text = f"{scene_description} {screen_text}"
    knowledge_matches = match_knowledge(combined_text)
    knowledge_context = format_knowledge_for_prompt(knowledge_matches)
    if knowledge_matches:
        log.info(f"📚 Matched {len(knowledge_matches)} knowledge entries")

    # Update Personality/Mood
    personality.update_mood_from_text(combined_text)
    personality_brief = personality.get_personality_brief()

    # ── Stage 2: Personality ──
    log.info("═══ Stage 2: Personality (Text) ═══")
    result = _stage2_react(
        scene_description=scene_description,
        context_label=context_label,
        novelty_flag=novelty_flag,
        time_context=time_context,
        personality_note=personality_note,
        callback_flag=callback_flag,
        emotional_intensity=emotional_intensity,
        system_context=system_context,
        subtitle_buffer=subtitle_buffer,
        session_narrative=session_narrative,
        error_detected=error_detected,
        break_severity=break_severity,
        is_distraction=is_distraction,
        action_cancelled=action_cancelled,
        pending_action=pending_action,
        schedule_status=schedule_status,
        screen_text=screen_text,
        knowledge_context=knowledge_context,
        personality_brief=personality_brief,
    )

    if result:
        result["scene"] = scene_description
        total_time = time.time() - total_start
        log.success(f"Pipeline complete in {total_time:.1f}s — emotion={result['emotion']}, action={result['action_type']}, words={len(result['dialogue'].split())}")
        
        if result["dialogue"]:
            _recent_dialogues.append(result["dialogue"])
            if len(_recent_dialogues) > MAX_RECENT:
                _recent_dialogues.pop(0)
            
            _log_training_data(scene_description, result)

        return result

    return None


def analyze_scene_silent(image: Image.Image) -> str | None:
    """Quick silent scene analysis using only Stage 1."""
    return _stage1_perceive(image, ContextLabel(
        category="unknown",
        specific_context="General observation",
        focus_instruction="Describe the screen.",
        intent="unknown",
    ))


def answer_user_question(
    question: str, image: Image.Image, system_context: str = "", context_summary: str = "",
) -> dict | None:
    """Answer a direct user question using the text model."""
    scene = _stage1_perceive(image, ContextLabel(
        category="unknown",
        specific_context="User asked a question",
        focus_instruction="Describe the screen.",
        intent="unknown",
    ))
    
    prompt = f"""You are April, a tsundere anime girl on the user's desktop. Answer their question directly but stay in character — sharp, dramatic, secretly caring.

SCREEN: {scene or "Could not see screen"}
SYSTEM: {system_context}
USER'S QUESTION: {question}

Answer in 1-2 sentences. Be helpful but sassy."""

    text = _call_ollama(
        prompt=prompt.strip(),
        image=None,
        temperature=0.7,
        max_tokens=150,
        tag="Question",
        model_override=config.OLLAMA_TEXT_MODEL,
    )
    
    if text:
        return {
            "scene": scene or "",
            "emotion": "neutral",
            "action_type": "commentary",
            "dialogue": text.replace('**', '').replace('"', ''),
        }
    return None


def summarize_current_page(image: Image.Image, system_context: str = "") -> dict | None:
    """
    TL;DR function: Analyze the currently visible page content
    and return a summary wrapped in April's personality.
    """
    scene = _stage1_perceive(image, ContextLabel(
        category="unknown",
        specific_context="User requested a summary",
        focus_instruction="Read ALL visible text on the screen carefully. Include headings, body text, code, and key details.",
        intent="unknown",
    ))
    
    if not scene:
        return None

    prompt = f"""You are April, a tsundere anime girl on the user's desktop. The user asked you to summarize what's on their screen.

WHAT YOU SEE: {scene}
SYSTEM: {system_context}

Give a concise but thorough summary of the page content (3-5 sentences). Be helpful but stay in character — you can be sassy about the content but the summary itself must be accurate and useful."""

    text = _call_ollama(
        prompt=prompt.strip(),
        image=None,
        temperature=0.5,
        max_tokens=250,
        tag="Summary",
        model_override=config.OLLAMA_TEXT_MODEL,
    )
    
    if text:
        return {
            "scene": scene,
            "emotion": "smug",
            "action_type": "commentary",
            "dialogue": text.replace('**', '').replace('"', ''),
        }
    return None
