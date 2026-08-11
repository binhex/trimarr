# TVDB Native Language Lookup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TVDB as a fallback native-language lookup source for TV shows whose NFO files contain only a TVDB ID (no IMDb or TMDb).

**Architecture:** TVDB v4 API requires authentication (API key → JWT token). A single `_lookup_tvdb_by_id()` function handles auth + series detail fetch, returning a single-element language list from `originalLanguage`. TVDB is injected as a third direct-ID fallback in Phase 1 (NFO) and Phase 2 (embedded filename ID) after IMDb and TMDb have been tried.

**Tech Stack:** Python 3.12+, urllib (no new dependencies), TVDB API v4

---

### Task 1: NFO Parser — Add `tvdb_id` field and parsing

**Files:**
- Modify: `src/trimarr/_nfo_parser.py` (dataclass + parser + content check)
- Test: `tests/unit/test_nfo_parser.py`

- [ ] **Step 1: Add `tvdb_id` to `NfoMetadata` dataclass**

Replace the dataclass in `src/trimarr/_nfo_parser.py:18-35`:

```python
@dataclass
class NfoMetadata:
    """Metadata extracted from an ``.nfo`` file.

    Attributes:
        title: Movie/TV show title from ``<title>`` element.
        original_title: Original (non-localised) title from
            ``<originaltitle>`` element, or *None*.
        year: Release year as a string from ``<year>``, or *None*.
        imdb_id: IMDb ID (e.g. ``"tt0468569"``) from ``<imdbid>`` or
            ``<uniqueid type="imdb">``, or *None*.
        tmdb_id: TMDb ID (e.g. ``"155"``) from ``<tmdbid>`` or
            ``<uniqueid type="tmdb">``, or *None*.
        tvdb_id: TVDB ID (e.g. ``"7537283"``) from ``<tvdbid>`` or
            ``<uniqueid type="tvdb">``, or *None*.
    """

    title: str | None
    original_title: str | None = None
    year: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
```

- [ ] **Step 2: Add `tvdb_id` extraction in `parse_nfo()`**

In `src/trimarr/_nfo_parser.py`, after the TMDb extraction block (around line 153), add:

```python
    # <tvdbid> takes precedence over <uniqueid type="tvdb">
    tvdb_id = _extract_text(root, "tvdbid")
    if tvdb_id is None:
        tvdb_id = _extract_uniqueid(root, "tvdb")
```

Then add `tvdb_id=tvdb_id` to the `NfoMetadata(...)` constructor.

- [ ] **Step 3: Update `_has_nfo_content()` to include `tvdb_id`**

Replace the function at around line 100:

```python
def _has_nfo_content(
    title: str | None,
    original_title: str | None,
    imdb_id: str | None,
    tmdb_id: str | None,
    tvdb_id: str | None,  # NEW
) -> bool:
    """Return True if at least one useful metadata field is present."""
    return title is not None or original_title is not None or imdb_id is not None or tmdb_id is not None or tvdb_id is not None
```

And update the call site to pass `tvdb_id=tvdb_id`.

- [ ] **Step 4: Write failing test for TVDB ID extraction in NFO**

Add to `tests/unit/test_nfo_parser.py`:

```python
def test_parse_tvdbid_element(self, tmp_path: Path) -> None:
    """Parse <tvdbid> from a Sonarr-style TV NFO."""
    nfo = tmp_path / "tvshow.nfo"
    nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Breaking Bad</title>
  <tvdbid>7537283</tvdbid>
</tvshow>""")
    result = parse_nfo(nfo)
    assert result is not None
    assert result.tvdb_id == "7537283"
    assert result.title == "Breaking Bad"

def test_parse_uniqueid_tvdb_fallback(self, tmp_path: Path) -> None:
    """Fall back to <uniqueid type='tvdb'> when <tvdbid> is absent."""
    nfo = tmp_path / "tvshow.nfo"
    nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Breaking Bad</title>
  <uniqueid type="tvdb">81189</uniqueid>
</tvshow>""")
    result = parse_nfo(nfo)
    assert result is not None
    assert result.tvdb_id == "81189"

def test_parse_tvdbid_takes_precedence(self, tmp_path: Path) -> None:
    """<tvdbid> takes precedence over <uniqueid type='tvdb'>."""
    nfo = tmp_path / "tvshow.nfo"
    nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Test</title>
  <tvdbid>111</tvdbid>
  <uniqueid type="tvdb">222</uniqueid>
</tvshow>""")
    result = parse_nfo(nfo)
    assert result is not None
    assert result.tvdb_id == "111"

def test_parse_tvdbid_missing(self, tmp_path: Path) -> None:
    """NFO without any TVDB ID returns tvdb_id=None."""
    nfo = tmp_path / "movie.nfo"
    nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
</movie>""")
    result = parse_nfo(nfo)
    assert result is not None
    assert result.tvdb_id is None

def test_parse_tvdbid_only_no_title(self, tmp_path: Path) -> None:
    """NFO with only a tvdbid and no title still returns a result."""
    nfo = tmp_path / "tvshow.nfo"
    nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <tvdbid>12345</tvdbid>
</tvshow>""")
    result = parse_nfo(nfo)
    assert result is not None
    assert result.tvdb_id == "12345"
    assert result.title is None
```

