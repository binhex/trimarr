# NFO Native Language Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.nfo` file parsing as the primary source for native language detection, falling back to the existing filename/directory chain when no NFO is available.

**Architecture:** A new `_nfo_parser.py` module handles NFO discovery and XML parsing. The existing lookup chain in `native_language.py` is extended with NFO-based steps at the front — direct ID lookups then NFO-title-based searches. Two new direct-ID lookup functions (`_lookup_imdbpie_by_id`, `_lookup_tmdb_by_id`) skip the fragile search+match step.

**Tech Stack:** Python 3.12, `xml.etree.ElementTree` (stdlib), `imdbpie`, `urllib.request` (stdlib)

---

### Task 1: Create NFO Parser Module

**Files:**
- Create: `src/trimarr/_nfo_parser.py`
- Test: `tests/unit/test_nfo_parser.py`

- [ ] **Step 1: Write the failing tests for NFO parsing and discovery**

Create `tests/unit/test_nfo_parser.py`:

```python
"""Unit tests for trimarr._nfo_parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from trimarr._nfo_parser import NfoMetadata, discover_nfo, parse_nfo


class TestParseNfo:
    """Tests for parse_nfo()."""

    def test_parse_movie_xml(self, tmp_path: Path) -> None:
        """Full movie XML returns correct NfoMetadata."""
        nfo = tmp_path / "movie.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>The Dark Knight</title>
  <originaltitle>The Dark Knight</originaltitle>
  <year>2008</year>
  <imdbid>tt0468569</imdbid>
  <tmdbid>155</tmdbid>
  <uniqueid type="imdb" default="true">tt0468569</uniqueid>
  <uniqueid type="tmdb">155</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.original_title == "The Dark Knight"
        assert result.year == "2008"
        assert result.imdb_id == "tt0468569"
        assert result.tmdb_id == "155"

    def test_parse_tvshow_xml(self, tmp_path: Path) -> None:
        """Full tvshow XML returns correct NfoMetadata."""
        nfo = tmp_path / "tvshow.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Breaking Bad</title>
  <year>2008</year>
  <imdbid>tt0903747</imdbid>
  <tmdbid>1396</tmdbid>
  <uniqueid type="imdb" default="true">tt0903747</uniqueid>
  <uniqueid type="tmdb">1396</uniqueid>
  <uniqueid type="tvdb">81189</uniqueid>
</tvshow>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Breaking Bad"
        assert result.year == "2008"
        assert result.imdb_id == "tt0903747"
        assert result.tmdb_id == "1396"

    def test_parse_minimal_movie(self, tmp_path: Path) -> None:
        """Minimal movie XML with just title works."""
        nfo = tmp_path / "minimal.nfo"
        nfo.write_text("""<?xml version="1.0"?><movie><title>Alien</title></movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Alien"
        assert result.original_title is None
        assert result.year is None
        assert result.imdb_id is None
        assert result.tmdb_id is None

    def test_parse_garbage(self, tmp_path: Path) -> None:
        """Garbage content returns None."""
        nfo = tmp_path / "bad.nfo"
        nfo.write_text("not xml at all <<<<")
        assert parse_nfo(nfo) is None

    def test_parse_empty(self, tmp_path: Path) -> None:
        """Empty file returns None."""
        nfo = tmp_path / "empty.nfo"
        nfo.write_text("")
        assert parse_nfo(nfo) is None

    def test_parse_no_relevant_fields(self, tmp_path: Path) -> None:
        """Valid XML but with only plot/studio fields returns None."""
        nfo = tmp_path / "metadata.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <plot>Some plot</plot>
  <studio>Warner Bros.</studio>
</movie>""")
        assert parse_nfo(nfo) is None

    def test_parse_originaltitle(self, tmp_path: Path) -> None:
        """originaltitle is separate from title."""
        nfo = tmp_path / "foreign.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Wo Hu Cang Long</title>
  <originaltitle>Crouching Tiger, Hidden Dragon</originaltitle>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Wo Hu Cang Long"
        assert result.original_title == "Crouching Tiger, Hidden Dragon"

    def test_parse_uniqueid_fallback_no_imdbid(self, tmp_path: Path) -> None:
        """When <imdbid> is missing, uses <uniqueid type='imdb'>."""
        nfo = tmp_path / "uniqueid.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <uniqueid type="imdb" default="true">tt1234567</uniqueid>
  <uniqueid type="tmdb">999</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt1234567"
        assert result.tmdb_id == "999"

    def test_parse_imdbid_takes_precedence(self, tmp_path: Path) -> None:
        """<imdbid> takes precedence over <uniqueid type='imdb'>."""
        nfo = tmp_path / "precedence.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <imdbid>tt0000001</imdbid>
  <uniqueid type="imdb" default="true">tt0000002</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt0000001"

    def test_parse_whitespace_stripped(self, tmp_path: Path) -> None:
        """Values have surrounding whitespace stripped."""
        nfo = tmp_path / "whitespace.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>  The Dark Knight  </title>
  <year>  2008  </year>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.year == "2008"


class TestDiscoverNfo:
    """Tests for discover_nfo()."""

    def test_stem_match(self, tmp_path: Path) -> None:
        """Same-stem .nfo is found."""
        movie_dir = tmp_path / "Movie (2024)"
        movie_dir.mkdir()
        nfo = movie_dir / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = movie_dir / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_any_nfo_in_dir(self, tmp_path: Path) -> None:
        """No stem match, any .nfo in dir found."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Some.Other.File.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_no_nfo(self, tmp_path: Path) -> None:
        """No .nfo anywhere returns None."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_tvshow_upwalk(self, tmp_path: Path) -> None:
        """tvshow.nfo found by walking up from episode directory."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == tvshow_nfo

    def test_episode_nfo_takes_priority(self, tmp_path: Path) -> None:
        """Episode-level NFO found before tvshow.nfo upwalk."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        episode_nfo = season / "Breaking Bad S01E01.nfo"
        episode_nfo.write_text("<movie><title>Episode</title></movie>")
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        # Should find episode nfo (stem match) before tvshow.nfo
        result = discover_nfo(mkv)
        assert result == episode_nfo

    def test_stem_match_over_any_nfo(self, tmp_path: Path) -> None:
        """Same-stem .nfo is preferred over other .nfo files in dir."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        other_nfo = d / "other.nfo"
        other_nfo.write_text("<movie><title>Other</title></movie>")
        stem_nfo = d / "Movie.nfo"
        stem_nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == stem_nfo

    def test_no_tvshow_nfo_upwalk(self, tmp_path: Path) -> None:
        """Walk up does not find tvshow.nfo -> None."""
        season = tmp_path / "Series" / "Season 1"
        season.mkdir(parents=True)
        mkv = season / "episode.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_any_nfo_mixed_case(self, tmp_path: Path) -> None:
        """Discovery handles .nfo, .NFO, .nfo case-insensitively."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.NFO"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v 2>&1 | head -30
```
Expected: ModuleNotFoundError or ImportError — `_nfo_parser` doesn't exist yet.

