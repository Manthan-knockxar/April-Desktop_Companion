"""
Scene Reactor — merged vision + dialogue in a SINGLE local model call.
Sends screenshot to llama3.2-vision (via Ollama), gets scene description AND
tsundere reaction in one shot.

Runs 100% locally — no cloud API, no rate limits, no API keys.
Now generalized for ANY screen content — desktop, browsers, apps, games, etc.
"""
import io
import time
import re
import random
import threading
import ollama
from PIL import Image
import config
from logger import Log

log = Log("Reactor")

# ─── Threading Lock for Ollama ───────────────────────────────
_ollama_lock = threading.Lock()


# ─── Recent dialogue memory (anti-repetition) ────────────────
_recent_dialogues: list[str] = []
MAX_RECENT = 12

INTENSITY_MODIFIERS = {
    "MAXIMUM_ANGER": "You are FURIOUS. Be extremely dramatic, yell at them.",
    "VERY_ANNOYED": "You are very annoyed. Show escalating frustration.",
    "SECRETLY_FOND": "You secretly like this person but would NEVER admit it. Compliment then immediately backtrack.",
    "WARMING_UP": "Slightly less hostile. Tiny cracks of kindness showing. Maybe give a genuine tip.",
    "GENUINELY_MAD": "Genuinely upset. Be cold and cutting.",
    "BORED_FRUSTRATED": "You are BORED OUT OF YOUR MIND. Dramatically demand they do something else. Be creative about HOW bored you are.",
    "DEFAULT_TSUNDERE": "Standard tsundere. A mix of insults, hidden care, and occasionally helpful observations.",
}

# Reaction variety templates — the model picks a style each time
REACTION_STYLES = [
    "Give a sarcastic observation about what they're doing on their screen",
    "Reluctantly give them a helpful tip while pretending you don't care",
    "React dramatically to something specific visible on screen",
    "Judge their productivity (or lack thereof) with tsundere flair",
    "Comment on their browsing habits, app choices, or workflow",
    "Express concern about their screen time but disguise it as an insult",
    "Challenge them to do something more impressive",
    "Make a witty comparison or metaphor about their current activity",
    "Roast their desktop organization or window management",
    "Give unsolicited advice about what they should be doing instead",
]


def _build_anti_repeat_section() -> str:
    if not _recent_dialogues:
        return ""
    lines = "\n".join(f'  - "{d}"' for d in _recent_dialogues[-6:])
    return f"""
YOUR RECENT LINES (DO NOT repeat these themes, phrases, or sentence structures):
{lines}

CRITICAL: Say something COMPLETELY DIFFERENT. Different opening word, different topic, different tone."""


def _downscale_image(image: Image.Image) -> Image.Image:
    """Downscale screenshot for faster local inference. Saves VRAM."""
    target_w = config.DOWNSCALE_WIDTH
    target_h = config.DOWNSCALE_HEIGHT
    if image.width > target_w or image.height > target_h:
        log.debug(f"Downscaling {image.width}x{image.height} → {target_w}x{target_h}")
        image = image.resize((target_w, target_h), Image.LANCZOS)
    return image