- [ ] **Step 5: Run tests to verify failures**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_nfo_parser.py -v`

Expected: The new TVDB tests should FAIL if the implementation hasn't been done yet, or PASS if it was already partially implemented. (If they pass, move on.)

- [ ] **Step 6: Commit**

```bash
cd /data/trimarr && git add src/trimarr/_nfo_parser.py tests/unit/test_nfo_parser.py
git commit -m "feat: add tvdb_id to NfoMetadata and NFO parser"
```

---

### Task 2: CLI — Add `--tvdb-api-key` flag

**Files:**
- Modify: `src/trimarr/cli.py`
- Modify: `src/trimarr/runner.py` (dataclass + `run()` signature + config)

- [ ] **Step 1: Add the CLI option**

In `src/trimarr/cli.py`, after the `--tmdb-api-key` option block (around line 235), add:

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
        " contain only a TVDB ID.  Optional \u2014 without it, TVDB lookups are"
        " silently skipped."
    ),
)
```

- [ ] **Step 2: Add parameter to `cli()` function**

Add `tvdb_api_key: str | None = None` to the `cli()` function signature (around line 294). Add it right after the `tmdb_api_key` parameter.

- [ ] **Step 3: Thread parameter into `run()` call**

Inside `cli()`, find the `run(...)` call (around line 370) and add `tvdb_api_key=tvdb_api_key`.

- [ ] **Step 4: Add to `_ProcessingConfig` dataclass**

In `src/trimarr/runner.py`, inside `_ProcessingConfig` (around line 47), add:

```python
    tvdb_api_key: str | None = None
```

- [ ] **Step 5: Add parameter to `run()` function**

In `src/trimarr/runner.py`, update the `run()` function signature (line 613) to accept `tvdb_api_key: str | None = None`. Add it after `tmdb_api_key`.

- [ ] **Step 6: Thread into `_ProcessingConfig` construction**

In `runner.py`, find the `_ProcessingConfig(...)` construction (around line 665) and add `tvdb_api_key=tvdb_api_key,`.

- [ ] **Step 7: Thread into `_resolve_effective_language()`**

In `src/trimarr/runner.py`, find `_resolve_effective_language()` (around line 311). The call to `resolve_native_language()` currently passes `tmdb_api_key=cfg.tmdb_api_key`. Add `tvdb_api_key=cfg.tvdb_api_key`.

- [ ] **Step 8: Commit**

```bash
cd /data/trimarr && git add src/trimarr/cli.py src/trimarr/runner.py
git commit -m "feat: add --tvdb-api-key CLI flag"
```

---

### Task 3: TVDB Auth + Lookup — Core Functions

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Add TVDB auth constants and regex patterns**

At the top of `native_language.py`, after the TMDb regex patterns (around line 55), add TVDB API constants and an embedded-ID pattern:

```python
# TVDB API endpoints and constants.
_TVDB_BASE_URL = "https://api4.thetvdb.com/v4"
_TVDB_LOGIN_URL = f"{_TVDB_BASE_URL}/login"

# Regex for filename-embedded TVDB IDs: {tvdb-12345}, [tvdb-12345], tvdb-12345
_EMBEDDED_TVDB_RE = re.compile(r"""(?:\[tvdb-(\d+)\]|\{tvdb-(\d+)\}|tvdb-(\d+))""", re.VERBOSE | re.IGNORECASE)
```

