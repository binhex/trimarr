# Resilient NFO Parsing — Handle Trailing Junk After Closing Root Tag

**Date:** 2026-06-04
**Status:** Design (approved)
**Issue:** https://github.com/binhex/trimarr/issues/66

## Problem

Some Radarr/Sonarr `.nfo` files (particularly from Kodi library exports) contain valid
XML metadata followed by trailing URLs **after** the root closing tag:

```xml
<movie>
  <title>Inception</title>
  <imdbid>tt1375666</imdbid>
  <tmdbid>27205</tmdbid>
  ...
</movie>

https://www.themoviedb.org/movie/27205
https://www.imdb.com/title/tt1375666
```

These trailing URLs make the file invalid XML.  `xml.etree.ElementTree.parse()`
raises `ParseError`, so `parse_nfo()` returns `None` and
`--keep-native-audio` falls through to filename-based search, which may fail.

Of ~4080 NFOs in the reporter's library, ~1144 (28%) had this trailing-junk pattern.

## Design Decision

**Approach:** Runtime resilience — when `ET.parse()` raises `ParseError`, attempt to
strip trailing content after the closing root tag and re-parse.  No separate CLI
repair command; trimarr handles these files transparently during normal processing.

**Chosen over:**
- *Raw-text regex scraping of fields* — riskier matching, more code, duplicate
  extraction logic.
- *External fix script only* — requires user to discover and run a repair tool
  before trimarr works correctly.

## Design

### Helper: `_strip_nfo_trailing_junk(raw: str) -> str | None`

A module-level function that attempts to recover parseable XML from a raw NFO
string with trailing content after the root closing tag.

**Algorithm:**

1. Match against a compiled regex for known NFO root opening tags:
   ```
   ^\s*<(movie|tvshow|episodedetails|season)(?:\s|>)
   ```
   with `re.MULTILINE` so `^` matches at line starts.

2. If no match → the file has no recognizable NFO root → return `None`.

3. Extract the root element name from capture group 1 (e.g. `"movie"`).

4. Find the **last** occurrence of the closing tag `</root_name>` in the
   raw text using `str.rfind()`.

5. If not found → return `None` (no closing tag — likely truncated or
   severely damaged).

6. Truncate the text at `</root_name> + ">"` (one-past the closing '>').

7. Return the cleaned text, stripped of trailing whitespace, with a
   trailing newline appended.

**Safety:** The function only runs on the *error path* — when `ET.parse()` has
already failed.  In the common case (valid XML), there is zero overhead.

### Integration: `parse_nfo()` — Recovery Path

The existing try/except in `parse_nfo()` gains a recovery attempt:

```
try:
    tree = ET.parse(path)              ← fast path, unchanged
except (ET.ParseError, ...):
    meta = _parse_nfo_with_cleanup(path)
    if meta is not None:
        return meta
    return None                         ← fallback: same as before
```

The new internal helper `_parse_nfo_with_cleanup(path)` orchestrates the
recovery:

1. Read the file as UTF-8 text (with `errors="replace"` for safety).
2. Call `_strip_nfo_trailing_junk()` on the raw text.
3. If cleaning returns `None` → return `None`.
4. Call `ET.fromstring(cleaned)`.
5. If parsing fails → return `None`.
6. Validate the root element via `_is_valid_nfo_root()`.
7. Extract metadata fields — **identical** extraction code that the main
   `parse_nfo()` path uses (reuses `_extract_text()`, `_extract_uniqueid()`,
   `_has_nfo_content()`).
8. Return the `NfoMetadata` result.

**Why a separate helper?** Keeps the recovery logic self-contained and
independently unit-testable, while the main `parse_nfo()` try/except block
remains readable.

### File: `_nfo_parser.py`

**New symbols:**

| Symbol | Kind | Description |
|---|---|---|
| `_ROOT_OPEN_RE` | Module constant | Compiled regex for NFO root opening tags |
| `_KNOWN_NFO_ROOTS` | Module constant | `("movie", "tvshow", "episodedetails", "season")` |
| `_strip_nfo_trailing_junk(raw)` | Module-level function | Strip trailing junk from raw NFO text |
| `_parse_nfo_with_cleanup(path)` | Module-level function | Read + clean + parse recovery path |

**Modified function:** `parse_nfo()` — recovery path in existing exception
handler.

## Testing

### Unit tests for `_strip_nfo_trailing_junk()`

| Test case | Input | Expected |
|---|---|---|
| Already-valid XML | Full `<movie>...</movie>` | Returns raw text unchanged (no trailing junk to strip) |
| Trailing URLs after `</movie>` | Valid NFO + URLs | Cleaned text ending at `</movie>` |
| Trailing URLs after `</tvshow>` | Valid TV NFO + URLs | Cleaned text ending at `</tvshow>` |
| Trailing URLs after `</episodedetails>` | Episode NFO + URLs | Cleaned text ending at `</episodedetails>` |
| No root element found | Plain text | `None` |
| No closing tag | `<movie>...` (no `</movie>`) | `None` |
| Empty string | `""` | `None` |
| Only trailing content | `https://example.com` | `None` (no opening tag matched) |
| Root with attributes | `<movie xmlns="...">...</movie> + URLs` | Cleaned |

### Integration tests for `parse_nfo()`

| Test case | Input | Expected |
|---|---|---|
| Valid NFO fast path | Normal `.nfo` file | Metadata returned (no text I/O) |
| Trailing junk recovered | `.nfo` with URLs after `</movie>` | Metadata extracted correctly |
| Trailing junk recovered | `.nfo` with URLs after `</tvshow>` | Metadata extracted correctly |
| Deep XML damage (unescaped `&`) | File with broken content | `None` (no false positive) |
| Non-existent file | Bad path | `None` |
| File with no root | Garbage content | `None` |

All existing tests must continue passing unchanged.

## Future Considerations (explicitly out of scope)

- **CLI repair command** — not needed; if desired later, the stripping logic
  could be exposed as a `trimarr fix-nfo` subcommand.
- **Other XML damage** — unescaped characters, multiple roots, truncated files
  are not addressed.  The fix targets the specific trailing-junk pattern
  reported in ~28% of the reporter's library.
- **Sonarr `<episodedetails>` NFOs** — these are already handled by the
  `tvshow.nfo` upwalk fallback added in the v1.2.8 fix cycle.  The stripping
  logic includes `episodedetails` in its regex for completeness, even though
  `_is_valid_nfo_root()` will still reject them.