- [ ] **Step 3: Create `src/trimarr/_nfo_parser.py`**

```python
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
        # Also check case-variant
        tvshow_nfo_upper = current / "tvshow.NFO"
        if tvshow_nfo_upper.is_file():
            return tvshow_nfo_upper
        parent_of = current.parent
        if parent_of == current:
            break  # reached filesystem root
        current = parent_of

    return None


def _extract_text(parent: ET.Element | None, tag: str) -> str | None:
    """Return the stripped text content of *tag* inside *parent*, or None."""
    if parent is None:
        return None
    elem = parent.find(tag)
    if elem is None or elem.text is None:
        return None
    stripped = elem.text.strip()
    return stripped if stripped else None


def _extract_uniqueid(parent: ET.Element | None, id_type: str) -> str | None:
    """Extract text from a ``<uniqueid type='{id_type}'>`` element, or None."""
    if parent is None:
        return None
    # uniqueid element has no namespace; type attribute identifies it
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
        logger.debug("NFO '%s' has unexpected root element: %s", path, root.tag if root is not None else "None")
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v
```
Expected: All 15+ tests PASS (green).

- [ ] **Step 5: Commit**

```bash
cd /data/trimarr && git add src/trimarr/_nfo_parser.py tests/unit/test_nfo_parser.py && git commit -m "feat: add NFO file parser module with discovery and XML parsing"
```

---

### Task 2: Add Direct ID Lookup Functions

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write failing tests for direct ID lookups**

Append to `tests/unit/test_native_language.py`:

```python
class TestLookupImdbpieById:
    """Tests for _lookup_imdbpie_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct IMDb ID lookup returns spoken languages."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result == ["ger"]
        instance.search_for_title.assert_not_called()

    def test_aux_failure(self, mocker) -> None:
        """When get_title_auxiliary raises, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.side_effect = RuntimeError("API error")
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_no_spoken_languages(self, mocker) -> None:
        """When aux has no spokenLanguages, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_imdbpie_not_installed(self, mocker) -> None:
        """When imdbpie is not installed, returns None."""
        import builtins

        real_import = builtins.__import__

        def _block_imdbpie(name, *args, **kwargs):
            if name == "imdbpie":
                raise ImportError("No module named imdbpie")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=_block_imdbpie)
        from trimarr.native_language import _lookup_imdbpie_by_id

        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None


class TestLookupTmdbById:
    """Tests for _lookup_tmdb_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct TMDb ID lookup returns language codes."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "zh"}
        """
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["chi"]

    def test_network_error(self, mocker) -> None:
        """Network error during detail fetch returns None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Connection refused")
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_empty_original_language(self, mocker) -> None:
        """Detail has no original_language -> None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": ""}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_three_letter_code(self, mocker) -> None:
        """3-letter original_language is normalised (e.g. deu -> ger)."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "deu"}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["ger"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupImdbpieById tests/unit/test_native_language.py::TestLookupTmdbById -v
```
Expected: ImportError or AttributeError — functions not defined yet.

- [ ] **Step 3: Add `_lookup_imdbpie_by_id` to `native_language.py`**

Insert after `_lookup_imdbpie` (around line 120, before `_extract_spoken_code_from_string`):

```python
def _lookup_imdbpie_by_id(imdb_id: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie using a known IMDb ID.

    Skips the search+match step and directly fetches auxiliary data by ID.
    Returns *None* when imdbpie is unavailable, the client cannot be created,
    or no spoken language data is returned.
    """
    if not _HAS_IMDBPIE:
        logger.debug("imdbpie not installed — cannot perform IMDb ID lookup.")
        return None
    try:
        client = _imdbpie.Imdb()
    except Exception as exc:
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None
    return _fetch_imdb_spoken_languages(client, imdb_id)
```

- [ ] **Step 4: Add `_lookup_tmdb_by_id` to `native_language.py`**

Insert after `_lookup_tmdb` (around line 210):

```python
def _lookup_tmdb_by_id(tmdb_id: str, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb using a known TMDb ID.

    Skips the search+match step and directly fetches the movie detail by ID.
    Returns *None* when the detail endpoint fails or returns no usable
    ``original_language``.
    """
    encoded_key = urllib.parse.quote(api_key, safe="")
    detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={encoded_key}"
    try:
        with urllib.request.urlopen(detail_url, timeout=15) as resp:
            detail = json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("TMDb detail failed for id %s: %s", tmdb_id, exc)
        return None
    return _extract_tmdb_language_code(detail)
```

- [ ] **Step 5: Run the direct-ID tests**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupImdbpieById tests/unit/test_native_language.py::TestLookupTmdbById -v
```
Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py && git commit -m "feat: add direct IMDb/TMDb ID lookup functions for NFO-based lookups"
```

---

### Task 3: Extend Lookup Chain with NFO Steps

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write failing tests for NFO chain integration**

Append to `tests/unit/test_native_language.py`:

