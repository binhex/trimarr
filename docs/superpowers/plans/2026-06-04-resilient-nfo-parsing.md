# Resilient NFO Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `parse_nfo()` resilient to NFO files that have trailing content (URLs) after the closing XML root tag — a common artifact from Kodi library exports.

**Architecture:** When `ET.parse()` raises `ParseError`, read the file as raw text, strip everything after the last closing root tag (`</movie>`, `</tvshow>`, etc.), then re-parse with `ET.fromstring()` using identical extraction logic. Zero impact on the common case (valid XML).

**Tech Stack:** Python 3.12+, `xml.etree.ElementTree`, `re`, `pathlib`

**Spec:** `docs/superpowers/specs/2026-06-04-resilient-nfo-parsing-design.md`

---

### File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/trimarr/_nfo_parser.py` | NFO discovery + parsing | Add `_strip_nfo_trailing_junk()`, `_parse_nfo_with_cleanup()`, recovery path in `parse_nfo()` |
| `tests/unit/test_nfo_parser.py` | All tests for NFO parser | Add `TestNfoCleanup` class with unit + integration tests |

---

### Task 1: Implement trailing-junk stripping and recovery

**Files:**
- Modify: `src/trimarr/_nfo_parser.py`

- [ ] **Step 1: Add `import re` to the imports section**

Find `import logging` at line 8 and add `import re` after it:

```python
import logging
import re
import xml.etree.ElementTree as ET
```

- [ ] **Step 2: Add module constants after `_MAX_TVSHOW_UPWALK_DEPTH`**

```python
_MAX_TVSHOW_UPWALK_DEPTH = 3

_KNOWN_NFO_ROOTS = ("movie", "tvshow", "episodedetails", "season")
"""Root element names that can appear in NFO files produced by
Kodi / Radarr / Sonarr."""

_ROOT_OPEN_RE = re.compile(
    r"^\s*<(" + "|".join(_KNOWN_NFO_ROOTS) + r")(?:\s|>)",
    re.MULTILINE,
)
"""Matches the opening tag of a known NFO root element.
Capture group 1 contains the element name (e.g. "movie")."""
```

- [ ] **Step 3: Add `_strip_nfo_trailing_junk()` before `_extract_text()`**

Insert before `def _extract_text(parent: ...`:

```python
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
```

- [ ] **Step 4: Add `_parse_nfo_with_cleanup()` before `parse_nfo()`**

Insert before `def parse_nfo(path: ...`:

```python
def _parse_nfo_with_cleanup(path: Path) -> NfoMetadata | None:
    """Read *path* as raw text, strip trailing junk, and parse as NFO XML.

    Called as a recovery path when ``ET.parse()`` fails on *path*.
    Returns structured metadata or *None* if the file cannot be read
    or still fails to parse after cleanup.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    cleaned = _strip_nfo_trailing_junk(raw)
    if cleaned is None:
        return None

    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return None

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
    )
```

- [ ] **Step 5: Add recovery path inside `parse_nfo()` exception handler**

Find the existing `except` block:

```python
    except (ET.ParseError, FileNotFoundError, PermissionError, IsADirectoryError) as exc:
        logger.debug("Failed to parse NFO '%s': %s", path, exc)
        return None
```

Replace with:

```python
    except (ET.ParseError, FileNotFoundError, PermissionError, IsADirectoryError) as exc:
        logger.debug("Failed to parse NFO '%s': %s", path, exc)
        meta = _parse_nfo_with_cleanup(path)
        if meta is not None:
            return meta
        return None
```

