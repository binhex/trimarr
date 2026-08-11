# Keep Native Audio — Design Spec

**Goal:** Preserve the original/native language audio track(s) of a film even when they do not match the user's preferred language(s), preventing the loss of the original language in dubbed films (e.g., preserving Chinese audio in a Chinese action movie that also has an English dub).

**Architecture:** A new CLI flag (`--keep-native-audio`) triggers per-file metadata lookup via IMDbPie (primary) and TMDb (fallback) to determine the film's spoken language(s). The native language(s) are merged into the effective language list before the existing track-filtering pipeline runs, so all existing guards, fallbacks, and phases apply uniformly.

---

## 1. CLI Options

### `--keep-native-audio`

When set, trimarr identifies the film's native/original spoken language(s) and preserves all audio tracks in those languages, even if they do not match `--language`.

- **Type:** `bool` (flag)
- **Required:** No
- **Default:** `False`
- **Help text:**
  ```
  If specified, trimarr identifies the film's native/original spoken
  language(s) via IMDb (or TMDb as fallback) and keeps all audio tracks
  in those languages alongside your --language preference. Ignored when
  --keep-audio is set. Requires an internet connection for first-time
  lookups; results are cached in the database.
  ```

### `--tmdb-api-key <key>`

TMDb API key for fallback language lookups when IMDbPie cannot identify the film.

- **Type:** `click.STRING`
- **Required:** No
- **Default:** `None`
- **Help text:**
  ```
  TMDb API key used as fallback when IMDbPie cannot identify a film's
  native language. Optional — without it, lookups that fail on IMDbPie
  silently fall back to standard behaviour.
  ```

### Interaction with existing flags

| Scenario | Behaviour |
|---|---|
| `--keep-native-audio` alone | Native audio kept alongside user's preferred language(s) |
| `--keep-native-audio` + `--keep-audio` | `--keep-native-audio` ignored — `--keep-audio` already keeps everything |
| Native language cannot be identified | Silent no-op — current behaviour applies |
| Native language matches `--language` | Deduplication — no change to effective language list |
| Multiple spoken languages (e.g. German + English) | All spoken-language audio tracks kept |

---

## 2. Database Caching

### New table: `metadata_cache`

```sql
CREATE TABLE IF NOT EXISTS metadata_cache (
    file_path        TEXT PRIMARY KEY,
    file_hash        TEXT NOT NULL,
    native_languages TEXT,
    lookup_source    TEXT,
    lookup_error     TEXT,
    cached_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- **`file_hash`**: The same fingerprint format as `processed_files.file_hash` (`size:mtime_ns:sha256_prefix`). Used to detect file replacement at the same path.
- **`native_languages`**: JSON array of ISO 639-2/B codes, e.g. `'["chi"]'` or `'["ger","eng"]'`. `NULL` when lookup failed or feature is off.
- **`lookup_source`**: `"imdbpie"`, `"tmdb"`, or `NULL`.
- **`lookup_error`**: Human-readable error message from a failed lookup. `NULL` on success.

### Cache lifecycle

1. Before calling the lookup API, check `metadata_cache` by `file_path`
2. If the cache entry exists **and** `file_hash` matches the current fingerprint → return stored `native_languages`
3. If cache miss **or** fingerprint mismatch → perform API lookup, upsert result, return
4. On lookup failure → store `native_languages = NULL` with the error; the file is not retried on subsequent runs (same fingerprint → same cache entry)

### Migration

`Database.open()` gains a `CREATE TABLE IF NOT EXISTS metadata_cache` statement alongside the existing `processed_files` schema. No migration needed — it is a new table.

---

## 3. Lookup Module

### New module: `src/trimarr/native_language.py`

Responsibilities:
1. Parse an MKV file path to extract a searchable movie title and year
2. Query IMDbPie (primary) then TMDb (fallback) for the film's spoken languages
3. Convert language names/codes to ISO 639-2/B format

### Filename parsing — `_parse_movie_title(file_path: Path) -> tuple[str, str | None]`

```
Input:  "/data/Movies/Das Boot (1981) [BluRay-2160p].mkv"
Output: ("Das Boot", "1981")

