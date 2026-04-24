"""
Schedule Manager — Background timer/reminder/Pomodoro daemon for April.

Runs as a daemon thread with a priority queue of timed events.
When an event fires, it injects a callback into the main reaction loop,
which April then speaks as a natural dialogue.

Features:
  - General-purpose timers ("Remind me in 30 minutes")
  - Pomodoro cycles (work → break → work → ...)
  - Break detection (warns after long coding sessions)
  - Distraction detection during focus sessions
"""
import heapq
import time
import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Callable

import config
from logger import Log

log = Log("Schedule")


@dataclass(order=True)
class ScheduledEvent:
    """A timed event in the priority queue."""
    trigger_time: float
    label: str = field(compare=False)
    callback: Callable = field(compare=False)
    repeating: bool = field(compare=False, default=False)
    repeat_interval: float = field(compare=False, default=0.0)


class ScheduleManager:
    """Background daemon that manages timers, reminders, and Pomodoro cycles."""

    def __init__(self):
        self._queue: list[ScheduledEvent] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._pending_callbacks: list[tuple[str, Callable]] = []
        self._callback_lock = threading.Lock()

        # Pomodoro state
        self._pomodoro_active = False
        self._pomodoro_phase = "idle"  # "idle", "work", "break"
        self._pomodoro_cycle = 0
        self._focus_violations: list[float] = []  # timestamps of distractions

        # Break tracking
        self._last_break_warning: float = 0.0
        self._last_distraction_warning: float = 0.0

    def start(self):
        """Start the scheduler daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Schedule manager started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        log.info("Schedule manager stopped")

    def _run(self):
        """Main scheduler loop — sleeps until next event."""
        while self._running:
            with self._lock:
                if not self._queue:
                    next_wait = 1.0
                else:
                    next_event = self._queue[0]
                    wait = next_event.trigger_time - time.time()
                    if wait <= 0:
                        event = heapq.heappop(self._queue)
                        self._fire_event(event)
                        continue
                    next_wait = min(wait, 1.0)

            time.sleep(next_wait)

    def _fire_event(self, event: ScheduledEvent):
        """Fire an event's callback and optionally re-queue if repeating."""
        log.info(f"⏰ Event fired: '{event.label}'")
        try:
            # Queue the callback for the main loop to pick up
            with self._callback_lock:
                self._pending_callbacks.append((event.label, event.callback))

            # Re-queue if repeating
            if event.repeating and event.repeat_interval > 0:
                event.trigger_time = time.time() + event.repeat_interval
                with self._lock:
                    heapq.heappush(self._queue, event)
                log.debug(f"Re-queued repeating event: '{event.label}' in {event.repeat_interval}s")
        except Exception as e:
            log.error(f"Event callback failed: '{event.label}'", exc=e)

    # ─── Public API ──────────────────────────────────────────

    def set_timer(self, label: str, minutes: float, callback: Callable):
        """Set a one-shot timer that fires after `minutes` minutes."""
        trigger = time.time() + (minutes * 60)
        event = ScheduledEvent(
            trigger_time=trigger,
            label=label,
            callback=callback,
        )
        with self._lock:
            heapq.heappush(self._queue, event)
        log.success(f"Timer set: '{label}' in {minutes:.0f} minutes")

    def set_reminder(self, label: str, minutes: float, message: str, callback: Callable):
        """Set a reminder with a specific message."""
        self.set_timer(f"Reminder: {message}", minutes, callback)

    def cancel_timer(self, label: str) -> bool:
        """Cancel a timer by label. Returns True if found and removed."""
        with self._lock:
            original_len = len(self._queue)
            self._queue = [e for e in self._queue if e.label != label]
            heapq.heapify(self._queue)
            removed = original_len - len(self._queue)
        if removed:
            log.info(f"Cancelled timer: '{label}'")
        return removed > 0

    def get_pending_callbacks(self) -> list[tuple[str, Callable]]:
        """Retrieve and clear any pending event callbacks for the main loop."""
        with self._callback_lock:
            callbacks = list(self._pending_callbacks)
            self._pending_callbacks.clear()
        return callbacks

    # ─── Pomodoro ─────────────────────────────────────────────

    def start_pomodoro(self, work_mins: float = None, break_mins: float = None,
                       on_work_end: Callable = None, on_break_end: Callable = None):
        """Start a Pomodoro work session."""
        work = work_mins or config.POMODORO_DEFAULT_WORK
        brk = break_mins or config.POMODORO_DEFAULT_BREAK

        self._pomodoro_active = True
        self._pomodoro_phase = "work"
        self._pomodoro_cycle += 1
        self._focus_violations.clear()

        def _on_work_complete():
            self._pomodoro_phase = "break"
            if on_work_end:
                on_work_end()
            # Auto-start break timer
            self.set_timer(
                f"Pomodoro break #{self._pomodoro_cycle}",
                brk,
                lambda: self._on_break_complete(on_break_end),
            )

        self.set_timer(
            f"Pomodoro work #{self._pomodoro_cycle}",
            work,
            _on_work_complete,
        )
        log.success(f"Pomodoro started: {work}min work → {brk}min break (cycle #{self._pomodoro_cycle})")

    def _on_break_complete(self, callback: Callable = None):
        """Called when a Pomodoro break ends."""
        self._pomodoro_phase = "idle"
        self._pomodoro_active = False
        log.info(f"Pomodoro cycle #{self._pomodoro_cycle} complete")
        if callback:
            callback()

    def stop_pomodoro(self):
        """Cancel the active Pomodoro."""
        if not self._pomodoro_active:
            return False
        self.cancel_timer(f"Pomodoro work #{self._pomodoro_cycle}")
        self.cancel_timer(f"Pomodoro break #{self._pomodoro_cycle}")
        self._pomodoro_active = False
        self._pomodoro_phase = "idle"
        log.info("Pomodoro cancelled")
        return True

    def record_distraction(self):
        """Record a focus violation during an active Pomodoro."""
        if self._pomodoro_active and self._pomodoro_phase == "work":
            self._focus_violations.append(time.time())
            log.info(f"Focus violation #{len(self._focus_violations)} recorded")

    @property
    def pomodoro_active(self) -> bool:
        return self._pomodoro_active

    @property
    def pomodoro_phase(self) -> str:
        return self._pomodoro_phase

    @property
    def focus_violations(self) -> int:
        return len(self._focus_violations)

    # ─── Break Detection ─────────────────────────────────────

    def check_break_needed(self, activity: str, duration_mins: int) -> str | None:
        """
        Check if a break warning/demand should fire based on activity duration.
        Returns a severity string or None.

        Severity levels:
          - "warning": gentle concern (90+ min coding)
          - "demand": aggressive demand (120+ min coding)
          - None: no break needed
        """
        if not config.PROACTIVE_BREAK_REMINDERS:
            return None

        # Only trigger for productive activities
        if activity not in ("productive_coding",):
            return None

        now = time.time()

        if duration_mins >= config.BREAK_DEMAND_MINUTES:
            if now - self._last_break_warning > 600:  # don't spam, max every 10 min
                self._last_break_warning = now
                return "demand"
        elif duration_mins >= config.BREAK_WARNING_MINUTES:
            if now - self._last_break_warning > 900:  # max every 15 min
                self._last_break_warning = now
                return "warning"

        return None

    def check_distraction(self, activity: str) -> bool:
        """
        Check if the current activity is a distraction during a Pomodoro.
        Returns True if April should call it out.
        """
        if not self._pomodoro_active or self._pomodoro_phase != "work":
            return False

        distraction_intents = {"leisure_video", "leisure_gaming", "distracted_browsing", "communication"}
        if activity in distraction_intents:
            now = time.time()
            if now - self._last_distraction_warning > config.DISTRACTION_COOLDOWN:
                self._last_distraction_warning = now
                self.record_distraction()
                return True

        return False

    # ─── Status ───────────────────────────────────────────────

    def get_active_timers(self) -> list[str]:
        """Get labels of all active timers."""
        with self._lock:
            now = time.time()
            return [
                f"{e.label} (in {(e.trigger_time - now) / 60:.0f}m)"
                for e in self._queue
            ]

    def get_status_summary(self) -> str:
        """Human-readable status for injection into prompts."""
        parts = []
        if self._pomodoro_active:
            parts.append(f"Pomodoro {self._pomodoro_phase} (cycle #{self._pomodoro_cycle}, {len(self._focus_violations)} violations)")
        
        timers = self.get_active_timers()
        if timers:
            parts.append(f"Active timers: {', '.join(timers)}")

        return " | ".join(parts) if parts else ""