- [ ] **Step 6: Run existing tests to verify no regression**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v --no-header --no-cov 2>&1 | tail -40`

Expected: All existing tests pass (the recovery path is dead code for valid files).

- [ ] **Step 7: Commit**

```bash
cd /data/trimarr
git add src/trimarr/_nfo_parser.py
git commit -m "feat: add trailing-junk stripping and recovery path to parse_nfo()"
```

---

### Task 2: Unit tests for `_strip_nfo_trailing_junk()`

**Files:**
- Modify: `tests/unit/test_nfo_parser.py`

- [ ] **Step 1: Write the failing tests**

Add a new test class **after** `TestDiscoverNfo` (at end of file) and import `_strip_nfo_trailing_junk`:

Update the import line at the top:
```python
from trimarr._nfo_parser import discover_nfo, parse_nfo, _strip_nfo_trailing_junk
```

Add at the end of the file:

```python
class TestNfoCleanup:
    """Tests for _strip_nfo_trailing_junk() and parse_nfo() recovery."""

    # ── _strip_nfo_trailing_junk unit tests ──────────────────────────

    def test_cleanup_valid_xml_unchanged(self) -> None:
        """Already-valid XML returns unchanged (no trailing junk to strip)."""
        raw = "<movie>\n  <title>Test</title>\n</movie>\n"
        result = _strip_nfo_trailing_junk(raw)
        assert result == raw

    def test_cleanup_trailing_urls_after_movie(self) -> None:
        """Trailing URLs after </movie> are stripped."""
        raw = (
            '<movie>\n'
            '  <title>Inception</title>\n'
            '  <imdbid>tt1375666</imdbid>\n'
            '</movie>\n'
            '\n'
            'https://www.themoviedb.org/movie/27205\n'
            'https://www.imdb.com/title/tt1375666\n'
        )
        expected = '<movie>\n  <title>Inception</title>\n  <imdbid>tt1375666</imdbid>\n</movie>\n'
        assert _strip_nfo_trailing_junk(raw) == expected

    def test_cleanup_trailing_urls_after_tvshow(self) -> None:
        """Trailing URLs after </tvshow> are stripped."""
        raw = (
            '<tvshow>\n'
            '  <title>Breaking Bad</title>\n'
            '  <tvdbid>81189</tvdbid>\n'
            '</tvshow>\n'
            '\n'
            'https://www.thetvdb.com/series/81189\n'
        )
        expected = '<tvshow>\n  <title>Breaking Bad</title>\n  <tvdbid>81189</tvdbid>\n</tvshow>\n'
        assert _strip_nfo_trailing_junk(raw) == expected

    def test_cleanup_trailing_urls_after_episodedetails(self) -> None:
        """Trailing URLs after </episodedetails> are handled."""
        raw = (
            '<episodedetails>\n'
            '  <title>S01E01</title>\n'
            '</episodedetails>\n'
            '\n'
            'https://example.com/junk\n'
        )
        expected = '<episodedetails>\n  <title>S01E01</title>\n</episodedetails>\n'
        assert _strip_nfo_trailing_junk(raw) == expected

    def test_cleanup_no_root_element(self) -> None:
        """Text with no recognizable NFO root returns None."""
        assert _strip_nfo_trailing_junk("just some random text") is None

    def test_cleanup_no_closing_tag(self) -> None:
        """Raw text with opening tag but no closing tag returns None."""
        raw = "<movie>\n  <title>Incomplete</title>\n"
        assert _strip_nfo_trailing_junk(raw) is None

    def test_cleanup_empty_string(self) -> None:
        """Empty string returns None."""
        assert _strip_nfo_trailing_junk("") is None

    def test_cleanup_only_trailing_content(self) -> None:
        """Only trailing content with no XML returns None."""
        assert _strip_nfo_trailing_junk("https://example.com/junk\n") is None

    def test_cleanup_root_with_attributes(self) -> None:
        """Root element with XML attributes is still detected."""
        raw = (
            '<movie xmlns="http://example.com">\n'
            '  <title>Test</title>\n'
            '</movie>\n'
            '\n'
            'trailing\n'
        )
        expected = '<movie xmlns="http://example.com">\n  <title>Test</title>\n</movie>\n'
        assert _strip_nfo_trailing_junk(raw) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py::TestNfoCleanup -v --no-header --no-cov 2>&1 | tail -30`

Expected: The `test_cleanup_no_root_element` test may pass (since `_strip_nfo_trailing_junk` is implemented), but all others should fail because `_strip_nfo_trailing_junk` is not yet imported. Actually since Task 1 already implemented it, they should all pass. Let me re-think...

Actually, the plan is sequential — Task 1 implements the functions, Task 2 adds tests. So after Task 1, the function exists and tests should pass. The tests were written as the first step of Task 2 (TDD style with RED → GREEN).

Hmm, but since Task 1 already provides the implementation, the tests should pass immediately (GREEN). For true TDD, we'd need to reverse the order, but in a multi-task plan where Task 1 has already been committed, Task 2's tests should pass first time. Let me adjust the plan approach — Task 2 writes the tests and runs them immediately as verification.

Actually, re-reading the writing-plans skill: "Each step is one action (2-5 minutes)" and "Write the failing test" / "Run it to make sure it fails". The plan assumes sequential execution. If executed in order, Task 1 already implemented the code, so Task 2's tests won't fail. That's fine — the test step is "write the tests and verify they pass". Let me adapt.

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py::TestNfoCleanup -v --no-header --no-cov 2>&1 | tail -30`