Input:  "/data/Movies/Some.Movie.2024.1080p.WEBRip.x265.mkv"
Output: ("Some Movie", "2024")

Input:  "/data/Movies/Unknown.mkv"
Output: ("Unknown", None)
```

Strips the extension, replaces `.` and `_` with spaces, removes common release tags (`BluRay`, `WEBRip`, `2160p`, `x264`, `x265`, `HDR`, etc.), and extracts a 4-digit year if present.

### IMDbPie strategy — `_lookup_imdbpie(title: str, year: str | None) -> list[str] | None`

1. `imdbpie.Imdb().search_for_title(f"{title} {year}")` → list of candidate hits
2. Match by comparing normalised title + year (same approach as movarr's `_match_imdbpie_hit`)
3. `client.get_title_auxiliary(imdb_id)` → extract `spokenLanguages` field
4. Convert each language name to ISO 639-2/B code via `pycountry`:
   - `pycountry.languages.get(name="German")` → `alpha_2="de"` → map to `ger` via the existing `_ISO_639_1_TO_2` table in `processor.py`
5. Return deduplicated list of ISO 639-2/B codes

### TMDb fallback strategy — `_lookup_tmdb(title: str, year: str | None, api_key: str) -> list[str] | None`

1. `GET https://api.themoviedb.org/3/search/movie?query={title}&year={year}&api_key={key}`
2. Find first result matching the title + year
3. `GET https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={key}`
4. Extract `original_language` field — a single ISO 639-1 code (e.g. `"zh"`)
5. Convert to ISO 639-2/B via the existing `_ISO_639_1_TO_2` table
6. Return `[code]` or `None`

### Public function — `resolve_native_language(...)`

```python
def resolve_native_language(
    file_path: Path,
    db: Database,
    tmdb_api_key: str | None,
    logger: Logger,
) -> list[str] | None:
    """Return native language codes for *file_path*, or None if unknown.

    Checks metadata_cache first. On miss, runs IMDbPie then TMDb,
    caches the result. Returns ISO 639-2/B codes.
    """
```

### New dependencies

| Package | Reason | Source (already in movarr) |
|---|---|---|
| `imdbpie` | IMDb metadata lookup (no API key required) | Yes |
| `pycountry` | Language name → ISO code conversion | Yes |

---

## 4. Pipeline Integration

### Change in `runner.py` — `_process_one_file()`

**Before:**
```python
# profile_hash computed once in run(), passed as argument
# build_mkvmerge_command(language=cfg.language, ...)
```

**After:**
```python
# Compute effective language for this specific file
effective_language = _resolve_effective_language(
    file_path=file_path,
    cfg=cfg,
    db=db,
    logger=logger,
)

# Per-file profile hash (needed because effective language differs per file)
per_file_hash = _build_profile_hash(
    language=effective_language,
    keep_audio=cfg.keep_audio,
    keep_subtitles=cfg.keep_subtitles,
    edit_metadata_title=cfg.edit_metadata_title,
    delete_metadata_title=cfg.delete_metadata_title,
    strip_lower_channels=cfg.strip_lower_channels,
    strip_commentary=cfg.strip_commentary,
    strip_subtitle_regex_patterns=cfg.strip_subtitle_regex_patterns,
)

# Skip check uses the per-file hash
if db.is_processed(file_path, profile_hash=per_file_hash):
    ...

# Command built with effective language
cmd = build_mkvmerge_command(language=effective_language, ...)

# Mark processed with the per-file hash
db.mark_processed(file_path, profile_hash=per_file_hash, ...)
```

### New helper — `_resolve_effective_language()`