def _pil_to_bytes(image: Image.Image) -> bytes:
    """Convert PIL Image to JPEG bytes for Ollama vision input.
    JPEG is ~4x smaller than PNG, speeding up payload transfer."""
    buf = io.BytesIO()
    if image.mode == "RGBA":
        image = image.convert("RGB")
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _call_ollama(prompt: str, image: Image.Image | None = None,
                 temperature: float = 0.95, max_tokens: int = 400,
                 tag: str = "Ollama") -> str | None:
    """
    Unified call to Ollama local model with retry logic.
    Supports text-only and multimodal (image) requests.
    Returns the response text or None on failure.
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

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"[{tag}] Calling {config.OLLAMA_MODEL} (attempt {attempt}/{max_retries})")
            
            with log.timed(f"[{tag}] Waiting for Ollama lock"):
                acquired = _ollama_lock.acquire(timeout=90)
            if not acquired:
                log.error(f"[{tag}] Ollama lock acquisition timed out (90s) — skipping")
                return None
            try:
                with log.timed(f"[{tag}] Local inference"):
                    response = ollama.chat(
                        model=config.OLLAMA_MODEL,
                        messages=messages,
                        options={
                            "temperature": temperature,
                            "num_predict": max_tokens,
                            "num_ctx": 2048,
                            "num_gpu": config.OLLAMA_NUM_GPU,
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
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                log.error(f"[{tag}] Cannot reach Ollama at {config.OLLAMA_BASE_URL} — is it running?")
            if attempt < max_retries:
                time.sleep(3)

    log.error(f"[{tag}] All {max_retries} attempts failed")
    return None


def analyze_and_react(
    image: Image.Image,
    context_summary: str,
    emotional_intensity: str,
    boredom_count: int = 0,
    accumulated_scenes: list[str] | None = None,
    system_context: str = "",
    enriched_window: str = "",
) -> dict | None:
    """
    Send screenshot to local llama3.2-vision and get BOTH scene analysis AND
    tsundere dialogue in a single inference call. Returns dict with 'scene',
    'dialogue', 'emotion', and 'action_type' keys, or None on failure.
    """
    log.info(f"Starting reaction — intensity={emotional_intensity}, boredom={boredom_count}, "
             f"accumulated_scenes={len(accumulated_scenes or [])}, anti_repeat_pool={len(_recent_dialogues)}")

    # Downscale for performance
    image = _downscale_image(image)

    intensity_note = INTENSITY_MODIFIERS.get(emotional_intensity, INTENSITY_MODIFIERS["DEFAULT_TSUNDERE"])
    anti_repeat = _build_anti_repeat_section()

    # Pick a reaction style based on cycle count for variety
    style_hint = random.choice(REACTION_STYLES)
    log.debug(f"Style hint: \"{style_hint}\"")

    # Build accumulated context from silent captures
    accumulated_context = ""
    if accumulated_scenes:
        accumulated_context = "\nRECENT OBSERVATIONS (what you've been silently watching):\n"
        for i, s in enumerate(accumulated_scenes[-4:], 1):
            accumulated_context += f"  {i}. {s}\n"
        accumulated_context += "Use these observations to give a more informed, context-rich reaction.\n"

    # Add boredom context
    boredom_note = ""
    if boredom_count >= 3:
        boredom_note = (
            "\n⚠️ BOREDOM ALERT: The user has been doing THE SAME THING for a while now. "
            "Express extreme frustration OR give them a creative suggestion of what to do instead."
        )
    elif boredom_count >= 2:
        boredom_note = (
            "\n⚠️ The user is still doing the same thing. Show growing impatience "
            "or suggest they try something different."
        )

    prompt = f"""You are April, a tsundere anime girl desktop companion. You watch the user's screen and react.

ACTIVITY: {enriched_window}
STYLE: {style_hint}
MOOD: {intensity_note}
{boredom_note}

RULES:
- You are April. Use "I"/"me". Sharp-tongued, dramatic, secretly caring.
- React to VISUAL DETAILS on screen, not just the app/title name.
- The user is a VIEWER of content unless they are clearly coding/gaming themselves.
- Keep REACTION to 1-2 sentences. Be snarky, specific, and creative.
- NEVER start with "Seriously?" or end with "it's not like I care".
- NEVER repeat the SCENE text in your REACTION.
{accumulated_context}
SYSTEM: {system_context}
{anti_repeat}

Respond with EXACTLY these 4 lines and NOTHING ELSE — no headers, no markdown, no extra commentary:
SCENE: [1-2 sentence factual description of what you see on screen]
EMOTION: [ONE word: neutral, angry, happy, smug, flustered, disappointed, worried]
ACTION: [ONE word: commentary, roast, impressed, concerned, bored]
REACTION: [Your spoken dialogue — snarky comment directed at the user]

