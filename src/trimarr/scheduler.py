"""Drift-corrected scheduler for trimarr."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Logger

_UNITS: dict[str, int] = {
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}
_MAX_INTERVAL_SECONDS: int = 365 * _UNITS["d"]


def parse_interval(interval: str) -> int:
    """Parse an interval string like '30m', '6h', '2d', or '1w' into seconds. Valid units: m/h/d/w."""
    interval = interval.strip()
    if not interval:
        raise ValueError("Interval must not be empty.")
    unit = interval[-1]
    if unit not in _UNITS:
        valid = ", ".join(f"'{u}'" for u in _UNITS)
        raise ValueError(f"Unknown unit '{unit}'. Valid units: {valid}.")

    count_str = interval[:-1]
    if count_str != count_str.strip():
        raise ValueError(
            f"Invalid interval '{interval}': N must be immediately adjacent to the unit, no whitespace allowed."
        )
    try:
        count = int(count_str)
    except ValueError:
        raise ValueError(f"Invalid interval '{interval}': '{count_str}' is not an integer.") from None

    if count <= 0:
        raise ValueError(f"Interval N must be a positive integer, got {count}.")

    result = count * _UNITS[unit]
    max_interval = _MAX_INTERVAL_SECONDS
    if result > max_interval:
        raise ValueError(
            f"Interval '{interval}' ({result}s) exceeds the maximum allowed of 365 days ({max_interval}s)."
        )

    return result


def _format_duration(seconds: float) -> str:
    """Convert *seconds* to a compact human-readable string (e.g. '6h 30m', '45m', '0s')."""
    total = int(seconds)
    parts = []
    for unit_char, unit_secs in [
        ("w", _UNITS["w"]),
        ("d", _UNITS["d"]),
        ("h", _UNITS["h"]),
        ("m", _UNITS["m"]),
        ("s", 1),
    ]:
        count, total = divmod(total, unit_secs)
        if count:
            parts.append(f"{count}{unit_char}")
    return " ".join(parts) if parts else "0s"


def _sleep_interruptible(seconds: float) -> None:
    """Sleep for *seconds* in 1-second ticks so KeyboardInterrupt is caught promptly.

    Args:
        seconds: Non-negative duration to sleep.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(1.0, max(0.0, remaining)))


def _log_initial_wait(interval_seconds: int, logger: Logger) -> None:
    """Log the initial scheduler start message and sleep until the first run.

    Args:
        interval_seconds: Seconds until the first scheduled run.
        logger: Loguru logger for status messages.
    """
    try:
        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        next_run_str = f" First run at {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
    except OverflowError:
        next_run_str = ""
    logger.info(f"Scheduler started.{next_run_str} (in {_format_duration(interval_seconds)}).")
    _sleep_interruptible(interval_seconds)


def _log_next_run(elapsed: float, interval_seconds: int, sleep_secs: float, logger: Logger) -> None:
    """Log the next scheduled run time or an overrun warning.

    Args:
        elapsed: Seconds the last run took.
        interval_seconds: Target seconds between run starts.
        sleep_secs: Seconds to sleep until the next run.
        logger: Loguru logger for status and warning messages.
    """
    if elapsed > interval_seconds:
        logger.warning(
            f"Scheduler: run took {_format_duration(elapsed)} which exceeds"
            f" the {_format_duration(interval_seconds)} interval."
            " Firing next run immediately."
        )
    else:
        try:
            next_run = datetime.now() + timedelta(seconds=sleep_secs)
            next_run_str = f"Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {_format_duration(sleep_secs)})."
        except OverflowError:
            next_run_str = f"Next run in {_format_duration(sleep_secs)}."
        logger.info(next_run_str)


def _run_and_sleep(
    run_fn: Callable[[], None],
    interval_seconds: int,
    logger: Logger,
) -> None:
    """Execute *run_fn* once, log the next run time, and sleep until it is due.

    :exc:`SystemExit` with code 130 (runner's KeyboardInterrupt sentinel) is
    re-raised as :exc:`KeyboardInterrupt` so the scheduler's outer handler can
    log "Scheduler stopped." and exit cleanly.  Any other exit code propagates
    as-is.  Non-system exceptions are logged and suppressed so that a transient
    run failure does not require a manual restart.
    """
    t0 = time.monotonic()
    try:
        run_fn()
    except SystemExit as exc:
        # Exit code 130 means the run was interrupted by Ctrl+C (runner.py converts
        # KeyboardInterrupt to sys.exit(130)).  Re-raise as KeyboardInterrupt so the
        # scheduler's outer handler can log "Scheduler stopped." and exit cleanly.
        # Any other exit code (e.g. 2 for CorruptOutputError) propagates as-is.
        if exc.code == 130:
            raise KeyboardInterrupt from exc
        raise
    except Exception as exc:
        logger.error(f"Scheduler: run failed: {exc}")
    elapsed = time.monotonic() - t0
    sleep_secs = max(0.0, interval_seconds - elapsed)
    _log_next_run(elapsed, interval_seconds, sleep_secs, logger)
    _sleep_interruptible(sleep_secs)


def run_scheduled(
    run_fn: Callable[[], None],
    interval_seconds: int,
    run_on_start: bool,
    logger: Logger,
) -> None:
    """Run *run_fn* on a drift-corrected schedule until KeyboardInterrupt.

    The interval is measured from the *start* of each run so the scheduler stays
    on cadence even when individual runs take significant time.  An unhandled
    exception from *run_fn* is logged and the loop continues — a transient fault
    does not require a manual restart.

    Args:
        run_fn: Zero-argument callable invoked on each tick.
        interval_seconds: Seconds between run starts.
        run_on_start: When True, fire one run immediately before the first sleep.
        logger: Loguru logger instance for status and warning messages.
    """
    try:
        if not run_on_start:
            _log_initial_wait(interval_seconds, logger)
        while True:
            _run_and_sleep(run_fn, interval_seconds, logger)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
