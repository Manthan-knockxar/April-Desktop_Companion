import re
from dataclasses import dataclass
from typing import Any

from logger import Log

log = Log("ContextResolver")

@dataclass
class ContextLabel:
    category: str
    specific_context: str
    focus_instruction: str
    intent: str


# ─── Context Resolution Rules ────────────────────────────────
# Ordered top to bottom, first match wins.

CONTEXT_RULES: list[dict[str, Any]] = [
    {
        "process_patterns": ["chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)leetcode"],
        "category": "coding",
        "intent": "productive_coding",
        "specific_context_template": "LeetCode — {title}",
        "focus_instruction_template": "The user is solving a LeetCode problem. The problem name is visible in the title. Look at their code editor: What is their approach? Brute force or optimized? What language? How complete is the solution? Any visible errors?"
    },
    {
        "process_patterns": ["code", "pycharm", "idea164", "devenv", "webstorm", "clion"],
        "title_patterns": [r".*"],
        "category": "coding",
        "intent": "productive_coding",
        "specific_context_template": "IDE/Editor — {title}",
        "focus_instruction_template": "The user is writing code. Identify the language from syntax. What are they building? Are there visible errors, red underlines, debug panels, or terminal output? What's the state of their work — starting out, mid-implementation, debugging?"
    },
    {
        "process_patterns": ["chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)github"],
        "category": "coding",
        "intent": "productive_coding",
        "specific_context_template": "GitHub — {title}",
        "focus_instruction_template": "The user is on GitHub. Are they reading a repo, reviewing a PR, looking at issues, or browsing code? What repo or file is visible?"
    },
    {
        "process_patterns": ["chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)youtube"],
        "category": "video",
        "intent": "leisure_video",
        "specific_context_template": "YouTube Video — {title}",
        "focus_instruction_template": "The user is watching YouTube. Look at the video frame — what is the content? Tutorial, entertainment, music, gaming? What's the visual energy of the current frame?"
    },
    {
        "process_patterns": ["netflix", "chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)netflix"],
        "category": "video",
        "intent": "leisure_video",
        "specific_context_template": "Netflix Show — {title}",
        "focus_instruction_template": "The user is watching Netflix. What show or movie appears to be playing? Look at the scene — characters, setting, emotional tone of the current moment."
    },
    {
        "process_patterns": ["chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)crunchyroll|funimation|hidive"],
        "category": "video",
        "intent": "leisure_video",
        "specific_context_template": "Anime Stream — {title}",
        "focus_instruction_template": "The user is watching anime. Look at the current frame — what kind of scene is this? Action, emotional, comedic? Who appears to be on screen? What's the visual intensity?"
    },
    {
        "process_patterns": ["steam", "epicgameslauncher", "valkyrie", "genshinimpact", "honkaistarrail"],
        "title_patterns": [r".*"],
        "category": "gaming",
        "intent": "leisure_gaming",
        "specific_context_template": "Gaming — {title}",
        "focus_instruction_template": "The user is playing a game. What game is on screen? What's happening right now — exploration, combat, menu, cutscene, inventory? Do they look like they're winning or struggling?"
    },
    {
        "process_patterns": ["discord"],
        "title_patterns": [r".*"],
        "category": "communication",
        "intent": "communication",
        "specific_context_template": "Discord — {title}",
        "focus_instruction_template": "The user has Discord open. Are they in a voice channel, reading messages, or in a server? What server or channel is visible if readable?"
    },
    {
        "process_patterns": ["explorer", "taskmgr", "systemsettings"],
        "title_patterns": [r".*"],
        "category": "system",
        "intent": "system_admin",
        "specific_context_template": "System Tools — {title}",
        "focus_instruction_template": "The user is doing system work. What folder are they in or what system tool is open? What does the content suggest they're looking for or managing?"
    },
    {
        "process_patterns": ["chrome", "msedge", "firefox", "opera", "brave"],
        "title_patterns": [r"(?i)twitter|x|reddit|instagram|tiktok|facebook"],
        "category": "browsing",
        "intent": "distracted_browsing",
        "specific_context_template": "Social Media — {title}",
        "focus_instruction_template": "The user is on social media. What content is visible? Are they scrolling, reading a post, or watching embedded media?"
    },
    # UNIVERSAL FALLBACK
    {
        "process_patterns": [r".*"],
        "title_patterns": [r".*"],
        "category": "unknown",
        "intent": "unknown",
        "specific_context_template": "Unknown Application — {title} ({exe})",
        "focus_instruction_template": "Describe what the user is doing. What application or content is visible? What specifically are they looking at or working on right now?"
    }
]

# ─── April's Personal Opinions ───────────────────────────────
APRIL_OPINIONS = {
    "dark_mode": "April approves of dark mode. She considers this baseline competence.",
    "light_mode": "April finds this visually offensive and will say so.",
    "terminal": "April secretly respects people who use terminal. She'd never admit it.",
    "leetcode": "April thinks LeetCode grinding is simultaneously admirable and sad.",
    "youtube_work_hours": "April considers this a personal betrayal.",
    "late_night_coding": "April has a soft spot for people debugging at midnight. She considers this a kindred spirit moment but will not show it.",
    "social_media_work_hours": "April finds this behavior deeply disappointing.",
    "multiple_monitors": "April thinks this is actually impressive setup but calls it overcompensation."
}


def _matches_pattern(text: str, patterns: list[str]) -> bool:
    """Check if the text matches any of the given regex/substring patterns."""
    text = text.lower()
    for pat in patterns:
        if pat == ".*":
            return True
        # substring for process names
        if pat.lower() in text:
            return True
        # regex for titles
        try:
            if re.search(pat, text, re.IGNORECASE):
                return True
        except re.error:
            pass
    return False

def resolve(title_text: str, process_name: str) -> ContextLabel:
    """
    Given the active window's title and process, determine intent, 
    generate specific contexts and output a focus instruction for the VLM.
    """
    for rule in CONTEXT_RULES:
        if _matches_pattern(process_name, rule["process_patterns"]) and \
           _matches_pattern(title_text, rule["title_patterns"]):
            
            ctx = rule["specific_context_template"].format(
                title=title_text if title_text else "Unknown",
                exe=process_name
            )
            
            return ContextLabel(
                category=rule["category"],
                specific_context=ctx,
                focus_instruction=rule["focus_instruction_template"],
                intent=rule["intent"]
            )
            
    # Should never hit here due to universal fallback rule, but just in case
    return ContextLabel(
        category="unknown",
        specific_context=f"Unknown App — {title_text}",
        focus_instruction="Describe what the user is doing.",
        intent="unknown"
    )

if __name__ == "__main__":
    test_cases = [
        ("Two Sum - LeetCode", "chrome.exe"),
        ("main.py - Visual Studio Code", "code.exe"),
        ("One Piece Episode 1015 - Crunchyroll", "firefox.exe"),
        ("r/programming - Reddit", "msedge.exe"),
        ("Windows PowerShell", "windowsterminal.exe"),
        ("Elden Ring", "eldenring.exe"),
    ]
    
    for t, p in test_cases:
        label = resolve(t, p)
        print(f"[{p}] '{t}'")
        print(f"  Category: {label.category}")
        print(f"  Intent:   {label.intent}")
        print(f"  Context:  {label.specific_context}")
        print(f"  Focus:    {label.focus_instruction[:80]}...\n")