If screen is black: SCENE: idle desktop / EMOTION: disappointed / ACTION: bored / REACTION: [react to emptiness]"""

    text = _call_ollama(
        prompt=prompt,
        image=image,
        temperature=config.OLLAMA_REACT_TEMPERATURE,
        max_tokens=400,
        tag="React",
    )

    if text is None:
        log.error("Local inference failed — no response")
        return None

    # Parse the structured response
    result = _parse_response(text)
    if result:
        # ── Hard duplicate check: reject if too similar to recent dialogue ──
        new_dialogue = result["dialogue"].lower()
        new_words = set(new_dialogue.split())
        for prev in _recent_dialogues:
            prev_words = set(prev.lower().split())
            if len(new_words) > 5 and len(prev_words) > 5:
                overlap = len(new_words & prev_words) / max(len(new_words), 1)
                if overlap > 0.50:
                    log.warn(f"Rejected duplicate dialogue ({overlap:.0%} overlap with recent): "
                             f"\"{result['dialogue'][:60]}...\"")
                    result = None
                    break

    if result:
        log.success(f"Reaction — emotion={result['emotion']}, "
                     f"action={result['action_type']}, dialogue_len={len(result['dialogue'])}")
        # Track dialogue for anti-repetition
        if result["dialogue"]:
            _recent_dialogues.append(result["dialogue"])
            if len(_recent_dialogues) > MAX_RECENT:
                _recent_dialogues.pop(0)
        return result
    else:
        log.warn("Response failed parsing")
        return None


def analyze_scene_silent(image: Image.Image) -> str | None:
    """
    Quick scene-only analysis for context gathering (no dialogue).
    Used during silent capture cycles between reactions.
    """
    _silent_log = Log("Silent")

    # Downscale for performance
    image = _downscale_image(image)

    prompt = (
        "Describe what the user is doing on their computer in 1 short sentence. "
        "Include: what app or website is visible, what activity they seem to be doing. "
        "If the screen is black or locked, respond with NOTHING_NOTABLE."
    )

    text = _call_ollama(
        prompt=prompt,
        image=image,
        temperature=config.OLLAMA_VISION_TEMPERATURE,
        max_tokens=100,
        tag="Silent",
    )

    if text is None:
        _silent_log.error("Silent context inference failed")
        return None

    if "NOTHING_NOTABLE" in text.upper():
        _silent_log.debug("Screen is blank/locked — nothing notable")
        return None

    _silent_log.success(f"Context: \"{text[:100]}\"")
    return text


def _parse_response(text: str) -> dict | None:
    """Parse the SCENE:/EMOTION:/ACTION:/REACTION: formatted response.
    
    Handles model quirks:
    - Markdown bold wrappers (**SCENE:**)
    - Extra headers/commentary after the 4 fields
    - Multi-word emotion lines ("I'm feeling smug" → "smug")
    - Action aliases ("comment" → "commentary")
    """
    scene = ""
    emotion = "neutral"
    action_type = "commentary"
    reaction = ""

    valid_emotions = {"neutral", "angry", "happy", "smug", "flustered", "disappointed", "worried"}
    valid_actions = {"commentary", "roast", "impressed", "concerned", "bored"}
    
    # Aliases for common model action typos
    action_aliases = {
        "comment": "commentary", "commenting": "commentary",
        "roasting": "roast", "impress": "impressed",
        "concern": "concerned", "concerning": "concerned",
        "bore": "bored", "boring": "bored",
    }

    # ── Pre-clean: strip markdown headers the model sometimes wraps output in ──
    # Remove lines like "**April's Response**", "**STRETCHING GUIDELINES:**", etc.
    text = re.sub(r'^\s*\*{2,}[^*\n]+\*{2,}\s*$', '', text, flags=re.MULTILINE)
    # Remove everything after recognized "extra" sections the model invents
    for cutoff in ["STRETCHING GUIDELINES", "FILLING THE SPACE", "REVIEWER", "Note:", "---"]:
        idx = text.upper().find(cutoff.upper())
        if idx > 0:
            text = text[:idx]

    # ── Regex extraction (handles **bold** field names) ──
    scene_m = re.search(r'\*{0,2}SCENE\*{0,2}[:\s]\s*(.*?)(?=\n\s*\*{0,2}EMOTION|\Z)', text, re.IGNORECASE | re.DOTALL)
    emotion_m = re.search(r'\*{0,2}EMOTION\*{0,2}[:\s]\s*([^\n]+)', text, re.IGNORECASE)
    action_m = re.search(r'\*{0,2}ACTION\*{0,2}[:\s]\s*([^\n]+)', text, re.IGNORECASE)
    react_m = re.search(r'\*{0,2}REACTION\*{0,2}[:\s]\s*(.*?)(?=\n\s*(?:I hope|Let me|Here is|Note:|\*{2})|\n\n|\Z)', text, re.IGNORECASE | re.DOTALL)

    if scene_m:
        scene = scene_m.group(1).strip().strip('"\'*')
    
    if emotion_m:
        raw = emotion_m.group(1)
        cleaned = raw.replace('*', '').replace('"', '').replace("'", '').strip().lower()
        # Use regex to find the first valid emotion keyword ANYWHERE in the line
        emo_match = re.search(r'\b(' + '|'.join(valid_emotions) + r')\b', cleaned)
        if emo_match:
            emotion = emo_match.group(1)
        else:
            log.debug(f"Invalid emotion '{cleaned}', defaulting to 'neutral'")
            
    if action_m:
        raw = action_m.group(1)
        parsed = raw.replace('*', '').replace('"', '').replace("'", '').strip().lower()
        if parsed in valid_actions:
            action_type = parsed
        elif parsed in action_aliases:
            action_type = action_aliases[parsed]
            log.debug(f"Action alias '{parsed}' → '{action_type}'")
        else:
            # Try to find a valid action keyword in the line
            act_match = re.search(r'\b(' + '|'.join(valid_actions) + r')\b', parsed)
            if act_match:
                action_type = act_match.group(1)
            else:
                log.debug(f"Invalid action '{parsed}', defaulting to 'commentary'")
            
    if react_m:
        reaction = react_m.group(1).strip().strip('"\'*')
        # Clean stray quotes that might have been stranded due to stripping
        if reaction.startswith('"') and reaction.endswith('"'):
            reaction = reaction[1:-1]
        # Strip markdown formatting that sounds terrible in TTS
        reaction = reaction.replace('**', '').replace('__', '')
        reaction = reaction.replace('*', '').replace('_', '')
        # Strip trailing "– April" or similar attribution
        reaction = re.sub(r'\s*[–—-]\s*April\s*$', '', reaction, flags=re.IGNORECASE)
        reaction = reaction.strip()

    if not scene or not reaction or reaction.lower() == "none":
        log.warn("Parse failed — no usable reaction text")
        log.debug(f"Raw model output was: {text[:500]}")
        return None

    # ── Validate: REACTION must be spoken dialogue, not a scene description ──
    _desc_prefixes = (
        "the user is", "the user has", "the user's", "the screen shows",
        "you are in", "you're in", "you have", "you're looking at",
        "you're staring at", "the user appears",
    )
    reaction_lower = reaction.lower().strip()
    is_description = any(reaction_lower.startswith(p) for p in _desc_prefixes)

    if is_description:
        log.warn(f"Rejected: reaction starts with description prefix → \"{reaction[:60]}...\"")
        return None

    # Also check if reaction is suspiciously similar to the scene (relaxed threshold)
    if scene and reaction:
        scene_words = set(scene.lower().split())
        react_words = set(reaction.lower().split())
        if len(scene_words) > 5 and len(react_words) > 5:
            overlap = len(scene_words & react_words) / max(len(react_words), 1)
            if overlap > 0.70:
                log.warn(f"Rejected: reaction overlaps scene by {overlap:.0%} → \"{reaction[:60]}...\"")
                return None
            elif overlap > 0.4:
                log.debug(f"Scene/reaction overlap: {overlap:.0%} (within threshold)")

    return {
        "scene": scene,
        "emotion": emotion,
        "action_type": action_type,
        "dialogue": reaction,
    }


def answer_user_question(
    question: str,
    image: Image.Image,
    system_context: str = "",
    context_summary: str = "",
) -> dict | None:
    """
    Answer a direct user question with tsundere personality.
    Uses the current screenshot + system context for awareness.
    Returns dict with 'scene', 'dialogue', 'emotion', 'action_type'.
    """
    _qa_log = Log("Q&A")
    _qa_log.info(f"Answering question: \"{question[:80]}\"")

    image = _downscale_image(image)
    anti_repeat = _build_anti_repeat_section()

    prompt = f"""You are April, a tsundere anime girl who lives on someone's desktop as their AI companion.