```python
class TestResolveNativeLanguageNfo:
    """Tests for resolve_native_language with NFO integration."""

    def test_nfo_imdbpie_id_success(self, mocker, tmp_path: Path) -> None:
        """NFO with IMDb ID uses direct lookup (no search)."""
        from trimarr.native_language import resolve_native_language

        # Create an NFO file alongside the mock MKV
        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <year>2024</year>
  <imdbid>tt1234567</imdbid>
  <tmdbid>999</tmdbid>
</movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }

        result = resolve_native_language(mkv, db=None)
        assert result == ["eng"]
        # Should NOT have called search (direct ID lookup)
        instance.search_for_title.assert_not_called()

    def test_nfo_tmdb_id_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IMDb ID fails, TMDb ID succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <year>2024</year>
  <imdbid>tt1234567</imdbid>
  <tmdbid>999</tmdbid>
</movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        # IMDbPie fails
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}

        # TMDb succeeds
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "fr"}'
        mock_urlopen.return_value = detail_response

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["fre"]

    def test_nfo_title_search_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IDs fail, NFO title used for search succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Das Boot</title>
  <year>1981</year>
</movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # First call (ID lookup with no ID -> None), second call (title search -> success)
        # Actually: nfo has no IDs so ID steps get None; but the chain calls _lookup_imdbpie_by_id
        # which returns None when get_title_auxiliary fails... let's make it work:
        # NFO has no IDs so ID steps skip, then title search via _lookup_imdbpie succeeds
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }

        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]

    def test_nfo_all_fail_then_filename(self, mocker, tmp_path: Path) -> None:
        """NFO steps all fail, falls through to existing filename search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        nfo = d / "Das Boot.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Das Boot</title>
  <year>1981</year>
  <imdbid>tt0000000</imdbid>
</movie>""")
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value

        # get_title_auxiliary called with NFO ID -> fails
        # get_title_auxiliary called with search result ID -> succeeds
        def aux_side_effect(imdb_id: str) -> dict | None:
            if imdb_id == "tt0000000":
                return {"spokenLanguages": None}  # NFO ID fails
            if imdb_id == "tt0082096":
                return {"spokenLanguages": [{"name": "German"}]}  # Filename wins
            return {"spokenLanguages": None}

        instance.get_title_auxiliary.side_effect = aux_side_effect
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]

        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        # Should have used filename search (step 5+)
        instance.search_for_title.assert_called()

    def test_no_nfo_existing_chain(self, mocker, tmp_path: Path) -> None:
        """No NFO file -> existing filename chain behaviour unchanged."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }

        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        instance.search_for_title.assert_called_once()

    def test_nfo_cache_source_label(self, mocker, tmp_path: Path) -> None:
        """Cache source label includes 'nfo' prefix for nfo-based results."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <imdbid>tt1234567</imdbid>
</movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }

        resolve_native_language(mkv, db=mock_db)
        call_args = mock_db.set_native_language_cache.call_args
        assert call_args is not None
        source = call_args[0][2]
        assert source.startswith("nfo_")

    def test_nfo_with_db_cache_hit(self, mocker, tmp_path: Path) -> None:
        """DB cache hit returns without NFO re-parse."""
        from trimarr.native_language import resolve_native_language

        mkv = Path("/data/test.mkv")
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "nfo_imdbpie_id", None)
        result = resolve_native_language(mkv, db=mock_db)
        assert result == ["chi"]
        # NFO should not be parsed since cache hit
        mock_db.get_native_language_cache.assert_called_once()
```

- [ ] **Step 2: Run the new tests — they should fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestResolveNativeLanguageNfo -v
```
Expected: failures due to missing chain integration.

- [ ] **Step 3: Add NFO support to the lookup chain in `native_language.py`**

First, add the import and `_get_nfo_metadata` helper near the top of the chain area (around line 230, after `_describe_failure`):

```python
# Added import at the top of native_language.py:
from trimarr._nfo_parser import NfoMetadata, discover_nfo, parse_nfo


def _get_nfo_metadata(file_path: Path) -> NfoMetadata | None:
    """Discover and parse an .nfo file for *file_path*.

    Returns structured metadata, or *None* if no suitable .nfo file
    exists or cannot be parsed.
    """
    nfo_path = discover_nfo(file_path)
    if nfo_path is None:
        return None
    return parse_nfo(nfo_path)
```

Then replace the existing `_lookup_chain` function:

```python
def _lookup_chain(tmdb_api_key: str | None) -> list[tuple]:
    """Return ordered (lookup_fn, title_or_meta_fn, source_label) triples.

    The chain iterates from most to least reliable — NFO-based lookups
    first (using direct IDs, then clean titles), followed by the existing
    filename/directory fallbacks.
    """
    chain: list[tuple] = [
        # NFO direct ID lookups (most reliable — skips search+match)
        (_lookup_imdbpie_by_id, _get_nfo_imdb_id, "nfo_imdbpie_id"),
        (_lookup_tmdb_by_id_from_nfo, _get_nfo_tmdb_id, "nfo_tmdb_id"),
        # NFO title-based search (clean title from NFO)
        (_lookup_imdbpie, _get_nfo_search_title, "nfo_imdbpie_title"),
        (_lookup_tmdb_from_nfo, _get_nfo_search_title, "nfo_tmdb_title"),
        # Filename/directory fallbacks (existing)
        (_lookup_imdbpie, _get_filename_title, "imdbpie_filename"),
        (_lookup_imdbpie, _get_directory_title, "imdbpie_directory"),
    ]
    if tmdb_api_key:
        chain += [
            (lambda t, y: _lookup_tmdb(t, y, tmdb_api_key), _get_filename_title, "tmdb_filename"),
            (lambda t, y: _lookup_tmdb(t, y, tmdb_api_key), _get_directory_title, "tmdb_directory"),
        ]
    return chain
```

Wait, this is getting complex. Let me think about the architecture more carefully.

The chain tuple is `(lookup_fn, title_or_meta_fn, source_label)`.

For the existing chain:
- `_lookup_imdbpie(title, year)` — takes title string and optional year
- `_get_filename_title(file_path)` → returns `(title, year)` tuple

For NFO direct ID lookups:
- `_lookup_imdbpie_by_id(imdb_id)` — takes a string ID, not title+year
- We need a function that extracts just the imdb_id from the file path

For NFO title search:
- Same as existing — `_lookup_imdbpie(title, year)` but with NFO's title

So the NFO steps need different argument types. Currently the loop in `resolve_native_language` does:

```python
for lookup_fn, title_fn, source_label in chain:
    title, year = title_fn(file_path)
    codes = lookup_fn(title, year)
