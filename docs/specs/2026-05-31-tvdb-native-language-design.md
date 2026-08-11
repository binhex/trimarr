# TVDB Native Language Lookup

**Date:** 2026-05-31
**Status:** Design approved
**Issue:** [binhex/trimarr#58](https://github.com/binhex/trimarr/issues/58)

## Problem

Sonarr-generated NFO files for TV shows often contain only a `<tvdbid>` element
(e.g. `7537283`) with no `<imdbid>` or `<tmdbid>`. The current
`resolve_native_language()` chain can only resolve IMDb and TMDb IDs, so native
language lookups fail for these files, leaving `no match from IMDbPie` in the
database.

## Scope

**Approach B** from the brainstorming session:

- Add TVDB as a direct-ID lookup source in the NFO phase (Phase 1)
- Also parse embedded `{tvdb-...}` / `[tvdb-...]` IDs in filenames (Phase 2)
- TVDB is only consulted when IMDbPie and TMDb have already failed for the
  current ID source
- Requires a user-supplied `--tvdb-api-key` (TVDB v4 requires authentication
  for all requests)
- No title-based search via TVDB — search is higher complexity and adds little
  value since IMDbPie search already covers the title-search gap

## Design

### 1. Data Model — `NfoMetadata`

Add `tvdb_id` field to the existing dataclass:

```python
@dataclass
class NfoMetadata:
    title: str | None
    original_title: str | None = None
    year: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None   # NEW
```

### 2. NFO Parser — `parse_nfo()`

Extract `<tvdbid>` (Sonarr format) with fallback to `<uniqueid type="tvdb">`
(Kodi format). Same precedence pattern as `imdb_id` / `tmdb_id`:

```python
tvdb_id = _extract_text(root, "tvdbid")
if tvdb_id is None:
    tvdb_id = _extract_uniqueid(root, "tvdb")
```

Update `_has_nfo_content()` to include `tvdb_id` so a TVDB-only NFO still
returns a result.

### 3. CLI — `--tvdb-api-key`

New optional flag following the exact `--tmdb-api-key` pattern:

```python
@click.option(
    "--tvdb-api-key",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<key>",
    help=(
        "TVDB API key used as a fallback when IMDbPie and TMDb cannot identify"
        " a file's native language.  Useful for TV shows whose NFO files"
        " contain only a TVDB ID.  Optional — without it, TVDB lookups are"
        " silently skipped."
    ),
)
```

Passed through to `run()` → `_ProcessingConfig` → `resolve_native_language()`.

### 4. TVDB Authentication

TVDB v4 requires a two-step auth flow:

1. **Login:** POST `https://api4.thetvdb.com/v4/login`
   - Body: `{"apikey": "<key>"}`
   - Response: `{"data": {"token": "<JWT>"}}`
   - Token is valid for **1 month**

2. **Authenticated requests:** `Authorization: Bearer <JWT>` header on all
   subsequent API calls

**Token management:**
- Login once per trimarr invocation, reuse the token for all lookups
- No persistent token caching between runs (the token lasts a month, but
  trimarr is typically run daily or on a cron schedule — the login cost is
  negligible)
- On 401 response, re-authenticate once and retry the failed request

Auth logic lives in a new module-level `_TvdbClient` (or free functions in
`native_language.py`).

### 5. TVDB Lookup Function — `_lookup_tvdb_by_id()`

```python
def _lookup_tvdb_by_id(tvdb_id: str, api_key: str) -> list[str] | None:
```

**Flow:**
1. Authenticate (login → JWT)
2. GET `https://api4.thetvdb.com/v4/series/{tvdb_id}/extended`
   - Header: `Authorization: Bearer <JWT>`
   - Timeout: 15 seconds (same as TMDb lookups)
3. Parse response JSON, extract `originalLanguage` field
   - This is a 3-letter ISO 639-2 code (e.g. `"eng"`, `"jpn"`, `"kor"`)
4. If present, return `[normalize_language_code(originalLanguage)]`
5. On any failure (network, 404, missing field), return `None`

**No `spokenLanguages` array on TVDB series records** — only a single
`originalLanguage` code. This is adequate because we use TVDB as a fallback
after IMDbPie (which returns multiple spoken languages) has already been tried.

### 6. Embedded ID Parsing — `_extract_embedded_id()`

Add TVDB ID patterns to the embedded ID regex/parser:

```
{tvdb-12345}   [tvdb-12345]   tvdb-12345
```

When found, dispatch to `_lookup_tvdb_by_id()` in Phase 2. This follows the
same dual-ID pattern used for IMDb+TMDb embedded IDs — if both IMDb and TVDB
IDs are found, try IMDb first, fall back to TVDB.

### 7. Chain Integration

The updated `resolve_native_language()` function signature:

```python
def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
    tvdb_api_key: str | None = None,   # NEW
) -> list[str] | None:
```

**Phase 1 — NFO direct ID lookups (updated):**

```
 1. NFO IMDb ID  → _lookup_imdbpie_by_id()
 2. NFO TMDb ID  → _lookup_tmdb_by_id()       [if tmdb_api_key]
 3. NFO TVDB ID  → _lookup_tvdb_by_id()        [if tvdb_api_key]  ← NEW
```

**Phase 2 — Embedded IDs (updated):**

```
 1. Embedded IMDb ID → _lookup_imdbpie_by_id()
    ├─ on failure → embedded TMDb ID (if both present) → _lookup_tmdb_by_id()
    └─ on failure → embedded TVDB ID (if both present) → _lookup_tvdb_by_id()  ← NEW
 2. Embedded TMDb ID → _lookup_tmdb_by_id()
    └─ on failure → embedded TVDB ID (if both present) → _lookup_tvdb_by_id()  ← NEW
 3. Embedded TVDB ID only → _lookup_tvdb_by_id()                               ← NEW
```

### 8. Caching

TVDB results use the existing `metadata_cache` table — no schema changes needed.
The `lookup_source` column stores `"nfo_tvdb_id"` or `"tvdb_embedded_id"`.

### 9. Error Handling

| Scenario | Behaviour |
|---|---|
| No `--tvdb-api-key` | TVDB phase skipped silently |
| Network error / timeout | Log debug message, return `None` |
| 401 (expired token) | Re-authenticate once, retry |
| 404 (invalid TVDB ID) | Log debug, return `None` |
| Response has no `originalLanguage` | Log debug, return `None` |

### 10. Testing

**Test file:** `tests/unit/test_native_language.py`

| Test class / function | What it covers |
|---|---|
| `test_tvdb_lookup_by_id` | Happy path: valid TVDB ID returns correct language codes |
| `test_tvdb_lookup_no_key` | API key is `None` → skip |
| `test_tvdb_lookup_network_failure` | Network error → `None` |
| `test_tvdb_lookup_invalid_id` | 404 → `None` |
| `test_tvdb_lookup_no_original_language` | Response missing `originalLanguage` → `None` |
| `test_nfo_tvdb_id_only` | NFO with only `<tvdbid>` → TVDB lookup succeeds |
| `test_nfo_tvdbid_element` | NFO parser extracts `<tvdbid>` correctly |
| `test_nfo_uniqueid_tvdb` | NFO parser extracts `<uniqueid type="tvdb">` as fallback |
| `test_embedded_tvdb_id_curly` | `{tvdb-12345}` in filename → TVDB lookup |
| `test_embedded_tvdb_id_square` | `[tvdb-12345]` in filename → TVDB lookup |
| `test_embedded_imdb_and_tvdb` | Both `{imdb-tt...}` and `{tvdb-...}` → IMDb first, TVDB fallback |

### 11. Files Changed

| File | Change |
|---|---|
| `src/trimarr/_nfo_parser.py` | Add `tvdb_id` field, parsing, content check |
| `src/trimarr/native_language.py` | Add TVDB auth, lookup, embedded ID, chain integration |
| `src/trimarr/runner.py` | Thread `tvdb_api_key` through to `resolve_native_language()` |
| `src/trimarr/cli.py` | Add `--tvdb-api-key` option |
| `tests/unit/test_native_language.py` | Add test cases for all TVDB scenarios |
| `tests/unit/test_nfo_parser.py` | Add NFO TVDB ID parsing tests |
