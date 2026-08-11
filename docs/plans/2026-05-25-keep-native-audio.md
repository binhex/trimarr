# Keep Native Audio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--keep-native-audio` flag that preserves a film's native/original language audio track(s) even when they don't match `--language`, using IMDbPie (primary) and TMDb (fallback) for language lookup, cached in the database.

**Architecture:** Per-file language enrichment in `_process_one_file()` before the existing filter pipeline. A new `native_language.py` module handles path parsing and API lookups. A new `metadata_cache` SQLite table stores results. The effective language list (user + native) is used for both the profile hash and the mkvmerge command.

**Tech Stack:** Python 3.12+, Click, loguru, imdbpie, pycountry, sqlite3

**Spec:** `docs/superpowers/specs/2026-05-25-keep-native-audio-design.md`

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `imdbpie` and `pycountry` to dependencies**

```toml
dependencies = [
    ...
    "click",
    "imdbpie",
    "loguru",
    "pycountry",
    ...
]
```

Add mypy overrides for the untyped packages:

```toml
[[tool.mypy.overrides]]
module = ["imdbpie", "imdbpie.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "pycountry"
ignore_missing_imports = true
```

- [ ] **Step 2: Install and verify**

Run: `uv sync`
Expected: `Resolved N packages in X.XXs` — no errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add imdbpie and pycountry dependencies"
```

---

### Task 2: Add metadata_cache table to database.py

**Files:**
- Modify: `src/trimarr/database.py`

- [ ] **Step 1: Add the new table schema constant**

After the existing `_MIGRATE_ADD_PROFILE_HASH` line:

```python
# Metadata cache table for native language lookups.
_METADATA_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_cache (
    file_path        TEXT PRIMARY KEY,
    file_hash        TEXT NOT NULL,
    native_languages TEXT,
    lookup_source    TEXT,
    lookup_error     TEXT,
    cached_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
```

- [ ] **Step 2: Execute the schema in `open()`**

Add after the profile_hash migration (after the `if "profile_hash" not in existing_cols:` block):

```python
self._conn.executescript(_METADATA_CACHE_SCHEMA)
self._conn.commit()
```

- [ ] **Step 3: Add `get_native_language_cache()` and `set_native_language_cache()` methods to `Database`**

Add after the `total_bytes_saved()` method:

```python
def get_native_language_cache(
    self, path: Path
) -> tuple[list[str] | None, str | None, str | None] | None:
    """Return (native_languages, lookup_source, lookup_error) or None if not cached.

    Only returns a hit when the stored file_hash matches the current
    fingerprint. A mismatched hash means the file was replaced — caller
    should re-lookup.
    """
    conn = self._require_connection()
    row = conn.execute(
        "SELECT file_hash, native_languages, lookup_source, lookup_error "
        "FROM metadata_cache WHERE file_path = ?",
        (str(path),),
    ).fetchone()
    if row is None:
        return None
    stored_hash, json_langs, source, error = row
    current_hash = fingerprint(path)
    if stored_hash != current_hash:
        return None  # file changed, cache is stale
    langs: list[str] | None = json.loads(json_langs) if json_langs else None
    return langs, source, error

def set_native_language_cache(
    self,
    path: Path,
    native_languages: list[str] | None,
    lookup_source: str | None,
    lookup_error: str | None,
) -> None:
    """Store native language lookup result for *path*.

    native_languages may be None when the lookup failed — this avoids
    retrying on every subsequent run for the same file.
    """
    conn = self._require_connection()
    current_hash = fingerprint(path)
    conn.execute(
        """
        INSERT INTO metadata_cache (file_path, file_hash, native_languages,
                                    lookup_source, lookup_error)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_hash        = excluded.file_hash,
            native_languages = excluded.native_languages,
            lookup_source    = excluded.lookup_source,
            lookup_error     = excluded.lookup_error,
            cached_at        = CURRENT_TIMESTAMP
        """,
        (
            str(path),
            current_hash,
            json.dumps(native_languages) if native_languages is not None else None,
            lookup_source,
            lookup_error,
        ),
    )
    conn.commit()
```

Add the `json` import at the top of the file if not already present.

- [ ] **Step 4: Write a quick test to verify the cache round-trips**

Run: `cd /data/trimarr && uv run python -c "
from pathlib import Path
from trimarr.database import Database
import tempfile, os

with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db_path = f.name
try:
    with Database(db_path) as db:
        p = Path('/tmp/test.mkv')
        # Create a dummy file so fingerprint() works
        Path('/tmp/test.mkv').write_text('hello')
        db.set_native_language_cache(p, ['chi'], 'imdbpie', None)
        result = db.get_native_language_cache(p)
        assert result == (['chi'], 'imdbpie', None), f'Got {result}'
        print('PASS: cache round-trip')
finally:
    os.unlink(db_path)
    Path('/tmp/test.mkv').unlink(missing_ok=True)
"`
Expected: `PASS: cache round-trip`

- [ ] **Step 5: Commit**

```bash
git add src/trimarr/database.py
git commit -m "feat: add metadata_cache table for native language lookups"
```

---

### Task 3: Create native_language.py

**Files:**
- Create: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py` (written in Task 7)

- [ ] **Step 1: Create the module skeleton with filename parsing**

```python
"""Native/original language detection for MKV files.

Uses IMDbPie (primary) and TMDb (fallback) to identify the spoken
language(s) of a film from its file path.  Results are cached in
the metadata_cache SQLite table to avoid redundant API calls.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from trimarr.processor import _ISO_639_1_TO_2, normalize_language_code

if TYPE_CHECKING:
    from trimarr.database import Database

logger = logging.getLogger(__name__)

# Common release-group tags and container metadata to strip from filenames.
_RELEASE_TAGS_RE = re.compile(
    r"""
    \b(?:BluRay|WEBRip|WEB-DL|BRRip|HDRip|DVDRip|BDRip|HDTV|PDTV|WEB|H264|H\.264|H265|H\.265|
       x264|x265|XviD|DivX|AVC|HEVC|MPEG-?2|MPEG-?4|
       2160p|1080p|720p|480p|360p|
       Atmos|TrueHD|DTS-HD|DTS|AC3|AAC|FLAC|DD5\.1|DD2\.0|5\.1|7\.1|
       MULTi|DUAL|PROPER|REPACK|EXTENDED|UNRATED|DIRECTOR.?S.?CUT|
       REMUX|ENCODE|INTERNAL|READNFO|COMPLETE|
       HDR|HDR10|HDR10PLUS|SDR|DV|DoVi|DolbyVision|
       AMZN|NF|WEB|iTunes|MA|DSNP|HMAX|ATVP)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Characters that separate words in filenames — replace with space before parsing.
_WORD_SEPARATORS = re.compile(r"[._\-+]")


def _normalise_title(s: str) -> str:
    """Lower-case, collapse whitespace, and strip leading/trailing spaces."""
    return " ".join(s.lower().split())


def _strip_release_tags(title: str) -> str:
    """Remove known release-group tags that might cause false positives in searches."""
    return _RELEASE_TAGS_RE.sub("", title).strip()


def parse_movie_title(file_path: Path) -> tuple[str, str | None]:
    """Extract a searchable movie title and optional year from *file_path*.

    Returns ``(title, year)`` where *title* is the cleaned movie name
    suitable for IMDb/TMDb search queries and *year* is a 4-digit string
    or ``None``.

    Examples:
        >>> parse_movie_title(Path("/data/Das Boot (1981).mkv"))
        ("Das Boot", "1981")

        >>> parse_movie_title(Path("/data/Some.Movie.2024.2160p.WEBRip.mkv"))
        ("Some Movie", "2024")
    """
    stem = file_path.stem  # e.g. "Das Boot (1981)" or "Some.Movie.2024.2160p.WEBRip"

    # Replace word separators with spaces
    cleaned = _WORD_SEPARATORS.sub(" ", stem)

    # Extract year
    year: str | None = None
    year_match = _YEAR_RE.search(cleaned)
    if year_match:
        year = year_match.group(1)
        # Remove the year from the title so it's not part of the search
        cleaned = cleaned.replace(year_match.group(0), "")

    # Remove bracketed content that isn't the year e.g. "[BluRay-2160p]"
    # But keep the year we already extracted. Pattern: remove [...] blocks
    # that don't contain a year.
    def _strip_brackets(s: str) -> str:
        # Remove [...] that don't contain a year
        parts = re.split(r"(\[[^\]]*\])", s)
        result = []
        for part in parts:
            if part.startswith("[") and part.endswith("]"):
                if not _YEAR_RE.search(part):
                    continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_brackets(cleaned)

    # Remove parens that don't contain a year
    def _strip_parens(s: str) -> str:
        parts = re.split(r"(\([^)]*\))", s)
        result = []
        for part in parts:
            if part.startswith("(") and part.endswith(")"):
                if not _YEAR_RE.search(part):
                    continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_parens(cleaned)

    # Strip release tags
    cleaned = _strip_release_tags(cleaned)

    # Clean up whitespace
    title = _normalise_title(" ".join(cleaned.split()))
    if not title:
        title = _normalise_title(file_path.stem)  # fallback to raw stem

    return title, year
```

- [ ] **Step 2: Add IMDbPie lookup**

```python
def _lookup_imdbpie(title: str, year: str | None) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie, or None on failure.

    Searches IMDb by title+year, then fetches auxiliary metadata to
    extract spoken languages.
    """
    try:
        import imdbpie  # noqa: PLC0415
    except ImportError:
        logger.warning("imdbpie not installed — cannot perform IMDb lookup.")
        return None

    try:
        client = imdbpie.Imdb()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None

    search_term = title
    if year:
        search_term = f"{title} {year}"

    try:
        hits = client.search_for_title(search_term)
    except Exception as exc:  # noqa: BLE001
        logger.debug("IMDbPie search failed for '%s': %s", search_term, exc)
        return None

    if not hits:
        logger.debug("IMDbPie returned no hits for '%s'.", search_term)
        return None

    # Find the first hit that matches title + year
    matched_id: str | None = None
    for hit in hits:
        hit_title = (hit.get("title") or "").strip().lower()
        if _normalise_title(hit_title) != _normalise_title(title):
            continue
        hit_year = hit.get("year")
        if year and hit_year is not None:
            try:
                if int(hit_year) != int(year):
                    continue
            except (ValueError, TypeError):
                continue
        matched_id = hit.get("imdb_id")
        if matched_id:
            break

    if not matched_id:
        logger.debug("IMDbPie no title+year match for '%s'.", search_term)
        return None

    # Fetch auxiliary data for spoken languages
    try:
        aux = client.get_title_auxiliary(matched_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("IMDbPie aux data failed for '%s': %s", matched_id, exc)
        return None

    spoken = aux.get("spokenLanguages") if aux else None
    if not spoken:
        return None

    # Convert language names to ISO 639-2/B codes
    codes: list[str] = []
    for entry in spoken:
        if isinstance(entry, dict):
            lang_name = entry.get("name") or entry.get("description", "")
        else:
            lang_name = str(entry)
        code = _language_name_to_iso_639_2(lang_name)
        if code and code not in codes:
            codes.append(code)

    return codes or None


def _language_name_to_iso_639_2(name: str) -> str | None:
    """Convert a spoken language name (e.g. 'German', 'English') to ISO 639-2/B.

    Uses pycountry for name-to-code resolution, then maps alpha-2 to
    alpha-3/bibliographic via the existing tables in processor.py.
    """
    try:
        import pycountry  # noqa: PLC0415
    except ImportError:
        # No pycountry available — try a minimal built-in mapping
        return _fallback_lang_name_to_code(name)

    # Try pycountry by name
    lang = pycountry.languages.get(name=name)
    if lang is not None:
        alpha_2 = getattr(lang, "alpha_2", None)
        if alpha_2:
            return _ISO_639_1_TO_2.get(alpha_2.lower(), alpha_2.lower())
        alpha_3 = getattr(lang, "alpha_3", None)
        if alpha_3:
            return normalize_language_code(alpha_3.lower())

    # Try pycountry by alpha_3 (e.g. "deu" -> bibliographic "ger")
    lang = pycountry.languages.get(alpha_3=name.lower())
    if lang is not None:
        alpha_2 = getattr(lang, "alpha_2", None)
        if alpha_2:
            return _ISO_639_1_TO_2.get(alpha_2.lower(), alpha_2.lower())
        return normalize_language_code(name.lower())

    # Try pycountry by bibliographic
    lang = pycountry.languages.get(bibliographic=name.lower())
    if lang is not None:
        alpha_2 = getattr(lang, "alpha_2", None)
        if alpha_2:
            return _ISO_639_1_TO_2.get(alpha_2.lower(), alpha_2.lower())
        return normalize_language_code(name.lower())

    return _fallback_lang_name_to_code(name)


# Minimal fallback for common languages when pycountry is not available.
_LANG_NAME_TO_CODE: dict[str, str] = {
    "english": "eng",
    "german": "ger",
    "french": "fre",
    "spanish": "spa",
    "italian": "ita",
    "japanese": "jpn",
    "chinese": "chi",
    "korean": "kor",
    "russian": "rus",
    "arabic": "ara",
    "portuguese": "por",
    "dutch": "dut",
    "polish": "pol",
    "turkish": "tur",
    "swedish": "swe",
    "danish": "dan",
    "finnish": "fin",
    "norwegian": "nor",
    "czech": "cze",
    "hungarian": "hun",
    "romanian": "rum",
    "thai": "tha",
    "vietnamese": "vie",
    "hindi": "hin",
}


def _fallback_lang_name_to_code(name: str) -> str | None:
    """Fallback language name → ISO 639-2/B code without pycountry."""
    return _LANG_NAME_TO_CODE.get(name.lower().strip())
```

- [ ] **Step 3: Add TMDb fallback lookup**

```python
def _lookup_tmdb(title: str, year: str | None, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb, or None on failure.

    TMDb returns a single ``original_language`` field (ISO 639-1 code)
    which we map to ISO 639-2/B.
    """
    encoded_title = urllib.parse.quote(title)
    search_url = (
        f"https://api.themoviedb.org/3/search/movie"
        f"?query={encoded_title}&api_key={api_key}"
    )
    if year:
        search_url += f"&year={year}"

    try:
        with urllib.request.urlopen(search_url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        logger.debug("TMDb search failed for '%s': %s", title, exc)
        return None

    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        logger.debug("TMDb no results for '%s'.", title)
        return None

    # Find first result whose title (or original_title) matches
    for hit in results:
        for field in ("title", "original_title"):
            hit_title = (hit.get(field) or "").strip().lower()
            if hit_title and _normalise_title(hit_title) == _normalise_title(title):
                # Fetch detail for original_language
                tmdb_id = hit.get("id")
                if tmdb_id is None:
                    continue
                detail_url = (
                    f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                    f"?api_key={api_key}"
                )
                try:
                    with urllib.request.urlopen(detail_url, timeout=15) as resp2:
                        detail = json.loads(resp2.read().decode())
                except Exception as exc:  # noqa: BLE001
                    logger.debug("TMDb detail failed for id %s: %s", tmdb_id, exc)
                    continue

                raw_lang = detail.get("original_language")
                if not raw_lang:
                    continue
                # Map alpha-2 to alpha-3/bibliographic
                code = _ISO_639_1_TO_2.get(raw_lang.lower())
                if code:
                    return [normalize_language_code(code)]
                # If it's already 3-char, normalize
                if len(raw_lang) == 3 and raw_lang.isalpha():
                    return [normalize_language_code(raw_lang.lower())]

    return None
```

- [ ] **Step 4: Add the main `resolve_native_language()` function**

```python
def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
) -> list[str] | None:
    """Return ISO 639-2/B native language codes for *file_path*, or None.

    Checks the database cache first (by file_path + fingerprint).  On miss,
    attempts IMDbPie lookup followed by TMDb fallback.  Caches the result
    (including failures) so subsequent runs are fast.

    Args:
        file_path: Path to the MKV file.
        db: An open Database instance, or None to skip caching.
        tmdb_api_key: Optional TMDb API key for fallback lookups.

    Returns:
        A list of ISO 639-2/B language codes, or None if the native
        language could not be determined.
    """
    # Cache check
    if db is not None:
        cached = db.get_native_language_cache(file_path)
        if cached is not None:
            langs, source, error = cached
            if langs:
                logger.debug("Native language cache hit for '%s': %s (source: %s)", file_path.name, langs, source)
            return langs

    # Parse filename
    title, year = parse_movie_title(file_path)
    if not title:
        logger.debug("Could not parse movie title from '%s'.", file_path.name)
        _maybe_cache_failure(db, file_path, "unable to parse title")
        return None

    logger.debug("Looking up native language for '%s' (title=%s, year=%s).", file_path.name, title, year)

    # Primary: IMDbPie
    codes = _lookup_imdbpie(title, year)
    source = "imdbpie"
    error: str | None = None

    # Fallback: TMDb
    if not codes and tmdb_api_key:
        codes = _lookup_tmdb(title, year, tmdb_api_key)
        source = "tmdb"
    elif not codes:
        error = "IMDbPie returned no data and no TMDb API key configured"

    if not codes:
        logger.debug("No native language found for '%s'.", file_path.name)
        _maybe_cache_failure(db, file_path, error or "no match from API")
        return None

    logger.info("Identified native language(s) for '%s': %s (source: %s).", file_path.name, codes, source)

    # Cache success
    if db is not None:
        db.set_native_language_cache(file_path, codes, source, None)

    return codes


def _maybe_cache_failure(db: Database | None, file_path: Path, error: str) -> None:
    """Store a failed lookup result so we don't retry on every run."""
    if db is not None:
        db.set_native_language_cache(file_path, None, None, error)
```

- [ ] **Step 5: Verify the module imports cleanly**

Run: `cd /data/trimarr && uv run python -c "from trimarr.native_language import parse_movie_title, resolve_native_language; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Verify filename parsing**

Run: `cd /data/trimarr && uv run python -c "
from pathlib import Path
from trimarr.native_language import parse_movie_title

tests = [
    ('/data/Das Boot (1981).mkv', ('Das Boot', '1981')),
    ('/data/Some.Movie.2024.2160p.WEBRip.mkv', ('Some Movie', '2024')),
    ('/data/Unknown.mkv', ('unknown', None)),
    ('/data/Movie.Name.2022.1080p.BluRay.x265.mkv', ('Movie Name', '2022')),
]
for path_str, expected in tests:
    result = parse_movie_title(Path(path_str))
    status = 'PASS' if result == expected else f'FAIL (got {result})'
    print(f'{status}: {path_str}')
"`
Expected: All four tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/trimarr/native_language.py
git commit -m "feat: add native_language module for film language detection"
```

---

### Task 4: Add effective language resolution to runner.py

**Files:**
- Modify: `src/trimarr/runner.py`

- [ ] **Step 1: Add `_resolve_effective_language()` helper**

Add this function before `_process_one_file()`:

```python
def _resolve_effective_language(
    file_path: Path,
    cfg: _ProcessingConfig,
    db: Database,
    logger: Logger,
) -> list[str]:
    """Return the effective language list for *file_path*.

    If ``keep_native_audio`` is set and ``keep_audio`` is not, merges
    the film's native language(s) into the user's ``--language`` list.
    The original ``cfg.language`` list is never mutated.
    """
    if not cfg.keep_native_audio or cfg.keep_audio:
        return cfg.language

    from trimarr.native_language import resolve_native_language  # noqa: PLC0415

    native = resolve_native_language(
        file_path=file_path,
        db=db,
        tmdb_api_key=cfg.tmdb_api_key,
    )
    if not native:
        return cfg.language

    # Merge and deduplicate — cfg.language is a list[str]
    seen = set(cfg.language)
    return cfg.language + [code for code in native if code not in seen]
```

- [ ] **Step 2: Modify `_process_one_file()` for per-file profile hash**

Change the function signature and early-exit to compute the effective language and per-file hash:

Current signature:
```python
def _process_one_file(
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    db: Database,
    profile_hash: str,
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
) -> None:
```

Change to derive `profile_hash` from the effective language inside the function rather than accepting it as a parameter:

```python
def _process_one_file(
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    db: Database,
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
) -> None:
    # Compute effective language for this file
    effective_language = _resolve_effective_language(
        file_path=file_path,
        cfg=cfg,
        db=db,
        logger=logger,
    )

    # Build per-file profile hash
    profile_hash = _build_profile_hash(
        language=effective_language,
        keep_audio=cfg.keep_audio,
        keep_subtitles=cfg.keep_subtitles,
        edit_metadata_title=cfg.edit_metadata_title,
        delete_metadata_title=cfg.delete_metadata_title,
        strip_lower_channels=cfg.strip_lower_channels,
        strip_commentary=cfg.strip_commentary,
        strip_subtitle_regex_patterns=cfg.strip_subtitle_regex_patterns,
    )

    # Skip unchanged files processed with the same profile
    if db.is_processed(file_path, profile_hash=profile_hash):
        ...
```

- [ ] **Step 3: Use `effective_language` in `build_mkvmerge_command`**

In the `build_mkvmerge_command()` call inside `_process_one_file()`, replace `cfg.language` with `effective_language`:

```python
    cmd = build_mkvmerge_command(
        mkvmerge_path=cfg.mkvmerge_path,
        input_path=file_path,
        output_path=file_path,
        tracks=tracks,
        language=effective_language,  # was cfg.language
        ...
    )
```

- [ ] **Step 4: Update `_process_one_file_guarded()` call site**

Update the function call in `_process_directory_groups()` to match the new signature (remove `profile_hash` argument):

```python
_process_one_file_guarded(
    file_path=file_path,
    root=root,
    idx=global_idx,
    total=total,
    db=db,
    # profile_hash removed — now computed inside _process_one_file
    cfg=cfg,
    counts=counts,
    failures=failures,
    logger=logger,
)
```

Also remove `profile_hash` from `_process_one_file_guarded`'s own signature and its call to `_process_one_file`.

- [ ] **Step 5: Update `_dir_has_work()` for per-file hashes**

When `--keep-native-audio` is active, `_dir_has_work` conservatively returns `True` since it can't compute per-file profile hashes without doing per-file lookups:

```python
def _dir_has_work(
    files_in_dir: list[tuple[Path, Path]],
    db: Database,
    profile_hash: str,
    cfg: _ProcessingConfig,  # NEW parameter
    logger: Logger,
) -> bool:
    # When keep_native_audio is active, eagerly fire hooks since
    # per-file profile hashes can't be computed here.
    if cfg.keep_native_audio and not cfg.keep_audio:
        return True

    # existing logic unchanged...
```

Update the call site in `_process_directory_groups()`:

```python
dir_has_work = _dir_has_work(files_in_dir, db, profile_hash, cfg, logger)
```

- [ ] **Step 6: Remove the now-unnecessary pre-computed `profile_hash` from `_process_directory_groups()`**

The `profile_hash` parameter in `_process_directory_groups()` is no longer needed for the file processing loop (it was only passed to `_process_one_file`). It is still used for `_dir_has_work` since we kept the base hash for the logging path. Update the call in `run()` to not pass it:

```python
counts, interrupted, failures = _process_directory_groups(
    dir_groups=dir_groups,
    database_path=database_path,
    # profile_hash removed — handled per-file now
    cfg=cfg,
    total=total,
    ...
)
```

Wait — the `profile_hash` is still used inside `_process_directory_groups()` for `_dir_has_work` and also for logging. Let me trace through more carefully.

Looking at `_process_directory_groups()`:

```python
def _process_directory_groups(
    dir_groups, database_path, profile_hash, cfg, total, ...
) -> tuple[_RunCounts, bool, list[tuple[Path, str]]]:
    ...
    with Database(database_path) as db:
        ...
        for dir_path, files_in_dir in dir_groups.items():
            dir_has_work = _dir_has_work(files_in_dir, db, profile_hash, logger)
            ...
```

`_dir_has_work` uses the base `profile_hash`. When `--keep-native-audio` is active, `_dir_has_work` now returns True early (from Step 5), so the base hash doesn't matter there. When inactive, the base hash is correct for all files. So keep `profile_hash` as a parameter for `_process_directory_groups()` but don't pass it through to `_process_one_file()`.

Actually, looking at it again, `_dir_has_work` always uses the same `profile_hash` for all files. When `--keep-native-audio` is NOT active, this is correct (same hash for all files). When it IS active, we return True early. So we keep the `profile_hash` in `_process_directory_groups` for the non-native-audio path.

The only change needed: stop passing `profile_hash` from `_process_directory_groups()` to `_process_one_file()` / `_process_one_file_guarded()`, since `_process_one_file()` now computes its own.

Let me write this clearly:

- [ ] **Step 6: Update `_process_directory_groups` to not pass `profile_hash` to per-file functions**

In the file-processing loop inside `_process_directory_groups`, remove the `profile_hash` argument from both `_process_one_file_guarded` and the underlying `_process_one_file`:

```python
# In _process_directory_groups, the call becomes:
_process_one_file_guarded(
    file_path=file_path,
    root=root,
    idx=global_idx,
    total=total,
    db=db,
    cfg=cfg,
    counts=counts,
    failures=failures,
    logger=logger,
)
```

- [ ] **Step 7: Verify the module still compiles**

Run: `cd /data/trimarr && uv run python -c "from trimarr.runner import run; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add src/trimarr/runner.py
git commit -m "feat: add per-file effective language resolution for keep-native-audio"
```

---

### Task 5: Add config fields and CLI flags

**Files:**
- Modify: `src/trimarr/cli.py`
- Modify: `src/trimarr/runner.py`

- [ ] **Step 1: Add fields to `_ProcessingConfig` in runner.py**

```python
@dataclass(frozen=True)
class _ProcessingConfig:
    ...
    keep_native_audio: bool = False
    tmdb_api_key: str | None = None
```

- [ ] **Step 2: Update `run()` function signature in runner.py**

Add `keep_native_audio` and `tmdb_api_key` parameters:

```python
def run(
    language: list[str],
    ...
    skip_size_check: bool = False,
    keep_native_audio: bool = False,  # NEW
    tmdb_api_key: str | None = None,  # NEW
    ...
) -> None:
```

Pass them to `_ProcessingConfig`:

```python
cfg = _ProcessingConfig(
    ...
    keep_native_audio=keep_native_audio,
    tmdb_api_key=tmdb_api_key,
)
```

- [ ] **Step 3: Add `--keep-native-audio` flag to cli.py**

Add after the `--keep-audio` option:

```python
@click.option(
    "--keep-native-audio",
    is_flag=True,
    default=False,
    help=(
        "If specified, trimarr identifies the film's native/original spoken"
        " language(s) via IMDb (or TMDb as fallback) and keeps all audio tracks"
        " in those languages alongside your --language preference."
        " Ignored when --keep-audio is set."
        " Requires an internet connection for first-time lookups;"
        " results are cached in the database."
    ),
)
```

- [ ] **Step 4: Add `--tmdb-api-key` option to cli.py**

Add after `--keep-native-audio`:

```python
@click.option(
    "--tmdb-api-key",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<key>",
    help=(
        "TMDb API key used as fallback when IMDbPie cannot identify a film's"
        " native language. Optional — without it, lookups that fail on IMDbPie"
        " silently fall back to standard behaviour."
    ),
)
```

- [ ] **Step 5: Pass new flags through `cli()` to `run()`**

Add parameters to `cli()` function signature:

```python
keep_native_audio: bool,
tmdb_api_key: str | None,
```

Add to the `_run()` closure:

```python
def _run() -> None:
    run(
        ...
        keep_native_audio=keep_native_audio,
        tmdb_api_key=tmdb_api_key,
    )
```

- [ ] **Step 6: Verify CLI parses correctly**

Run: `cd /data/trimarr && uv run python -m trimarr.cli --help 2>&1 | grep -A1 "keep-native-audio\|tmdb-api-key"`
Expected: Both new options appear in the help output.

- [ ] **Step 7: Commit**

```bash
git add src/trimarr/cli.py src/trimarr/runner.py
git commit -m "feat: add --keep-native-audio and --tmdb-api-key CLI flags"
```

---

### Task 6: Write tests for native_language.py

**Files:**
- Create: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write filename parsing tests**

```python
"""Unit tests for trimarr.native_language."""

from __future__ import annotations

from pathlib import Path

import pytest

from trimarr.native_language import (
    _language_name_to_iso_639_2,
    _lookup_imdbpie,
    _lookup_tmdb,
    parse_movie_title,
    resolve_native_language,
)


class TestParseMovieTitle:
    """Tests for parse_movie_title()."""

    @pytest.mark.parametrize(
        ("path_str", "expected_title", "expected_year"),
        [
            ("/data/Das Boot (1981).mkv", "Das Boot", "1981"),
            ("/data/Some.Movie.2024.2160p.WEBRip.mkv", "Some Movie", "2024"),
            ("/data/Movie.Name.2022.1080p.BluRay.x265.mkv", "Movie Name", "2022"),
            ("/data/Unknown.mkv", "unknown", None),
            ("/data/test movie 1999.mkv", "test movie", "1999"),
            ("/data/[Group] Movie Title (2020).mkv", "movie title", "2020"),
            ("/data/Movie_Title_2023_HDR.mkv", "movie title", "2023"),
        ],
    )
    def test_parse_movie_title(
        self,
        path_str: str,
        expected_title: str,
        expected_year: str | None,
    ) -> None:
        result = parse_movie_title(Path(path_str))
        assert result == (expected_title, expected_year)

    def test_no_year(self) -> None:
        result = parse_movie_title(Path("/data/SomeMovie.mkv"))
        assert result[1] is None
        assert isinstance(result[0], str) and len(result[0]) > 0
```

- [ ] **Step 2: Write language name conversion tests**

```python
class TestLanguageNameToCode:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("English", "eng"),
            ("German", "ger"),
            ("French", "fre"),
            ("Chinese", "chi"),
            ("Spanish", "spa"),
            ("Japanese", "jpn"),
            ("Korean", "kor"),
            ("", None),
            ("UnknownLanguage", None),
        ],
    )
    def test_language_name_to_code(self, name: str, expected: str | None) -> None:
        result = _language_name_to_iso_639_2(name)
        assert result == expected
