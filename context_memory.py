"""
Context memory system — tracks recent events, affection, reaction streaks,
boredom escalation, and prevents repetitive reactions.
"""
import config


class ContextMemory:
    def __init__(self):
        self.recent_events: list[dict] = []
        self.affection: int = config.AFFECTION_START
        self.roast_streak: int = 0
        self.last_reaction_label: str = ""
        self.last_scene_description: str = ""
        self.cycles_since_last_reaction: int = 0
        self.similar_scene_streak: int = 0  # how many times we've seen a similar scene
        self.total_interactions: int = 0

    def add_event(self, action_type: str, label: str, description: str):
        """Record a new event and update emotional state."""
        event = {
            "action_type": action_type,
            "label": label,
            "description": description,
        }
        self.recent_events.append(event)
        self.total_interactions += 1

        # Trim to window size
        if len(self.recent_events) > config.MEMORY_WINDOW:
            self.recent_events = self.recent_events[-config.MEMORY_WINDOW:]

        # Update affection based on general action types
        if action_type == "impressed":
            self.affection = min(self.affection + 1, config.AFFECTION_MAX)
            self.roast_streak = 0
        elif action_type == "roast":
            self.affection = max(self.affection - 1, config.AFFECTION_MIN)
            self.roast_streak += 1
        elif action_type == "concerned":
            # Concern doesn't change affection but tracks engagement
            self.roast_streak = 0
        else:
            # commentary, bored — neutral
            self.roast_streak = 0

    def set_last_reaction(self, label: str, scene_desc: str = ""):
        """Track what we last reacted to."""
        self.last_reaction_label = label
        self.last_scene_description = scene_desc
        self.cycles_since_last_reaction = 0

    def update_boredom(self, scene_is_similar: bool):
        """
        Track how long the user has been doing the same thing.
        Resets when scene changes significantly.
        """
        if scene_is_similar:
            self.similar_scene_streak += 1
        else:
            self.similar_scene_streak = 0

    def get_emotional_intensity(self) -> str:
        """Get current emotional intensity level."""
        # Boredom overrides other emotions when streak is high
        if self.similar_scene_streak >= 3:
            return "BORED_FRUSTRATED"

        if self.roast_streak >= config.ESCALATION_THRESHOLD:
            return "MAXIMUM_ANGER"
        elif self.roast_streak >= 2:
            return "VERY_ANNOYED"
        elif self.affection >= 7:
            return "SECRETLY_FOND"
        elif self.affection >= 4:
            return "WARMING_UP"
        elif self.affection <= -5:
            return "GENUINELY_MAD"
        else:
            return "DEFAULT_TSUNDERE"

    def get_context_summary(self) -> str:
        """Build a summary string of recent context."""
        if not self.recent_events:
            return "No prior context. This is the first interaction."

        recent = self.recent_events[-5:]
        lines = []
        for evt in recent:
            lines.append(f"- {evt['action_type']}: {evt['description']}")

        summary = "\n".join(lines)
        summary += f"\nAffection level: {self.affection} ({self.get_emotional_intensity()})"
        summary += f"\nRoast streak: {self.roast_streak}"
        if self.similar_scene_streak >= 2:
            summary += f"\nBoredom streak: {self.similar_scene_streak} cycles of same activity"
        return summary

    def should_react(self, action_type: str, scene_is_similar: bool) -> bool:
        """
        Decide if we should generate a reaction.

        Logic:
        - roast/concerned: ALWAYS react
        - impressed: always react
        - commentary with new scene: react
        - commentary with similar scene: react 2-3 times (with escalating
          boredom), then go quiet until something changes
        """
        self.cycles_since_last_reaction += 1

        # Roasts and concerns — always react
        if action_type in ("roast", "concerned"):
            return True

        # Impressed — always react
        if action_type == "impressed":
            return True

        # Commentary — boredom-aware
        if action_type == "commentary":
            if not scene_is_similar:
                # New scene! Always react
                return True

            # Similar scene — allow 5 reactions then start throttling
            if self.similar_scene_streak <= 5:
                return True

            # After 5 similar reactions, only react every 3 cycles
            return self.cycles_since_last_reaction >= 3

        return True
