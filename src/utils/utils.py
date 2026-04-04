"""General utility functions for trimarr."""

from __future__ import annotations

import os
import platform
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

# ELF magic bytes — first 4 bytes of any Linux ELF binary
_ELF_MAGIC = b"\x7fELF"
# PE magic bytes — first 2 bytes of any Windows PE binary (MZ header)
_PE_MAGIC = b"MZ"
# Minimum acceptable size for an extracted mkvmerge binary.
# A real mkvmerge binary is several MiB; anything smaller indicates a
# truncated or corrupt download.
_MIN_BINARY_BYTES = 1024 * 1024  # 1 MiB


def get_app_data_dir() -> Path:
    """Return the platform-appropriate application data directory.

    * **Windows** — ``%LOCALAPPDATA%\\trimarr`` (falls back to ``%APPDATA%``,
      then ``~\\AppData\\Local``).
    * **Linux / other** — ``$XDG_DATA_HOME/trimarr`` when *XDG_DATA_HOME* is set
      to an absolute path, otherwise ``~/.local/share/trimarr``.
    """
    if platform.system() == "Windows":
        app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "trimarr"
        return Path.home() / "AppData" / "Local" / "trimarr"

    # Linux / other POSIX: honour XDG Base Directory Specification.
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg:
        xdg_path = Path(xdg)
        if xdg_path.is_absolute():
            return xdg_path / "trimarr"
    return Path.home() / ".local" / "share" / "trimarr"


# GitHub repo that publishes statically compiled MKVToolNix binaries
_MKVTOOLNIX_REPO = "Jesseatgao/MKVToolNix-static-builds"
_GITHUB_API = "https://api.github.com"
# Filename written alongside the mkvmerge binary to record the installed release tag.
_VERSION_FILE = "mkvmerge.version"


def _get_platform_asset() -> tuple[str, str]:
    """Return ``(asset_filename, binary_name)`` for the current OS and CPU architecture.

    Supported platforms:

    * **Linux x86_64** — ``mkvtoolnix-x86_64-linux.tar.xz`` / ``mkvmerge``
    * **Windows x86_64** — ``mkvtoolnix-x86_64-win.zip`` / ``mkvmerge.exe``

    Returns:
        Tuple of ``(asset_filename, binary_name)``.

    Raises:
        RuntimeError: If the current platform has no pre-built binary available.
    """
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "mkvtoolnix-x86_64-linux.tar.xz", "mkvmerge"
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "mkvtoolnix-x86_64-win.zip", "mkvmerge.exe"

    raise RuntimeError(
        f"No pre-built mkvmerge binary is available for {system}/{platform.machine()}. "
        "Install mkvmerge manually and specify its path with --mkvmerge-path."
    )


def _extract_from_tar(archive_path: Path, binary_name: str, tmp_dir: Path) -> Path:
    """Extract *binary_name* from a ``.tar.xz`` archive and return its path."""
    with tarfile.open(archive_path, "r:xz") as tar:
        member = next(
            (m for m in tar.getmembers() if Path(m.name).name == binary_name),
            None,
        )
        if member is None:
            raise RuntimeError(f"Could not find '{binary_name}' inside '{archive_path.name}'.")
        tar.extract(member, path=tmp_dir, filter="data")
    matches = list(tmp_dir.rglob(binary_name))
    if not matches:
        raise RuntimeError(f"Could not locate '{binary_name}' after extraction.")
    return matches[0]


def _extract_from_zip(archive_path: Path, binary_name: str, tmp_dir: Path) -> Path:
    """Extract *binary_name* from a ``.zip`` archive and return its path."""
    with zipfile.ZipFile(archive_path) as zf:
        names = [n for n in zf.namelist() if Path(n).name == binary_name]
        if not names:
            raise RuntimeError(f"Could not find '{binary_name}' inside '{archive_path.name}'.")
        zf.extract(names[0], path=tmp_dir)
    matches = list(tmp_dir.rglob(binary_name))
    if not matches:
        raise RuntimeError(f"Could not locate '{binary_name}' after extraction.")
    return matches[0]


def _fetch_latest_release(repo: str) -> dict:
    """Return the parsed JSON body of the latest GitHub release for *repo*.

    Args:
        repo: GitHub repo in ``owner/name`` format.

    Raises:
        requests.HTTPError: On non-2xx GitHub API responses.
    """
    url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return dict(response.json())