```python
def _resolve_effective_language(
    file_path: Path,
    cfg: _ProcessingConfig,
    db: Database,
    logger: Logger,
) -> list[str]:
    """Return the effective language list for *file_path*.

    If --keep-native-audio is set and --keep-audio is not, merges the
    film's native language(s) into the user's --language list.
    """
    if not cfg.keep_native_audio or cfg.keep_audio:
        return cfg.language

    native = resolve_native_language(file_path, db, cfg.tmdb_api_key, logger)
    if not native:
        return cfg.language

    # Merge and deduplicate — never mutates cfg.language
    seen = set(cfg.language)
    return cfg.language + [code for code in native if code not in seen]
```

### `_ProcessingConfig` changes

```python
@dataclass(frozen=True)
class _ProcessingConfig:
    ...
    keep_native_audio: bool = False
    tmdb_api_key: str | None = None
```

### `run()` changes

- Accepts `keep_native_audio` and `tmdb_api_key` parameters
- Forwards them to `_ProcessingConfig`
- The pre-computed `profile_hash` is still used for logging but `_process_one_file()` now computes its own per-file hash

### `_dir_has_work()` interaction

When `--keep-native-audio` is active, `_dir_has_work()` conservatively returns `True` (hooks always fire) since per-file hashes cannot be computed without per-file lookups. This only affects the pre/post-process hook optimization — no impact on correctness.

---

## 5. Error Handling & Edge Cases

| Scenario | Behaviour |
|---|---|
| IMDbPie/TMDb lookup fails | Silent no-op — `effective_language = cfg.language`. Cached as `NULL` so not retried on same fingerprint. |
| Multiple spoken languages | All preserved. Deduplication handles overlap with user's `--language`. |
| Native == user language | Deduplication → no change to effective list. No performance cost. |
| `--keep-audio` also set | Native lookup skipped entirely (documented behaviour). |
| Obscure film / misnamed file | Lookup fails → no-op. File processed with user's preferences only. |
| Network unavailable (offline/nas) | IMDbPie/TMDb HTTP errors → no-op. `--keep-native-audio` is best-effort. |
| No TMDB API key and IMDbPie fails | No-op for that file. DEBUG log notes the fallback is unavailable. |
| Subtitle tracks unaffected | Only audio tracks are preserved by this feature. Subtitle filtering is unchanged. |

---

## 6. Testing

### New test file: `tests/unit/test_native_language.py`

| Test area | Coverage |
|---|---|
| **Filename parsing** | Various naming conventions (dots, brackets, release tags, no year) |
| **IMDbPie lookup** | Mocked `imdbpie.Imdb` — success, no match, API error |
| **TMDb fallback** | Mocked HTTP — success (single language), no match, API error |
| **Both fail** | Returns `None` |
| **Deduplication** | Overlapping user + native languages, multiple native languages |
| **Database caching** | Cache hit (matching fingerprint), cache miss, stale fingerprint |
| **Pipeline integration** | `--keep-native-audio` + `--keep-audio` → native lookup skipped |
| | Per-file hash differs from base hash with native language found |
| | `_resolve_effective_language()` never mutates `cfg.language` |
| **CLI parsing** | Flag accepted, TMDB API key accepted, both optional |

### Mocking strategy

- `imdbpie.Imdb` — `unittest.mock.patch` at class level
- TMDB HTTP calls — mock `urllib.request` or use `responses` library
- Database — in-memory SQLite (existing pattern)

### Quality gates

- 95%+ coverage on new module
- Existing test suite remains green (all 352 tests)
- Ruff lint, mypy, pre-commit hooks all pass

---

## 7. Implementation Order

1. Add `imdbpie` and `pycountry` to `pyproject.toml` dependencies
2. Create `native_language.py` — filename parsing, IMDbPie lookup, TMDb fallback
3. Add `metadata_cache` schema to `database.py`
4. Add `resolve_native_language()` with caching logic
5. Add `--keep-native-audio` and `--tmdb-api-key` to `cli.py` and `_ProcessingConfig`
6. Modify `_process_one_file()` in `runner.py` — per-file effective language + profile hash
7. Write tests for all layers
8. Run full QA gate (tests, lint, mypy, pre-commit)
