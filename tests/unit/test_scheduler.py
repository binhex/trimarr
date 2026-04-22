"""Unit tests for trimarr.scheduler."""

from __future__ import annotations

import pytest

from trimarr.scheduler import parse_interval


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
