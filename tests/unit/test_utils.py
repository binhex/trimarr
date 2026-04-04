"""Unit tests for utils.utils."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as req

from utils.utils import get_app_data_dir, get_installed_mkvmerge_tag, get_latest_mkvmerge_tag


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


class TestGetInstalledMkvmergeTag:
    """Tests for get_installed_mkvmerge_tag()."""

    def test_returns_none_when_version_file_absent(self, tmp_path: Path) -> None:
        assert get_installed_mkvmerge_tag(tmp_path) is None

    def test_returns_tag_from_version_file(self, tmp_path: Path) -> None:
        (tmp_path / "mkvmerge.version").write_text("v58.0.0-mingw-w64-posixv1.8el9", encoding="utf-8")
        assert get_installed_mkvmerge_tag(tmp_path) == "v58.0.0-mingw-w64-posixv1.8el9"

    def test_strips_surrounding_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "mkvmerge.version").write_text("  v58.0.0-tag\n", encoding="utf-8")
        assert get_installed_mkvmerge_tag(tmp_path) == "v58.0.0-tag"

    def test_returns_none_for_empty_version_file(self, tmp_path: Path) -> None:
        (tmp_path / "mkvmerge.version").write_text("", encoding="utf-8")
        assert get_installed_mkvmerge_tag(tmp_path) is None

    def test_uses_default_dir_when_none_given(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        # No version file → None
        assert get_installed_mkvmerge_tag() is None

    def test_uses_default_dir_and_finds_tag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        bin_dir = tmp_path / "trimarr" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mkvmerge.version").write_text("v99.0.0-test", encoding="utf-8")
        assert get_installed_mkvmerge_tag() == "v99.0.0-test"


class TestGetLatestMkvmergeTag:
    """Tests for get_latest_mkvmerge_tag()."""

    def test_returns_tag_from_api_response(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag_name": "v58.0.0-mingw-w64-posixv1.8el9", "assets": []}
        with patch("utils.utils.requests.get", return_value=mock_response):
            assert get_latest_mkvmerge_tag() == "v58.0.0-mingw-w64-posixv1.8el9"

    def test_raises_on_missing_tag_name(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"assets": []}
        with (
            patch("utils.utils.requests.get", return_value=mock_response),
            pytest.raises(RuntimeError, match="Could not determine latest mkvmerge release tag"),
        ):
            get_latest_mkvmerge_tag()

    def test_raises_on_http_error(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("404")
        with patch("utils.utils.requests.get", return_value=mock_response), pytest.raises(req.HTTPError):
            get_latest_mkvmerge_tag()