```

For ID lookups, we need different calling conventions. Let me think about how to handle this cleanly.

One approach: use lambda wrappers or partial application in the chain to adapt the calling convention.

Actually, a simpler approach: keep the chain structure but use sentinel / different argument types. For example, the title function for ID lookups returns `(id_string, None)` where `id_string` has a special prefix like `"imdb:tt1234567"` or `"tmdb:155"`, and the lookup functions know how to handle these.

That's hacky. Better approach: have a single chain type that handles both cases.

Actually, let me just make the chain more flexible. Instead of the `(lookup_fn, title_fn)` tuple where title_fn always returns `(title, year)`, I'll use a simpler pattern: the chain entries are tuples of `(lookup_fn, context_fn, source_label)` where context_fn returns whatever the lookup_fn needs. The lookup functions for the existing chain take `(title, year)`, and the ID lookup functions take just a string.

Actually, the simplest approach is to not use a generic chain loop at all. Instead, have `resolve_native_language` do:

```python
# Try NFO first
nfo_meta = _get_nfo_metadata(file_path)
if nfo_meta is not None:
    # Try direct ID lookups
    if nfo_meta.imdb_id:
        codes = _lookup_imdbpie_by_id(nfo_meta.imdb_id)
        if codes is not None:
            return _cache_and_return(...)
    if nfo_meta.tmdb_id and tmdb_api_key:
        codes = _lookup_tmdb_by_id(nfo_meta.tmdb_id, tmdb_api_key)
        if codes is not None:
            return _cache_and_return(...)
    # Try NFO title search
    search_title = nfo_meta.original_title or nfo_meta.title
    if search_title:
        codes = _lookup_imdbpie(search_title, nfo_meta.year)
        if codes is not None:
            return _cache_and_return(...)
        if tmdb_api_key:
            codes = _lookup_tmdb(search_title, nfo_meta.year, tmdb_api_key)
            if codes is not None:
                return _cache_and_return(...)

# Fall through to filename/directory chain (existing code)
```

Hmm, but that breaks the nice chain abstraction. Let me think about what the cleanest approach is.

Actually, I think the cleanest approach is to use a more flexible chain entry. Instead of `(lookup_fn, title_fn)` where both use `(title, year)`, I'll use callable chain entries that encapsulate the complete logic:

```python
def _build_chain_entries(tmdb_api_key): ...
```

Each entry is a callable that takes `(file_path)` and returns `(codes, source_label)` or `(None, _)` for skip.

Actually, let me just keep it simple. I'll add the NFO handling as an explicit check before the existing chain loop, and the existing chain stays unchanged. This is the clearest approach.

Let me restructure:

```python
def resolve_native_language(file_path, db, tmdb_api_key=None):
    # Check cache (unchanged)
    cached_langs = _check_native_language_cache(db, file_path)
    if cached_langs is not _CACHE_MISS:
        return cached_langs

    last_error = "no match from any source"

    # --- Phase 1: NFO-based lookups ---
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        # Try direct IMDb ID lookup
        if nfo_meta.imdb_id:
            codes = _lookup_imdbpie_by_id(nfo_meta.imdb_id)
            if codes is not None:
                _maybe_cache(db, file_path, codes, "nfo_imdbpie_id", None)
                return codes
            last_error = "no match from IMDbPie (tried NFO ID lookup)"

        # Try direct TMDb ID lookup
        if nfo_meta.tmdb_id and tmdb_api_key:
            codes = _lookup_tmdb_by_id(nfo_meta.tmdb_id, tmdb_api_key)
            if codes is not None:
                _maybe_cache(db, file_path, codes, "nfo_tmdb_id", None)
                return codes
            last_error = "no match from IMDbPie or TMDb (tried NFO ID lookups)"

        # Try NFO title search
        search_title = nfo_meta.original_title or nfo_meta.title
        if search_title:
            codes = _lookup_imdbpie(search_title, nfo_meta.year)
            if codes is not None:
                _maybe_cache(db, file_path, codes, "nfo_imdbpie_title", None)
                return codes
            codes = _lookup_tmdb(search_title, nfo_meta.year, tmdb_api_key) if tmdb_api_key else None
            if codes is not None:
                _maybe_cache(db, file_path, codes, "nfo_tmdb_title", None)
                return codes

    # --- Phase 2: Filename/directory chain (existing) ---
    chain = _lookup_chain(tmdb_api_key)
    for lookup_fn, title_fn, source_label in chain:
        ...
```

Actually, this is getting quite verbose with repetition. Let me use a cleaner pattern. I'll define a `_ChainStep` class or use a simple array of callable steps.

You know what, let me just look at the existing code again and think about what minimal change is needed.

Current `_lookup_chain`:
```python
def _lookup_chain(tmdb_api_key: str | None) -> list[tuple]:
    chain: list[tuple] = [
        (_lookup_imdbpie, _get_filename_title, "imdbpie_filename"),
        (_lookup_imdbpie, _get_directory_title, "imdbpie_directory"),
    ]
    if tmdb_api_key:
        chain += [
            (partial(_lookup_tmdb, api_key=tmdb_api_key), _get_filename_title, "tmdb_filename"),
            (partial(_lookup_tmdb, api_key=tmdb_api_key), _get_directory_title, "tmdb_directory"),
        ]
    return chain
```

Current `resolve_native_language` loop:
```python
last_error = "no match from any source"
chain = _lookup_chain(tmdb_api_key)
for lookup_fn, title_fn, source_label in chain:
    title, year = title_fn(file_path)
    if not title or not year:
        continue
    codes = lookup_fn(title, year)
    if codes is not None:
        # cache and return
    last_error = ...
