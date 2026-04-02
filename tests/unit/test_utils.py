"""Unit tests for utils.utils."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from utils.utils import get_app_data_dir

if TYPE_CHECKING:
    import pytest


class TestGetAppDataDir:
    """Tests for get_app_data_dir()."""

    def test_default_path_when_xdg_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    def test_respects_absolute_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        result = get_app_data_dir()
        assert result == tmp_path / "trimarr"

    def test_ignores_relative_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
        result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    def test_ignores_empty_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "")
        result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    def test_returns_path_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert isinstance(get_app_data_dir(), Path)
