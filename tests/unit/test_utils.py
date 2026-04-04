"""Unit tests for utils.utils."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as req

from utils.utils import (
    _extract_from_tar,
    _extract_from_zip,
    _get_latest_release_info,
    _get_platform_asset,
    download_mkvmerge,
    get_app_data_dir,
    get_installed_mkvmerge_tag,
    get_latest_mkvmerge_tag,
)

# ---------------------------------------------------------------------------
# Archive helpers for download tests
# ---------------------------------------------------------------------------

_ELF_CONTENT = b"\x7fELF" + b"\x00" * 200
_PE_CONTENT = b"MZ" + b"\x00" * 200
_BAD_CONTENT = b"BADMAGIC" + b"\x00" * 200


def _make_tar_xz(filename: str, content: bytes) -> bytes:
    """Return bytes of a .tar.xz archive containing *filename* with *content*."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        info = tarfile.TarInfo(name=f"mkvtoolnix/{filename}")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_zip(filename: str, content: bytes) -> bytes:
    """Return bytes of a .zip archive containing *filename* with *content*."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"mkvtoolnix/{filename}", content)
    return buf.getvalue()


def _streaming_response(data: bytes) -> MagicMock:
    """Return a mock requests.Response that streams *data* via iter_content."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=iter([data]))
    return resp


class TestGetAppDataDir:
    """Tests for get_app_data_dir()."""

    # -- Linux / XDG tests ---------------------------------------------------

    def test_linux_default_path_when_xdg_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        with patch("utils.utils.platform.system", return_value="Linux"):
            result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    def test_linux_respects_absolute_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        with patch("utils.utils.platform.system", return_value="Linux"):
            result = get_app_data_dir()
        assert result == tmp_path / "trimarr"

    def test_linux_ignores_relative_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
        with patch("utils.utils.platform.system", return_value="Linux"):
            result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    def test_linux_ignores_empty_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "")
        with patch("utils.utils.platform.system", return_value="Linux"):
            result = get_app_data_dir()
        assert result == Path.home() / ".local" / "share" / "trimarr"

    # -- Windows tests -------------------------------------------------------

    def test_windows_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\user\AppData\Local")
        monkeypatch.setenv("APPDATA", r"C:\Users\user\AppData\Roaming")
        with patch("utils.utils.platform.system", return_value="Windows"):
            result = get_app_data_dir()
        assert result == Path(r"C:\Users\user\AppData\Local") / "trimarr"

    def test_windows_falls_back_to_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("APPDATA", r"C:\Users\user\AppData\Roaming")
        with patch("utils.utils.platform.system", return_value="Windows"):
            result = get_app_data_dir()
        assert result == Path(r"C:\Users\user\AppData\Roaming") / "trimarr"

    def test_windows_falls_back_to_home_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        with patch("utils.utils.platform.system", return_value="Windows"):
            result = get_app_data_dir()
        assert result == Path.home() / "AppData" / "Local" / "trimarr"

    def test_returns_path_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        with patch("utils.utils.platform.system", return_value="Linux"):
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
        with patch("utils.utils.platform.system", return_value="Linux"):
            assert get_installed_mkvmerge_tag() is None

    def test_uses_default_dir_and_finds_tag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        bin_dir = tmp_path / "trimarr" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mkvmerge.version").write_text("v99.0.0-test", encoding="utf-8")
        with patch("utils.utils.platform.system", return_value="Linux"):
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


