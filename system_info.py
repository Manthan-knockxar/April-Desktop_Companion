"""
System info gatherer — provides April with real-time awareness of
what's running on the computer beyond just the screenshot.

Gathers: active window, all open windows, CPU/RAM usage, battery,
and a summary of running apps. Uses Win32 API via ctypes for
window enumeration (zero extra dependencies) and psutil for stats.
"""
import re
import ctypes
import ctypes.wintypes
from collections import Counter

import psutil
from logger import Log

log = Log("SysInfo")


# ─── Win32 Window Enumeration ────────────────────────────────

# Callback type for EnumWindows
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)

# Window titles to ignore (system/invisible windows)
_IGNORE_TITLES = {
    "", "Program Manager", "Settings", "MSCTFIME UI",
    "Default IME", "Windows Input Experience",
    "Microsoft Text Input Application",
}


def get_active_window_title() -> str:
    """Get the title of the currently focused window."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return "Desktop"
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or "Desktop"
    except Exception:
        return "Unknown"


def get_all_visible_windows() -> list[str]:
    """Get titles of all visible windows (open apps)."""
    titles = []

    def _enum_callback(hwnd, _lparam):
        try:
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if title and title not in _IGNORE_TITLES:
                        titles.append(title)
        except Exception:
            pass
        return True

    try:
        ctypes.windll.user32.EnumWindows(_WNDENUMPROC(_enum_callback), 0)
    except Exception:
        pass

    return titles


# ─── App Name Extraction ─────────────────────────────────────

# Map common executable/window patterns to friendly app names
_APP_PATTERNS = {
    "Google Chrome": "Chrome",
    "Mozilla Firefox": "Firefox",
    "Microsoft Edge": "Edge",
    "Opera": "Opera",
    "Brave": "Brave",
    "Visual Studio Code": "VS Code",
    "Discord": "Discord",
    "Steam": "Steam",
    "Spotify": "Spotify",
    "File Explorer": "File Explorer",
    "Notepad": "Notepad",
    "Windows Terminal": "Terminal",
    "PowerShell": "PowerShell",
    "Command Prompt": "Terminal",
    "py.exe": "Python",
    "python": "Python",
    "Task Manager": "Task Manager",
    "Blender": "Blender",
    "OBS": "OBS Studio",
    "Photoshop": "Photoshop",
    "Premiere": "Premiere Pro",
    "Word": "Word",
    "Excel": "Excel",
    "Outlook": "Outlook",
    "Teams": "Teams",
    "Slack": "Slack",
    "Telegram": "Telegram",
    "WhatsApp": "WhatsApp",
    "YouTube": "YouTube",
    "Twitter": "Twitter",
    "Reddit": "Reddit",
    "Netflix": "Netflix",
    "Twitch": "Twitch",
    "Epic Games": "Epic Games",
    "Riot Client": "Valorant/LoL",
    "Minecraft": "Minecraft",
}


def _identify_app(window_title: str) -> str:
    """Extract a friendly app name from a window title."""
    for pattern, friendly_name in _APP_PATTERNS.items():
        if pattern.lower() in window_title.lower():
            return friendly_name
    # Fallback: use the last part after " - " (common convention)
    if " - " in window_title:
        return window_title.rsplit(" - ", 1)[-1].strip()
    # Truncate long titles
    if len(window_title) > 40:
        return window_title[:37] + "..."
    return window_title


def _count_browser_windows(titles: list[str]) -> dict[str, int]:
    """Count how many windows each browser has (rough tab proxy)."""
    browsers = {"Chrome": 0, "Firefox": 0, "Edge": 0, "Opera": 0, "Brave": 0}
    for title in titles:
        for browser in browsers:
            if browser.lower() in title.lower() or \
               (browser == "Chrome" and "Google Chrome" in title):
                browsers[browser] += 1
                break
    return {k: v for k, v in browsers.items() if v > 0}


# ─── System Stats ─────────────────────────────────────────────

def get_system_stats() -> dict:
    """Get CPU, RAM, and battery info."""
    stats = {}
    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=0)
    except Exception:
        stats["cpu_percent"] = -1

    try:
        mem = psutil.virtual_memory()
        stats["ram_percent"] = mem.percent
        stats["ram_used_gb"] = round(mem.used / (1024**3), 1)
        stats["ram_total_gb"] = round(mem.total / (1024**3), 1)
    except Exception:
        stats["ram_percent"] = -1

    try:
        battery = psutil.sensors_battery()
        if battery:
            stats["battery_percent"] = round(battery.percent)
            stats["battery_plugged"] = battery.power_plugged
        else:
            stats["battery_percent"] = None  # desktop PC, no battery
    except Exception:
        stats["battery_percent"] = None

    return stats


# ─── Window Title Enrichment ─────────────────────────────────

def _parse_youtube_title(title: str) -> str:
    """Extract video title from: '(1263) Video Title - YouTube - Google Chrome'"""
    # Strip browser suffix
    cleaned = re.sub(r'\s*-\s*Google Chrome$', '', title, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*-\s*Mozilla Firefox$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*-\s*Microsoft Edge$', '', cleaned, flags=re.IGNORECASE)
    # Strip " - YouTube" suffix
    cleaned = re.sub(r'\s*-\s*YouTube$', '', cleaned, flags=re.IGNORECASE)
    # Strip leading notification count like "(1263) "
    cleaned = re.sub(r'^\(\d+\)\s*', '', cleaned)
    return cleaned.strip()


def _parse_generic_title(title: str) -> str:
    """Extract meaningful part from generic browser titles."""
    # Strip browser suffix
    cleaned = re.sub(r'\s*-\s*(Google Chrome|Mozilla Firefox|Microsoft Edge|Opera|Brave)$', '', title, flags=re.IGNORECASE)
    return cleaned.strip()


def _parse_editor_title(title: str) -> str:
    """Extract filename from VS Code / editor titles."""
    # VS Code: "filename.py - ProjectName - Visual Studio Code"
    cleaned = re.sub(r'\s*-\s*Visual Studio Code$', '', title, flags=re.IGNORECASE)
    # Take the first part (the filename)
    parts = cleaned.split(' - ')
    return parts[0].strip() if parts else cleaned.strip()


def enrich_active_window(window_title: str) -> str:
    """
    Extract semantic content from the active window title.
    Returns a human-readable sentence describing WHAT the user is doing,
    not just WHICH app they are using.

    This is the key insight: window titles already contain the content
    description for free — we just need to parse and surface it.
    """
    if not window_title:
        return "Desktop is idle or no active window"

    title_lower = window_title.lower()

    # ── YouTube ──
    if "youtube" in title_lower:
        video = _parse_youtube_title(window_title)
        if video:
            return f"Watching YouTube video: '{video}'"
        return "Browsing YouTube"

    # ── Netflix / Streaming ──
    if "netflix" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Watching Netflix: '{content}'"

    if "twitch" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Watching Twitch stream: '{content}'"

    if "crunchyroll" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Watching anime on Crunchyroll: '{content}'"

    # ── Spotify ──
    if "spotify" in title_lower:
        # Spotify title: "Song Name - Artist Name — Spotify"
        cleaned = re.sub(r'\s*[—-]\s*Spotify.*$', '', window_title, flags=re.IGNORECASE)
        if cleaned and cleaned.lower() != 'spotify':
            return f"Listening to music: '{cleaned}'"
        return "Browsing Spotify"

    # ── Code Editors ──
    if "visual studio code" in title_lower or "vscode" in title_lower:
        filename = _parse_editor_title(window_title)
        return f"Editing code file: '{filename}'"

    if "dev-c++" in title_lower or "devcpp" in title_lower:
        return f"Coding in Dev-C++: '{_parse_generic_title(window_title)}'"

    if "pycharm" in title_lower:
        return f"Coding in PyCharm: '{_parse_generic_title(window_title)}'"

    if "intellij" in title_lower:
        return f"Coding in IntelliJ: '{_parse_generic_title(window_title)}'"

    # ── GitHub ──
    if "github" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Browsing GitHub: '{content}'"

    # ── StackOverflow ──
    if "stack overflow" in title_lower or "stackoverflow" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Reading StackOverflow: '{content}'"

    # ── Reddit ──
    if "reddit" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Browsing Reddit: '{content}'"

    # ── Twitter / X ──
    if "twitter" in title_lower or "/ x" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Scrolling Twitter/X: '{content}'"

    # ── Social / Chat ──
    if "discord" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Chatting on Discord: '{content}'"

    if "whatsapp" in title_lower:
        return f"Using WhatsApp: '{_parse_generic_title(window_title)}'"

    if "telegram" in title_lower:
        return f"Using Telegram: '{_parse_generic_title(window_title)}'"

    # ── Gaming ──
    if "minecraft" in title_lower:
        return "Playing Minecraft"

    if "riot client" in title_lower or "valorant" in title_lower:
        return "Playing Valorant"

    if "league of legends" in title_lower:
        return "Playing League of Legends"

    if "steam" in title_lower:
        content = _parse_generic_title(window_title)
        return f"Using Steam: '{content}'"

    # ── File Explorer ──
    if "file explorer" in title_lower or "explorer" in title_lower:
        return f"Browsing files: '{_parse_generic_title(window_title)}'"

    # ── Fallback: generic browser tab ──
    browser_suffixes = ["google chrome", "mozilla firefox", "microsoft edge", "opera", "brave"]
    for browser in browser_suffixes:
        if browser in title_lower:
            content = _parse_generic_title(window_title)
            return f"Browsing: '{content}'"

    # ── Ultimate fallback ──
    return f"Active window: '{window_title[:80]}'"


# ─── Main Context Builder ────────────────────────────────────

def get_system_context() -> str:
    """
    Build a formatted system context string for injection into the AI prompt.
    This gives April awareness of what's happening beyond just the screenshot.

    Returns a multi-line string ready to paste into the prompt.
    """
    try:
        active = get_active_window_title()
        all_windows = get_all_visible_windows()
        stats = get_system_stats()

        log.debug(f"Active window: \"{active}\"")
        log.debug(f"Visible windows: {len(all_windows)}")

        # Identify and count apps
        app_names = [_identify_app(t) for t in all_windows]
        app_counts = Counter(app_names)

        # Browser window counts
        browser_windows = _count_browser_windows(all_windows)

        # Build formatted output
        lines = []
        lines.append(f"Active window: \"{active}\"")

        # Open apps summary
        app_list = []
        for app, count in app_counts.most_common(12):
            if count > 1:
                app_list.append(f"{app} ({count} windows)")
            else:
                app_list.append(app)
        if app_list:
            lines.append(f"Open apps: {', '.join(app_list)}")

        # Browser tab hints
        for browser, count in browser_windows.items():
            if count > 0:
                lines.append(f"{browser} windows open: {count}")

        # System stats
        stat_parts = []
        if stats["cpu_percent"] >= 0:
            stat_parts.append(f"CPU: {stats['cpu_percent']:.0f}%")
        if stats["ram_percent"] >= 0:
            stat_parts.append(f"RAM: {stats['ram_used_gb']}GB/{stats['ram_total_gb']}GB ({stats['ram_percent']:.0f}%)")
        if stats.get("battery_percent") is not None:
            plug = "charging" if stats["battery_plugged"] else "on battery"
            stat_parts.append(f"Battery: {stats['battery_percent']}% ({plug})")
        if stat_parts:
            lines.append(f"System: {' | '.join(stat_parts)}")

        context = "\n".join(lines)
        log.debug(f"Context built: {len(context)} chars, {len(app_counts)} unique apps, "
                  f"CPU={stats.get('cpu_percent', '?')}%, RAM={stats.get('ram_percent', '?')}%")
        return context

    except Exception as e:
        log.error("Failed to gather system context", exc=e)
        return f"(system info unavailable: {e})"


def get_enriched_context() -> tuple[str, str]:
    """
    Returns (system_context, enriched_window) as a tuple.
    - system_context: the full multi-line system context string
    - enriched_window: a single semantic sentence about what the user is doing
    """
    system_context = get_system_context()
    active = get_active_window_title()
    enriched = enrich_active_window(active)
    log.debug(f"Enriched window: {enriched}")
    return system_context, enriched
