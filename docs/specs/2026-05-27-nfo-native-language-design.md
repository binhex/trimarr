# NFO File Native Language Detection — Design Spec

**Goal:** Add `.nfo` file parsing as the primary source for native language detection in Trimarr, before the existing filename/directory/API search fallback chain.

**Architecture:** A new `_nfo_parser.py` module handles discovery and XML parsing of `.nfo` files. The existing lookup chain in `native_language.py` is extended with NFO-based steps at the front: direct ID lookups (using IMDb/TMDb IDs from the NFO) followed by NFO-title-based searches. Only when NFO is absent or all NFO steps fail does the system fall through to the existing filename/directory chain.

---

## 1. NFO Parser — New Module `src/trimarr/_nfo_parser.py`

### `NfoMetadata` dataclass

```python
@dataclass
class NfoMetadata:
    title: str | None
    original_title: str | None
    year: str | None
    imdb_id: str | None        # from <imdbid> or <uniqueid type="imdb">
    tmdb_id: str | None        # from <tmdbid> or <uniqueid type="tmdb">
```

### `discover_nfo(mkv_path: Path) -> Path | None`

Discovery order:
1. **Same-stem** — `{mkv_stem}.nfo` in the same directory as the MKV
2. **Any `.nfo` in directory** — first alphabetical `.nfo` file in the MKV's directory
3. **TV show root** — walk up from the MKV's directory, checking each parent for `tvshow.nfo` (stops at the first one found, up to 3 levels deep to avoid runaway walks)

### `parse_nfo(path: Path) -> NfoMetadata | None`

- Uses `xml.etree.ElementTree` (stdlib, no dependencies)
- Handles both `<movie>` and `<tvshow>` root elements
- Extracts: `<title>`, `<originaltitle>`, `<year>`, `<imdbid>`, `<tmdbid>`, `<uniqueid type="imdb">`, `<uniqueid type="tmdb">`
- Returns `None` on parse failure, missing root element, or missing all useful fields (title, imdb_id, tmdb_id all None)

### TV show `tvshow.nfo` convention

The `tvshow.nfo` file sits at the series root (e.g. `/tv/Breaking Bad/tvshow.nfo`) and applies to all episodes within. Walking up to find it avoids needing a per-episode NFO.

---

## 2. Extended Lookup Chain

### New lookup functions (in `native_language.py`)

**`_lookup_imdbpie_by_id(imdb_id: str) -> list[str] | None`**
- Skips IMDbPie `search_for_title` entirely
- Calls `client.get_title_auxiliary(imdb_id)` directly
- Falls back through existing `_fetch_imdb_spoken_languages` logic

**`_lookup_tmdb_by_id(tmdb_id: str, api_key: str) -> list[str] | None`**
- Calls TMDb detail endpoint `/movie/{tmdb_id}` directly (no search)
- Extracts `original_language` same as existing `_extract_tmdb_language_code`

### New helper

**`_get_nfo_metadata(file_path: Path) -> NfoMetadata | None`**
- Calls `discover_nfo`, then `parse_nfo`
- Returns metadata or None

### Chain ordering

```
 1. nfo_imdbpie_id       — nfo imdb_id → direct IMDb lookup
 2. nfo_tmdb_id          — nfo tmdb_id → direct TMDb lookup
 3. nfo_imdbpie_title    — nfo title+year → IMDbPie search
 4. nfo_tmdb_title       — nfo title+year → TMDb search
 5. imdbpie_filename     — (existing, unchanged)
 6. imdbpie_directory    — (existing, unchanged)
 7. tmdb_filename        — (existing, unchanged, requires TMDb key)
 8. tmdb_directory       — (existing, unchanged, requires TMDb key)
```

Steps 3-4 use the NFO's `<originaltitle>` (preferred) or `<title>` as the search term with the existing IMDbPie/TMDb search functions. IDs from the NFO take priority because they bypass the fragile search+match step entirely.

### Cache source labels