```

- [ ] **Step 3: Write IMDbPie lookup tests (mocked)**

```python
class TestLookupImdbpie:
    def test_success(self, mocker) -> None:
        """IMDbPie finds the movie and returns languages."""
        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [
                {"name": "German"},
                {"name": "English"},
            ],
        }

        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is not None
        assert "ger" in result
        assert "eng" in result

    def test_no_match(self, mocker) -> None:
        """IMDbPie returns no matching title."""
        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []

        result = _lookup_imdbpie("Unknown Movie", None)
        assert result is None

    def test_api_error(self, mocker) -> None:
        """IMDbPie raises an exception."""
        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.side_effect = RuntimeError("API error")

        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None
```

- [ ] **Step 4: Write TMDb fallback tests (mocked)**

```python
class TestLookupTmdb:
    def test_success(self, mocker) -> None:
        """TMDb search + detail returns original_language."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")

        # First call: search results
        search_response = mocker.MagicMock()
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Wo Hu Cang Long", "original_title": "Wo Hu Cang Long"}]}
        """
        # Second call: movie detail
        detail_response = mocker.MagicMock()
        detail_response.read.return_value = b"""
        {"original_language": "zh"}
        """
        mock_urlopen.side_effect = [search_response, detail_response]

        result = _lookup_tmdb("Wo Hu Cang Long", "2000", "fake-key")
        assert result == ["chi"]

    def test_no_results(self, mocker) -> None:
        """TMDb returns empty results."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        response = mocker.MagicMock()
        response.read.return_value = b'{"results": []}'
        mock_urlopen.return_value = response

        result = _lookup_tmdb("Unknown", None, "fake-key")
        assert result is None

    def test_no_api_key(self) -> None:
        """No TMDb API key means no lookup is attempted."""
        result = _lookup_tmdb("Test", "2020", "")
        assert result is None
```

- [ ] **Step 5: Write integration test for `resolve_native_language` (mocked)**

```python
class TestResolveNativeLanguage:
    def test_cache_hit(self, mocker) -> None:
        """Database cache hit returns stored languages without API call."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "imdbpie", None)

        result = resolve_native_language(
            Path("/data/test.mkv"),
            db=mock_db,
        )
        assert result == ["chi"]
        mock_db.get_native_language_cache.assert_called_once()
        # No API lookup called
        import trimarr.native_language as nl
        # Should not reach imdbpie

    def test_cache_miss_then_imdbpie_success(self, mocker) -> None:
        """Cache miss triggers IMDbPie lookup, result is cached."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None  # miss

        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }

        result = resolve_native_language(
            Path("/data/Das Boot (1981).mkv"),
            db=mock_db,
        )
        assert result == ["ger"]
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Das Boot (1981).mkv"), ["ger"], "imdbpie", None
        )

    def test_cache_miss_all_fail(self, mocker) -> None:
        """Cache miss + all APIs fail returns None and caches failure."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None

        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []

        result = resolve_native_language(
            Path("/data/Unknown.mkv"),
            db=mock_db,
        )
        assert result is None
        # Failure should be cached so we don't retry
        mock_db.set_native_language_cache.assert_called_once()

    def test_no_db_provided(self, mocker) -> None:
        """When db is None, lookup still works but results aren't cached."""
        mock_client = mocker.patch("trimarr.native_language.imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Test", "year": 2020, "imdb_id": "tt0000000"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }

        result = resolve_native_language(
            Path("/data/Test (2020).mkv"),
            db=None,
        )
        assert result == ["eng"]