```

OK, I think the cleanest approach is to restructure `resolve_native_language` to handle NFO first, then fall through to the existing chain. I'll keep the existing chain loop unchanged and add an NFO phase before it. Since the tests are comprehensive, this should work.

Let me write the implementation code:

```python
def _get_nfo_search_title(file_path: Path) -> tuple[str | None, str | None]:
    """Extract a searchable title and year from the NFO file for *file_path*.

    Uses ``originaltitle`` if available, falling back to ``title``.
    Returns ``(title, year)`` or ``(None, None)`` when no NFO or title exists.
    """
    meta = _get_nfo_metadata(file_path)
    if meta is None:
        return None, None
    title = meta.original_title or meta.title
    return title, meta.year


def _get_nfo_imdb_id(file_path: Path) -> tuple[str | None, None]:
    """Extract the IMDb ID from NFO as a (id, None) tuple for chain compatibility."""
    meta = _get_nfo_metadata(file_path)
    if meta is None or meta.imdb_id is None:
        return None, None
    return meta.imdb_id, None


def _get_nfo_tmdb_id(file_path: Path) -> tuple[str | None, None]:
    """Extract the TMDb ID from NFO as a (id, None) tuple."""
    meta = _get_nfo_metadata(file_path)
    if meta is None or meta.tmdb_id is None:
        return None, None
    return meta.tmdb_id, None
```

Hmm wait, this doesn't work because for direct ID lookups, `lookup_fn(title, year)` would receive `(imdb_id, None)` but the function expects `(imdb_id)` only.

Let me think about this differently. I think the cleanest approach for the implementation plan is to restructure `resolve_native_language` to handle NFO explicitly, calling the NFO parser and direct lookup functions directly, without trying to force them into the existing chain abstraction. Then the existing chain stays as-is for the fallback.

Here's the restructured `resolve_native_language`:

```python
def resolve_native_language(file_path, db, tmdb_api_key=None):
    # Cache check (unchanged)
    ...

    last_error = "no match from any source"

    # Phase 1: NFO-based lookups
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        # 1a. Direct IMDb ID lookup
        if nfo_meta.imdb_id:
            codes = _lookup_imdbpie_by_id(nfo_meta.imdb_id)
            if codes is not None:
                _cache_and_return(...)
            last_error = "..."

        # 1b. Direct TMDb ID lookup
        if nfo_meta.tmdb_id and tmdb_api_key:
            codes = _lookup_tmdb_by_id(nfo_meta.tmdb_id, tmdb_api_key)
            if codes is not None:
                _cache_and_return(...)
            last_error = "..."

        # 1c. NFO title search
        search_title = nfo_meta.original_title or nfo_meta.title
        if search_title:
            codes = _lookup_imdbpie(search_title, nfo_meta.year)
            if codes is not None:
                _cache_and_return(...)
            if tmdb_api_key:
                codes = _lookup_tmdb(search_title, nfo_meta.year, tmdb_api_key)
                if codes is not None:
                    _cache_and_return(...)

    # Phase 2: Existing filename/directory chain (unchanged)
    ...
```

This is clean, explicit, and doesn't try to force-fit the NFO steps into a generic chain abstraction. Let me write the plan with this approach.

Now for the error messages - I need a clean `_describe_failure` that handles NFO too. Let me revise:

The error message logic:
- If NFO was found but all NFO steps failed + with TMDb key: `"no match from any source (tried NFO, filename, and directory name)"`
- If NFO was found but all NFO steps failed + no TMDb key: `"no match from IMDbPie (tried NFO, filename, and directory name, no TMDb API key configured)"`
- If NFO not found: existing messages unchanged

OK, let me write this up properly in the plan.<｜end▁of▁thinking｜>Let me continue writing the plan. The chain restructure is the core change.

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="write">
<｜｜DSML｜｜parameter name="content" string="true"># NFO Native Language Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.nfo` file parsing as the primary source for native language detection, falling back to the existing filename/directory chain when no NFO is available.

**Architecture:** A new `_nfo_parser.py` module handles NFO discovery and XML parsing. `resolve_native_language()` gains an explicit NFO phase before the existing chain — first tries direct IMDb/TMDb ID lookups (skip search-matching), then NFO-title-based searches, and only falls through to the filename/directory chain when no NFO is found or all NFO steps fail.

**Tech Stack:** Python 3.12, `xml.etree.ElementTree` (stdlib), `imdbpie`, `urllib.request` (stdlib)

---

### Task 1: Create NFO Parser Module

**Files:**
- Create: `src/trimarr/_nfo_parser.py`
- Test: `tests/unit/test_nfo_parser.py`

- [ ] **Step 1: Write the failing tests for NFO parsing and discovery**

Create `tests/unit/test_nfo_parser.py`:

