"""
Context memory system — Session narrative, intent tracking, time-of-day awareness,
and escalating emotion thresholds.
"""
import time
from collections import deque
from datetime import datetime
import json
import os

import config
from datetime import datetime

def get_time_context() -> dict:
    """Phase 4 — Time of Day Awareness."""
    now = datetime.now()
    hour = now.hour
    
    if 0 <= hour < 5:
        period = "late_night"
    elif 5 <= hour < 8:
        period = "early_morning"
    elif 8 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 21:
        period = "evening"
    else:
        period = "night"
        
    is_weekend = now.weekday() >= 5
    day_name = now.strftime("%A")
    time_str = now.strftime("%I:%M %p")
    time_string = f"{time_str} on a {day_name}"
    
    return {
        "hour": hour,
        "period": period,
        "is_weekend": is_weekend,
        "time_string": time_string
    }


class ContextMemory:
    def __init__(self):
        # Base emotional state
        self.affection: int = config.AFFECTION_START
        self.roast_streak: int = 0
        
        # Phase 3 Fields: Session Tracking
        self.current_activity: str = "unknown"
        self.activity_session_start: float = time.time()
        self.previous_activity: str = ""
        self.activity_history: list[tuple] = []  # (activity, start_time, end_time)
        self.session_start: float = time.time()
        self.session_narrative: str = ""
        self.last_seen_apps: dict = {}
        
        # Phase 7 & 8 fields
        self.seen_contexts: set = set()
        self.subtitle_buffer: deque = deque(maxlen=15)
        
        # Legacy fields for throttle logic
        self.cycles_since_last_reaction: int = 0
        self.similar_scene_streak: int = 0
        self.total_interactions: int = 0
        self.recent_events: list[dict] = []
        self.last_reaction_label: str = "commentary"
        self.last_scene_description: str = ""
        
        # Phase 9: Episodic Memory
        self.past_session_summary: str = ""
        self._load_memory()
        
        # April 2.0: Productivity Tracking
        self.productive_minutes: float = 0.0
        self.unproductive_minutes: float = 0.0
        self.longest_productive_streak: float = 0.0   # minutes
        self._current_streak_start: float | None = None
        self._last_tick: float = time.time()
        
        # April 2.0: Action cancellation memory
        self.action_was_cancelled: bool = False  # set True when user cancels an action
        self.cancelled_action_label: str = ""     # what was cancelled

    def _load_memory(self):
        """Loads previous session's narrative to inject into system context."""
        try:
            db_path = "memory_db.json"
            if os.path.exists(db_path):
                with open(db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "last_session_narrative" in data:
                        self.past_session_summary = data["last_session_narrative"]
        except Exception as e:
            pass

    def _save_memory(self):
        """Saves current state so she remembers next time."""
        try:
            db_path = "memory_db.json"
            data = {"last_session_narrative": self.session_narrative}
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            pass

    def update_activity(self, new_intent: str, specific_context: str):
        """Update current activity, log history if changed, rebuild narrative."""
        now = time.time()
        
        if new_intent != self.current_activity:
            # End old activity
            if self.current_activity != "unknown":
                self.activity_history.append((self.current_activity, self.activity_session_start, now))
                if len(self.activity_history) > 20:
                    self.activity_history.pop(0)
            
            # Start new activity
            self.previous_activity = self.current_activity
            self.current_activity = new_intent
            self.activity_session_start = now
            self.last_seen_apps[new_intent] = now
            
            
            # Narrative is now stale, rebuild it
            self.build_session_narrative()
            self._save_memory()

    def get_activity_duration(self) -> int:
        """Minutes spent in current activity."""
        return int((time.time() - self.activity_session_start) / 60)

    def tick_productivity(self, current_intent: str):
        """Called every reaction cycle to accumulate productive/unproductive time."""
        now = time.time()
        elapsed = (now - self._last_tick) / 60.0  # minutes since last tick
        self._last_tick = now
        
        productive_intents = {"productive_coding", "system_admin"}
        unproductive_intents = {"distracted_browsing", "leisure_video", "leisure_gaming"}
        
        if current_intent in productive_intents:
            self.productive_minutes += elapsed
            # Track streak
            if self._current_streak_start is None:
                self._current_streak_start = now
            streak = (now - self._current_streak_start) / 60.0
            if streak > self.longest_productive_streak:
                self.longest_productive_streak = streak
        else:
            if current_intent in unproductive_intents:
                self.unproductive_minutes += elapsed
            # Break streak
            self._current_streak_start = None

    @property
    def focus_score(self) -> float:
        """Focus score from 0.0 to 1.0 — ratio of productive time."""
        total = self.productive_minutes + self.unproductive_minutes
        if total < 1.0:
            return 0.5  # not enough data
        return min(1.0, self.productive_minutes / total)

    def get_daily_report(self) -> str:
        """Generate a narrative daily productivity report."""
        session_mins = int((time.time() - self.session_start) / 60)
        score = self.focus_score
        
        # Grade
        if score >= 0.8:
            grade = "S-Rank"
            comment = "You actually impressed me today. Don't let it go to your head."
        elif score >= 0.6:
            grade = "A-Rank"
            comment = "Decent work. I've seen better, but I've definitely seen worse."
        elif score >= 0.4:
            grade = "B-Rank"
            comment = "Mediocre. You spent half the time goofing off."
        elif score >= 0.2:
            grade = "C-Rank"
            comment = "Pathetic. You call that a work session?"
        else:
            grade = "F-Rank"
            comment = "You literally did nothing productive. I'm disappointed."
        
        report = f"""# April's Daily Report — {datetime.now().strftime('%B %d, %Y')}

Session Duration: {session_mins} minutes
Focus Score: {score:.0%} ({grade})
Productive Time: {self.productive_minutes:.0f} min
Distracted Time: {self.unproductive_minutes:.0f} min  
Longest Focus Streak: {self.longest_productive_streak:.0f} min
Total Interactions: {self.total_interactions}

April's Verdict: {comment}
"""
        return report

    def record_action_cancelled(self, action_label: str):
        """Record that the user cancelled one of April's announced actions."""
        self.action_was_cancelled = True
        self.cancelled_action_label = action_label

    def consume_action_cancelled(self) -> tuple[bool, str]:
        """Check and clear the action-cancelled flag. Returns (was_cancelled, label)."""
        if self.action_was_cancelled:
            self.action_was_cancelled = False
            label = self.cancelled_action_label
            self.cancelled_action_label = ""
            return True, label
        return False, ""

    def should_callback(self, activity: str) -> bool:
        """Returns True if this activity was seen >15 mins ago."""
        if activity in self.last_seen_apps:
            last_seen = self.last_seen_apps[activity]
            if (time.time() - last_seen) > 900:  # 15 minutes
                return True
        return False

    def build_session_narrative(self) -> str:
        """Generates a cohesive chronological narrative of the session."""
        lines = []
        now = time.time()
        session_duration = int((now - self.session_start) / 60)
        
        lines.append(f"April has been watching you for {session_duration} minutes.")
        
        if not self.activity_history:
            lines.append(f"You have been exclusively on {self.current_activity}.")
        else:
            # Describe history
            first_act, first_start, _ = self.activity_history[0]
            lines.append(f"You started on {first_act}.")
            
            if len(self.activity_history) > 1:
                mid_act, _, _ = self.activity_history[-1]
                lines.append(f"You recently switched from {mid_act}.")
                
            duration = self.get_activity_duration()
            lines.append(f"You are currently on {self.current_activity} and have been for {duration} mins.")
            
        self.session_narrative = " ".join(lines)
        return self.session_narrative

    def add_event(self, action_type: str, label: str, description: str):
        """Record immediate reaction events to adjust affection/streaks."""
        self.recent_events.append({"action_type": action_type})
        self.total_interactions += 1
        
        if len(self.recent_events) > config.MEMORY_WINDOW:
            self.recent_events.pop(0)

        if action_type == "impressed":
            self.affection = min(self.affection + 1, config.AFFECTION_MAX)
            self.roast_streak = 0
        elif action_type == "roast":
            self.affection = max(self.affection - 1, config.AFFECTION_MIN)
            self.roast_streak += 1
        elif action_type in ("concerned", "curious"):
            self.roast_streak = 0
        else:
            self.roast_streak = 0

    def update_boredom(self, scene_is_similar: bool):
        if scene_is_similar:
            self.similar_scene_streak += 1
        else:
            self.similar_scene_streak = 0

    def set_last_reaction(self, action_type: str, scene: str):
        self.last_reaction_label = action_type
        self.last_scene_description = scene
        self.cycles_since_last_reaction = 0

    def get_emotional_intensity(self) -> str:
        """Determines overriding emotional bound based on complex state logic."""
        duration = self.get_activity_duration()
        time_ctx = get_time_context()
        
        # 1. Boredom overrides
        if self.current_activity == "idle" and duration > 10:
            return "BORED_FRUSTRATED"
        if self.similar_scene_streak >= 5:
            return "BORED_FRUSTRATED"
            
        # 2. Productive vs Leisure specific flows
        if self.current_activity == "productive_coding" and duration > 45:
            return "CONCERNED_FOND"
            
        if self.current_activity == "leisure_video":
            # Did they work hard before this?
            worked_hard = any(act == "productive_coding" and (end - start)/60 > 60 for act, start, end in self.activity_history)
            if worked_hard:
                return "WARMING_UP"
                
        # 3. Schedule overrides
        if self.current_activity == "distracted_browsing" and time_ctx["period"] in ("morning", "afternoon") and not time_ctx["is_weekend"]:
            # Fast escalation during work hours
            if self.roast_streak >= 1:
                return "MAXIMUM_ANGER"
                
        if time_ctx["period"] in ("late_night", "early_morning"):
            return "LATE_NIGHT_MODE"
            
        # 4. Standard Streak/Affection logic
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
            
        return "DEFAULT_TSUNDERE"

    def get_context_summary(self) -> str:
        """Returns the Phase 3 formatted narrative for the prompt."""
        duration = self.get_activity_duration()
        intensity = self.get_emotional_intensity()
        
        summary = self.session_narrative
        if not summary:
            summary = self.build_session_narrative()
            
        past_note = f"\nPast Memory: {self.past_session_summary}" if self.past_session_summary else ""
            
        return f"{summary}{past_note}\nAffection: {self.affection}\nIntensity Label: {intensity}\nDuration on current task: {duration} mins"

    def should_react(self, action_type: str, scene_is_similar: bool) -> bool:
        """Rate limiting for commentary to reduce spam."""
        self.cycles_since_last_reaction += 1

        if action_type in ("roast", "concerned", "impressed", "curious"):
            return True

        if action_type == "commentary":
            if not scene_is_similar:
                return True
            if self.similar_scene_streak <= 5:
                return True
            return self.cycles_since_last_reaction >= 3

        return True