class TestGetPlatformAsset:
    """Tests for _get_platform_asset() — platform detection logic."""

    def test_linux_x86_64(self) -> None:
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
        ):
            asset, binary = _get_platform_asset()
            assert asset == "mkvtoolnix-x86_64-linux.tar.xz"
            assert binary == "mkvmerge"

    def test_windows_amd64(self) -> None:
        with (
            patch("utils.utils.platform.system", return_value="Windows"),
            patch("utils.utils.platform.machine", return_value="AMD64"),
        ):
            asset, binary = _get_platform_asset()
            assert asset == "mkvtoolnix-x86_64-win.zip"
            assert binary == "mkvmerge.exe"

    def test_linux_aarch64_raises(self) -> None:
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="aarch64"),
            pytest.raises(RuntimeError, match="No pre-built mkvmerge binary"),
        ):
            _get_platform_asset()

    def test_unsupported_os_raises(self) -> None:
        with (
            patch("utils.utils.platform.system", return_value="Darwin"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            pytest.raises(RuntimeError, match="No pre-built mkvmerge binary"),
        ):
            _get_platform_asset()

    def test_error_message_includes_platform_info(self) -> None:
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="aarch64"),
            pytest.raises(RuntimeError, match="--mkvmerge-path"),
        ):
            _get_platform_asset()


# ---------------------------------------------------------------------------
# _extract_from_tar
# ---------------------------------------------------------------------------


class TestExtractFromTar:
    """Tests for _extract_from_tar()."""

    def test_extracts_binary_from_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.tar.xz"
        archive.write_bytes(_make_tar_xz("mkvmerge", _ELF_CONTENT))
        extracted = _extract_from_tar(archive, "mkvmerge", tmp_path / "out")
        assert extracted.read_bytes() == _ELF_CONTENT

    def test_raises_when_binary_not_in_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.tar.xz"
        archive.write_bytes(_make_tar_xz("other_binary", _ELF_CONTENT))
        with pytest.raises(RuntimeError, match="Could not find 'mkvmerge'"):
            _extract_from_tar(archive, "mkvmerge", tmp_path / "out")


# ---------------------------------------------------------------------------
# _extract_from_zip
# ---------------------------------------------------------------------------


class TestExtractFromZip:
    """Tests for _extract_from_zip()."""

    def test_extracts_binary_from_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.zip"
        archive.write_bytes(_make_zip("mkvmerge.exe", _PE_CONTENT))
        extracted = _extract_from_zip(archive, "mkvmerge.exe", tmp_path / "out")
        assert extracted.read_bytes() == _PE_CONTENT

    def test_raises_when_binary_not_in_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.zip"
        archive.write_bytes(_make_zip("other.exe", _PE_CONTENT))
        with pytest.raises(RuntimeError, match="Could not find 'mkvmerge.exe'"):
            _extract_from_zip(archive, "mkvmerge.exe", tmp_path / "out")


# ---------------------------------------------------------------------------
# _get_latest_release_info — URL trust validation
# ---------------------------------------------------------------------------


class TestGetLatestReleaseInfo:
    """Tests for _get_latest_release_info() URL trust enforcement."""

    def _release(self, url: str) -> dict:
        return {"tag_name": "v1.0", "assets": [{"name": "asset.tar.xz", "browser_download_url": url}]}

    def test_accepts_github_https_url(self) -> None:
        release = self._release("https://github.com/owner/repo/releases/download/v1.0/asset.tar.xz")
        with patch("utils.utils._fetch_latest_release", return_value=release):
            url, tag = _get_latest_release_info("owner/repo", "asset.tar.xz")
        assert url.startswith("https://github.com")
        assert tag == "v1.0"

    def test_accepts_objects_githubusercontent_url(self) -> None:
        release = self._release("https://objects.githubusercontent.com/releases/asset.tar.xz")
        with patch("utils.utils._fetch_latest_release", return_value=release):
            url, tag = _get_latest_release_info("owner/repo", "asset.tar.xz")
        assert "githubusercontent.com" in url

    def test_rejects_http_url(self) -> None:
        release = self._release("http://github.com/owner/repo/releases/asset.tar.xz")
        with (
            patch("utils.utils._fetch_latest_release", return_value=release),
            pytest.raises(RuntimeError, match="trusted GitHub domain"),
        ):
            _get_latest_release_info("owner/repo", "asset.tar.xz")

    def test_rejects_non_github_https_url(self) -> None:
        release = self._release("https://evil.com/mkvmerge.tar.xz")
        with (
            patch("utils.utils._fetch_latest_release", return_value=release),
            pytest.raises(RuntimeError, match="trusted GitHub domain"),
        ):
            _get_latest_release_info("owner/repo", "asset.tar.xz")

    def test_raises_when_asset_not_found(self) -> None:
        release = {"tag_name": "v1.0", "assets": []}
        with (
            patch("utils.utils._fetch_latest_release", return_value=release),
            pytest.raises(RuntimeError, match="Asset 'asset.tar.xz' not found"),
        ):
            _get_latest_release_info("owner/repo", "asset.tar.xz")