Expected: All 9 tests pass.

- [ ] **Step 3: Commit**

```bash
cd /data/trimarr
git add tests/unit/test_nfo_parser.py
git commit -m "test: add unit tests for _strip_nfo_trailing_junk()"
```

---

### Task 3: Integration tests for `parse_nfo()` recovery

**Files:**
- Modify: `tests/unit/test_nfo_parser.py`

- [ ] **Step 1: Add integration tests to `TestNfoCleanup`**

Append inside `TestNfoCleanup` after the unit tests:

```python
    # ── parse_nfo integration tests ─────────────────────────────────

    def test_parse_recover_movie_trailing_urls(self, tmp_path: Path) -> None:
        """parse_nfo() recovers movie NFO with trailing URLs."""
        nfo = tmp_path / "movie.nfo"
        nfo.write_text(
            '<?xml version="1.0"?>\n'
            '<movie>\n'
            '  <title>The Dark Knight</title>\n'
            '  <year>2008</year>\n'
            '  <imdbid>tt0468569</imdbid>\n'
            '  <tmdbid>155</tmdbid>\n'
            '</movie>\n'
            '\n'
            'https://www.themoviedb.org/movie/155\n'
            'https://www.imdb.com/title/tt0468569\n'
        )
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.year == "2008"
        assert result.imdb_id == "tt0468569"
        assert result.tmdb_id == "155"

    def test_parse_recover_tvshow_trailing_urls(self, tmp_path: Path) -> None:
        """parse_nfo() recovers tvshow NFO with trailing URLs."""
        nfo = tmp_path / "tvshow.nfo"
        nfo.write_text(
            '<?xml version="1.0"?>\n'
            '<tvshow>\n'
            '  <title>Breaking Bad</title>\n'
            '  <year>2008</year>\n'
            '  <imdbid>tt0903747</imdbid>\n'
            '  <tmdbid>1396</tmdbid>\n'
            '  <tvdbid>81189</tvdbid>\n'
            '</tvshow>\n'
            '\n'
            'https://www.thetvdb.com/series/81189\n'
        )
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Breaking Bad"
        assert result.year == "2008"
        assert result.imdb_id == "tt0903747"
        assert result.tmdb_id == "1396"
        assert result.tvdb_id == "81189"

    def test_parse_deep_xml_damage_still_fails(self, tmp_path: Path) -> None:
        """Deep XML damage (unescaped &) still returns None — no false positive."""
        nfo = tmp_path / "damaged.nfo"
        nfo.write_text(
            '<?xml version="1.0"?>\n'
            '<movie>\n'
            '  <title>Bad & broken</title>\n'
            '</movie>\n'
        )
        assert parse_nfo(nfo) is None

    def test_parse_valid_nfo_fast_path_unchanged(self, tmp_path: Path) -> None:
        """Valid NFO still parses correctly via fast path (no regression)."""
        nfo = tmp_path / "valid.nfo"
        nfo.write_text(
            '<?xml version="1.0"?>\n'
            '<movie>\n'
            '  <title>Alien</title>\n'
            '</movie>\n'
        )
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Alien"
        assert result.imdb_id is None
```

- [ ] **Step 2: Run all tests to verify no regression**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v --no-header --no-cov 2>&1 | tail -50`

Expected: All **41 tests pass** (28 existing + 9 cleanup unit + 4 integration).

- [ ] **Step 3: Full suite regression check**

Run: `cd /data/trimarr && uv run pytest --no-header -q --no-cov 2>&1`

Expected: No test failures across the entire suite.

- [ ] **Step 4: Commit**

```bash
cd /data/trimarr
git add tests/unit/test_nfo_parser.py
git commit -m "test: add integration tests for parse_nfo() trailing-junk recovery"
```

---

### Task 4: Final QA gate

- [ ] **Step 1: Ruff lint + format**

Run: `cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .`

Expected: Clean.

- [ ] **Step 2: Mypy type check**

Run: `cd /data/trimarr && uv run mypy .`

Expected: `Success: no issues found in 25 source files`

- [ ] **Step 3: Coverage check**

Run: `cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-report=term-missing 2>&1 | tail -20`

Expected: Coverage remains >= 95%.

- [ ] **Step 4: Pre-commit**

Run: `cd /data/trimarr && uv run pre-commit run --all-files 2>&1`

Expected: All hooks pass.

- [ ] **Step 5: Commit**

```bash
cd /data/trimarr
git add -A
git commit -m "chore: QA gate — ruff, mypy, coverage, pre-commit"
```
