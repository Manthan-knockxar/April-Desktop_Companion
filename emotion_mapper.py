"""
Emotion mapper — maps emotional state + dialogue text to the correct
Noraneko Sabrina sprite filename.

Sprite naming convention (Casual outfit):
  Sabby_Casual_{expression}.png

Expressions:
  Open              — eyes open, neutral mouth (neutral/talking)
  Smile             — eyes open, smiling (happy)
  Frown             — eyes open, frowning (angry)
  Closed_Open       — eyes closed, mouth open (talking animated)
  Closed_Smile      — eyes closed, smiling (smug)
  Closed_Frown      — eyes closed, frowning (disappointed)
  *_Blush           — any of above + blushing (flustered/tsundere)
"""

import re

# ─── Emotion → Sprite expression mapping ─────────────────────

# Primary expressions (mouth closed = idle, mouth open = talking)
EMOTION_SPRITES = {
    # emotion_tag: (idle_expression, talking_expression)
    "neutral":       ("Open",         "Closed_Open"),
    "angry":         ("Frown",        "Closed_Frown"),
    "happy":         ("Smile",        "Closed_Smile"),
    "smug":          ("Closed_Smile", "Smile"),
    "flustered":     ("Open_Blush",   "Closed_Open_Blush"),
    "disappointed":  ("Closed_Frown", "Frown"),
    "worried":       ("Open_Blush",   "Closed_Open_Blush"),
}

# ─── Blush keyword triggers ──────────────────────────────────
# If dialogue contains these patterns, force blush variant
BLUSH_TRIGGERS = [
    r"\bi-i\b", r"\bn-not?\b", r"\bhmph\b", r"\bb-b", r"\bstupid\b.*\bcare\b",
    r"\bnot like i\b", r"\bdon'?t get the wrong idea\b", r"\bidiot\b",
    r"\bb-baka\b", r"\bblush", r"\bflustered\b", r"\bembarrass",
    r"\bw-what\b", r"\bw-why\b", r"\bshut up\b",
]

# ─── Action-type → emotion fallback ──────────────────────────
ACTION_EMOTION_MAP = {
    "roast":      "angry",
    "concerned":  "worried",
    "impressed":  "flustered",   # secretly impressed → tsundere blush
    "commentary": "neutral",
    "bored":      "disappointed",
}

# ─── Emotional intensity overrides ────────────────────────────
INTENSITY_EMOTION_MAP = {
    "MAXIMUM_ANGER":   "angry",
    "VERY_ANNOYED":    "angry",
    "GENUINELY_MAD":   "disappointed",
    "SECRETLY_FOND":   "flustered",
    "WARMING_UP":      "happy",
    "BORED_FRUSTRATED": "disappointed",
    "DEFAULT_TSUNDERE": None,  # use action-based mapping
}


def _has_blush_trigger(dialogue: str) -> bool:
    """Check if dialogue text contains tsundere blush trigger words."""
    text = dialogue.lower()
    return any(re.search(pattern, text) for pattern in BLUSH_TRIGGERS)


def _add_blush(expression: str) -> str:
    """Add _Blush suffix to an expression if not already present."""
    if expression.endswith("_Blush"):
        return expression
    return f"{expression}_Blush"


def get_sprite_expression(
    emotion: str | None = None,
    action_type: str = "commentary",
    emotional_intensity: str = "DEFAULT_TSUNDERE",
    dialogue: str = "",
    is_talking: bool = False,
) -> str:
    """
    Determine the correct sprite expression based on all available context.

    Returns the expression suffix (e.g., "Frown_Blush") to be used in the
    sprite filename: Sabby_Casual_{expression}.png

    Priority:
      1. LLM-provided emotion tag (if valid)
      2. Emotional intensity override
      3. Action-type fallback
      4. Blush trigger from dialogue text (overlaid on any of the above)
    """
    # 1. Try LLM-provided emotion
    resolved_emotion = None
    if emotion and emotion.lower() in EMOTION_SPRITES:
        resolved_emotion = emotion.lower()

    # 2. Try intensity override
    if not resolved_emotion:
        intensity_override = INTENSITY_EMOTION_MAP.get(emotional_intensity)
        if intensity_override:
            resolved_emotion = intensity_override

    # 3. Fallback to action type
    if not resolved_emotion:
        resolved_emotion = ACTION_EMOTION_MAP.get(action_type, "neutral")

    # Get the expression pair (idle, talking)
    idle_expr, talk_expr = EMOTION_SPRITES.get(
        resolved_emotion, EMOTION_SPRITES["neutral"]
    )

    # Pick based on talking state
    expression = talk_expr if is_talking else idle_expr

    # 4. Apply blush override from dialogue keywords
    if _has_blush_trigger(dialogue) and not expression.endswith("_Blush"):
        expression = _add_blush(expression)

    return expression


def get_sprite_filename(expression: str) -> str:
    """Convert expression to full sprite filename."""
    return f"Sabby_Casual_{expression}.png"


def map_result_to_emotion(result: dict) -> str:
    """
    Extract emotion from a scene_reactor result dict.
    Falls back to action-type mapping if no explicit emotion provided.
    """
    # Check for explicit emotion from LLM
    emotion = result.get("emotion", "").lower().strip()
    if emotion in EMOTION_SPRITES:
        return emotion

    # Fallback: infer from dialogue keywords
    dialogue = result.get("dialogue", "")
    if _has_blush_trigger(dialogue):
        return "flustered"

    return "neutral"
