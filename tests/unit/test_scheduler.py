"""Unit tests for trimarr.scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trimarr.scheduler import parse_interval, run_scheduled


class TestParseInterval:
    """parse_interval converts valid strings to seconds and rejects invalid input."""

    def test_minutes(self) -> None:
        assert parse_interval("30m") == 1800

    def test_hours(self) -> None:
        assert parse_interval("6h") == 21600

    def test_days(self) -> None:
        assert parse_interval("2d") == 172800

    def test_weeks(self) -> None:
        assert parse_interval("1w") == 604800

    def test_single_unit(self) -> None:
        assert parse_interval("1m") == 60

    def test_whitespace_stripped(self) -> None:
        assert parse_interval("  2h  ") == 7200

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown unit"):
            parse_interval("5M")

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            parse_interval("0h")

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            parse_interval("-1d")

    def test_non_integer_n_raises(self) -> None:
        with pytest.raises(ValueError, match="not an integer"):
            parse_interval("1.5h")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_interval("")

    def test_unit_only_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_interval("h")

    def test_no_unit_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown unit"):
            parse_interval("60")


class TestRunScheduled:
    """run_scheduled calls run_fn on cadence and handles Ctrl+C cleanly."""

    def test_run_on_start_calls_run_fn_before_sleep(self) -> None:
        """With run_on_start=True, run_fn fires before any sleep."""
        call_order: list[str] = []

        def run_fn() -> None:
            call_order.append("run")
            raise KeyboardInterrupt

        logger = MagicMock()
        with patch("trimarr.scheduler._sleep_interruptible") as mock_sleep, pytest.raises(SystemExit) as exc_info:
            run_scheduled(run_fn, interval_seconds=3600, run_on_start=True, logger=logger)

        assert exc_info.value.code == 0
        assert call_order == ["run"]
        mock_sleep.assert_not_called()

    def test_no_run_on_start_sleeps_full_interval_first(self) -> None:
        """Without run_on_start, the full interval sleep fires before run_fn."""
        slept: list[float] = []

        def mock_sleep(seconds: float) -> None:
            slept.append(seconds)
            raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_interruptible", side_effect=mock_sleep),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(MagicMock(), interval_seconds=3600, run_on_start=False, logger=logger)

        assert exc_info.value.code == 0
        assert slept == [3600]

    def test_run_fn_exception_is_logged_and_loop_continues(self) -> None:
        """An exception from run_fn is logged as an error and the loop continues."""
        calls = [0]

        def run_fn() -> None:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("boom")
            raise KeyboardInterrupt

        logger = MagicMock()
        with patch("trimarr.scheduler._sleep_interruptible"), pytest.raises(SystemExit):
            run_scheduled(run_fn, interval_seconds=3600, run_on_start=True, logger=logger)

        assert calls[0] == 2
        logger.error.assert_called_once()
        assert "boom" in logger.error.call_args[0][0]

    def test_keyboard_interrupt_during_sleep_exits_zero(self) -> None:
        """Ctrl+C during sleep logs 'Scheduler stopped.' and exits 0."""
        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_interruptible", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_scheduled(MagicMock(), interval_seconds=3600, run_on_start=False, logger=logger)

        assert exc_info.value.code == 0
        stop_messages = [str(c) for c in logger.info.call_args_list]
        assert any("stopped" in m.lower() for m in stop_messages)

    def test_overrun_logs_warning_and_sleep_is_zero(self) -> None:
        """When a run takes longer than the interval, a warning is logged and sleep=0."""
        calls = [0]

        def run_fn() -> None:
            calls[0] += 1
            if calls[0] >= 2:
                raise KeyboardInterrupt

        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_interruptible") as mock_sleep,
            patch("time.monotonic", side_effect=[0.0, 7200.0, 7200.0]),
            pytest.raises(SystemExit),
        ):
            run_scheduled(run_fn, interval_seconds=3600, run_on_start=True, logger=logger)

        logger.warning.assert_called_once()
        mock_sleep.assert_called_once_with(0.0)

    def test_system_exit_from_run_fn_propagates(self) -> None:
        """SystemExit raised by run_fn (e.g. exit code 2 from CorruptOutputError) must propagate."""

        def run_fn() -> None:
            raise SystemExit(2)

        logger = MagicMock()
        with patch("trimarr.scheduler._sleep_interruptible"), pytest.raises(SystemExit) as exc_info:
            run_scheduled(run_fn, interval_seconds=3600, run_on_start=True, logger=logger)

        assert exc_info.value.code == 2
        logger.error.assert_not_called()

    def test_drift_correction_sleep_shrinks_by_elapsed(self) -> None:
        """After a 10-minute run on a 1-hour schedule, sleep is 50 minutes (3000s)."""
        calls = [0]

        def run_fn() -> None:
            calls[0] += 1
            if calls[0] >= 2:
                raise KeyboardInterrupt

        slept: list[float] = []
        logger = MagicMock()
        with (
            patch("trimarr.scheduler._sleep_interruptible", side_effect=lambda s: slept.append(s)),
            patch("time.monotonic", side_effect=[0.0, 600.0, 600.0]),
            pytest.raises(SystemExit),
        ):
            run_scheduled(run_fn, interval_seconds=3600, run_on_start=True, logger=logger)

        assert slept == [3000.0]
