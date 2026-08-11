# Filename-Embedded IMDb/TMDb ID Detection — Design Spec

**Goal:** Before parsing the filename for a movie/TV title, scan the filename stem for embedded IMDb (`{imdb-ttXXXXX}`) or TMDb (`{tmdb-XXXXX}`) IDs and use them for a direct API lookup. This is inserted between NFO-direct-ID (priority 1) and NFO-title-search (priority 3) in the existing lookup chain.

---

## 1. Lookup Chain — Full Ordered List

| Priority | Step | Source | What it does |
|---|---|---|---|
| 1 | **NFO-direct-ID** | `.nfo` on disk | Parse `.nfo`, extract IMDb/TMDb ID → direct API lookup |
| **2 (NEW)** | **Filename-embedded ID** | Filename stem | Regex scan for `{imdb-tt...}` / `{tmdb-...}` → direct API lookup |
| 3 | **NFO-title-search** | `.nfo` on disk | Use NFO `<title>` + `<year>` → IMDbPie/TMDb search |
| 4 | **Filename → IMDbPie** | Filename stem | Parse scene filename → IMDbPie search |
| 5 | **Directory → IMDbPie** | Parent dir name | Use directory name → IMDbPie search |
| 6 | **Filename → TMDb** | Filename stem | Parsed title → TMDb search (needs API key) |
| 7 | **Directory → TMDb** | Parent dir name | Dir name → TMDb search (needs API key) |

---

## 2. Regex Extraction

A single function added to `src/trimarr/native_language.py`:

### `_extract_embedded_id(stem: str) -> tuple[str, str] | None`

```python
_EMBEDDED_IMDB_RE = re.compile(r"""[\[\{]?imdb-(tt\d+)[\]\}]?""", re.VERBOSE | re.IGNORECASE)
_EMBEDDED_TMDB_RE = re.compile(r"""[\[\{]?tmdb-(\d+)[\]\}]?""", re.VERBOSE | re.IGNORECASE)
```

- IMDb checked first (more specific prefix)
- Matches **curly** `{imdb-ttXXX}`, **square** `[imdb-ttXXX]`, and **bare** `imdb-ttXXX`
- Same for TMDb: `{tmdb-XXX}`, `[tmdb-XXX]`, `tmdb-XXX`
- Only the filename stem is searched (extension already stripped)
- `re.search()` — ID can appear anywhere in the stem
- Returns `("imdb", "tt0077914")` or `("tmdb", "77914")` or `None`

### Match examples

| Filename stem | Result |
|---|---|
| `Martin (1977) {tmdb-77914} 2160p.mkv` | `("tmdb", "77914")` |
| `Martin (1977) {imdb-tt0077914} 2160p.mkv` | `("imdb", "tt0077914")` |
| `Martin [tmdb-77914] 2160p.mkv` | `("tmdb", "77914")` |
| `Martin tmdb-77914 2160p.mkv` | `("tmdb", "77914")` |
| `Martin (1977) 2160p.mkv` | `None` |
| `tmdb-abc` (non-numeric) | `None` |

---

## 3. Pipeline Integration

Insertion point in `resolve_native_language()` — after NFO-direct-ID phase, before NFO-title-search:

```
→ NFO-direct-ID          → if succeeded, return
→ filename-embedded-ID   → if found, direct lookup via _lookup_imdbpie_by_id / _lookup_tmdb_by_id
→ NFO-title-search       → if succeeded, return
→ filename-IMDbPie       → ...
```

The embedded-ID block:

```python
# Phase 2: Embedded ID in filename
embedded = _extract_embedded_id(file_stem)
if embedded is not None:
    source, eid = embedded
    if source == "imdb":
        codes = _lookup_imdbpie_by_id(eid, logger)
        if codes is not None:
            _cache_result(db, file_path, fingerprint, codes, "imdbpie")
            return codes
    else:
        codes = _lookup_tmdb_by_id(eid, tmdb_api_key, logger)
        if codes is not None:
            _cache_result(db, file_path, fingerprint, codes, "tmdb")
            return codes
    # On API failure, fall through to next phase
```

**Why clean:** `_lookup_imdbpie_by_id()` and `_lookup_tmdb_by_id()` already exist from the NFO feature. `_cache_result()` already exists. No new infrastructure needed.

---

## 4. No New Dependencies

- `re` — already imported
- `_lookup_imdbpie_by_id` / `_lookup_tmdb_by_id` — already exist
- `_cache_result` — already exists

---

## 5. Error Handling

| Scenario | Behavior |
|---|---|
| ID found, API succeeds | Cache result, return native languages |
| ID found, API fails (network, rate-limit, no metadata) | Fall through to next phase (NFO-title-search) |
| No embedded ID in filename | `_extract_embedded_id` returns None → skip phase entirely |

---

## 6. Testing

All new tests in `tests/unit/test_native_language.py`:

| Test | Coverage |
|---|---|
| `test_extract_embedded_id_imdb_curly` | `{imdb-tt0077914}` → `("imdb", "tt0077914")` |
| `test_extract_embedded_id_imdb_square` | `[imdb-tt0077914]` → `("imdb", "tt0077914")` |
| `test_extract_embedded_id_imdb_bare` | `imdb-tt0077914` → `("imdb", "tt0077914")` |
| `test_extract_embedded_id_tmdb` | Curly, square, bare TMDb → `("tmdb", "77914")` |
| `test_extract_embedded_id_tmdb_non_numeric` | `tmdb-abc` → `None` |
| `test_extract_embedded_id_no_match` | Plain filename → `None` |
| `test_extract_embedded_id_both_present` | Both IDs present → IMDb wins |
| `test_integration_embedded_imdb` | Mock `_lookup_imdbpie_by_id`, verify call + cache |
| `test_integration_embedded_tmdb` | Mock `_lookup_tmdb_by_id`, verify call + cache |
| `test_integration_embedded_fails_fallthrough` | ID found but API fails → reaches next phase |

Coverage target: 95%+ on the extraction function, full branch coverage on integration tests.
