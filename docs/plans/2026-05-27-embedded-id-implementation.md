# Filename-Embedded IMDb/TMDb ID — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regex-based scanning of the filename stem for embedded IMDb (`{imdb-ttXXXXX}`) or TMDb (`{tmdb-XXXXX}`) IDs as priority 2 in the native language resolution chain — between NFO-direct-ID and NFO-title-search.

**Architecture:** A single private function `_extract_embedded_id(stem)` checks the filename stem for ID patterns (curly, square, or bare brackets). If found, the ID is passed to existing `_lookup_imdbpie_by_id()` or `_lookup_tmdb_by_id()` for a direct API lookup. On failure, the chain falls through to the next phase.

**Tech Stack:** Python 3.12, stdlib `re` (already imported), `imdbpie`, `urllib.request`

---

### Task 1: Write Tests for `_extract_embedded_id`

**Files:**
- Modify: `tests/unit/test_native_language.py` — add `TestExtractEmbeddedId` class after existing test classes

- [ ] **Step 1: Add the test class with parametrized tests**

Add the following block to `tests/unit/test_native_language.py` after the last test class (before any file-end marker). Find a suitable insertion point — the last class is likely `TestResolveNativeLanguage` or similar.

```python
class TestExtractEmbeddedId:
    """Tests for _extract_embedded_id()."""

    def test_not_imported_yet(self) -> None:
        """Placeholder to verify we find the right module location."""
        from trimarr.native_language import _extract_embedded_id  # noqa: F811
```

- [ ] **Step 2: Run placeholder test to confirm it fails**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestExtractEmbeddedId -v
```

Expected: `FAILED` with `ImportError: cannot import name '_extract_embedded_id' from 'trimarr.native_language'`

- [ ] **Step 3: Replace placeholder with real tests**

Replace the placeholder test with the full test suite:

```python
class TestExtractEmbeddedId:
    """Tests for _extract_embedded_id()."""

    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            # IMDb: curly, square, bare
            ("Martin (1977) {imdb-tt0077914} 2160p", ("imdb", "tt0077914")),
            ("Martin [imdb-tt0077914] 2160p", ("imdb", "tt0077914")),
            ("Martin imdb-tt0077914 2160p", ("imdb", "tt0077914")),
            # IMDb: case insensitive
            ("Martin {IMDB-tt0077914} 2160p", ("imdb", "tt0077914")),
            # TMDb: curly, square, bare
            ("Martin (1977) {tmdb-77914} 2160p", ("tmdb", "77914")),
            ("Martin [tmdb-77914] 2160p", ("tmdb", "77914")),
            ("Martin tmdb-77914 2160p", ("tmdb", "77914")),
        ],
    )
    def test_extract_embedded_id(self, stem: str, expected: tuple[str, str]) -> None:
        from trimarr.native_language import _extract_embedded_id

        result = _extract_embedded_id(stem)
        assert result == expected

    @pytest.mark.parametrize(
        "stem",
        [
            "Martin (1977) 2160p",
            "Martin [some-other-id] 2160p",
            "tmdb-abc",  # TMDb ID must be numeric
            "imdb-movie",  # IMDb ID must start with tt
        ],
    )
    def test_extract_embedded_id_no_match(self, stem: str) -> None:
        from trimarr.native_language import _extract_embedded_id

        assert _extract_embedded_id(stem) is None

    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            # Both present: IMDb wins (checked first)
            ("{imdb-tt0077914} {tmdb-77914}", ("imdb", "tt0077914")),
            ("[tmdb-77914] [imdb-tt0077914]", ("imdb", "tt0077914")),
        ],
    )
    def test_extract_embedded_id_both_present(self, stem: str, expected: tuple[str, str]) -> None:
        from trimarr.native_language import _extract_embedded_id

        result = _extract_embedded_id(stem)
        assert result == expected
```

- [ ] **Step 4: Verify all tests fail (RED phase)**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestExtractEmbeddedId -v
```

Expected: All tests FAIL with `ImportError: cannot import name '_extract_embedded_id'`

- [ ] **Step 5: Commit the RED tests**

```bash
git add tests/unit/test_native_language.py
git commit -m "test: add RED tests for _extract_embedded_id"
```

---

### Task 2: Implement `_extract_embedded_id`

**Files:**
- Modify: `src/trimarr/native_language.py` — add regex constants + extraction function

- [ ] **Step 1: Add regex constants near the top of the file**

Add after existing `_RELEASE_TAGS_RE` definition (around line 158). Find the exact location:

```bash
cd /data/trimarr && grep -n "_RELEASE_TAGS_RE\|_NFO_IMDB_ID_RE\|def _strip_release_tags" src/trimarr/native_language.py
```

Insert before `_RELEASE_TAGS_RE`:

```python
# Regex patterns for filename-embedded IMDb/TMDb IDs.
# Matches {imdb-tt123}, [imdb-tt123], imdb-tt123 and same for tmdb-{id}.
_EMBEDDED_IMDB_RE = re.compile(r"""[\[\{]?imdb-(tt\d+)[\]\}]?""", re.VERBOSE | re.IGNORECASE)
_EMBEDDED_TMDB_RE = re.compile(r"""[\[\{]?tmdb-(\d+)[\]\}]?""", re.VERBOSE | re.IGNORECASE)
```

- [ ] **Step 2: Add `_extract_embedded_id` function**

Add after the regex constants (before `_strip_release_tags` or `parse_movie_title`):

```python
def _extract_embedded_id(stem: str) -> tuple[str, str] | None:
    """Scan *stem* for an embedded IMDb or TMDb ID.

    Supports curly ``{imdb-tt...}``, square ``[imdb-tt...]``, and
    bare ``imdb-tt...`` syntax (same for ``tmdb-...``).

    Returns ``("imdb", "tt0077914")``, ``("tmdb", "77914")``, or
    ``None`` if no recognised ID pattern is found.
    """
    m = _EMBEDDED_IMDB_RE.search(stem)
    if m:
        return ("imdb", m.group(1).lower())
    m = _EMBEDDED_TMDB_RE.search(stem)
    if m:
        return ("tmdb", m.group(1))
    return None
```

- [ ] **Step 3: Run the unit tests to confirm they pass (GREEN phase)**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestExtractEmbeddedId -v
```

Expected: All tests PASS

- [ ] **Step 4: Run full test suite for regressions**

```bash
cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -5
```

Expected: All tests pass (no regressions)

- [ ] **Step 5: Commit**

```bash
git add src/trimarr/native_language.py tests/unit/test_native_language.py
git commit -m "feat: add _extract_embedded_id for filename-embedded IMDb/TMDb IDs"
```

---

### Task 3: Integration Tests (embedded ID in `resolve_native_language`)

**Files:**
- Modify: `tests/unit/test_native_language.py` — add integration tests in `TestResolveNativeLanguage`

- [ ] **Step 1: Read the existing integration test class to understand mocks used**

```bash
cd /data/trimarr && grep -n "class TestResolveNativeLanguage" tests/unit/test_native_language.py
```

Read the first ~60 lines of that class to see mock patterns, then add tests after the existing integration tests.

- [ ] **Step 2: Add integration test — embedded IMDb ID succeeds**

Insert after the last test in `TestResolveNativeLanguage`:

```python
    def test_integration_embedded_imdb_id(
        self, tmp_path: Path, mock_db: MagicMock
    ) -> None:
        """Embedded IMDb ID in filename triggers direct lookup."""
        from trimarr.native_language import _extract_embedded_id, resolve_native_language

        mkv = tmp_path / "Movie {imdb-tt0077914} 2024.mkv"
        mkv.write_text("dummy content")

        with (
            patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=["ger", "eng"]) as mock_lookup,
            patch("trimarr.native_language._lookup_tmdb_by_id") as mock_tmdb,
        ):
            result = resolve_native_language(mkv, mock_db, tmdb_api_key="test-key")

        assert result == ["ger", "eng"]
        mock_lookup.assert_called_once_with("tt0077914")
        mock_tmdb.assert_not_called()
```

- [ ] **Step 3: Add integration test — embedded TMDb ID succeeds**

```python
    def test_integration_embedded_tmdb_id(
        self, tmp_path: Path, mock_db: MagicMock
    ) -> None:
        """Embedded TMDb ID in filename triggers TMDb direct lookup."""
        from trimarr.native_language import resolve_native_language

        mkv = tmp_path / "Movie {tmdb-77914} 2024.mkv"
        mkv.write_text("dummy content")

        with patch("trimarr.native_language._lookup_tmdb_by_id", return_value=["chi"]) as mock_lookup:
            result = resolve_native_language(mkv, mock_db, tmdb_api_key="test-key")

        assert result == ["chi"]
        mock_lookup.assert_called_once_with("77914", "test-key")
```

- [ ] **Step 4: Add integration test — embedded ID found but API fails → fall through**

```python
    def test_integration_embedded_id_fails_fallthrough(
        self, tmp_path: Path, mock_db: MagicMock
    ) -> None:
        """Embedded ID found but API fails; falls through to next phase."""
        from trimarr.native_language import resolve_native_language

        mkv = tmp_path / "Movie {imdb-tt0077914} 2024.mkv"
        mkv.write_text("dummy content")

        with (
            patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=None),
            patch("trimarr.native_language._lookup_tmdb_by_id") as mock_tmdb,
            patch("trimarr.native_language._lookup_imdbpie", return_value=["eng"]) as mock_search,
        ):
            result = resolve_native_language(mkv, mock_db, tmdb_api_key="test-key")

        # Falls through to search (no NFO, so filename search is next)
        assert result == ["eng"]
        mock_tmdb.assert_not_called()
        mock_search.assert_called_once()