def _get_latest_release_info(repo: str, asset_name: str) -> tuple[str, str]:
    """Return ``(browser_download_url, tag_name)`` for *asset_name* in the latest release of *repo*.

    Args:
        repo: GitHub repo in ``owner/name`` format.
        asset_name: Exact filename of the release asset.

    Returns:
        Tuple of (validated HTTPS download URL, release tag string).

    Raises:
        RuntimeError: If the asset cannot be found in the latest release or the URL is not from GitHub.
        requests.HTTPError: On non-2xx GitHub API responses.
    """
    release = _fetch_latest_release(repo)
    tag = str(release.get("tag_name", "unknown"))

    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            download_url = str(asset["browser_download_url"])
            # Validate the URL is from a trusted GitHub domain and uses HTTPS
            parsed = urlparse(download_url)
            trusted = {"github.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
            if parsed.scheme != "https" or parsed.hostname not in trusted:
                raise RuntimeError(
                    f"Download URL '{download_url}' is not from a trusted GitHub domain over HTTPS. Refusing to use it."
                )
            return download_url, tag

    raise RuntimeError(f"Asset '{asset_name}' not found in latest release '{tag}' of '{repo}'.")


def get_latest_mkvmerge_tag() -> str:
    """Return the tag name of the latest mkvmerge release via a lightweight GitHub API call.

    Only the release metadata JSON (~few KB) is fetched — no binary download occurs.

    Returns:
        Release tag string (e.g. ``"v58.0.0-mingw-w64-posixv1.8el9"``).

    Raises:
        RuntimeError: If the release tag cannot be determined.
        requests.HTTPError: On non-2xx GitHub API responses.
    """
    release = _fetch_latest_release(_MKVTOOLNIX_REPO)
    tag = release.get("tag_name")
    if not tag:
        raise RuntimeError("Could not determine latest mkvmerge release tag from GitHub API response.")
    return str(tag)


def get_installed_mkvmerge_tag(dest_dir: str | Path | None = None) -> str | None:
    """Return the release tag stored alongside the installed mkvmerge binary, or *None*.

    The tag is written to ``<dest_dir>/mkvmerge.version`` by :func:`download_mkvmerge`.
    Returns *None* if the file does not exist (e.g. first install or pre-versioning install).

    Args:
        dest_dir: Directory containing the mkvmerge binary.  Defaults to ``<app_data_dir>/bin``.
    """
    if dest_dir is None:
        dest_dir = get_app_data_dir() / "bin"
    version_file = Path(dest_dir) / _VERSION_FILE
    if not version_file.exists():
        return None
    return version_file.read_text(encoding="utf-8").strip() or None


def download_mkvmerge(dest_dir: str | Path | None = None) -> Path:
    """Download the latest statically compiled mkvmerge binary and install it.

    Detects the current OS and CPU architecture to select the correct asset from
    ``Jesseatgao/MKVToolNix-static-builds``.  Supported platforms:

    * **Linux x86_64** — ``.tar.xz`` archive, ELF binary
    * **Windows x86_64** — ``.zip`` archive, PE binary

    Args:
        dest_dir: Directory to place the binary.  Defaults to ``<app_data_dir>/bin``.

    Returns:
        :class:`~pathlib.Path` to the installed mkvmerge binary.

    Raises:
        RuntimeError: If the platform is unsupported, the asset or binary cannot be
            located, or the extracted file is not a valid executable.
        requests.HTTPError: On download failures.
    """
    asset_name, binary_name = _get_platform_asset()
    is_windows = platform.system() == "Windows"

    if dest_dir is None:
        dest_dir = get_app_data_dir() / "bin"

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_binary = dest_dir / binary_name

    # Resolve latest download URL and release tag via GitHub API
    download_url, release_tag = _get_latest_release_info(_MKVTOOLNIX_REPO, asset_name)

    # Download archive into a temp directory
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / asset_name

        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()

        with archive_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract the mkvmerge binary from the archive
        if is_windows:
            extracted = _extract_from_zip(archive_path, binary_name, Path(tmp))
        else:
            extracted = _extract_from_tar(archive_path, binary_name, Path(tmp))

        # Sanity-check: validate the binary header matches the expected format
        expected_magic = _PE_MAGIC if is_windows else _ELF_MAGIC
        binary_type = "PE" if is_windows else "ELF"
        with extracted.open("rb") as fh:
            magic = fh.read(4)
        if not magic.startswith(expected_magic):
            raise RuntimeError(
                f"Downloaded '{binary_name}' does not appear to be a valid {binary_type} binary (magic={magic!r})."
            )

        extracted_size = extracted.stat().st_size
        if extracted_size < _MIN_BINARY_BYTES:
            raise RuntimeError(
                f"Extracted '{binary_name}' is only {extracted_size} bytes; "
                f"download appears truncated (expected >= {_MIN_BINARY_BYTES} bytes)."
            )

        # Atomically install the binary: write to a uniquely-named temp file in dest_dir,
        # set permissions, then rename into place so concurrent readers never observe a
        # partial write and concurrent trimarr processes do not clobber each other.
        tmp_bin_fd, tmp_bin_str = tempfile.mkstemp(dir=dest_dir, prefix=".mkvmerge.bin.", suffix=".tmp")
        os.close(tmp_bin_fd)
        tmp_bin = Path(tmp_bin_str)
        try:
            tmp_bin.write_bytes(extracted.read_bytes())
            if not is_windows:
                tmp_bin.chmod(tmp_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(tmp_bin, dest_binary)
        except Exception:
            tmp_bin.unlink(missing_ok=True)
            raise

    # Atomically write the version file so the binary and version are never mismatched.
    # Use a unique temp name to prevent concurrent trimarr processes clobbering each other.
    tmp_ver_fd, tmp_ver_str = tempfile.mkstemp(dir=dest_dir, prefix=".mkvmerge.version.", suffix=".tmp")
    os.close(tmp_ver_fd)
    tmp_ver = Path(tmp_ver_str)
    try:
        tmp_ver.write_text(release_tag, encoding="utf-8")
        os.replace(tmp_ver, dest_dir / _VERSION_FILE)
    except Exception:
        tmp_ver.unlink(missing_ok=True)
        raise

    return dest_binary
