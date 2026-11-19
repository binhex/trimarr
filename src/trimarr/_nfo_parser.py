"""NFO file parsing and discovery for movie/TV show metadata.

Provides ``NfoMetadata`` (dataclass), ``discover_nfo()`` for locating
.nfo files on disk, and ``parse_nfo()`` for extracting metadata from
XML-formatted .nfo files created by media library managers (Radarr,
Sonarr, Kodi, etc.).
"""

from __future__ import annotations

import logging
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_TVSHOW_UPWALK_DEPTH = 3

_MAX_NFO_BYTES = 5 * 1024 * 1024
"""Upper size bound for an ``.nfo`` file.  Real NFO files are a few KB;
anything larger is rejected before it is read, bounding memory and CPU use."""

_UTF16_32_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")
"""Byte-order marks for UTF-16/UTF-32.  The raw DTD scan below only works on
ASCII-compatible encodings, so these files are rejected outright."""

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
"""Detects a DTD declaration.  NFO files never need one, and a crafted
``<!DOCTYPE>`` enables XML entity-expansion ("billion laughs") attacks."""

_CONVENTIONAL_NFO_STEMS = ("movie", "tvshow", "episodedetails", "season")
"""NFO file stems that conventionally describe the media beside them, so a
sole one is trusted even when the folder holds several files (edition sets)."""

_KNOWN_NFO_ROOTS = ("movie", "tvshow", "episodedetails", "season")
"""Root element names that can appear in NFO files produced by
Kodi / Radarr / Sonarr."""

