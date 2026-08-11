# Directory Name Title Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `--keep-native-audio` fails to match a filename against IMDbPie/TMDb, fall back to using the **parent directory name** as the movie title for a second attempt.

**Architecture:** Restructure `resolve_native_language` to iterate through a 4-step ordered chain of `(lookup_fn, title_fn, source_label)` triples: IMDbPie+filename → IMDbPie+directory → TMDb+filename → TMDb+directory. Each step fires only if the previous returned `None`. New helpers generate the chain and compose error messages.

**Tech Stack:** Python 3.12, imdbpie, pycountry

---

### Scope Check

The spec covers a single subsystem — the native language lookup fallback logic in `native_language.py`. No decomposition needed.

---

### File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `src/trimarr/native_language.py` | Core lookup logic | Add 3 helpers, restructure `resolve_native_language` |
| `tests/unit/test_native_language.py` | Tests | Add 6 new test methods, update 2 existing ones |

---

### Task 1: Add helper functions

**Files:**
- Modify: `src/trimarr/native_language.py` (add helpers before `resolve_native_language`)

- [ ] **Step 1: Add `_get_filename_title` and `_get_directory_title`**

These are simple selectors that extract title+year from the file path using `parse_movie_title`. Add them right before `resolve_native_language` (before line 522):

```python
def _get_filename_title(file_path: Path) -> tuple[str, str | None]:
    """Extract movie title from the filename stem via parse_movie_title."""
    return parse_movie_title(file_path)


def _get_directory_title(file_path: Path) -> tuple[str, str | None]:
    """Extract movie title from the parent directory name via parse_movie_title."""
    return parse_movie_title(file_path.parent)
```

- [ ] **Step 2: Add `_lookup_chain` builder**

Generates the ordered chain of lookup steps. Each entry is a `(lookup_fn, title_fn, source_label)` triple — the source label is stored directly rather than computed via identity comparison (because TMDb steps use `partial()` wrappers).

Add the import at the top of the file:

```python
from functools import partial
```

```python
def _lookup_chain(tmdb_api_key: str | None) -> list[tuple]:
    """Return ordered (lookup_fn, title_fn, source_label) triples."""
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

Note: `functools.partial` binds `api_key` to `_lookup_tmdb(title, year, api_key)` so the wrapped function has the same `(title, year)` signature as `_lookup_imdbpie`.

- [ ] **Step 3: Add `_describe_failure` helper**

Generates the combined error message for the last failed step:

```python
def _describe_failure(source_label: str, tmdb_api_key: str | None) -> str:
    """Return an error message for a failed lookup step.

    Produces messages like:
    - "no match from IMDbPie (tried filename and directory name, no TMDb API key configured)"
    - "no match from IMDbPie or TMDb (tried filename and directory name)"
    """
    # Determine if this step involves TMDb and/or directory
    has_tmdb = tmdb_api_key is not None
    if "tmdb" in source_label:
        return "no match from IMDbPie or TMDb (tried filename and directory name)"
    if not has_tmdb:
        return "no match from IMDbPie (tried filename and directory name, no TMDb API key configured)"
    return "no match from IMDbPie (no TMDb API key configured for fallback)"
```

- [ ] **Step 4: Run tests to verify no regressions yet**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py --no-header -q
```
Expected: all existing tests pass (the new helpers aren't called yet).

- [ ] **Step 5: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py
git commit -m "chore: add directory-title fallback helpers"
```

---

### Task 2: Restructure `resolve_native_language`

**Files:**
- Modify: `src/trimarr/native_language.py` (replace the function body)

- [ ] **Step 1: Replace the body of `resolve_native_language`**

Current function (lines 522-565) has a linear flow. Replace with the chain-based loop:

```python
def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
) -> list[str] | None:
    """Return ISO 639-2/B native language codes for *file_path*, or None.

    Checks the database cache first (by file_path + fingerprint).  On miss,
    iterates through a fallback chain: IMDbPie+filename → IMDbPie+directory
    → TMDb+filename → TMDb+directory.  Caches the result (including
    failures) so subsequent runs are fast.
    """
    cached_langs = _check_native_language_cache(db, file_path)
    if cached_langs is not _CACHE_MISS:
        return cast("list[str] | None", cached_langs)

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
            file_path.name, title, year, source_label,
        )
        codes = lookup_fn(title, year)
        if codes is not None:
            logger.info(
                "Identified native language(s) for '%s': %s (source=%s).",
                file_path.name, codes, source_label,
            )
            if db is not None:
                db.set_native_language_cache(file_path, codes, source_label, None)
            return codes
        last_error = _describe_failure(source_label, tmdb_api_key)
        logger.debug(
            "No native language found for '%s' via %s.",
            file_path.name, source_label,
        )

    logger.debug("No native language found for '%s' after exhausting all sources.", file_path.name)
    _maybe_cache_failure(db, file_path, last_error)
    return None
```

- [ ] **Step 2: Run the full test suite to check for regressions**

```bash
cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-fail-under=95
```
Expected: failures — the existing tests mock the old linear flow and assert on specific error messages/source labels. Task 3 fixes them.

- [ ] **Step 3: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py
git commit -m "feat: restructure resolve_native_language with directory title fallback chain"
```

---

### Task 3: Update existing tests for new error messages and source labels

**Files:**
- Modify: `tests/unit/test_native_language.py`

- [ ] **Step 1: Update `test_cache_miss_then_imdbpie_success`**

Change the expected `source_label` from `"imdbpie"` to `"imdbpie_filename"`:

```python
    def test_cache_miss_then_imdbpie_success(self, mocker) -> None:
        """Cache miss triggers IMDbPie lookup, result is cached."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(Path("/data/Das Boot (1981).mkv"), db=mock_db)
        assert result == ["ger"]
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Das Boot (1981).mkv"), ["ger"], "imdbpie_filename", None
        )
```

