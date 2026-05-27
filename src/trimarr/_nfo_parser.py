"""NFO file parsing and discovery for movie/TV show metadata.

Provides ``NfoMetadata`` (dataclass), ``discover_nfo()`` for locating
.nfo files on disk, and ``parse_nfo()`` for extracting metadata from
XML-formatted .nfo files created by media library managers (Radarr,
Sonarr, Kodi, etc.).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_TVSHOW_UPWALK_DEPTH = 3


@dataclass
class NfoMetadata:
    """Metadata extracted from an ``.nfo`` file.

    Attributes:
        title: Movie/TV show title from ``<title>`` element.
        original_title: Original (non-localised) title from
            ``<originaltitle>`` element, or *None*.
        year: Release year as a string from ``<year>``, or *None*.
        imdb_id: IMDb ID (e.g. ``"tt0468569"``) from ``<imdbid>`` or
            ``<uniqueid type="imdb">``, or *None*.
        tmdb_id: TMDb ID (e.g. ``"155"``) from ``<tmdbid>`` or
            ``<uniqueid type="tmdb">``, or *None*.
    """

    title: str | None
    original_title: str | None = None
    year: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None


def discover_nfo(mkv_path: Path) -> Path | None:
    """Locate the ``.nfo`` file corresponding to *mkv_path*.

    Discovery order:
    1. Same stem — ``{mkv_stem}.nfo`` in the same directory.
    2. Any ``.nfo`` — first alphabetical ``*.nfo`` in the same directory.
    3. TV show root — walk up from the MKV's directory, checking each
       parent for ``tvshow.nfo`` (up to ``_MAX_TVSHOW_UPWALK_DEPTH``
       levels).

    Returns:
        Path to the first matching ``.nfo`` file, or *None* if no suitable
        file exists.
    """
    parent = mkv_path.parent

    # 1. Same stem
    stem_nfo = parent / f"{mkv_path.stem}.nfo"
    if stem_nfo.is_file():
        return stem_nfo

    # 2. Any .nfo in directory (case-insensitive glob)
    nfo_files = sorted(parent.glob("*.[nN][fF][oO]"))
    if nfo_files:
        return nfo_files[0]

    # 3. Walk up for tvshow.nfo
    current = parent
    for _ in range(_MAX_TVSHOW_UPWALK_DEPTH):
        tvshow_nfo = current / "tvshow.nfo"
        if tvshow_nfo.is_file():
            return tvshow_nfo
        tvshow_nfo_upper = current / "tvshow.NFO"
        if tvshow_nfo_upper.is_file():
            return tvshow_nfo_upper
        parent_of = current.parent
        if parent_of == current:
            break  # reached filesystem root
        current = parent_of

    return None


def _extract_text(parent: ET.Element, tag: str) -> str | None:
    """Return the stripped text content of *tag* inside *parent*, or None."""
    elem = parent.find(tag)
    if elem is None or elem.text is None:
        return None
    stripped = elem.text.strip()
    return stripped if stripped else None


def _extract_uniqueid(parent: ET.Element, id_type: str) -> str | None:
    """Extract text from a ``<uniqueid type='{id_type}'>`` element, or None."""
    for child in parent.iter("uniqueid"):
        if child.get("type") == id_type and child.text:
            stripped = child.text.strip()
            if stripped:
                return stripped
    return None


def parse_nfo(path: Path) -> NfoMetadata | None:
    """Parse an ``.nfo`` XML file and return a structured ``NfoMetadata``.

    Handles both ``<movie>`` and ``<tvshow>`` root elements.  Returns
    *None* when the file cannot be parsed or contains no useful metadata
    (no title, imdb_id, or tmdb_id).

    Args:
        path: Path to the ``.nfo`` XML file.

    Returns:
        An ``NfoMetadata`` instance, or *None* on parse failure or empty
        result.
    """
    try:
        tree = ET.parse(path)
    except (ET.ParseError, FileNotFoundError, PermissionError) as exc:
        logger.debug("Failed to parse NFO '%s': %s", path, exc)
        return None

    root = tree.getroot()
    if root is None or root.tag not in ("movie", "tvshow"):
        logger.debug(
            "NFO '%s' has unexpected root element: %s",
            path,
            root.tag if root is not None else "None",
        )
        return None

    title = _extract_text(root, "title")
    original_title = _extract_text(root, "originaltitle")
    year = _extract_text(root, "year")

    # <imdbid> takes precedence over <uniqueid type="imdb">
    imdb_id = _extract_text(root, "imdbid")
    if imdb_id is None:
        imdb_id = _extract_uniqueid(root, "imdb")

    # <tmdbid> takes precedence over <uniqueid type="tmdb">
    tmdb_id = _extract_text(root, "tmdbid")
    if tmdb_id is None:
        tmdb_id = _extract_uniqueid(root, "tmdb")

    if title is None and imdb_id is None and tmdb_id is None:
        logger.debug("NFO '%s' has no usable fields (no title/IMDb/TMDb ID).", path)
        return None

    return NfoMetadata(
        title=title,
        original_title=original_title,
        year=year,
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
    )