PERSONALITY:
- Your name is April. Always use "I" or "me".
- Sharp-tongued but secretly helpful. You WILL answer their question properly.
- Even when helping, you wrap it in tsundere attitude — reluctant help, backhanded compliments
- Keep your answer concise but complete: 1-3 sentences MAX
- If the question is about something on their screen, look at the screenshot carefully

The user is asking you a DIRECT QUESTION. You MUST answer it helpfully (even if you pretend to be annoyed about it).

SYSTEM CONTEXT:
{system_context}

{context_summary}
{anti_repeat}

USER'S QUESTION: "{question}"

TASK: Look at the screenshot and answer their question. Respond with EXACTLY this format:
SCENE: [brief factual description of what's on screen]
EMOTION: [Pick ONE: neutral, angry, happy, smug, flustered, disappointed, worried]
ACTION: [Pick ONE: commentary, roast, impressed, concerned, bored]
REACTION: [Your spoken answer to their question — this is what you SAY OUT LOUD]

⚠️ REACTION must be YOUR SPOKEN WORDS answering their question.
NOT a description of the screen. You are TALKING to the user.
Example: "Ugh, fine. You need to declare a 2D array first, then use nested for-loops. It's basic stuff, look it up!"
BAD example: "The user is looking at Dev-C++ with an empty file" ← NEVER do this."""

    text = _call_ollama(
        prompt=prompt,
        image=image,
        temperature=config.OLLAMA_REACT_TEMPERATURE,
        max_tokens=500,
        tag="Q&A",
    )

    if text is None:
        _qa_log.error("Q&A inference failed")
        return None

    result = _parse_response(text)
    if result:
        _qa_log.success(f"Answered — emotion={result['emotion']}, "
                        f"dialogue: \"{result['dialogue'][:80]}\"")
        if result["dialogue"]:
            _recent_dialogues.append(result["dialogue"])
            if len(_recent_dialogues) > MAX_RECENT:
                _recent_dialogues.pop(0)
        return result
    else:
        _qa_log.warn("Q&A response failed parsing")
        return None