# ---------------------------------------------------------------------------
# download_mkvmerge
# ---------------------------------------------------------------------------


class TestDownloadMkvmerge:
    """Tests for download_mkvmerge() — extraction, validation, atomic install."""

    def _linux_patches(self, archive_bytes: bytes) -> tuple:
        return (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.tar.xz", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
        )

    def test_happy_path_linux_installs_binary_and_version_file(self, tmp_path: Path) -> None:
        archive_bytes = _make_tar_xz("mkvmerge", _ELF_CONTENT)
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.tar.xz", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
        ):
            result = download_mkvmerge(dest_dir=tmp_path)

        assert result == tmp_path / "mkvmerge"
        assert result.read_bytes() == _ELF_CONTENT
        assert (tmp_path / "mkvmerge.version").read_text(encoding="utf-8") == "v58.0.0"
        assert not list(tmp_path.glob(".mkvmerge.bin.*.tmp"))

    def test_happy_path_windows_installs_binary_and_version_file(self, tmp_path: Path) -> None:
        archive_bytes = _make_zip("mkvmerge.exe", _PE_CONTENT)
        with (
            patch("utils.utils.platform.system", return_value="Windows"),
            patch("utils.utils.platform.machine", return_value="AMD64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.zip", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
        ):
            result = download_mkvmerge(dest_dir=tmp_path)

        assert result == tmp_path / "mkvmerge.exe"
        assert result.read_bytes() == _PE_CONTENT
        assert (tmp_path / "mkvmerge.version").read_text(encoding="utf-8") == "v58.0.0"

    def test_wrong_magic_bytes_raises_and_no_binary_installed(self, tmp_path: Path) -> None:
        """A binary with wrong magic bytes must raise RuntimeError; nothing installed."""
        archive_bytes = _make_tar_xz("mkvmerge", _BAD_CONTENT)
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.tar.xz", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
            pytest.raises(RuntimeError, match="valid ELF binary"),
        ):
            download_mkvmerge(dest_dir=tmp_path)

        assert not (tmp_path / "mkvmerge").exists()
        assert not list(tmp_path.glob(".mkvmerge.bin.*.tmp"))

    def test_temp_binary_cleaned_up_when_atomic_replace_fails(self, tmp_path: Path) -> None:
        """If os.replace raises during install, no .tmp file must be left behind."""
        archive_bytes = _make_tar_xz("mkvmerge", _ELF_CONTENT)
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.tar.xz", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
            patch("utils.utils.os.replace", side_effect=OSError("simulated replace failure")),
            pytest.raises(OSError),
        ):
            download_mkvmerge(dest_dir=tmp_path)

        assert not list(tmp_path.glob(".mkvmerge.bin.*.tmp"))

    def test_missing_binary_in_archive_raises(self, tmp_path: Path) -> None:
        """Archive that doesn't contain mkvmerge must raise RuntimeError."""
        archive_bytes = _make_tar_xz("not_mkvmerge", _ELF_CONTENT)
        with (
            patch("utils.utils.platform.system", return_value="Linux"),
            patch("utils.utils.platform.machine", return_value="x86_64"),
            patch(
                "utils.utils._get_latest_release_info",
                return_value=("https://github.com/fake/asset.tar.xz", "v58.0.0"),
            ),
            patch("utils.utils.requests.get", return_value=_streaming_response(archive_bytes)),
            pytest.raises(RuntimeError, match="Could not find 'mkvmerge'"),
        ):
            download_mkvmerge(dest_dir=tmp_path)
