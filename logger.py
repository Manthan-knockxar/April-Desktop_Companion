"""
Centralized logging for April — Desktop Companion.

Provides:
  - Color-coded, timestamped console output
  - Module-tagged messages so you know WHERE each log came from
  - Elapsed-time helper for profiling API calls
  - Severity levels (DEBUG, INFO, WARN, ERROR, SUCCESS)

Usage:
    from logger import Log
    log = Log("ModuleName")
    log.info("Something happened")
    log.success("It worked!")
    log.warn("Hmm...")
    log.error("Oh no", exc=some_exception)

    # Timing:
    with log.timed("Local inference call"):
        result = do_something()
"""
import time
from contextlib import contextmanager


# ─── ANSI Colors ──────────────────────────────────────────────

PINK = "\033[95m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
GREEN = "\033[92m"
BLUE = "\033[94m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
WHITE = "\033[97m"
MAGENTA = "\033[35m"


# Color map for module tags (auto-cycles through colors)
_TAG_COLORS = [CYAN, PINK, YELLOW, BLUE, MAGENTA, GREEN]
_tag_color_map: dict[str, str] = {}
_tag_index = 0


def _get_tag_color(tag: str) -> str:
    global _tag_index
    if tag not in _tag_color_map:
        _tag_color_map[tag] = _TAG_COLORS[_tag_index % len(_TAG_COLORS)]
        _tag_index += 1
    return _tag_color_map[tag]


class Log:
    """Logger instance bound to a specific module/component."""

    def __init__(self, module: str):
        self.module = module
        self._color = _get_tag_color(module)

    def _format(self, level_icon: str, level_color: str, msg: str) -> str:
        ts = time.strftime("%H:%M:%S")
        ms = f"{time.time() % 1:.3f}"[1:]  # .XXX milliseconds
        tag = f"{self._color}[{self.module}]{RESET}"
        return f"{DIM}[{ts}{ms}]{RESET} {tag} {level_color}{level_icon} {msg}{RESET}"

    def debug(self, msg: str):
        """Low-level details — model names, param values, sizes."""
        print(self._format("·", DIM, msg))

    def info(self, msg: str):
        """Standard operational messages."""
        print(self._format("→", WHITE, msg))

    def success(self, msg: str):
        """Something completed successfully."""
        print(self._format("✓", GREEN, msg))

    def warn(self, msg: str):
        """Non-fatal issue — retries, fallbacks, skips."""
        print(self._format("⚠", YELLOW, msg))

    def error(self, msg: str, exc: Exception | None = None):
        """Something failed."""
        line = msg
        if exc:
            line += f" — {type(exc).__name__}: {exc}"
        print(self._format("✗", RED, line))

    def reaction(self, icon: str, msg: str):
        """Special formatted line for dialogue/reactions (keeps the pretty output)."""
        ts = time.strftime("%H:%M:%S")
        ms = f"{time.time() % 1:.3f}"[1:]
        tag = f"{self._color}[{self.module}]{RESET}"
        print(f"{DIM}[{ts}{ms}]{RESET} {tag} {icon} {msg}")

    @contextmanager
    def timed(self, label: str):
        """Context manager that logs elapsed time for an operation."""
        self.debug(f"{label}...")
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            if elapsed < 1:
                time_str = f"{elapsed * 1000:.0f}ms"
            else:
                time_str = f"{elapsed:.2f}s"
            self.debug(f"{label} — took {time_str}")