New labels for the `lookup_source` column:

| Source label | Meaning |
|---|---|
| `nfo_imdbpie_id` | NFO → direct IMDb ID lookup succeeded |
| `nfo_tmdb_id` | NFO → direct TMDb ID lookup succeeded |
| `nfo_imdbpie_title` | NFO title → IMDbPie search succeeded |
| `nfo_tmdb_title` | NFO title → TMDb search succeeded |

Existing labels unchanged: `imdbpie_filename`, `imdbpie_directory`, `tmdb_filename`, `tmdb_directory`.

### Error messages

When all 8 steps exhausted:

| Scenario | Error |
|---|---|
| NFO found but all lookups failed, TMDb key available | `"no match from any source (tried NFO, filename, and directory name)"` |
| NFO found, no TMDb key | `"no match from IMDbPie (tried NFO, filename, and directory name, no TMDb API key configured)"` |
| No NFO found, TMDb key available | `"no match from IMDbPie or TMDb (tried filename and directory name)"` (existing) |
| No NFO found, no TMDb key | `"no match from IMDbPie (tried filename and directory name, no TMDb API key configured)"` (existing) |

---

## 3. Files Changed

| File | Change |
|---|---|
| `src/trimarr/_nfo_parser.py` | **Create** — NfoMetadata, discover_nfo, parse_nfo |
| `src/trimarr/native_language.py` | **Modify** — Add direct ID lookups, _get_nfo_metadata, extend chain, new source labels |
| `tests/unit/test_nfo_parser.py` | **Create** — Tests for discovery and parsing |
| `tests/unit/test_native_language.py` | **Modify** — Tests for nfo chain steps, direct lookups, source labels |

No CLI changes needed — this is transparent to the user.

---

## 4. Edge Cases

| Case | Behaviour |
|---|---|
| No `.nfo` exists | Skip all NFO steps, fall through to filename/directory chain (exactly as today) |
| NFO exists but is unparseable (garbage XML) | `parse_nfo` returns None → skip NFO steps |
| NFO has title but no IDs | Skip direct ID lookups, use NFO title through search (steps 3-4) |
| NFO has IDs but API call fails | Proceed to NFO title search (steps 3-4) |
| Multiple `.nfo` files in directory | Same-stem wins; otherwise first alphabetical |
| TV episode with both episode NFO and tvshow.nfo | Episode NFO takes priority (same-stem or dir-level), tvshow.nfo only used as fallback |
| Walk-up reaches filesystem root with no tvshow.nfo | discovery returns None → skip NFO steps |

---

## 5. Testing Strategy

| Test | What it covers |
|---|---|
| `parse_nfo_movie` | Full movie XML → correct NfoMetadata |
| `parse_nfo_tvshow` | Full tvshow XML → correct NfoMetadata |
| `parse_nfo_minimal_movie` | Minimal movie XML (title only) → works |
| `parse_nfo_garbage` | Invalid XML → returns None |
| `parse_nfo_empty` | Empty file → returns None |
| `parse_nfo_no_relevant_fields` | Valid XML but no title, imdbid, tmdbid → returns None |
| `parse_nfo_uniqueid_fallback` | Uses uniqueid type="imdb"/"tmdb" when imdbid/tmdbid missing |
| `discover_nfo_stem_match` | Same-stem .nfo found |
| `discover_nfo_any_in_dir` | No stem match, any .nfo in dir found |
| `discover_nfo_no_nfo` | No .nfo anywhere → None |
| `discover_nfo_tvshow_upwalk` | tvshow.nfo found by walking up |
| `nfo_imdbpie_id_lookup` | Direct ID lookup succeeds |
| `nfo_tmdb_id_lookup` | Direct ID lookup succeeds |
| `nfo_title_search_fallback` | IDs fail, NFO title search succeeds |
| `nfo_chain_takes_priority` | NFO available → chain tries NFO first before filename |
| `resolve_native_language_no_nfo` | No NFO → existing behaviour unchanged |
