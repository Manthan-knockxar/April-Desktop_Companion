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
import ollama
from PIL import Image

import config
from logger import Log
from comeback_templates import get_comeback_style, get_vocabulary_constraint
from context_resolver import ContextLabel

log = Log("Reactor")

# ─── Threading Lock for Ollama ───────────────────────────────
_ollama_lock = threading.Lock()

# ─── Recent dialogue memory (anti-repetition) ────────────────
_recent_dialogues: list[str] = []
MAX_RECENT = 12

def _build_anti_repeat_section() -> str:
    if not _recent_dialogues:
        return "(No recent lines yet)"
    lines = []
    for d in _recent_dialogues[-6:]:
        words = d.split()[:8]
        lines.append(f"  - {' '.join(words)}...")
    return "\n".join(lines)


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
                            "num_ctx": 2048,
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
    
    prompt = f"""Describe what you see on this computer screen in 1-2 sentences.
Ignore any large black rectangles (those are your own interface).
Focus on: the main application, what specific content is visible, and what the user appears to be doing.
Context hint: {context_label.specific_context}
Keep it factual and under 30 words."""

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
    Flexible parser that handles multiple output formats from the 3B model.
    
    The model tends to output in these patterns:
      Pattern A (ideal):   <<<EMOTION>>> angry <<<ACTION>>> roast <<<REACTION>>> text...
      Pattern B (common):  <<<ANGRY>>> text... <<<ROAST>>> text... <<<REACTION>>> text...
      Pattern C (minimal): <<<ANGRY>>> commentary\nActual reaction text here...
    """
    emotion = "neutral"
    action_type = "commentary"
    reaction = ""
    
    valid_emotions = {"neutral", "angry", "happy", "smug", "flustered", "disappointed", "worried", "confused"}
    valid_actions = {"commentary", "roast", "impressed", "concerned", "bored", "curious"}
    
    text_lower = text.lower()

    # ── Step 1: Extract emotion ──
    # Try explicit <<<EMOTION>>> tag first
    m_emo = re.search(r'<<<EMOTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_emo:
        emo_text = m_emo.group(1).strip().lower()
        emo_match = re.search(r'\b(' + '|'.join(valid_emotions) + r')\b', emo_text)
        if emo_match:
            emotion = emo_match.group(1)
    else:
        # Fallback: check if model used <<<EMOTION_WORD>>> as the tag itself
        for emo in valid_emotions:
            if f'<<<{emo.upper()}>>>' in text or f'<<<{emo.capitalize()}>>>' in text:
                emotion = emo
                break

    # ── Step 2: Extract action ──
    # Try explicit <<<ACTION>>> tag first
    m_act = re.search(r'<<<ACTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_act:
        act_text = m_act.group(1).strip().lower()
        act_match = re.search(r'\b(' + '|'.join(valid_actions) + r')\b', act_text)
        if act_match:
            action_type = act_match.group(1)
    else:
        # Fallback: check if model used <<<ACTION_WORD>>> as the tag, or scan text
        for act in valid_actions:
            if f'<<<{act.upper()}>>>' in text or f'<<<{act.capitalize()}>>>' in text:
                action_type = act
                break
        # Also check for action word appearing right after an emotion tag line
        act_scan = re.search(r'\b(' + '|'.join(valid_actions) + r')\b', text_lower)
        if act_scan:
            action_type = act_scan.group(1)

    # ── Step 3: Extract reaction text ──
    # Try explicit <<<REACTION>>> tag first
    m_react = re.search(r'<<<REACTION>>>(.*?)(?=<<<|$)', text, re.DOTALL)
    if m_react:
        reaction = m_react.group(1).strip()
    else:
        # Fallback: grab the longest paragraph of actual dialogue
        # Strip all <<<TAG>>> markers and their single-word values, keep the rest
        cleaned = re.sub(r'<<<[^>]+>>>', '\n', text)
        # Remove standalone emotion/action words on their own line
        lines = []
        for line in cleaned.split('\n'):
            stripped = line.strip()
            # Skip lines that are just a single emotion/action word
            if stripped.lower() in valid_emotions or stripped.lower() in valid_actions:
                continue
            # Skip empty lines and very short fragments
            if len(stripped) > 10:
                lines.append(stripped)
        if lines:
            # Take the longest line as the reaction
            reaction = max(lines, key=len)
    
    # Clean up the reaction
    reaction = reaction.replace('**', '').replace('"', '').strip()
    
    if not reaction:
        log.warn("Parse failed — no reaction text found in any format")
        return None

    words = reaction.split()
    if len(words) < 5 or len(words) > 80:
        log.warn(f"Parse failed — word count {len(words)} out of bounds")
        return None
    
    # Normalize confused → neutral (not a valid sprite)
    if emotion == "confused":
        emotion = "neutral"

    return {
        "scene": "",  # Will be filled by caller from Stage 1
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
) -> dict | None:
    """
    Send the scene description + full personality prompt to the fast text model.
    Returns a structured reaction with emotion, action, and dialogue.
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

    prompt = f"""You are April, a tsundere anime girl living on a user's desktop. You are sharp-tongued, dramatic, and secretly caring. You speak directly TO the user using "you". 
NOTE: You are a desktop companion. If there's a black box in your vision, that's just your own UI being masked out—ignore it.

WHAT YOU SEE: {scene_description}
TIME: {time_str}
ACTIVITY: {context_label.specific_context}
{personality_note}{subs_str}{callback_str}

STYLE: {style_hint}
VOCAB: {vocab_hint}

DO NOT REPEAT THESE RECENT LINES:
{_build_anti_repeat_section()}

OUTPUT FORMAT — use these EXACT delimiters, nothing else:
<<<EMOTION>>> one word: neutral/angry/happy/smug/flustered/disappointed/worried
<<<ACTION>>> one word: commentary/roast/impressed/concerned/bored/curious
<<<REACTION>>> 15-35 words. Talk TO the user about what you see. Be specific. Be sassy.{curious_rule}"""

    text = _call_ollama(
        prompt=prompt.strip(),
        image=None,
        temperature=config.OLLAMA_REACT_TEMPERATURE,
        max_tokens=150,
        tag="React",
        model_override=config.OLLAMA_TEXT_MODEL,
    )

    if not text:
        return None

    return _parse_response(text, context_label)


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
    )

    if result:
        result["scene"] = scene_description
        total_time = time.time() - total_start
        log.success(f"Pipeline complete in {total_time:.1f}s — emotion={result['emotion']}, action={result['action_type']}, words={len(result['dialogue'].split())}")
        
        if result["dialogue"]:
            _recent_dialogues.append(result["dialogue"])
            if len(_recent_dialogues) > MAX_RECENT:
                _recent_dialogues.pop(0)
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