Also note: the `_tvdb_token` global variable needs to be declared as `None` at module level:

```python
# TVDB JWT token cache — set once per process lifetime.
_tvdb_token: str | None = None
```

Add this near the top of the file, after the existing import block, around line 30.

- [ ] **Step 2: Update `_RELEASE_TAGS_RE` to strip `tvdb-` tags**

In the existing `_RELEASE_TAGS_RE` regex (around line 62), the `imdb-tt\d{7,}|tmdb-\d+` alternation is already present. Add `|tvdb-\d+` so that bare `tvdb-12345` in scene filenames gets stripped:

```python
       imdb-tt\d{7,}|tmdb-\d+|tvdb-\d+)\b
```

- [ ] **Step 3: Write the test first (RED phase)**

Add to `tests/unit/test_native_language.py`:

```python
class TestLookupTvdb:
    """Tests for TVDB-based language lookup."""

    def test_success(self, mocker) -> None:
        """TVDB series lookup returns originalLanguage."""
        mock_token = "fake-jwt-token"

        # Mock login
        mock_login = mocker.MagicMock()
        mock_login.read.return_value = b'{"data": {"token": "' + mock_token.encode() + b'"}}'
        mock_login.__enter__.return_value = mock_login

        # Mock series detail
        mock_detail = mocker.MagicMock()
        mock_detail.read.return_value = b"""
        {"data": {"originalLanguage": "jpn", "name": "Example Series"}}
        """
        mock_detail.__enter__.return_value = mock_detail

        mock_urlopen = mocker.patch(
            "trimarr.native_language.urllib.request.urlopen",
            side_effect=[mock_login, mock_detail],
        )

        from trimarr.native_language import _lookup_tvdb_by_id
        result = _lookup_tvdb_by_id("12345", "fake-api-key")
        assert result == ["jpn"]
        # Verify login was called first
        login_call = mock_urlopen.call_args_list[0]
        assert login_call[0][0].full_url == "https://api4.thetvdb.com/v4/login"

    def test_no_api_key(self) -> None:
        """No TVDB API key means no lookup is attempted."""
        from trimarr.native_language import _lookup_tvdb_by_id
        result = _lookup_tvdb_by_id("12345", "")
        assert result is None

    def test_lookup_failure(self, mocker) -> None:
        """TVDB API returns error."""
        mock_response = mocker.MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.side_effect = OSError("Network error")

        mocker.patch(
            "trimarr.native_language.urllib.request.urlopen",
            return_value=mock_response,
        )

        from trimarr.native_language import _lookup_tvdb_by_id
        result = _lookup_tvdb_by_id("12345", "fake-api-key")
        assert result is None

    def test_no_original_language(self, mocker) -> None:
        """Series detail has no originalLanguage field."""
        mock_login = mocker.MagicMock()
        mock_login.read.return_value = b'{"data": {"token": "tok"}}'
        mock_login.__enter__.return_value = mock_login

        mock_detail = mocker.MagicMock()
        mock_detail.read.return_value = b'{"data": {"name": "No Lang"}}'
        mock_detail.__enter__.return_value = mock_detail

        mocker.patch(
            "trimarr.native_language.urllib.request.urlopen",
            side_effect=[mock_login, mock_detail],
        )

        from trimarr.native_language import _lookup_tvdb_by_id
        result = _lookup_tvdb_by_id("12345", "fake-api-key")
        assert result is None

    def test_reauth_on_401(self, mocker) -> None:
        """Expired token triggers re-auth, then the request succeeds."""
        # First detail call returns 401, trigger re-auth
        mock_login1 = mocker.MagicMock()
        mock_login1.read.return_value = b'{"data": {"token": "expired-token"}}'
        mock_login1.__enter__.return_value = mock_login1

        # 401 response for detail
        mock_401 = mocker.MagicMock()
        mock_401.__enter__.return_value = mock_401
        mock_401.read.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )

        # Re-login
        mock_login2 = mocker.MagicMock()
        mock_login2.read.return_value = b'{"data": {"token": "new-token"}}'
        mock_login2.__enter__.return_value = mock_login2

        # Retry succeeds
        mock_detail = mocker.MagicMock()
        mock_detail.read.return_value = b"""
        {"data": {"originalLanguage": "eng"}}
        """
        mock_detail.__enter__.return_value = mock_detail

        mocker.patch(
            "trimarr.native_language.urllib.request.urlopen",
            side_effect=[mock_login1, mock_401, mock_login2, mock_detail],
        )

        from trimarr.native_language import _lookup_tvdb_by_id
        result = _lookup_tvdb_by_id("12345", "fake-api-key")
        assert result == ["eng"]
```