```

- [ ] **Step 5: Run the integration tests**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -k "test_integration_embedded" -v
```

Expected: All tests PASS (they should pass immediately because the implementation already exists from Task 2)

- [ ] **Step 6: Run full test suite**

```bash
cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -5
```

Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_native_language.py
git commit -m "test: add integration tests for embedded ID in native language resolution"
```

---

### Task 4: Wire Embedded ID Phase into `resolve_native_language`

**Files:**
- Modify: `src/trimarr/native_language.py` — add Phase 2 (embedded ID) in `resolve_native_language()`

- [ ] **Step 1: Insert the embedded ID phase in `resolve_native_language()`**

The current `resolve_native_language()` has:

```python
    # Phase 1 — NFO-based lookups
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        result = _resolve_nfo_id_lookups(...)
        if result is not None:
            return result
        result = _resolve_nfo_title_search(...)
        if result is not None:
            return result

    # Phase 2 — Filename/directory fallback chain (existing behaviour)
    return _run_filename_directory_chain(file_path, db, tmdb_api_key)
```

Change to:

```python
    # Phase 1 — NFO-based lookups
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        result = _resolve_nfo_id_lookups(...)
        if result is not None:
            return result
        result = _resolve_nfo_title_search(...)
        if result is not None:
            return result

    # Phase 2 — Embedded ID in filename (before filename/directory parsing)
    file_stem = file_path.stem
    embedded = _extract_embedded_id(file_stem)
    if embedded is not None:
        source, eid = embedded
        if source == "imdb":
            codes = _lookup_imdbpie_by_id(eid)
            if codes is not None:
                _maybe_cache_result(db, file_path, codes, "imdbpie_embedded_id", None)
                return codes
        else:  # tmdb
            if tmdb_api_key:
                codes = _lookup_tmdb_by_id(eid, tmdb_api_key)
                if codes is not None:
                    _maybe_cache_result(db, file_path, codes, "tmdb_embedded_id", None)
                    return codes
            else:
                logger.debug(
                    "Skipping TMDb embedded ID lookup for '%s' — no API key configured.",
                    file_path.name,
                )

    # Phase 3 — Filename/directory fallback chain (existing behaviour)
    return _run_filename_directory_chain(file_path, db, tmdb_api_key)
```

- [ ] **Step 2: Run the full test suite**

```bash
cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -5
```

Expected: All tests pass (the integration tests from Task 3 verify the embedded ID phase works)

- [ ] **Step 3: Run coverage to ensure no uncovered branches**

```bash
cd /data/trimarr && uv run pytest --cov=src/trimarr --cov-report=term-missing 2>&1 | tail -20
```

Expected: Coverage >= 97%, no uncovered branches in `_extract_embedded_id` or the new Phase 2 block

- [ ] **Step 4: Commit**

```bash
git add src/trimarr/native_language.py
git commit -m "feat: add embedded ID phase (phase 2) to resolve_native_language chain"
```

---

### Task 5: Quality Gate

**Files:** None — run CI checks only

- [ ] **Step 1: Ruff check and format**

```bash
cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .
```

Expected: Clean (no errors, no changes needed)

- [ ] **Step 2: Mypy**

```bash
cd /data/trimarr && uv run mypy .
```

Expected: Success (no type errors)

- [ ] **Step 3: Full test suite with coverage**

```bash
cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-fail-under=80 2>&1 | tail -10
```

Expected: All tests pass, coverage >= 80%

- [ ] **Step 4: Pre-commit hook**

```bash
cd /data/trimarr && pre-commit run --all-files 2>&1
```

Expected: All checks pass

- [ ] **Step 5: Final commit (if any QA fixes were needed)**

```bash
git add -A
git commit -m "chore: qa gate fixes for embedded ID feature"
```

---

## Plan Self-Review Checklist

- [ ] **Spec coverage:** Every spec requirement (regex patterns, bracket styles, integration point, error handling, testing) is covered by a task
- [ ] **Placeholder scan:** No TBDs, TODOs, or vague steps — every step has exact code and commands
- [ ] **Type consistency:** `_extract_embedded_id` returns `tuple[str, str] | None` consistently across tests (Task 1) and implementation (Task 2). Integration tests (Task 3) use the same return type
- [ ] **Ambiguity check:** The TMDb embedded ID path requires `tmdb_api_key` — the plan shows the guard (`if tmdb_api_key:`) in Task 4, and integration tests mock it properly
