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


def parse_interval(interval: str) -> int:
    """Parse an interval string such as '30m', '6h', '2d', or '1w' into seconds.

    Args:
        interval: A positive integer immediately followed by a unit character.
            Valid units: m (minutes), h (hours), d (days), w (weeks).

    Returns:
        The number of seconds represented by the interval.

    Raises:
        ValueError: If the format is invalid, the unit is unrecognised, or N is not positive.
    """
    interval = interval.strip()
    if not interval:
        raise ValueError("Interval must not be empty.")

    unit = interval[-1]
    if unit not in _UNITS:
        valid = ", ".join(f"'{u}'" for u in _UNITS)
        raise ValueError(f"Unknown unit '{unit}'. Valid units: {valid}.")

    n_str = interval[:-1]
    if n_str != n_str.strip():
        raise ValueError(
            f"Invalid interval '{interval}': N must be immediately adjacent to the unit, no whitespace allowed."
        )
    try:
        n = int(n_str)
    except ValueError:
        raise ValueError(f"Invalid interval '{interval}': '{n_str}' is not an integer.") from None

    if n <= 0:
        raise ValueError(f"Interval N must be a positive integer, got {n}.")

    result = n * _UNITS[unit]
    max_interval = 365 * 86400  # 1 year in seconds
    if result > max_interval:
        raise ValueError(
            f"Interval '{interval}' ({result}s) exceeds the maximum allowed of 365 days ({max_interval}s)."
        )

    return result


def _format_duration(seconds: float) -> str:
    """Return a human-readable duration string such as '6h 30m' or '45m'.

    Args:
        seconds: Non-negative duration in seconds.

    Returns:
        A compact string using w/d/h/m/s units with only non-zero parts shown.
    """
    total = int(seconds)
    weeks, remainder = divmod(total, 604800)
    days, remainder = divmod(remainder, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs:
        parts.append(f"{secs}s")

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
            try:
                next_run = datetime.now() + timedelta(seconds=interval_seconds)
                next_run_str = f" First run at {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
            except OverflowError:
                next_run_str = ""
            logger.info(f"Scheduler started.{next_run_str} (in {_format_duration(interval_seconds)}).")
            _sleep_interruptible(interval_seconds)

        while True:
            t0 = time.monotonic()
            try:
                run_fn()
            except Exception as exc:
                logger.error(f"Scheduler: run failed: {exc}")
            elapsed = time.monotonic() - t0

            sleep_secs = max(0.0, interval_seconds - elapsed)

            if elapsed > interval_seconds:
                logger.warning(
                    f"Scheduler: run took {_format_duration(elapsed)} which exceeds"
                    f" the {_format_duration(interval_seconds)} interval."
                    " Firing next run immediately."
                )
            else:
                try:
                    next_run = datetime.now() + timedelta(seconds=sleep_secs)
                    next_run_str = (
                        f"Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {_format_duration(sleep_secs)})."
                    )
                except OverflowError:
                    next_run_str = f"Next run in {_format_duration(sleep_secs)}."
                logger.info(next_run_str)
            _sleep_interruptible(sleep_secs)

    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