_ROOT_OPEN_RE = re.compile(r"<(" + "|".join(_KNOWN_NFO_ROOTS) + r")(?:\s|>)")
"""Matches the opening tag of a known NFO root element anywhere in the text.

Not anchored to the start of a line: exports sometimes put the XML declaration
and the root element on the same line, and the trailing-junk recovery path must
still find the root.  ``<!DOCTYPE>`` is rejected before parsing, so searching
the raw text cannot be tricked by a declaration.
Capture group 1 contains the element name (e.g. "movie")."""


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
        tvdb_id: TVDB ID (e.g. ``"7537283"``) from ``<tvdbid>`` or
            ``<uniqueid type="tvdb">``, or *None*.
        is_tvshow: True when the NFO root element was ``<tvshow>``, so callers
            can select TV endpoints without guessing from the filename.
    """

    title: str | None
    original_title: str | None = None
    year: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    is_tvshow: bool = False


def discover_nfo(mkv_path: Path) -> Path | None:
    """Locate the ``.nfo`` file corresponding to *mkv_path*.

    Discovery order:
    1. Same stem — ``{mkv_stem}.nfo`` in the same directory.
    2. Unambiguous directory match — a sole ``.nfo`` beside a sole ``.mkv``,
       or a sole ``.nfo`` whose stem is a conventional NFO name (``movie``,
       ``tvshow``, ``episodedetails``, ``season``) or matches the directory
       name.
    3. TV show root — check the MKV's directory and each parent for
       ``tvshow.nfo`` (up to ``_MAX_TVSHOW_UPWALK_DEPTH`` levels).

    Returns:
        Path to the first matching ``.nfo`` file, or *None* if no suitable
        file exists.
    """
    parent = mkv_path.parent

    # 1. Same stem
    stem_nfo = parent / f"{mkv_path.stem}.nfo"
    if stem_nfo.is_file():
        return stem_nfo

    # 2. Sole .nfo in an unambiguous directory.  Ownership is unambiguous when
    #    the directory holds exactly one .nfo and one .mkv, or when the .nfo
    #    stem matches the folder name (Radarr/Sonarr layout with edition
    #    files).  In a directory holding several unrelated media files an
    #    arbitrary .nfo may describe a different title, which would attach the
    #    wrong IMDb/TMDb/TVDB IDs to this file.
    nfo_files = sorted(parent.glob("*.[nN][fF][oO]"))
    mkv_files = sorted(parent.glob("*.[mM][kK][vV]"))
    if len(nfo_files) == 1:
        sole_stem = nfo_files[0].stem.lower()
        if (
            len(mkv_files) == 1
            # Radarr/Sonarr/Kodi conventional stems own the folder outright.
            or sole_stem in _CONVENTIONAL_NFO_STEMS
            or sole_stem == parent.name.lower()
        ):
            return nfo_files[0]

    # 3. Walk up for tvshow.nfo (case-insensitive)
    return find_tvshow_nfo(parent)


def find_tvshow_nfo(start_dir: Path) -> Path | None:
    """Walk up from *start_dir* looking for a ``tvshow.nfo`` file.

    Checks *start_dir* and each ancestor directory (up to
    ``_MAX_TVSHOW_UPWALK_DEPTH`` levels) for a case-insensitive
    ``tvshow.nfo``.  Returns the first match, or *None* if none exists.
    """
    current = start_dir
    for _ in range(_MAX_TVSHOW_UPWALK_DEPTH):
        for candidate in sorted(current.glob("*.[nN][fF][oO]")):
            if candidate.stem.lower() == "tvshow":
                return candidate
        parent_of = current.parent
        if parent_of == current:
            break  # reached filesystem root
        current = parent_of
    return None


def _strip_nfo_trailing_junk(raw: str) -> str | None:
    """Strip trailing content after the closing root tag in *raw* NFO text.

    Attempts to recover parseable XML from files that have trailing content
    (e.g. URLs) after the root closing tag — a common artifact from Kodi
    library exports.

    Returns the cleaned XML string, or *None* if no root element boundary
    can be identified.
    """
    m = _ROOT_OPEN_RE.search(raw)
    if not m:
        return None

    root_name = m.group(1)
    closing_tag = f"</{root_name}>"
    last = raw.rfind(closing_tag)
    if last == -1:
        return None

    cleaned = raw[: last + len(closing_tag)].rstrip() + "\n"
    return cleaned


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


def _is_valid_nfo_root(root: ET.Element | None) -> bool:
    """Return True if *root* is a valid NFO root element (movie/tvshow)."""
    return root is not None and root.tag in ("movie", "tvshow")


def _has_nfo_content(
    title: str | None,
    original_title: str | None,
    imdb_id: str | None,
    tmdb_id: str | None,
    tvdb_id: str | None,
) -> bool:
    """Return True if at least one useful metadata field is present."""
    return (
        title is not None
        or original_title is not None
        or imdb_id is not None
        or tmdb_id is not None
        or tvdb_id is not None
    )


def _extract_nfo_metadata(root: ET.Element) -> NfoMetadata | None:
    """Extract ``NfoMetadata`` from a parsed XML *root* element.

    Returns *None* if the root is not a recognised NFO element
    (movie/tvshow) or contains no useful metadata fields.
    """
    if not _is_valid_nfo_root(root):
        return None

    title = _extract_text(root, "title")
    original_title = _extract_text(root, "originaltitle")
    year = _extract_text(root, "year")

    imdb_id = _extract_text(root, "imdbid")
    if imdb_id is None:
        imdb_id = _extract_uniqueid(root, "imdb")

    tmdb_id = _extract_text(root, "tmdbid")
    if tmdb_id is None:
        tmdb_id = _extract_uniqueid(root, "tmdb")

    tvdb_id = _extract_text(root, "tvdbid")
    if tvdb_id is None:
        tvdb_id = _extract_uniqueid(root, "tvdb")

    if not _has_nfo_content(title, original_title, imdb_id, tmdb_id, tvdb_id):
        return None

    return NfoMetadata(
        title=title,
        original_title=original_title,
        year=year,
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        is_tvshow=root.tag == "tvshow",
    )


def _parse_nfo_with_cleanup(raw: str) -> NfoMetadata | None:
    """Strip trailing junk from *raw* NFO text and parse it as NFO XML.

    Called as a recovery path when the raw bytes are not valid XML.
    Returns structured metadata or *None* if no root element boundary can
    be identified or parsing still fails after cleanup.
    """
    cleaned = _strip_nfo_trailing_junk(raw)
    if cleaned is None:
        return None

    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return None

    return _extract_nfo_metadata(root)


def parse_nfo(path: Path) -> NfoMetadata | None:
    """Parse an ``.nfo`` XML file and return a structured ``NfoMetadata``.

    Handles both ``<movie>`` and ``<tvshow>`` root elements.  Returns
    *None* when the file cannot be parsed or contains no useful metadata
    (no title, original title, imdb_id, tmdb_id, or tvdb_id).

    Files larger than :data:`_MAX_NFO_BYTES` and files declaring a DTD are
    rejected without parsing, which bounds memory use and blocks XML
    entity-expansion attacks.

    Args:
        path: Path to the ``.nfo`` XML file.

    Returns:
        An ``NfoMetadata`` instance, or *None* on parse failure or empty
        result.
    """
    raw_bytes = _read_nfo_payload(path)
    if raw_bytes is None:
        return None

    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        logger.debug("Failed to parse NFO '%s': %s", path, exc)
        return _parse_nfo_with_cleanup(raw_bytes.decode("utf-8", errors="replace"))

    return _nfo_from_root(path, root)


def _read_nfo_payload(path: Path) -> bytes | None:
    """Read *path* after applying the size, encoding and DTD guards.

    The size is checked from ``stat()`` before the file is read, so an
    oversized ``.nfo`` is never loaded into memory.

    Returns:
        The file contents, or *None* if it cannot be read or fails a guard.
    """
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            # A FIFO, socket or device named *.nfo would block or read forever.
            logger.debug("NFO '%s' rejected: not a regular file.", path)
            return None
        if info.st_size > _MAX_NFO_BYTES:
            logger.debug("NFO '%s' rejected: file exceeds the size limit.", path)
            return None
        with path.open("rb") as handle:
            raw_bytes = handle.read(_MAX_NFO_BYTES + 1)
    except OSError as exc:
        logger.debug("Failed to read NFO '%s': %s", path, exc)
        return None

    if len(raw_bytes) > _MAX_NFO_BYTES:
        logger.debug("NFO '%s' rejected: %d bytes exceeds the size limit.", path, len(raw_bytes))
        return None
    if raw_bytes.startswith(_UTF16_32_BOMS) or b"\x00" in raw_bytes[:4096]:
        # BOM-less UTF-16 would also hide a <!DOCTYPE from the raw scan below.
        logger.debug("NFO '%s' rejected: UTF-16/UTF-32 encoding is not supported.", path)
        return None
    if _DOCTYPE_RE.search(raw_bytes):
        logger.debug("NFO '%s' rejected: declares a DTD.", path)
        return None
    return raw_bytes


def _nfo_from_root(path: Path, root: ET.Element | None) -> NfoMetadata | None:
    """Extract metadata from a parsed *root*, logging why it was unusable."""
    if root is None or not _is_valid_nfo_root(root):
        logger.debug(
            "NFO '%s' has unexpected root element: %s",
            path,
            root.tag if root is not None else "None",
        )
        return None

    meta = _extract_nfo_metadata(root)
    if meta is None:
        logger.debug("NFO '%s' has no usable fields (no title/IMDb/TMDb/TVDB ID).", path)
    return meta