- [ ] **Step 2: Update `test_imdbpie_fails_no_tmdb_key`**

The error message changed — IMDbPie now tries directory too before giving up:

```python
    def test_imdbpie_fails_no_tmdb_key(self, mocker) -> None:
        """IMDbPie returns no data, no TMDb API key configured."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        result = resolve_native_language(Path("/data/Test Movie (2020).mkv"), db=mock_db)
        assert result is None
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Test Movie (2020).mkv"),
            None,
            None,
            "no match from IMDbPie (tried filename and directory name, no TMDb API key configured)",
        )
```

- [ ] **Step 3: Run tests to verify**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -v --no-header -k "TestResolveNativeLanguage"
```
Expected: all existing tests pass with updated assertions.

- [ ] **Step 4: Commit**

```bash
cd /data/trimarr && git add tests/unit/test_native_language.py
git commit -m "test: update tests for new source labels and error messages"
```

---

### Task 4: Add new tests for directory title fallback

**Files:**
- Modify: `tests/unit/test_native_language.py` (add 6 new methods to `TestResolveNativeLanguage`)

- [ ] **Step 1: Add `test_dir_title_fallback`**

```python
    def test_dir_title_fallback(self, mocker) -> None:
        """Filename search returns None, directory search succeeds."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value

        def search_side_effect(term: str) -> list:
            if "CtrlHD" in term:
                return []  # noisy filename -> no results
            return [{"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"}]

        instance.search_for_title.side_effect = search_side_effect
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        # File with noisy scene name in parent dir with clean name
        path = Path("/media/Das Boot (1981)/Das.Boot.1981.DC.1080p.BluRay.x264-CtrlHD.mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["ger"]
        # Should have called search twice (filename failed, directory succeeded)
        assert instance.search_for_title.call_count == 2
        mock_db.set_native_language_cache.assert_called_once_with(
            path, ["ger"], "imdbpie_directory", None
        )
```

- [ ] **Step 2: Add `test_filename_success_no_fallback`**

```python
    def test_filename_success_no_fallback(self, mocker) -> None:
        """Filename succeeds - directory fallback is never attempted."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        path = Path("/data/Das Boot (1981).mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["ger"]
        # Should only have called search once (filename succeeded)
        assert instance.search_for_title.call_count == 1
        mock_db.set_native_language_cache.assert_called_once_with(
            path, ["ger"], "imdbpie_filename", None
        )
```

- [ ] **Step 3: Add `test_dir_title_no_year`**

```python
    def test_dir_title_no_year(self, mocker) -> None:
        """Directory has no year, directory step skipped, falls to TMDb."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Unknown Movie", "original_title": "Unknown Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "en"}
        """
        mock_urlopen.side_effect = [search_response, detail_response]
        # File in a directory with no year
        path = Path("/media/Some Movie/File.mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result == ["eng"]
        mock_db.set_native_language_cache.assert_called_once_with(
            path, ["eng"], "tmdb_filename", None
        )
```

- [ ] **Step 4: Add `test_all_steps_fail`**

```python
    def test_all_steps_fail(self, mocker) -> None:
        """All 4 steps return None -> cached failure with combined error."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Connection refused")
        path = Path("/data/Test Movie (2020).mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result is None
        mock_db.set_native_language_cache.assert_called_once()
        args = mock_db.set_native_language_cache.call_args
        assert args[0][3] == "no match from IMDbPie or TMDb (tried filename and directory name)"
```

- [ ] **Step 5: Add `test_tmdb_fallback_dir`**

```python
    def test_tmdb_fallback_dir(self, mocker) -> None:
        """IMDbPie fails both steps, TMDb with directory succeeds."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Some Movie", "original_title": "Some Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "fr"}
        """
        mock_urlopen.side_effect = [search_response, search_response, detail_response]
        # IMDbPie fails on both filename and dir; TMDb succeeds on dir
        path = Path("/media/Some Movie (2020)/Noisy.File.GROUP.mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result == ["fre"]
        mock_db.set_native_language_cache.assert_called_once_with(
            path, ["fre"], "tmdb_directory", None
        )
```

- [ ] **Step 6: Add `test_no_tmdb_key_dir`**

```python
    def test_no_tmdb_key_dir(self, mocker) -> None:
        """No TMDb key, IMDbPie with directory succeeds (TMDb steps skipped)."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value

        def search_side_effect(term: str) -> list:
            if "CtrlHD" in term or "Noisy" in term:
                return []
            return [{"title": "Some Movie", "year": 2020, "imdb_id": "tt0000000"}]

        instance.search_for_title.side_effect = search_side_effect
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }
        path = Path("/media/Some Movie (2020)/Noisy.File.mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["eng"]
        assert instance.search_for_title.call_count == 2
        mock_db.set_native_language_cache.assert_called_once_with(
            path, ["eng"], "imdbpie_directory", None
        )
```

- [ ] **Step 7: Run all native_language tests**

```bash
cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -v --no-header
```
Expected: all 74+ tests pass (59 existing + updates + 6 new).

- [ ] **Step 8: Commit**

```bash
cd /data/trimarr && git add tests/unit/test_native_language.py
git commit -m "test: add directory title fallback tests"
```

---

### Task 5: Run full QA gate

**Files:** No code changes — this is a verification pass.

- [ ] **Step 1: Run full test suite with coverage**

```bash
cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .
cd /data/trimarr && uv run mypy .
cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-fail-under=95
cd /data/trimarr && uv run pre-commit run --all-files
```

Expected: all gates green. Fix any issues.

- [ ] **Step 2: Commit any QA fixes**

```bash
cd /data/trimarr && git add -A && git commit -m "chore: QA gate fixes"
```