```python
"""Unit tests for trimarr._nfo_parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from trimarr._nfo_parser import NfoMetadata, discover_nfo, parse_nfo


class TestParseNfo:
    """Tests for parse_nfo()."""

    def test_parse_movie_xml(self, tmp_path: Path) -> None:
        """Full movie XML returns correct NfoMetadata."""
        nfo = tmp_path / "movie.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>The Dark Knight</title>
  <originaltitle>The Dark Knight</originaltitle>
  <year>2008</year>
  <imdbid>tt0468569</imdbid>
  <tmdbid>155</tmdbid>
  <uniqueid type="imdb" default="true">tt0468569</uniqueid>
  <uniqueid type="tmdb">155</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.original_title == "The Dark Knight"
        assert result.year == "2008"
        assert result.imdb_id == "tt0468569"
        assert result.tmdb_id == "155"

    def test_parse_tvshow_xml(self, tmp_path: Path) -> None:
        """Full tvshow XML returns correct NfoMetadata."""
        nfo = tmp_path / "tvshow.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Breaking Bad</title>
  <year>2008</year>
  <imdbid>tt0903747</imdbid>
  <tmdbid>1396</tmdbid>
  <uniqueid type="imdb" default="true">tt0903747</uniqueid>
  <uniqueid type="tmdb">1396</uniqueid>
  <uniqueid type="tvdb">81189</uniqueid>
</tvshow>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Breaking Bad"
        assert result.year == "2008"
        assert result.imdb_id == "tt0903747"
        assert result.tmdb_id == "1396"

    def test_parse_minimal_movie(self, tmp_path: Path) -> None:
        """Minimal movie XML with just title works."""
        nfo = tmp_path / "minimal.nfo"
        nfo.write_text("""<?xml version="1.0"?><movie><title>Alien</title></movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Alien"
        assert result.original_title is None
        assert result.year is None
        assert result.imdb_id is None
        assert result.tmdb_id is None

    def test_parse_garbage(self, tmp_path: Path) -> None:
        """Garbage content returns None."""
        nfo = tmp_path / "bad.nfo"
        nfo.write_text("not xml at all <<<<")
        assert parse_nfo(nfo) is None

    def test_parse_empty(self, tmp_path: Path) -> None:
        """Empty file returns None."""
        nfo = tmp_path / "empty.nfo"
        nfo.write_text("")
        assert parse_nfo(nfo) is None

    def test_parse_no_relevant_fields(self, tmp_path: Path) -> None:
        """Valid XML but with only plot/studio fields returns None."""
        nfo = tmp_path / "metadata.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <plot>Some plot</plot>
  <studio>Warner Bros.</studio>
</movie>""")
        assert parse_nfo(nfo) is None

    def test_parse_originaltitle(self, tmp_path: Path) -> None:
        """originaltitle is separate from title."""
        nfo = tmp_path / "foreign.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Wo Hu Cang Long</title>
  <originaltitle>Crouching Tiger, Hidden Dragon</originaltitle>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Wo Hu Cang Long"
        assert result.original_title == "Crouching Tiger, Hidden Dragon"

    def test_parse_uniqueid_fallback_no_imdbid(self, tmp_path: Path) -> None:
        """When <imdbid> is missing, uses <uniqueid type='imdb'>."""
        nfo = tmp_path / "uniqueid.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <uniqueid type="imdb" default="true">tt1234567</uniqueid>
  <uniqueid type="tmdb">999</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt1234567"
        assert result.tmdb_id == "999"

    def test_parse_imdbid_takes_precedence(self, tmp_path: Path) -> None:
        """<imdbid> takes precedence over <uniqueid type='imdb'>."""
        nfo = tmp_path / "precedence.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <imdbid>tt0000001</imdbid>
  <uniqueid type="imdb" default="true">tt0000002</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt0000001"

    def test_parse_whitespace_stripped(self, tmp_path: Path) -> None:
        """Values have surrounding whitespace stripped."""
        nfo = tmp_path / "whitespace.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>  The Dark Knight  </title>
  <year>  2008  </year>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.year == "2008"


class TestDiscoverNfo:
    """Tests for discover_nfo()."""

    def test_stem_match(self, tmp_path: Path) -> None:
        """Same-stem .nfo is found."""
        movie_dir = tmp_path / "Movie (2024)"
        movie_dir.mkdir()
        nfo = movie_dir / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = movie_dir / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_any_nfo_in_dir(self, tmp_path: Path) -> None:
        """No stem match, any .nfo in dir found."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Some.Other.File.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_no_nfo(self, tmp_path: Path) -> None:
        """No .nfo anywhere returns None."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_tvshow_upwalk(self, tmp_path: Path) -> None:
        """tvshow.nfo found by walking up from episode directory."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == tvshow_nfo

    def test_episode_nfo_takes_priority(self, tmp_path: Path) -> None:
        """Episode-level NFO found before tvshow.nfo upwalk."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        episode_nfo = season / "Breaking Bad S01E01.nfo"
        episode_nfo.write_text("<movie><title>Episode</title></movie>")
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == episode_nfo

    def test_stem_match_over_any_nfo(self, tmp_path: Path) -> None:
        """Same-stem .nfo is preferred over other .nfo files in dir."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        other_nfo = d / "other.nfo"
        other_nfo.write_text("<movie><title>Other</title></movie>")
        stem_nfo = d / "Movie.nfo"
        stem_nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == stem_nfo

    def test_no_tvshow_nfo_upwalk(self, tmp_path: Path) -> None:
        """Walk up does not find tvshow.nfo -> None."""
        season = tmp_path / "Series" / "Season 1"
        season.mkdir(parents=True)
        mkv = season / "episode.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_any_nfo_mixed_case(self, tmp_path: Path) -> None:
        """Discovery handles .nfo, .NFO, .nfo case-insensitively."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.NFO"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v 2>&1 | head -30
```
Expected: ModuleNotFoundError or ImportError — `_nfo_parser` doesn't exist yet.

- [ ] **Step 3: Create `src/trimarr/_nfo_parser.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v
```
Expected: All tests PASS (green).

- [ ] **Step 5: Commit**

```bash
cd /data/trimarr && git add src/trimarr/_nfo_parser.py tests/unit/test_nfo_parser.py && git commit -m "feat: add NFO file parser module with discovery and XML parsing"
```

---

### Task 2: Add Direct ID Lookup Functions

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write failing tests for direct ID lookups**

Append to `tests/unit/test_native_language.py`:

```python
class TestLookupImdbpieById:
    """Tests for _lookup_imdbpie_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct IMDb ID lookup returns spoken languages."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result == ["ger"]
        instance.search_for_title.assert_not_called()

    def test_aux_failure(self, mocker) -> None:
        """When get_title_auxiliary raises, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.side_effect = RuntimeError("API error")
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_no_spoken_languages(self, mocker) -> None:
        """When aux has no spokenLanguages, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_imdbpie_not_installed(self, mocker) -> None:
        """When imdbpie is not installed, returns None."""
        import builtins

        real_import = builtins.__import__
        def _block_imdbpie(name, *args, **kwargs):
            if name == "imdbpie":
                raise ImportError("No module named imdbpie")
            return real_import(name, *args, **kwargs)
        mocker.patch("builtins.__import__", side_effect=_block_imdbpie)
        from trimarr.native_language import _lookup_imdbpie_by_id
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None


class TestLookupTmdbById:
    """Tests for _lookup_tmdb_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct TMDb ID lookup returns language codes."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "zh"}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["chi"]

    def test_network_error(self, mocker) -> None:
        """Network error during detail fetch returns None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Connection refused")
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_empty_original_language(self, mocker) -> None:
        """Detail has no original_language -> None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": ""}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_three_letter_code(self, mocker) -> None:
        """3-letter original_language is normalised (e.g. deu -> ger)."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "deu"}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["ger"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupImdbpieById tests/unit/test_native_language.py::TestLookupTmdbById -v
```
Expected: ImportError or AttributeError — functions not defined yet.

