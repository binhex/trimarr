"""General utility functions for trimarr."""

from __future__ import annotations

import os
import stat
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

# ELF magic bytes — first 4 bytes of any Linux ELF binary
_ELF_MAGIC = b"\x7fELF"


def get_app_data_dir() -> Path:
    """Return the application data directory, honouring XDG_DATA_HOME.

    Returns ``$XDG_DATA_HOME/trimarr`` when the env var is set to an absolute
    path, otherwise ``~/.local/share/trimarr``.  Relative values and unexpanded
    tildes are ignored per the XDG Base Directory Specification.
    """
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg:
        xdg_path = Path(xdg)
        if xdg_path.is_absolute():
            return xdg_path / "trimarr"
    return Path.home() / ".local" / "share" / "trimarr"


# GitHub repo that publishes statically compiled MKVToolNix binaries for Linux
_MKVTOOLNIX_REPO = "Jesseatgao/MKVToolNix-static-builds"
_MKVTOOLNIX_ASSET = "mkvtoolnix-x86_64-linux.tar.xz"
_GITHUB_API = "https://api.github.com"
# Filename written alongside the mkvmerge binary to record the installed release tag.
_VERSION_FILE = "mkvmerge.version"


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
    """Download the latest statically compiled mkvmerge binary for Linux and install it.

    Fetches the latest release from ``Jesseatgao/MKVToolNix-static-builds``,
    downloads the ``mkvtoolnix-x86_64-linux.tar.xz`` archive, extracts the
    ``mkvmerge`` binary and places it at ``<dest_dir>/mkvmerge``.

    Args:
        dest_dir: Directory to place the ``mkvmerge`` binary in.  Defaults to
            ``<app_data_dir>/bin``.

    Returns:
        :class:`~pathlib.Path` to the installed ``mkvmerge`` binary.

    Raises:
        RuntimeError: If the asset or binary cannot be located, or the extracted
            file is not a valid ELF binary.
        requests.HTTPError: On download failures.
    """
    if dest_dir is None:
        dest_dir = get_app_data_dir() / "bin"

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_binary = dest_dir / "mkvmerge"

    # Resolve latest download URL and release tag via GitHub API
    download_url, release_tag = _get_latest_release_info(_MKVTOOLNIX_REPO, _MKVTOOLNIX_ASSET)

    # Download archive into a temp file
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / _MKVTOOLNIX_ASSET

        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()

        with archive_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract mkvmerge from the archive
        with tarfile.open(archive_path, "r:xz") as tar:
            # Find the mkvmerge member (may be nested inside a subdirectory)
            mkvmerge_member = next(
                (m for m in tar.getmembers() if Path(m.name).name == "mkvmerge"),
                None,
            )
            if mkvmerge_member is None:
                raise RuntimeError(f"Could not find 'mkvmerge' binary inside '{_MKVTOOLNIX_ASSET}'.")

            tar.extract(mkvmerge_member, path=tmp, filter="data")

            # Use rglob to locate the extracted file regardless of nesting depth
            matches = list(Path(tmp).rglob("mkvmerge"))
            if not matches:
                raise RuntimeError("Could not locate 'mkvmerge' after extraction.")
            extracted = matches[0]

            # Sanity-check: verify ELF magic bytes before installing
            with extracted.open("rb") as fh:
                magic = fh.read(4)
            if magic != _ELF_MAGIC:
                raise RuntimeError(f"Downloaded 'mkvmerge' does not appear to be a valid ELF binary (magic={magic!r}).")

            # Atomically install the binary: write to a uniquely-named temp file in dest_dir,
            # set permissions, then rename into place so concurrent readers never observe a
            # partial write and concurrent trimarr processes do not clobber each other.
            tmp_bin_fd, tmp_bin_str = tempfile.mkstemp(dir=dest_dir, prefix=".mkvmerge.bin.", suffix=".tmp")
            os.close(tmp_bin_fd)
            tmp_bin = Path(tmp_bin_str)
            try:
                tmp_bin.write_bytes(extracted.read_bytes())
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