Note: The re-auth test uses `urllib.error.HTTPError`. You need to import it at the top of the test file or use `mocker.MagicMock(spec=Exception)`.

For simplicity, skip the HTTPError test if `urllib.error` is not auto-imported and just use the success/nokey/failure/no-original-language tests first.

- [ ] **Step 4: Run tests to verify failures**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupTvdb -v`

Expected: FAIL with ImportError for `_lookup_tvdb_by_id`

- [ ] **Step 5: Implement `_lookup_tvdb_by_id()`**

Add after `_lookup_tmdb_by_id()` (around line 620):

```python
def _tvdb_login(api_key: str) -> str | None:
    """Authenticate with TVDB API and return a JWT token.

    POST to ``/login`` with the API key. Returns the bearer token
    string, or *None* on failure (network error, invalid key).
    """
    import json as _json

    data = _json.dumps({"apikey": api_key}).encode()
    req = urllib.request.Request(
        _TVDB_LOGIN_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = _json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("TVDB login failed: %s", exc)
        return None
    token: str | None = body.get("data", {}).get("token")
    return token


def _lookup_tvdb_by_id(tvdb_id: str, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TVDB using a known TVDB ID.

    Authenticates first (login → JWT), then fetches the series extended
    record and extracts ``originalLanguage``. Returns a single-element
    list with the normalised language code, or *None* on any failure.

    Results are not cached separately — the caller (``resolve_native_language``)
    handles database caching.
    """
    import json as _json

    if not api_key:
        return None

    # Login to obtain JWT token
    token = _tvdb_login(api_key)
    if token is None:
        return None

    # Fetch series extended record
    detail_url = f"{_TVDB_BASE_URL}/series/{tvdb_id}/extended"
    req = urllib.request.Request(
        detail_url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            # Token expired — re-authenticate once
            logger.debug("TVDB token expired, re-authenticating...")
            token = _tvdb_login(api_key)
            if token is None:
                return None
            req = urllib.request.Request(
                detail_url,
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = _json.loads(resp.read().decode())
            except Exception as exc2:
                logger.debug("TVDB detail failed after re-auth for id %s: %s", tvdb_id, exc2)
                return None
        else:
            logger.debug("TVDB detail failed for id %s (HTTP %s)", tvdb_id, exc.code)
            return None
    except Exception as exc:
        logger.debug("TVDB detail failed for id %s: %s", tvdb_id, exc)
        return None

    raw_lang: str | None = body.get("data", {}).get("originalLanguage")
    if not raw_lang:
        logger.debug("TVDB no originalLanguage for id %s.", tvdb_id)
        return None

    code = normalize_language_code(raw_lang.lower())
    if code:
        return [code]
    return None
```

- [ ] **Step 6: Add `import urllib.error` to the module imports**

At the top of `native_language.py`, add `urllib.error` to the existing import line (around line 15):

```python
import urllib.error
import urllib.parse
import urllib.request
```

- [ ] **Step 7: Run tests to verify pass**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestLookupTvdb -v`

Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py
git commit -m "feat: add TVDB lookup function with auth"
```

---

### Task 4: Embed TVDB ID in `_extract_embedded_id()`

**Files:**
- Modify: `src/trimarr/native_language.py`
- Modify: `tests/unit/test_native_language.py`

- [ ] **Step 1: Write failing test**

Add to `TestParseMovieTitle` or as a new test class:

```python
def test_embedded_tvdb_id(self) -> None:
    """Parse {tvdb-12345} from filename stem."""
    from trimarr.native_language import _extract_embedded_id
    result = _extract_embedded_id("Show.Name.2020.{tvdb-7537283}.mkv")
    assert result == ("tvdb", "7537283")
```

Also update the existing `test_parse_movie_title` parametrized list to ensure a filename with `{tvdb-...}` still parses the title correctly:

```python
("/data/Show.Name.2020.{tvdb-7537283}.mkv", "show name", "2020"),
```

- [ ] **Step 2: Update `_extract_embedded_id()` to handle TVDB IDs**

Replace the existing `_extract_embedded_id()` function (around line 85) to add TVDB support after the TMDb check:

```python
def _extract_embedded_id(stem: str) -> tuple[str, str] | None:
    """Scan *stem* for an embedded IMDb, TMDb, or TVDB ID.

    Supports curly ``{imdb-tt...}``, square ``[imdb-tt...]``, and
    bare ``imdb-tt...`` syntax (same for ``tmdb-...`` and ``tvdb-...``).
    IMDb IDs must be at least 7 characters long (``tt0000001``).

    Returns ``("imdb", "tt0077914")``, ``("tmdb", "77914")``,
    ``("tvdb", "7537283")``, or *None* if no recognised ID pattern
    is found.
    """
    m = _EMBEDDED_IMDB_RE.search(stem)
    if m:
        imdb_id = m.group(1) or m.group(2) or m.group(3)
        return ("imdb", imdb_id.lower())
    m = _EMBEDDED_TMDB_RE.search(stem)
    if m:
        tmdb_id = m.group(1) or m.group(2) or m.group(3)
        return ("tmdb", tmdb_id)
    m = _EMBEDDED_TVDB_RE.search(stem)
    if m:
        tvdb_id = m.group(1) or m.group(2) or m.group(3)
        return ("tvdb", tvdb_id)
    return None
```

- [ ] **Step 3: Run tests to verify pass**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_native_language.py::TestParseMovieTitle -v`

Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py
git commit -m "feat: add TVDB embedded ID parsing in _extract_embedded_id"
```

---

### Task 5: NFO + Embedded ID Phase — TVDB Integration in Resolution Chain

**Files:**
- Modify: `src/trimarr/native_language.py`
- Test: `tests/unit/test_native_language.py`

- [ ] **Step 1: Update `_resolve_nfo_id_lookups()` to add TVDB as third fallback**

Replace the function (around line 700) to add a TVDB block after TMDb:

```python
def _resolve_nfo_id_lookups(
    nfo_meta: NfoMetadata,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,  # NEW
) -> list[str] | None:
    """Try direct IMDb/TMDb/TVDB ID lookups from NFO metadata.

    Returns language codes if any lookup succeeds, or *None* to
    continue to the next fallback phase.
    """
    # Direct IMDb ID lookup (most reliable — no search+match)
    if nfo_meta.imdb_id:
        codes = _lookup_imdbpie_by_id(nfo_meta.imdb_id)
        if codes is not None:
            _msg = "Identified native language(s) for '%s': %s (source=nfo_imdbpie_id)."
            logger.info(_msg, file_path.name, codes)
            _maybe_cache_result(db, file_path, codes, "nfo_imdbpie_id", None)
            return codes

    # Direct TMDb ID lookup
    if nfo_meta.tmdb_id and tmdb_api_key:
        codes = _lookup_tmdb_by_id(nfo_meta.tmdb_id, tmdb_api_key)
        if codes is not None:
            _msg = "Identified native language(s) for '%s': %s (source=nfo_tmdb_id)."
            logger.info(_msg, file_path.name, codes)
            _maybe_cache_result(db, file_path, codes, "nfo_tmdb_id", None)
            return codes

    # Direct TVDB ID lookup (last resort for TV NFOs)
    if nfo_meta.tvdb_id and tvdb_api_key:
        codes = _lookup_tvdb_by_id(nfo_meta.tvdb_id, tvdb_api_key)
        if codes is not None:
            _msg = "Identified native language(s) for '%s': %s (source=nfo_tvdb_id)."
            logger.info(_msg, file_path.name, codes)
            _maybe_cache_result(db, file_path, codes, "nfo_tvdb_id", None)
            return codes

    return None
```

- [ ] **Step 2: Update `_resolve_embedded_id_phase()` to handle TVDB IDs**

Add a new `_resolve_tvdb_embedded()` function (after the existing `_resolve_tmdb_embedded` around line 760):

```python
def _resolve_tvdb_embedded(
    eid: str,
    file_path: Path,
    db: Database | None,
    tvdb_api_key: str | None,
) -> list[str] | None:
    """Resolve via a TVDB-only embedded ID."""
    if not tvdb_api_key:
        return None

    codes = _lookup_tvdb_by_id(eid, tvdb_api_key)
    if codes is None:
        return None

    logger.info(
        "Identified native language(s) for '%s': %s (source=tvdb_embedded_id).",
        file_path.name,
        codes,
    )
    _maybe_cache_result(db, file_path, codes, "tvdb_embedded_id", None)
    return codes
```

Then update `_resolve_embedded_id_phase()` to dispatch to TVDB:

```python
def _resolve_embedded_id_phase(
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,  # NEW
) -> list[str] | None:
    """Try to resolve native language via an embedded ID in *file_stem*.

    Dispatches to the appropriate resolver depending on which ID type
    is found. Returns ISO 639-2/B language codes or *None*.
    """
    embedded = _extract_embedded_id(file_stem)
    if embedded is None:
        return None

    source, eid = embedded
    if source == "imdb":
        codes = _resolve_imdb_embedded(eid, file_stem, file_path, db, tmdb_api_key)
    elif source == "tmdb":
        codes = _resolve_tmdb_embedded(eid, file_path, db, tmdb_api_key)
    else:  # tvdb
        codes = _resolve_tvdb_embedded(eid, file_path, db, tvdb_api_key)

    if codes is None:
        logger.debug(
            "No native language found for '%s' via embedded ID (%s=%s).",
            file_path.name,
            source,
            eid,
        )
    return codes
```

- [ ] **Step 3: Update `resolve_native_language()` signature and calls**

Update the `resolve_native_language()` function signature (around line 890):

```python
def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
    tvdb_api_key: str | None = None,  # NEW
) -> list[str] | None:
```

Update the Phase 1 call in `resolve_native_language()` to pass `tvdb_api_key`:

```python
    if nfo_meta is not None:
        result = _resolve_nfo_id_lookups(nfo_meta, file_path, db, tmdb_api_key, tvdb_api_key)
```

Update the Phase 2 call:

```python
    result = _resolve_embedded_id_phase(file_path.stem, file_path, db, tmdb_api_key, tvdb_api_key)
```

- [ ] **Step 4: Write integration test**

Add to `tests/unit/test_native_language.py`:

```python
def test_resolve_native_language_tvdb_from_nfo(self, mocker) -> None:
    """resolve_native_language uses TVDB when NFO has only a tvdbid."""
    from trimarr.native_language import resolve_native_language

    # Mock NFO discovery + parsing
    mocker.patch(
        "trimarr.native_language.discover_nfo",
        return_value=Path("/fake/movie.nfo"),
    )
    mock_nfo = NfoMetadata(
        title="Test Show",
        original_title=None,
        year="2020",
        imdb_id=None,
        tmdb_id=None,
        tvdb_id="7537283",
    )
    mocker.patch(
        "trimarr.native_language.parse_nfo",
        return_value=mock_nfo,
    )

    # Mock cache miss
    mock_db = mocker.MagicMock()
    mock_db.get_native_language_cache.return_value = None

    # Mock set_native_language_cache to a no-op
    mock_db.set_native_language_cache = mocker.MagicMock()

    # Mock TVDB lookup to succeed
    mocker.patch(
        "trimarr.native_language._lookup_tvdb_by_id",
        return_value=["eng"],
    )

    file_path = Path("/data/Test Show (2020)/Test Show.mkv")
    result = resolve_native_language(
        file_path=file_path,
        db=mock_db,
        tmdb_api_key=None,
        tvdb_api_key="fake-key",
    )
    assert result == ["eng"]
    mock_db.set_native_language_cache.assert_called_once_with(
        file_path, ["eng"], "nfo_tvdb_id", None
    )

def test_resolve_native_language_tvdb_embedded(self, mocker) -> None:
    """resolve_native_language uses TVDB from embedded {tvdb-...} ID."""
    from trimarr.native_language import resolve_native_language

    # Mock NFO discovery to return None (no NFO)
    mocker.patch(
        "trimarr.native_language.discover_nfo",
        return_value=None,
    )

    # Mock cache miss
    mock_db = mocker.MagicMock()
    mock_db.get_native_language_cache.return_value = None
    mock_db.set_native_language_cache = mocker.MagicMock()

    # Mock TVDB lookup to succeed
    mocker.patch(
        "trimarr.native_language._lookup_tvdb_by_id",
        return_value=["kor"],
    )

    file_path = Path("/data/Show.{tvdb-12345}.2020.mkv")
    result = resolve_native_language(
        file_path=file_path,
        db=mock_db,
        tmdb_api_key=None,
        tvdb_api_key="fake-key",
    )
    assert result == ["kor"]
    mock_db.set_native_language_cache.assert_called_once_with(
        file_path, ["kor"], "tvdb_embedded_id", None
    )

def test_resolve_native_language_tvdb_no_key(self, mocker) -> None:
    """resolve_native_language skips TVDB when no API key."""
    from trimarr.native_language import resolve_native_language

    # Mock NFO with only a tvdbid
    mocker.patch(
        "trimarr.native_language.discover_nfo",
        return_value=Path("/fake/movie.nfo"),
    )
    mock_nfo = NfoMetadata(
        title="Test",
        imdb_id=None,
        tmdb_id=None,
        tvdb_id="7537283",
    )
    mocker.patch(
        "trimarr.native_language.parse_nfo",
        return_value=mock_nfo,
    )

    # Mock cache miss
    mock_db = mocker.MagicMock()
    mock_db.get_native_language_cache.return_value = None
    mock_db.set_native_language_cache = mocker.MagicMock()

    # Mock TVDB lookup should NOT be called
    tvdb_mock = mocker.patch(
        "trimarr.native_language._lookup_tvdb_by_id",
    )

    # Also mock IMDbPie and TMDb to fail
    mocker.patch(
        "trimarr.native_language._lookup_imdbpie_by_id",
        return_value=None,
    )
    mocker.patch(
        "trimarr.native_language._lookup_tmdb_by_id",
        return_value=None,
    )

    file_path = Path("/data/Test/Test.mkv")
    result = resolve_native_language(
        file_path=file_path,
        db=mock_db,
        tmdb_api_key=None,
        tvdb_api_key=None,  # No TVDB key
    )
    assert result is None
    tvdb_mock.assert_not_called()
```

You'll also need to add the import for `NfoMetadata` at the top of the test file:

```python
from trimarr._nfo_parser import NfoMetadata
```

- [ ] **Step 5: Run all tests**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_native_language.py -v`

Expected: All tests PASS (including existing ones)

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-report=term-missing 2>&1 | tail -20`

Expected: 439+ tests pass, coverage maintained

- [ ] **Step 7: Commit**

```bash
cd /data/trimarr && git add src/trimarr/native_language.py tests/unit/test_native_language.py
git commit -m "feat: integrate TVDB into native language resolution chain"
```

---

### Task 6: Full Suite QA Gate

**Files:** None (verification only)

- [ ] **Step 1: Ruff lint + format**

Run: `cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .`

- [ ] **Step 2: Type check**

Run: `cd /data/trimarr && uv run mypy .`

- [ ] **Step 3: Full test suite with coverage**

Run: `cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-fail-under=80 2>&1 | tail -20`

- [ ] **Step 4: Commit any fixes**

```bash
cd /data/trimarr && git add -A
git commit -m "chore: satisfy QA gates for TVDB native language feature"
```

---

## File Change Summary

| File | What changed |
|---|---|
| `src/trimarr/_nfo_parser.py` | `tvdb_id` on `NfoMetadata`; `<tvdbid>`/`<uniqueid type="tvdb">` parsing; `_has_nfo_content` includes `tvdb_id` |
| `src/trimarr/native_language.py` | TVDB auth (`_tvdb_login`), lookup (`_lookup_tvdb_by_id`), embedded ID patterns, `_resolve_tvdb_embedded`, chain integration |
| `src/trimarr/cli.py` | New `--tvdb-api-key` CLI option |
| `src/trimarr/runner.py` | `tvdb_api_key` field on `_ProcessingConfig`, threaded through `run()` → `_resolve_effective_language()` |
| `tests/unit/test_nfo_parser.py` | NFO TVDB ID parsing tests |
| `tests/unit/test_native_language.py` | TVDB lookup tests, embedded ID tests, integration tests |