- [ ] **Step 3: Add `_lookup_imdbpie_by_id`**

Add this function after `_lookup_imdbpie` in `src/trimarr/native_language.py`:

```python
def _lookup_imdbpie_by_id(imdb_id: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie using a known IMDb ID.

    Skips the search+match step and directly fetches auxiliary data by ID.
    Returns *None* when imdbpie is unavailable, the client cannot be created,
    or no spoken language data is returned.
    """
    if not _HAS_IMDBPIE:
        logger.debug("imdbpie not installed — cannot perform IMDb ID lookup.")
        return None
    try:
        client = _imdbpie.Imdb()
    except Exception as exc:
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None
    return _fetch_imdb_spoken_languages(client, imdb_id)
```

- [ ] **Step 4: Add `_lookup_tmdb_by_id`**

Add this function after `_lookup_tmdb`:

```python
def _lookup_tmdb_by_id(tmdb_id: str, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb using a known TMDb ID.

    Skips the search+match step and directly fetches the movie detail by ID.
    Returns *None* when the detail endpoint fails or returns no usable
    ``original_language``.
    """
    encoded_key = urllib.parse.quote(api_key, safe="")
    detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={encoded_key}"
    try:
        with urllib.request.urlopen(detail_url, timeout=15) as resp:
            detail = json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("TMDb detail failed for id %s: %s", tmdb_id, exc)
        return None
    return _extract_tmdb_language_code(detail)
```

- [ ] **Step 5: Run the direct-ID tests**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupImdbpieById tests/unit/test_native_language.py::TestLookupTmdbById -v
```
Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py && git commit -m "feat: add direct IMDb/TMDb ID lookup functions for NFO-based lookups"
```

---

### Task 3: Restructure `resolve_native_language` with NFO Phase

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write failing tests for NFO chain integration**

Append to `tests/unit/test_native_language.py`:

```python
class TestResolveNativeLanguageNfo:
    """Tests for resolve_native_language with NFO integration."""

    def test_nfo_imdbpie_id_success(self, mocker, tmp_path: Path) -> None:
        """NFO with IMDb ID uses direct lookup, no search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><year>2024</year>
<imdbid>tt1234567</imdbid><tmdbid>999</tmdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["eng"]
        instance.search_for_title.assert_not_called()

    def test_nfo_tmdb_id_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IMDb ID fails, TMDb ID succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><year>2024</year>
<imdbid>tt1234567</imdbid><tmdbid>999</tmdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "fr"}'
        mock_urlopen.return_value = detail_response

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["fre"]

    def test_nfo_title_search_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IDs absent, NFO title used for search succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (1981)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Das Boot</title><year>1981</year></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]

    def test_nfo_all_fail_then_filename(self, mocker, tmp_path: Path) -> None:
        """NFO steps all fail, falls through to filename search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        nfo = d / "Das Boot.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Das Boot</title><year>1981</year>
<imdbid>tt0000000</imdbid></movie>""")
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        def aux_side_effect(imdb_id: str) -> dict:
            return {"spokenLanguages": None} if imdb_id == "tt0000000" else {"spokenLanguages": [{"name": "German"}]}
        instance.get_title_auxiliary.side_effect = aux_side_effect
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        instance.search_for_title.assert_called()

    def test_no_nfo_existing_chain(self, mocker, tmp_path: Path) -> None:
        """No NFO -> existing filename chain behaviour unchanged."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        instance.search_for_title.assert_called_once()

    def test_nfo_cache_source_label(self, mocker, tmp_path: Path) -> None:
        """Cache source label starts with 'nfo_' for nfo-based results."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><imdbid>tt1234567</imdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": [{"name": "English"}]}
        resolve_native_language(mkv, db=mock_db)
        call_args = mock_db.set_native_language_cache.call_args
        assert call_args is not None
        source = call_args[0][2]
        assert source.startswith("nfo_")

    def test_nfo_cache_hit(self, mocker, tmp_path: Path) -> None:
        """DB cache hit returns without NFO re-parse."""
        from trimarr.native_language import resolve_native_language

        mkv = Path("/data/test.mkv")
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "nfo_imdbpie_id", None)
        result = resolve_native_language(mkv, db=mock_db)
        assert result == ["chi"]
        mock_db.get_native_language_cache.assert_called_once()

    def test_nfo_tmdb_title_with_api_key(self, mocker, tmp_path: Path) -> None:
        """NFO title + TMDb search succeeds with API key."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2000)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Wo Hu Cang Long</title><year>2000</year></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b'{"results": [{"id": 123, "title": "Wo Hu Cang Long", "original_title": "Wo Hu Cang Long"}]}'
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "zh"}'
        mock_urlopen.side_effect = [search_response, detail_response]

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["chi"]
```

- [ ] **Step 2: Run the new tests — they should fail**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestResolveNativeLanguageNfo -v
```
Expected: failures because `resolve_native_language` doesn't handle NFO yet.

- [ ] **Step 3: Add import and `_get_nfo_metadata` helper**

At the top of `src/trimarr/native_language.py`, add the import:

```python
from trimarr._nfo_parser import NfoMetadata, discover_nfo, parse_nfo
```

Add the helper function after `_describe_failure`:

```python
def _get_nfo_metadata(file_path: Path) -> NfoMetadata | None:
    """Discover and parse an .nfo file for *file_path*.

    Returns structured metadata, or *None* if no suitable .nfo file
    exists or cannot be parsed.
    """
    nfo_path = discover_nfo(file_path)
    if nfo_path is None:
        return None
    return parse_nfo(nfo_path)
