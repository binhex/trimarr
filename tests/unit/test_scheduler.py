"""Unit tests for trimarr.scheduler (cron-based)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trimarr.scheduler import (
    _format_duration,
    _get_next_fire,
    _sleep_until,
    run_scheduled,
    validate_cron_expr,
)


class TestValidateCronExpr:
    """validate_cron_expr accepts valid cron expressions and rejects invalid ones."""

    def test_standard_5_field_accepted(self) -> None:
        validate_cron_expr("0 2 * * *")  # daily at 2am

    def test_step_syntax_accepted(self) -> None:
        validate_cron_expr("*/30 * * * *")  # every 30 minutes

    def test_range_syntax_accepted(self) -> None:
        validate_cron_expr("0 9-17 * * 1-5")  # weekdays 9am-5pm

    def test_list_syntax_accepted(self) -> None:
        validate_cron_expr("0 9,18 * * *")  # 9am and 6pm

    def test_special_daily_accepted(self) -> None:
        validate_cron_expr("@daily")

    def test_special_hourly_accepted(self) -> None:
        validate_cron_expr("@hourly")

    def test_special_weekly_accepted(self) -> None:
        validate_cron_expr("@weekly")

    def test_special_monthly_accepted(self) -> None:
        validate_cron_expr("@monthly")

    def test_special_yearly_accepted(self) -> None:
        validate_cron_expr("@yearly")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_cron_expr("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_cron_expr("   ")

    def test_too_few_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cron expression"):
            validate_cron_expr("0 2 * *")

    def test_too_many_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cron expression"):
            validate_cron_expr("0 2 * * * 2026")

    def test_invalid_minute_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cron expression"):
            validate_cron_expr("60 2 * * *")

    def test_invalid_hour_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cron expression"):
            validate_cron_expr("0 24 * * *")


class TestGetNextFire:
    """_get_next_fire correctly computes the next matching datetime."""

    # Use a fixed "now" for deterministic testing
    NOW = datetime(2026, 5, 20, 10, 0, 0)

    def test_every_hour_returns_next_hour(self) -> None:
        """*/30 * * * * — next fire at 10:30 when now is 10:00."""
        result = _get_next_fire("*/30 * * * *", self.NOW)
        assert result == datetime(2026, 5, 20, 10, 30, 0)

    def test_daily_at_2am(self) -> None:
        """0 2 * * * — next fire at 2:00 tomorrow."""
        result = _get_next_fire("0 2 * * *", self.NOW)
        assert result == datetime(2026, 5, 21, 2, 0, 0)

    def test_later_today(self) -> None:
        """0 11 * * * — next fire at 11:00 today."""
        result = _get_next_fire("0 11 * * *", self.NOW)
        assert result == datetime(2026, 5, 20, 11, 0, 0)

    def test_past_hour_today(self) -> None:
        """0 9 * * * — next fire at 9:00 tomorrow (9am already passed today)."""
        result = _get_next_fire("0 9 * * *", self.NOW)
        assert result == datetime(2026, 5, 21, 9, 0, 0)

    def test_specific_weekday(self) -> None:
        """0 10 * * 5 — next Friday at 10am."""
        # May 20 2026 is a Wednesday
        result = _get_next_fire("0 10 * * 5", self.NOW)
        assert result == datetime(2026, 5, 22, 10, 0, 0)

    def test_minute_step(self) -> None:
        """*/15 * * * * — next fire at 10:15."""
        result = _get_next_fire("*/15 * * * *", self.NOW)
        assert result == datetime(2026, 5, 20, 10, 15, 0)

    def test_default_base_is_now(self) -> None:
        """When base is None, it should not raise."""
        result = _get_next_fire("*/5 * * * *")
        assert result > datetime.now()


class TestSleepUntil:
    """_sleep_until sleeps in 1-second ticks until the target time."""

    def test_past_target_does_not_sleep(self) -> None:
        """If the target is already in the past, _sleep_until returns immediately."""
        with patch("trimarr.scheduler.time.sleep") as mock_sleep:
            _sleep_until(datetime(2020, 1, 1))
        mock_sleep.assert_not_called()

    def test_future_target_sleeps(self) -> None:
        """If the target is in the future, _sleep_until sleeps."""
        slept: list[float] = []
        with (
            patch("trimarr.scheduler.time.sleep", side_effect=lambda s: slept.append(s)),
            patch("trimarr.scheduler.datetime") as mock_dt,
        ):
            now = datetime(2026, 5, 20, 10, 0, 0)
            mock_dt.now.side_effect = [
                now,  # first check: before target
                now + timedelta(seconds=6),  # second check: at or past target
            ]
            _sleep_until(datetime(2026, 5, 20, 10, 0, 6))
        assert len(slept) == 1
        assert slept[0] <= 6.0


class TestFormatDuration:
    """_format_duration converts a number of seconds to a compact human-readable string."""

    def test_zero_seconds(self) -> None:
        assert _format_duration(0) == "0s"

    def test_weeks_only(self) -> None:
        assert _format_duration(604800) == "1w"

    def test_days_only(self) -> None:
        assert _format_duration(86400) == "1d"

    def test_weeks_and_days(self) -> None:
        assert _format_duration(604800 + 86400) == "1w 1d"

    def test_hours_and_minutes(self) -> None:
        assert _format_duration(3600 + 1800) == "1h 30m"

    def test_seconds_only(self) -> None:
        assert _format_duration(1) == "1s"

    def test_seconds_with_minutes(self) -> None:
        assert _format_duration(61) == "1m 1s"

    def test_all_units(self) -> None:
        total = 604800 + 86400 + 3600 + 60 + 1  # 1w 1d 1h 1m 1s
        assert _format_duration(total) == "1w 1d 1h 1m 1s"

    def test_hours_only(self) -> None:
        assert _format_duration(3600) == "1h"


class TestRunScheduled:
    """run_scheduled calls run_fn on a cron cadence and handles Ctrl+C cleanly."""

    NOW = datetime(2026, 5, 20, 10, 0, 0)

    def test_run_on_start_calls_run_fn_before_sleep(self) -> None:
        """With run_on_start=True, run_fn fires before any sleep."""
        call_order: list[str] = []

        def run_fn() -> None:
            call_order.append("run")
            raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_until") as mock_sleep,
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(run_fn, cron_expr="*/30 * * * *", run_on_start=True, logger=logger)

        assert exc_info.value.code == 0
        assert call_order == ["run"]
        mock_sleep.assert_not_called()

    def test_no_run_on_start_sleeps_to_first_cron_fire(self) -> None:
        """Without run_on_start, the scheduler sleeps until the first cron fire."""
        slept: list[datetime] = []

        def mock_sleep(target: datetime) -> None:
            slept.append(target)
            raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=datetime(2026, 5, 20, 10, 30, 0)),
            patch("trimarr.scheduler._sleep_until", side_effect=mock_sleep),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(MagicMock(), cron_expr="*/30 * * * *", run_on_start=False, logger=logger)

        assert exc_info.value.code == 0
        assert slept == [datetime(2026, 5, 20, 10, 30, 0)]

    def test_run_fn_exception_is_logged_and_loop_continues(self) -> None:
        """An exception from run_fn is logged as an error and the loop continues."""
        calls = [0]

        def run_fn() -> None:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("boom")
            raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=datetime(2026, 5, 20, 10, 30, 0)),
            patch("trimarr.scheduler._sleep_until"),
            pytest.raises(SystemExit),
        ):
            run_scheduled(run_fn, cron_expr="*/30 * * * *", run_on_start=True, logger=logger)

        assert calls[0] == 2
        logger.error.assert_called_once()
        assert "boom" in logger.error.call_args[0][0]

    def test_keyboard_interrupt_during_sleep_exits_zero(self) -> None:
        """Ctrl+C during sleep logs 'Scheduler stopped.' and exits 0."""
        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_until", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(MagicMock(), cron_expr="*/30 * * * *", run_on_start=False, logger=logger)

        assert exc_info.value.code == 0
        stop_messages = [str(c) for c in logger.info.call_args_list]
        assert any("stopped" in m.lower() for m in stop_messages)

    def test_system_exit_from_run_fn_propagates(self) -> None:
        """SystemExit(2) raised by run_fn (e.g. from CorruptOutputError) must propagate."""

        def run_fn() -> None:
            raise SystemExit(2)

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=datetime(2026, 5, 20, 10, 30, 0)),
            patch("trimarr.scheduler._sleep_until"),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(run_fn, cron_expr="*/30 * * * *", run_on_start=True, logger=logger)

        assert exc_info.value.code == 2
        logger.error.assert_not_called()

    def test_system_exit_130_from_run_fn_exits_zero(self) -> None:
        """SystemExit(130) from run_fn (Ctrl+C converted by runner.py) must exit cleanly."""

        def run_fn() -> None:
            raise SystemExit(130)

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=datetime(2026, 5, 20, 10, 30, 0)),
            patch("trimarr.scheduler._sleep_until"),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(run_fn, cron_expr="*/30 * * * *", run_on_start=True, logger=logger)

        assert exc_info.value.code == 0
        stop_messages = [str(c) for c in logger.info.call_args_list]
        assert any("stopped" in m.lower() for m in stop_messages)

    def test_overrun_detects_missed_fire_and_logs_warning(self) -> None:
        """When a run exceeds the inter-fire gap, a warning is logged."""
        calls = [0]

        def run_fn() -> None:
            calls[0] += 1
            if calls[0] >= 2:
                raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=datetime(2026, 5, 20, 10, 30, 0)),
            patch("trimarr.scheduler._sleep_until"),
            patch("trimarr.scheduler.datetime") as mock_dt,
            patch("time.monotonic", side_effect=[0.0, 3600.0, 3600.0]),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_dt.now.return_value = datetime(2026, 5, 20, 10, 0, 0)
            run_scheduled(
                run_fn,
                cron_expr="*/30 * * * *",  # fires every 30 minutes
                run_on_start=True,
                logger=logger,
            )

        assert exc_info.value.code == 0

        warning_messages = [str(c) for c in logger.warning.call_args_list]
        assert warning_messages, "Expected a warning about overrun"


class TestRunScheduledSpecialStrings:
    """run_scheduled accepts @daily, @hourly, etc. via croniter."""

    def test_at_daily_is_accepted(self) -> None:
        """@daily does not raise and schedules correctly."""
        logger = MagicMock()
        future = datetime(2099, 1, 1)
        with (
            patch("trimarr.scheduler._get_next_fire", return_value=future) as mock_get,
            patch("trimarr.scheduler._sleep_until", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit),
        ):
            run_scheduled(MagicMock(), cron_expr="@daily", run_on_start=False, logger=logger)

        mock_get.assert_called_once_with("@daily")
