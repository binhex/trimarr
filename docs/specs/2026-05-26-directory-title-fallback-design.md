# Directory Name Title Fallback — Design Spec

**Goal:** When `--keep-native-audio` cannot find a match using the filename as the movie title,
fall back to the **parent directory name** as an alternative title for IMDbPie and TMDb lookups.
This makes native-language detection more robust for files with noisy scene-style filenames.

---

## 1. Fallback Chain

Lookup proceeds through a strict 4-step chain. Each step only fires if the previous step returned
no results. On success at any step, the result is cached and returned immediately.

```
1. IMDbPie + filename title   → result? → cache & return
2. IMDbPie + directory title  → result? → cache & return
3. TMDb + filename title      → result? → cache & return
4. TMDb + directory title     → result? → cache & return
5. None — cache failure
```

**Key rules:**
- Steps 3–4 are skipped entirely when `--tmdb-api-key` is not configured (same as today).
- A successful search with no spoken languages returned (empty list) counts as a result — the
  chain stops. This is the same behaviour as today.
- Only a pure `None` (API failure, no results, or no year) triggers the next fallback.

---

## 2. Directory Name Parsing

Reuses the existing `parse_movie_title()` on the parent directory path. No new parsing code
is needed — the function already handles:

- Parentheses: `Das Boot (1981)` → title `"das boot"`, year `"1981"`
- Dots/underscores: `Some.Movie.2024` → title `"some movie"`, year `"2024"`
- No year in name → year `None` (step skipped — IMDbPie can't distinguish versions without a year)

**Example:**
```
Path: /media/Das Boot (1981)/Das.Boot.1981.DC.1080p.BluRay.x264-CtrlHD.mkv

Filename parse:   ("das boot dc ctrlhd", "1981")  ← noisy, IMDbPie fails
Directory parse:  ("das boot", "1981")            ← clean, IMDbPie succeeds
```

**Edge case — no directory available:**
If the file is at a root-like path with no meaningful parent (e.g. `/data/movie.mkv`),
`parse_movie_title` on the parent returns no year → the directory step is skipped automatically.

---

## 3. `resolve_native_language` Restructuring

The current function has a linear flow: try IMDbPie with filename, try TMDb with filename.
The new structure iterates through an ordered chain of `(lookup_fn, title_fn)` pairs:

```python
def _lookup_chain(tmdb_api_key: str | None) -> list[tuple]:
    """Return ordered (lookup_fn, title_fn) pairs for the fallback chain."""
    chain = [
        (_lookup_imdbpie, _get_filename_title),   # step 1
        (_lookup_imdbpie, _get_directory_title),  # step 2
    ]
    if tmdb_api_key:
        chain += [
            (_lookup_tmdb, _get_filename_title),   # step 3
            (_lookup_tmdb, _get_directory_title),  # step 4
        ]
    return chain
```

The lookup functions (`_lookup_imdbpie`, `_lookup_tmdb`) keep their current signatures:
`(title: str, year: str | None) -> list[str] | None`.

The title functions are new helpers:

| Helper | Returns |
|--------|---------|
| `_get_filename_title(file_path)` | `parse_movie_title(file_path)` |
| `_get_directory_title(file_path)` | `parse_movie_title(file_path.parent)` |

**Core loop in `resolve_native_language`:**

```python
cached = _check_native_language_cache(db, file_path)
if cached is not _CACHE_MISS:
    return cached

last_error = "no match from any source"
for lookup_fn, title_fn in _lookup_chain(tmdb_api_key):
    title, year = title_fn(file_path)
    if not title or not year:
        continue
    codes = lookup_fn(title, year)
    if codes is not None:
        # Success — codes may be empty list (film found but no spoken language)
        source = _source_label(lookup_fn, title_fn)
        _cache_and_return(db, file_path, codes, source)
        return codes
    last_error = _describe_failure(lookup_fn, title_fn, tmdb_api_key)

_cache_failure(db, file_path, last_error)
return None
```

---

## 4. Cache Labels

The `lookup_source` column in `metadata_cache` becomes more descriptive to distinguish which
title source produced the match:

| Source label | Meaning |
|---|---|
| `imdbpie_filename` | IMDbPie + filename title |
| `imdbpie_directory` | IMDbPie + directory title |
| `tmdb_filename` | TMDb + filename title |
| `tmdb_directory` | TMDb + directory title |

The existing cache schema (`file_path TEXT PK`, `native_languages TEXT`, `lookup_source TEXT`,
`lookup_error TEXT`, `file_hash TEXT`) is unchanged — only the values stored in `lookup_source`
change.

---

## 5. Error Messages

When all 4 steps are exhausted, the cached error message now reflects the full scope:

| Scenario | Error message |
|---|---|
| IMDbPie both steps failed, no TMDb key | `"no match from IMDbPie (tried filename and directory name, no TMDb API key configured)"` |
| All 4 steps failed with TMDb key | `"no match from IMDbPie or TMDb (tried filename and directory name)"` |

---

## 6. Files Changed

| File | Change |
|------|--------|
| `src/trimarr/native_language.py` | Add `_get_filename_title`, `_get_directory_title`, `_lookup_chain`, `_source_label`, `_describe_failure`. Restructure `resolve_native_language` to iterate through the chain. |
| `tests/unit/test_native_language.py` | Add tests for directory title fallback, source labels, error messages. |

---

## 7. Testing Strategy

| Test | What it covers |
|---|---|
| `test_directory_title_fallback` | Filename produces no match, directory title produces a match → returns correct codes with `imdbpie_directory` source |
| `test_filename_success_no_fallback` | Filename produces a match → directory title is never attempted |
| `test_directory_title_no_year` | Directory has no year → step skipped, falls through to TMDb |
| `test_all_steps_fail` | All 4 steps return None → cached failure with combined error message |
| `test_tmdb_fallback_dir` | IMDbPie both steps fail, TMDb with directory succeeds → `tmdb_directory` source |
| `test_no_tmdb_key_dir` | No TMDb key, IMDbPie with directory succeeds → `imdbpie_directory` source |