```

- [ ] **Step 4: Restructure `resolve_native_language`**

Replace the entire function body in `src/trimarr/native_language.py`. The new implementation adds an NFO phase before the existing chain:

```python
def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
) -> list[str] | None:
    """Return ISO 639-2/B native language codes for *file_path*, or None.

    Checks the database cache first (by file_path + fingerprint).  On miss,
    tries an NFO-based fast path (direct ID lookups, then NFO-title search),
    then falls through to the existing filename/directory chain.

    The NFO phase supports both ``<movie>`` and ``<tvshow>`` XML formats
    created by Radarr, Sonarr, Kodi, etc.
    """
    cached_langs = _check_native_language_cache(db, file_path)
    if cached_langs is not _CACHE_MISS:
        return cast("list[str] | None", cached_langs)

    # ------------------------------------------------------------------
    # Phase 1 — NFO-based lookups (direct IDs first, then title search)
    # ------------------------------------------------------------------
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        # 1a. Direct IMDb ID lookup (most reliable — no search+match)
        if nfo_meta.imdb_id:
            codes = _lookup_imdbpie_by_id(nfo_meta.imdb_id)
            if codes is not None:
                _msg = "Identified native language(s) for '%s': %s (source=nfo_imdbpie_id)."
                logger.info(_msg, file_path.name, codes)
                _maybe_cache_result(db, file_path, codes, "nfo_imdbpie_id", None)
                return cast("list[str]", codes)

        # 1b. Direct TMDb ID lookup
        if nfo_meta.tmdb_id and tmdb_api_key:
            codes = _lookup_tmdb_by_id(nfo_meta.tmdb_id, tmdb_api_key)
            if codes is not None:
                _msg = "Identified native language(s) for '%s': %s (source=nfo_tmdb_id)."
                logger.info(_msg, file_path.name, codes)
                _maybe_cache_result(db, file_path, codes, "nfo_tmdb_id", None)
                return cast("list[str]", codes)

        # 1c. NFO title search (clean title from NFO, no noisy scene tags)
        search_title = nfo_meta.original_title or nfo_meta.title
        if search_title:
            codes = _lookup_imdbpie(search_title, nfo_meta.year)
            if codes is not None:
                _msg = "Identified native language(s) for '%s': %s (source=nfo_imdbpie_title)."
                logger.info(_msg, file_path.name, codes)
                _maybe_cache_result(db, file_path, codes, "nfo_imdbpie_title", None)
                return cast("list[str]", codes)
            if tmdb_api_key:
                codes = _lookup_tmdb(search_title, nfo_meta.year, tmdb_api_key)
                if codes is not None:
                    _msg = "Identified native language(s) for '%s': %s (source=nfo_tmdb_title)."
                    logger.info(_msg, file_path.name, codes)
                    _maybe_cache_result(db, file_path, codes, "nfo_tmdb_title", None)
                    return cast("list[str]", codes)

    # ------------------------------------------------------------------
    # Phase 2 — Filename/directory fallback chain (existing behaviour)
    # ------------------------------------------------------------------
    last_error = "no match from any source"
    chain = _lookup_chain(tmdb_api_key)
    for lookup_fn, title_fn, source_label in chain:
        title, year = title_fn(file_path)
        if not title or not year:
            logger.debug(
                "Skipping lookup for '%s' — could not determine title/year.",
                file_path.name,
            )
            continue
        logger.debug(
            "Looking up native language for '%s' (title=%s, year=%s, source=%s).",
            file_path.name,
            title,
            year,
            source_label,
        )
        codes = lookup_fn(title, year)
        if codes is not None:
            logger.info(
                "Identified native language(s) for '%s': %s (source=%s).",
                file_path.name,
                codes,
                source_label,
            )
            if db is not None:
                db.set_native_language_cache(file_path, codes, source_label, None)
            return cast("list[str]", codes)
        last_error = _describe_failure(source_label, tmdb_api_key)
        logger.debug(
            "No native language found for '%s' via %s.",
            file_path.name,
            source_label,
        )

    logger.debug("No native language found for '%s' after exhausting all sources.", file_path.name)
    _maybe_cache_failure(db, file_path, last_error)
    return None
```

- [ ] **Step 5: Add `_maybe_cache_result` helper**

Add this small helper near `_maybe_cache_failure`:

```python
def _maybe_cache_result(
    db: Database | None,
    file_path: Path,
    codes: list[str],
    source: str,
    error: str | None,
) -> None:
    """Store a successful native language lookup result in the cache."""
    if db is not None:
        db.set_native_language_cache(file_path, codes, source, error)
```

- [ ] **Step 6: Run all native language tests**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -v
```
Expected: All tests PASS (both old and new tests).

- [ ] **Step 7: Run full test suite for regression**

```bash
cd /data/trimarr && uv run pytest -v
```
Expected: All existing tests still pass. No regressions.

- [ ] **Step 8: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py && git commit -m "feat: add NFO-based native language lookup phase before filename/directory chain"
```

---

### Task 4: Quality Gate — Lint, Type Check, Format

**Files:**
- Run: existing CI checks on all modified files

- [ ] **Step 1: Run ruff linter and formatter**

```bash
cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .
```

- [ ] **Step 2: Run mypy type checker**

```bash
cd /data/trimarr && uv run mypy src/trimarr/ tests/
```
Fix any type errors found.

- [ ] **Step 3: Run full test suite with coverage**

```bash
cd /data/trimarr && uv run pytest --cov=src/trimarr --cov-fail-under=80 -v
```
Expected: Coverage ≥ 80 %, all tests pass.

- [ ] **Step 4: Final commit with any qa fixes**

```bash
cd /data/trimarr && git add -A && git commit -m "chore: fix qa gate issues"
```

---

### Verification Checklist

- [ ] NFO file with IMDb ID → direct lookup succeeds, no search API call made
- [ ] NFO file with only TMDb ID → direct TMDb lookup succeeds
- [ ] NFO file with no IDs → falls back to NFO title search
- [ ] NFO file with all IDs failing → falls through to filename/directory chain
- [ ] No NFO file → existing behaviour completely unchanged
- [ ] `tvshow.nfo` found by walking up from episode directory
- [ ] Same-stem `.nfo` preferred over other `.nfo` files in directory
- [ ] `.NFO` (uppercase) handled case-insensitively
- [ ] Cache labels: `nfo_imdbpie_id`, `nfo_tmdb_id`, `nfo_imdbpie_title`, `nfo_tmdb_title`
- [ ] All existing `TestResolveNativeLanguage`, `TestLookupImdbpie`, `TestLookupTmdb` tests still pass
