"""Cron-driven scheduler for trimarr."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING

from croniter import croniter

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Logger

_UNITS: dict[str, int] = {
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def validate_cron_expr(expr: str) -> None:
    """Validate a cron expression, raising ValueError if it is invalid.

    Args:
        expr: A cron expression string (e.g. ``"*/5 * * * *"``).

    Raises:
        ValueError: If the expression is empty or cannot be parsed by croniter.
    """
    if not expr or not expr.strip():
        raise ValueError("Cron expression must not be empty.")
    try:
        croniter(expr, datetime.now())
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid cron expression '{expr}': {exc}") from exc


def _get_next_fire(cron_expr: str, base: datetime | None = None) -> datetime:
    """Return the next datetime matching *cron_expr* on or after *base*.

    Args:
        cron_expr: A valid cron expression.
        base: The reference datetime (defaults to ``datetime.now()``).

    Returns:
        The next matching :class:`datetime`.
    """
    cron = croniter(cron_expr, base or datetime.now())
    next_dt: datetime = cron.get_next(datetime)
    return next_dt


def _sleep_until(target: datetime) -> None:
    """Sleep in 1-second ticks until *target* is reached or passed.

    Uses ``time.monotonic()`` for deadline tracking so system clock
    adjustments (NTP, DST, manual changes) do not affect the sleep
    duration.

    The initial wall-clock remaining is converted to a monotonic deadline
    at the earliest practical moment to minimise the window where a clock
    jump between the ``datetime.now()`` call and the monotonic deadline
    setup could skew the sleep duration.

    Args:
        target: The :class:`datetime` to sleep until.
    """
    # Compute the initial wall-clock delta, then immediately convert to
    # a monotonic deadline.  Any clock jump between these two operations
    # is negligible (microseconds).  The monotonic deadline means the
    # loop is immune to subsequent clock adjustments.
    remaining = (target - datetime.now()).total_seconds()
    if remaining <= 0:
        return
    deadline = time.monotonic() + remaining
    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        time.sleep(min(1.0, max(0.0, left)))


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


def _safe_format_datetime(dt: datetime) -> str:
    """Format a datetime for display, returning empty string on error."""
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, ValueError):
        return ""


def _scheduler_initial_wait(
    cron_expr: str,
    run_on_start: bool,
    logger: Logger,
) -> None:
    """Optionally wait for the first scheduled fire before entering the main loop.

    When *run_on_start* is *True* the wait is skipped entirely (the caller
    fires the first run immediately).
    """
    if not run_on_start:
        next_run = _get_next_fire(cron_expr)
        formatted = _safe_format_datetime(next_run)
        wait_seconds = max(0.0, (next_run - datetime.now()).total_seconds())
        if formatted:
            first_run_str = f" First run at {formatted} (in {_format_duration(wait_seconds)})."
        else:
            first_run_str = f" First run in {_format_duration(wait_seconds)}."
        logger.info(f"Scheduler started.{first_run_str}")
        _sleep_until(next_run)


def _scheduler_wait_for_next_fire(
    cron_expr: str,
    elapsed: float,
    logger: Logger,
) -> None:
    """Compute the next cron fire, log it, and sleep until it arrives.

    If *elapsed* (the duration of the last run) is shorter than the interval
    to the next fire, a warning is logged so the operator knows one or more
    firings were skipped.
    """
    now = datetime.now()
    next_run = _get_next_fire(cron_expr, now)
    wait_seconds = max(0.0, (next_run - now).total_seconds())

    if 0 < wait_seconds < elapsed:
        logger.warning(
            f"Scheduler: run took {_format_duration(elapsed)}"
            f" which exceeds the {_format_duration(wait_seconds)} gap"
            f" to the next cron fire. One or more firings were skipped."
        )

    formatted = _safe_format_datetime(next_run)
    next_run_str = (
        f"Next run at {formatted} (in {_format_duration(wait_seconds)})."
        if formatted
        else f"Next run in {_format_duration(wait_seconds)}."
    )
    logger.info(next_run_str)
    _sleep_until(next_run)


def run_scheduled(
    run_fn: Callable[[], None],
    cron_expr: str,
    run_on_start: bool,
    logger: Logger,
) -> None:
    """Run *run_fn* on a cron-driven schedule until KeyboardInterrupt.

    After each run the next cron fire is recomputed from the current time, so
    any missed fires are skipped.

    Args:
        run_fn: Zero-argument callable invoked on each tick.
        cron_expr: A valid cron expression (e.g. ``"*/5 * * * *"``).
        run_on_start: When True, fire one run immediately before the first sleep.
        logger: Loguru logger instance for status and warning messages.
    """
    try:
        _scheduler_initial_wait(cron_expr, run_on_start, logger)

        while True:
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

            _scheduler_wait_for_next_fire(cron_expr, time.monotonic() - t0, logger)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
        sys.exit(0)
