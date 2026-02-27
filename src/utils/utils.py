"""General utility functions for trimarr."""

from __future__ import annotations

import stat
import tarfile
import tempfile
from pathlib import Path

import requests


def get_project_root() -> Path:
    """Return the project root directory (three levels up from this file)."""
    return Path(__file__).parent.parent.parent


# GitHub repo that publishes statically compiled MKVToolNix binaries for Linux
_MKVTOOLNIX_REPO = "Jesseatgao/MKVToolNix-static-builds"
_MKVTOOLNIX_ASSET = "mkvtoolnix-x86_64-linux.tar.xz"
_GITHUB_API = "https://api.github.com"


def _get_latest_release_asset_url(repo: str, asset_name: str) -> str:
    """Return the browser download URL for *asset_name* in the latest release of *repo*.

    Args:
        repo: GitHub repo in ``owner/name`` format.
        asset_name: Exact filename of the release asset.

    Returns:
        Download URL string.

    Raises:
        RuntimeError: If the asset cannot be found in the latest release.
        requests.HTTPError: On non-2xx GitHub API responses.
    """
    url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    release = response.json()
    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            return str(asset["browser_download_url"])

    tag = release.get("tag_name", "unknown")
    raise RuntimeError(f"Asset '{asset_name}' not found in latest release '{tag}' of '{repo}'.")


def download_mkvmerge(dest_dir: str | Path | None = None) -> Path:
    """Download the latest statically compiled mkvmerge binary for Linux and install it.

    Fetches the latest release from ``Jesseatgao/MKVToolNix-static-builds``,
    downloads the ``mkvtoolnix-x86_64-linux.tar.xz`` archive, extracts the
    ``mkvmerge`` binary and places it at ``<dest_dir>/mkvmerge``.

    Args:
        dest_dir: Directory to place the ``mkvmerge`` binary in.  Defaults to
            ``<project_root>/bin``.

    Returns:
        :class:`~pathlib.Path` to the installed ``mkvmerge`` binary.

    Raises:
        RuntimeError: If the asset or binary cannot be located.
        requests.HTTPError: On download failures.
    """
    if dest_dir is None:
        dest_dir = get_project_root() / "bin"

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_binary = dest_dir / "mkvmerge"

    # Resolve latest download URL via GitHub API
    download_url = _get_latest_release_asset_url(_MKVTOOLNIX_REPO, _MKVTOOLNIX_ASSET)

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

            # Extract to temp dir then move to final destination
            tar.extract(mkvmerge_member, path=tmp, filter="data")
            extracted = Path(tmp) / mkvmerge_member.name

            dest_binary.write_bytes(extracted.read_bytes())

    # Make the binary executable
    dest_binary.chmod(dest_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return dest_binary