```

- [ ] **Step 6: Run tests**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -v --no-header 2>&1`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_native_language.py
git commit -m "test: add native_language module tests"
```

---

### Task 7: Write pipeline integration tests

**Files:**
- Modify: `tests/unit/test_runner.py`

- [ ] **Step 1: Add effective language resolution tests**

Add to `tests/unit/test_runner.py`:

```python
class TestResolveEffectiveLanguage:
    """Tests for _resolve_effective_language()."""

    def test_no_flag(self) -> None:
        """keep_native_audio off → effective == cfg.language."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=False,
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng"]

    def test_keep_audio_overrides(self) -> None:
        """--keep-audio causes native lookup to be skipped."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=True,  # overrides
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),  # noqa: F821
            logger=MagicMock(),  # noqa: F821
        )
        assert result == ["eng"]

    def test_native_added_to_language(self, mocker) -> None:
        """Native language is merged into the effective list."""
        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        mock_resolve = mocker.patch(
            "trimarr.runner.resolve_native_language",
            return_value=["chi"],
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),  # noqa: F821
            logger=MagicMock(),  # noqa: F821
        )
        assert result == ["eng", "chi"]
        mock_resolve.assert_called_once()

    def test_native_dedup(self, mocker) -> None:
        """Native language already in --language list is not duplicated."""
        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng", "chi"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        mocker.patch(
            "trimarr.runner.resolve_native_language",
            return_value=["chi"],
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),  # noqa: F821
            logger=MagicMock(),  # noqa: F821
        )
        assert result == ["eng", "chi"]  # no duplicate
        assert result.count("chi") == 1

    def test_cfg_not_mutated(self, mocker) -> None:
        """_resolve_effective_language does not mutate cfg.language."""
        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        original = list(cfg.language)
        mocker.patch(
            "trimarr.runner.resolve_native_language",
            return_value=["chi"],
        )
        _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),  # noqa: F821
            logger=MagicMock(),  # noqa: F821
        )
        assert cfg.language == original  # unchanged
```

- [ ] **Step 2: Run the runner tests**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_runner.py -v --no-header 2>&1`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_runner.py
git commit -m "test: add effective language resolution tests for keep-native-audio"
```

---

### Task 8: Full QA gate

- [ ] **Step 1: Run Ruff lint + format**

Run: `cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .`
Expected: Clean output, no errors.

- [ ] **Step 2: Run mypy**

Run: `cd /data/trimarr && uv run mypy . 2>&1`
Expected: `Success: no issues found in X source file(s)`

- [ ] **Step 3: Run full test suite**

Run: `cd /data/trimarr && uv run pytest --cov=src/trimarr -v --no-header 2>&1`
Expected: All tests PASS, coverage ≥ 98%.

- [ ] **Step 4: Run pre-commit**

Run: `cd /data/trimarr && uv run pre-commit run --all-files 2>&1`
Expected: 13/13 hooks green.

- [ ] **Step 5: Verify CLI help**

Run: `cd /data/trimarr && uv run python -m trimarr.cli --help`
Expected: Help output includes `--keep-native-audio` and `--tmdb-api-key`.

- [ ] **Step 6: Final commit of all changes**

```bash
git add -A
git commit -m "feat: implement keep-native-audio feature"
```
