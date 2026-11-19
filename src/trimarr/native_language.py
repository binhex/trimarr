"""Native/original language detection for MKV files.

Uses IMDbPie, TMDb and TVDB to identify the native/original language(s)
of a media file from its path.  Results are cached in the metadata_cache
SQLite table to avoid redundant API calls.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from trimarr._nfo_parser import NfoMetadata, discover_nfo, find_tvshow_nfo, parse_nfo
from trimarr.processor import _ISO_639_1_TO_2, normalize_language_code

# Lazy module-level imports — attempted once at load time, cached in module
# globals so every function call does not re-attempt the import.
_imdbpie: Any
_HAS_IMDBPIE: bool
_IMDBPIE_WARNING_LOGGED = False
try:
    import imdbpie as _imdbpie_lib

    _HAS_IMDBPIE = True
    _imdbpie = _imdbpie_lib
except ImportError:
    _HAS_IMDBPIE = False
    _imdbpie = None

_pycountry: Any
_HAS_PYCOUNTRY: bool
try:
    import pycountry as _pycountry_lib

    _HAS_PYCOUNTRY = True
    _pycountry = _pycountry_lib
except ImportError:
    _HAS_PYCOUNTRY = False
    _pycountry = None

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from trimarr.database import Database

logger = logging.getLogger(__name__)

# Regex patterns for filename-embedded IMDb/TMDb IDs.
# Matches {imdb-tt123}, [imdb-tt123], imdb-tt123 and same for tmdb-{id}.
_IMDB_ID_RE = re.compile(r"^tt\d{7,}$")
"""A bare IMDb title ID, e.g. ``tt0082096``."""

_EMBEDDED_IMDB_RE = re.compile(
    r"""(?:\[imdb-(tt\d{7,})\]|\{imdb-(tt\d{7,})\}|imdb-(tt\d{7,}))""", re.VERBOSE | re.IGNORECASE
)
_EMBEDDED_TMDB_RE = re.compile(r"""(?:\[tmdb-(\d+)\]|\{tmdb-(\d+)\}|tmdb-(\d+))""", re.VERBOSE | re.IGNORECASE)

# TVDB API endpoints and constants.
_TVDB_BASE_URL = "https://api4.thetvdb.com/v4"
_TVDB_LOGIN_URL = f"{_TVDB_BASE_URL}/login"

_TVDB_TOKEN_CACHE: dict[str, str] = {}
"""JWT per API key, reused for every lookup in the process.

Without it a TV library issues one login per file, doubling API traffic and
risking rate limiting.  Entries are replaced when the server answers 401.
"""

# Regex for filename-embedded TVDB IDs: {tvdb-12345}, [tvdb-12345], tvdb-12345
_EMBEDDED_TVDB_RE = re.compile(r"""(?:\[tvdb-(\d+)\]|\{tvdb-(\d+)\}|tvdb-(\d+))""", re.VERBOSE | re.IGNORECASE)

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
       AMZN|NF|WEB|iTunes|MA|DSNP|HMAX|ATVP|
       imdb-tt\d{7,}|tmdb-\d+|tvdb-\d+)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_WORD_SEPARATORS = re.compile(r"[._\-+]")

# TV show subdirectory names to skip when looking up the show root.
# Matches "Season 1", "Season01", "Series 2", "Series2", "Specials",
# "Specials 0" etc.  Case-insensitive.  Rejects "Seasons" (plural) to
# avoid matching unrelated directories.
_TV_SUBDIR_RE = re.compile(r"^(?:Season|Series|Specials)(?:\s*\d+)?$", re.IGNORECASE)

# Regex for detecting TV episode filenames.
# Matches: S01E01, S1E1, 1x01, Season.1.Episode.1, Series.1.Episode.1
_TV_EPISODE_RE = re.compile(
    r"""
    (?:
        # Pattern 1: S01E01, S1E1, S01.E01, S01E01-E02, S01E01E02 (multi-episode)
        S\d{1,2}\.?\s?E\d{1,}(?:-?\.?\s?E\d{1,})*
        |
        # Pattern 2: 1x01, 01x01 (1-2 digit fields, not part of a longer
        # digit run, so resolutions such as 1920x1080 are not matched)
        (?<!\d)\d{1,2}x\d{1,2}(?!\d)
        |
        # Pattern 3: Season.1.Episode.1, Season 1 Episode 1, Season01Episode01
        # Requires an episode number suffix to avoid matching movie names like "Summer.Season.2022"
        (?:season|series)[\s\.]?\d+[\s\.]?episode[\s\.]?\d+
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _is_tv_episode_filename(stem: str) -> bool:
    """Return True if *stem* looks like a TV episode filename.

    Checks for common episode numbering patterns:
    - ``S01E01``, ``S01E01-E02``, ``S01E01E02`` (standard episode codes)
    - ``1x01``, ``01x01`` (alternative format)
    - ``Season.1.Episode.1``, ``Season01Episode01`` (verbose format)
    - Same patterns with ``Series`` in place of ``Season``

    Returns False for anything that does not match (movies, standalone
    files, directories, etc.).
    """
    return bool(_TV_EPISODE_RE.search(stem))


def _get_tmdb_endpoint(stem: str, default: str = "movie") -> str:
    """Return the TMDb endpoint type ("movie" or "tv") based on *stem*.

    When the filename stem matches a TV episode pattern, returns "tv"
    so the correct TMDb endpoint (``/tv/...``, ``/search/tv``) is used.
    Otherwise returns *default* (``"movie"``).
    """
    return "tv" if _is_tv_episode_filename(stem) else default


def _extract_embedded_id(stem: str) -> tuple[str, str] | None:
    """Scan *stem* for an embedded IMDb, TMDb, or TVDB ID.

    Supports curly ``{imdb-tt...}``, square ``[imdb-tt...]``, and
    bare ``imdb-tt...`` syntax (same for ``tmdb-...`` and ``tvdb-...``).
    IMDb IDs must be at least 7 characters long (``tt0000001``).

    Returns ``("imdb", "tt0077914")``, ``("tmdb", "77914")``,
    ``("tvdb", "7537283")``, or *None* if no recognised ID pattern
    is found.
    """
    for pattern, source in (
        (_EMBEDDED_IMDB_RE, "imdb"),
        (_EMBEDDED_TMDB_RE, "tmdb"),
        (_EMBEDDED_TVDB_RE, "tvdb"),
    ):
        m = pattern.search(stem)
        if m:
            eid = m.group(1) or m.group(2) or m.group(3)
            return (source, eid.lower() if source == "imdb" else eid)
    return None


def _normalise_title(s: str) -> str:
    """Lower-case, collapse whitespace, and strip leading/trailing spaces."""
    return " ".join(s.lower().split())


# Mapping of written number words and Roman numerals to digit strings.
# Only the first ten are mapped — higher ordinals are rarely relevant.
_WORD_TO_INT: dict[str, str] = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}
_ROMAN_TO_INT: dict[str, str] = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}

# Pre-compiled patterns for word/numeral replacement — avoids recompiling per call.
_NUMERAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:^|(?<=[\s.\-_]))({word})(?=[\s.\-_]|$)", re.IGNORECASE), digit)
    for mapping in (_WORD_TO_INT, _ROMAN_TO_INT)
    for word, digit in mapping.items()
]

# Strips possessive 's after an s-ending word (e.g. "Jones's" → "Jones").
_RE_POSSESSIVE_S = re.compile(r"(?<=s)['\u2018\u2019\u02bc\u02bb]s?\b", re.IGNORECASE)

# Separator characters to strip during comparison normalisation
# (also collapses whitespace by removing everything at once).
_RE_COMPARE_STRIP = re.compile(r"[\s\.\-\_\:\+\'\"\!\,\@\#\u2018\u2019\u02bc\u02bb]+")


def _normalise_for_compare(text: str) -> str:
    """Normalise a movie title for fuzzy comparison against API results.

    Steps (in order):
    1. Lower-case
    2. NFKD-decompose accented characters to ASCII
    3. Replace ``&`` with ``and``
    4. Strip ``imdb`` keyword
    5. Convert written/Roman numerals to digits
    6. Strip possessive ``'s`` after ``s``-ending words
    7. Strip punctuation and whitespace separators (collapses to a single
       continuous string for comparison)

    Based on movarr's ``parsing.normalise_for_compare``.
    """
    result = text.lower()
    # NFKD decomposition: é → e + combining accent → strip non-ASCII → "e"
    result = unicodedata.normalize("NFKD", result)
    result = result.encode("ascii", "ignore").decode("ascii")
    result = result.replace("&", "and")
    result = re.sub(r"\bimdb\b", "", result)
    for pattern, digit in _NUMERAL_PATTERNS:
        result = pattern.sub(digit, result)
    result = _RE_POSSESSIVE_S.sub("", result)
    result = _RE_COMPARE_STRIP.sub("", result)
    return result


def _strip_release_tags(title: str) -> str:
    """Remove known release-group tags that might cause false positives in searches."""
    return _RELEASE_TAGS_RE.sub("", title).strip()


def parse_movie_title(file_path: Path) -> tuple[str, str | None]:
    """Extract a searchable movie title and optional year from *file_path*.

    Searches the filename first, then falls back to the parent directory
    name for the year.  This handles two common naming conventions:

    * ``/path/Movie Name (2020).mkv`` — year in the filename
    * ``/path/Movie Name (2020)/Movie Name.mkv`` — year in the parent dir
    """
    stem = file_path.stem
    cleaned = _WORD_SEPARATORS.sub(" ", stem)
    year: str | None = None
    year_matches = _YEAR_RE.findall(cleaned)
    if year_matches:
        year = year_matches[-1]
        # Take everything BEFORE the last year occurrence as the title.
        # In scene naming conventions, everything after the year is
        # metadata (resolution, codec, release group, edition tags),
        # not part of the movie title.  Position-based extraction is
        # far more robust than maintaining a blocklist of scene tags.
        year_pos = cleaned.rfind(year)
        if year_pos != -1:
            before = cleaned[:year_pos].strip()
            # Strip orphaned opening braces/brackets/parens left behind
            # when the year was inside a curly or bracketed block.
            while before.endswith(("{", "(", "[")):
                before = before[:-1].strip()
            cleaned = before or cleaned[year_pos + len(year) :].strip()

    # If no year found in the filename, try the parent directory name
    if year is None:
        parent_cleaned = _WORD_SEPARATORS.sub(" ", file_path.parent.stem)
        parent_year_matches = _YEAR_RE.findall(parent_cleaned)
        if parent_year_matches:
            year = parent_year_matches[-1]

    def _strip_brackets(s: str) -> str:
        parts = re.split(r"(\[[^\]]*\])", s)
        result = []
        for part in parts:
            if part.startswith("[") and part.endswith("]") and not _YEAR_RE.search(part):
                continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_brackets(cleaned)

    def _strip_curlies(s: str) -> str:
        """Strip curly-brace blocks that don't contain a year."""
        parts = re.split(r"(\{[^}]*\})", s)
        result = []
        for part in parts:
            if part.startswith("{") and part.endswith("}") and not _YEAR_RE.search(part):
                continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_curlies(cleaned)

    def _strip_parens(s: str) -> str:
        parts = re.split(r"(\([^)]*\))", s)
        result = []
        for part in parts:
            if part.startswith("(") and part.endswith(")") and not _YEAR_RE.search(part):
                continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_parens(cleaned)
    # Strip any orphaned brackets or parens that survived position-based
    # extraction (e.g. the opening paren before a year-wrapped-in-parens
    # style filename like "Movie (2024)").
    cleaned = re.sub(r"[\[\]()]", "", cleaned)
    cleaned = _strip_release_tags(cleaned)
    title = _normalise_title(" ".join(cleaned.split()))
    if not title:
        title = _normalise_title(file_path.stem)
    return title, year


def _years_equal(expected: str | None, actual: object) -> bool:
    """Compare a year string against a hit's year value (int, str, or None).

    Returns *True* when the years match OR when *expected* is *None* (caller
    has no year to compare against).  Returns *False* when the values differ
    or cannot be parsed.
    """
    if expected is None or actual is None:
        return True
    if not isinstance(actual, (int, str)):
        return False
    try:
        return int(actual) == int(expected)
    except (ValueError, TypeError):
        return False


def _find_matching_imdb_id(
    hits: Sequence[Any],
    title: str,
    year: str | None,
) -> str | None:
    """Find and return the IMDb ID of the first hit matching *title* and *year*.

    *hits* is whatever the API returned: entries that are not JSON objects are
    skipped rather than raising.
    """
    norm_title = _normalise_for_compare(title)
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title_value = hit.get("title")
        if not isinstance(title_value, str):
            continue
        hit_title = title_value.strip()
        if _normalise_for_compare(hit_title) != norm_title:
            continue
        if not _years_equal(year, hit.get("year")):
            continue
        matched_id: str | None = hit.get("imdb_id")
        if matched_id:
            return matched_id
    return None


def _search_and_match_imdb(
    client: Any,
    title: str,
    year: str | None,
    search_term: str,
) -> str | None:
    """Search IMDbPie and match title+year, returning imdb_id or None."""
    try:
        hits = client.search_for_title(search_term)
    except Exception as exc:
        logger.debug("IMDbPie search failed for '%s': %s", search_term, exc)
        return None
    if not isinstance(hits, (list, tuple)) or not hits:
        logger.debug("IMDbPie returned no usable hits for '%s'.", search_term)
        return None
    matched_id = _find_matching_imdb_id(hits, title, year)
    if not matched_id:
        logger.debug("IMDbPie no title+year match for '%s'.", search_term)
        return None
    return matched_id


def _fetch_imdb_spoken_languages(client: Any, matched_id: str) -> list[str] | None:
    """Fetch auxiliary data and extract spoken language codes."""
    try:
        aux = client.get_title_auxiliary(matched_id)
    except Exception as exc:
        logger.debug("IMDbPie aux data failed for '%s': %s", matched_id, exc)
        return None
    spoken = aux.get("spokenLanguages") if isinstance(aux, dict) else None
    if not isinstance(spoken, (list, tuple)) or not spoken:
        return None
    return _extract_imdb_spoken_codes(spoken)


def _warn_missing_imdbpie() -> None:
    """Warn once per process that the optional imdbpie dependency is missing.

    The lookups run per file across several fallback phases, so warning at every
    call would flood the log on a large library.
    """
    global _IMDBPIE_WARNING_LOGGED
    if not _IMDBPIE_WARNING_LOGGED:
        _IMDBPIE_WARNING_LOGGED = True
        logger.warning("imdbpie not installed — IMDb lookups are disabled for this run.")


def _lookup_imdbpie(title: str, year: str | None) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie, or None on failure."""
    if not _HAS_IMDBPIE:
        _warn_missing_imdbpie()
        return None
    try:
        client = _imdbpie.Imdb()
    except Exception as exc:
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None
    search_term = title
    if year:
        search_term = f"{title} {year}"
    matched_id = _search_and_match_imdb(client, title, year, search_term)
    if not matched_id:
        return None
    return _fetch_imdb_spoken_languages(client, matched_id)


def _lookup_imdbpie_by_id(imdb_id: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie using a known IMDb ID.

    Skips the search+match step and directly fetches auxiliary data by ID.
    Returns *None* when imdbpie is unavailable, the client cannot be created,
    or no spoken language data is returned.
    """
    if not _HAS_IMDBPIE:
        _warn_missing_imdbpie()
        return None
    if not _IMDB_ID_RE.match(imdb_id):
        logger.debug("Ignoring malformed IMDb ID: %r", imdb_id)
        return None
    try:
        client = _imdbpie.Imdb()
    except Exception as exc:
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None
    return _fetch_imdb_spoken_languages(client, imdb_id)


def _extract_spoken_code_from_string(entry: str, codes: list[str]) -> None:
    """Handle a 2-letter ISO 639-1 code entry."""
    code = _ISO_639_1_TO_2.get(entry.lower())
    if code and code not in codes:
        codes.append(code)


def _extract_spoken_code_from_dict(entry: dict, codes: list[str]) -> None:
    """Handle a dict entry with name/description."""
    lang_name = entry.get("name") or entry.get("description") or ""
    if not isinstance(lang_name, str):
        return
    code = _language_name_to_iso_639_2(lang_name)
    if code and code not in codes:
        codes.append(code)


def _extract_imdb_spoken_codes(spoken: Sequence[Any]) -> list[str] | None:
    """Convert IMDb spoken language entries to ISO 639-2/B codes.

    IMDbPie returns ``spokenLanguages`` as a list of ISO 639-1 codes
    (e.g. ``["en", "de"]``).  Map each 2-letter code through the
    existing ``_ISO_639_1_TO_2`` table to get the 3-letter bibliographic
    form.  Fall back to language-name resolution for any entry that is
    not a recognised 2-letter code.
    """
    codes: list[str] = []
    for entry in spoken:
        if isinstance(entry, str):
            if len(entry) == 2:
                _extract_spoken_code_from_string(entry, codes)
            else:
                # 3+ char string — treat as a language name/code via name resolution
                code = _language_name_to_iso_639_2(entry)
                if code and code not in codes:
                    codes.append(code)
        elif isinstance(entry, dict):
            _extract_spoken_code_from_dict(entry, codes)
    return codes or None


def _resolve_alpha2_fallback(lang: object) -> str | None:
    """When alpha_2 exists but is not in ``_ISO_639_1_TO_2``, try
    bibliographic then alpha_3."""
    biblio = getattr(lang, "bibliographic", None)
    if isinstance(biblio, str) and biblio:
        return normalize_language_code(biblio.lower())
    alpha_3 = getattr(lang, "alpha_3", None)
    if isinstance(alpha_3, str) and alpha_3:
        return normalize_language_code(alpha_3.lower())
    return None


def _lookup_pycountry_language(
    lang: object | None,
    fallback_code: str | None = None,
) -> str | None:
    """Extract ISO 639-2/B code from a pycountry language object.

    Args:
        lang: A pycountry language object, or *None*.
        fallback_code: When provided, use this as the code if no ``alpha_2``
            was found.  Used for alpha_3 / bibliographic lookups where the
            input *name* IS the lookup key.

    Returns:
        ISO 639-2/B code string, or *None* if *lang* is *None* or has no
        usable code.
    """
    if lang is not None:
        result = _try_alpha2_lookup(lang)
        if result is not None:
            return result
        if fallback_code is not None:
            return normalize_language_code(fallback_code)
        alpha_3 = getattr(lang, "alpha_3", None)
        if isinstance(alpha_3, str) and alpha_3:
            return normalize_language_code(alpha_3.lower())
    return None


def _try_alpha2_lookup(lang: object) -> str | None:
    """Try to extract an ISO 639-2/B code from *lang* via alpha_2.

    Returns the code from the well-known BCP-47 map, or falls back to
    bibliographic/alpha_3 resolution.  Returns *None* when no usable
    alpha_2 field exists.
    """
    alpha_2 = getattr(lang, "alpha_2", None)
    if not isinstance(alpha_2, str) or not alpha_2:
        return None
    code = _ISO_639_1_TO_2.get(alpha_2.lower())
    if code:
        return code
    return _resolve_alpha2_fallback(lang)


def _language_name_to_iso_639_2(name: str) -> str | None:
    """Convert a spoken language name (e.g. 'German', 'English') to ISO 639-2/B.

    Uses the built-in fallback dict first since it contains well-known English
    language names that IMDbPie returns.  Pycountry is only consulted for names
    not in the dict — this avoids pycountry matching short/ambiguous names
    (e.g. ``"En"``) to completely different ISO codes (``"enc"``).
    """
    # Check the well-known fallback dict first — these are the language names
    # IMDbPie actually returns ("English", "German", etc.).
    fallback = _fallback_lang_name_to_code(name)
    if fallback:
        return fallback
    if not _HAS_PYCOUNTRY:
        return None
    result = _lookup_pycountry_language(_pycountry.languages.get(name=name))
    if result:
        return result
    result = _lookup_pycountry_language(_pycountry.languages.get(alpha_3=name.lower()), fallback_code=name.lower())
    if result:
        return result
    result = _lookup_pycountry_language(
        _pycountry.languages.get(bibliographic=name.lower()), fallback_code=name.lower()
    )
    if result:
        return result
    return None


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
    """Fallback language name to ISO 639-2/B without pycountry."""
    return _LANG_NAME_TO_CODE.get(name.lower().strip())


def _is_three_letter_code(s: str) -> bool:
    """Return True if *s* is a 3-letter alphabetic string."""
    return len(s) == 3 and s.isalpha()


def _extract_tmdb_language_code(detail: dict) -> list[str] | None:
    """Extract ISO 639-2/B language codes from a TMDb detail response.

    Returns a single-element list with the normalised code, or *None* if
    the detail response has no usable ``original_language`` field.
    """
    raw_lang = detail.get("original_language")
    if not isinstance(raw_lang, str) or not raw_lang:
        return None
    code = _ISO_639_1_TO_2.get(raw_lang.lower())
    if code:
        return [normalize_language_code(code)]
    if _is_three_letter_code(raw_lang):
        return [normalize_language_code(raw_lang.lower())]
    return None


_HTTP_TIMEOUT_SECONDS = 15
"""Timeout applied to every TMDb/TVDB request."""


def _fetch_json(
    url: str,
    *,
    label: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Fetch *url* and return its decoded JSON body, or *None* on failure.

    *label* identifies the request in debug logs.  Network errors, non-2xx
    statuses and undecodable bodies all degrade to *None* so a single bad
    response can never abort a run.

    Args:
        url: Absolute request URL.
        label: Human-readable description used in the debug log message.
        data: Request body; its presence switches the method to POST.
        headers: Extra request headers (e.g. a bearer token).

    Returns:
        The decoded JSON value (any type), or *None* when the request failed.
    """
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        logger.debug("%s failed: %s", label, exc)
        return None


def _is_numeric_id(value: object) -> bool:
    """Return True when *value* is a positive numeric ID safe for a URL path.

    TMDb returns ``id`` as a JSON number, while NFO files carry it as text, so
    both ``int`` and digit-only ``str`` are accepted.  Anything else (bool,
    float, path-like text) is rejected before it reaches a URL.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and value.isdigit()


def _tmdb_detail_codes(tmdb_id: Any, api_key: str, media_type: str) -> list[str] | None:
    """Return language codes from the TMDb detail endpoint for *tmdb_id*."""
    if not _is_numeric_id(tmdb_id):
        logger.debug("Ignoring non-numeric TMDb ID: %r", tmdb_id)
        return None
    encoded_key = urllib.parse.quote(api_key, safe="")
    detail_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={encoded_key}"
    detail = _fetch_json(detail_url, label=f"TMDb detail for id {tmdb_id}")
    if not isinstance(detail, dict):
        return None
    return _extract_tmdb_language_code(detail)


def _try_tmdb_detail(hit: dict, title: str, api_key: str, media_type: str = "movie") -> list[str] | None:
    """If *hit* matches *title*, fetch TMDb detail and extract language codes.

    For TV results (``media_type="tv"``) uses ``name``/``original_name`` fields
    and the ``/tv/{id}`` endpoint. For movies uses ``title``/``original_title``
    and ``/movie/{id}``.
    """
    _title_fields = ("name", "original_name") if media_type == "tv" else ("title", "original_title")
    for field in _title_fields:
        field_value = hit.get(field)
        if not isinstance(field_value, str):
            continue
        if _normalise_title(field_value.strip().lower()) != _normalise_title(title):
            continue
        tmdb_id = hit.get("id")
        if tmdb_id is None:
            continue
        codes = _tmdb_detail_codes(tmdb_id, api_key, media_type)
        if codes:
            return codes
    return None


def _build_tmdb_search_url(title: str, year: str | None, api_key: str, media_type: str) -> str:
    """Return the TMDb search URL for *title*, optionally narrowed by *year*."""
    encoded_title = urllib.parse.quote(title)
    encoded_key = urllib.parse.quote(api_key, safe="")
    search_url = f"https://api.themoviedb.org/3/search/{media_type}?query={encoded_title}&api_key={encoded_key}"
    if not year:
        return search_url
    year_param = "first_air_date_year" if media_type == "tv" else "year"
    return f"{search_url}&{year_param}={year}"


def _first_tmdb_match(results: list[Any], title: str, api_key: str, media_type: str) -> list[str] | None:
    """Return language codes from the first matching TMDb search result."""
    for hit in results:
        if not isinstance(hit, dict):
            continue
        codes = _try_tmdb_detail(hit, title, api_key, media_type)
        if codes:
            return codes
    return None


def _lookup_tmdb(title: str, year: str | None, api_key: str, media_type: str = "movie") -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb, or None on failure.

    Args:
        title: Movie or TV show title to search for.
        year: Optional release year to narrow the search.
        api_key: TMDb API key.
        media_type: ``"movie"`` or ``"tv"`` — selects the TMDb search endpoint.
    """
    if not api_key:
        # Every caller gates on a truthy key; guard here so a blank key never
        # turns into an unauthenticated request to the live API.
        return None
    search_url = _build_tmdb_search_url(title, year, api_key, media_type)
    data = _fetch_json(search_url, label=f"TMDb search for '{title}'")
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        logger.debug("TMDb no results for '%s'.", title)
        return None
    return _first_tmdb_match(results, title, api_key, media_type)


def _lookup_tmdb_by_id(tmdb_id: str, api_key: str, media_type: str = "movie") -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb using a known TMDb ID.

    Skips the search+match step and directly fetches the detail by ID.
    Uses ``/movie/{id}`` or ``/tv/{id}`` depending on *media_type*.
    Returns *None* when the detail endpoint fails or returns no usable
    ``original_language``.
    """
    return _tmdb_detail_codes(tmdb_id, api_key, media_type)


def _tvdb_login(api_key: str) -> str | None:
    """Authenticate with TVDB API and return a JWT token.

    POST to ``/login`` with the API key. Returns the bearer token
    string, or *None* on failure (network error, invalid key).
    """
    body = _fetch_json(
        _TVDB_LOGIN_URL,
        label="TVDB login",
        data=json.dumps({"apikey": api_key}).encode(),
        headers={"Content-Type": "application/json"},
    )
    data_block = body.get("data") if isinstance(body, dict) else None
    token = data_block.get("token") if isinstance(data_block, dict) else None
    return token if isinstance(token, str) and token else None


def _tvdb_token(api_key: str) -> str | None:
    """Return a cached TVDB JWT for *api_key*, authenticating on a miss."""
    cached = _TVDB_TOKEN_CACHE.get(api_key)
    if cached is not None:
        return cached
    token = _tvdb_login(api_key)
    if token is not None:
        _TVDB_TOKEN_CACHE[api_key] = token
    return token


def _tvdb_fetch_detail(
    tvdb_id: str,
    api_key: str,
    detail_url: str,
    token: str,
) -> dict | None:
    """Fetch series extended detail, retrying once on 401."""

    req = urllib.request.Request(
        detail_url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return cast("dict", json.loads(resp.read().decode()))
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            logger.debug("TVDB detail failed for id %s (HTTP %s)", tvdb_id, exc.code)
            return None
        # Token expired — re-authenticate once
        logger.debug("TVDB token expired, re-authenticating...")
        new_token = _tvdb_login(api_key)
        if new_token is None:
            return None
        _TVDB_TOKEN_CACHE[api_key] = new_token
        req2 = urllib.request.Request(
            detail_url,
            headers={"Authorization": f"Bearer {new_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req2, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                return cast("dict", json.loads(resp.read().decode()))
        except Exception as exc2:
            logger.debug("TVDB detail failed after re-auth for id %s: %s", tvdb_id, exc2)
            return None
    except Exception as exc:
        logger.debug("TVDB detail failed for id %s: %s", tvdb_id, exc)
        return None


def _lookup_tvdb_by_id(tvdb_id: str, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TVDB using a known TVDB ID.

    Obtains a JWT token (cached per API key, logging in only on a miss),
    then fetches the series extended record and extracts
    ``originalLanguage``. Returns a single-element list with the normalised
    language code, or *None* on any failure.

    Results are not cached separately — the caller (``resolve_native_language``)
    handles database caching.
    """
    if not api_key:
        return None
    if not _is_numeric_id(tvdb_id):
        logger.debug("Ignoring non-numeric TVDB ID: %r", tvdb_id)
        return None

    # Login to obtain JWT token (reused across lookups for this key)
    token = _tvdb_token(api_key)
    if token is None:
        return None

    detail_url = f"{_TVDB_BASE_URL}/series/{tvdb_id}/extended"
    body = _tvdb_fetch_detail(tvdb_id, api_key, detail_url, token)
    codes = _extract_tvdb_language(body)
    if codes is None:
        logger.debug("TVDB no originalLanguage for id %s.", tvdb_id)
    return codes


def _extract_tvdb_language(body: Any) -> list[str] | None:
    """Return the normalised ``originalLanguage`` from a TVDB detail body."""
    data_block = body.get("data") if isinstance(body, dict) else None
    raw_lang = data_block.get("originalLanguage") if isinstance(data_block, dict) else None
    if not isinstance(raw_lang, str) or not raw_lang:
        return None
    code = _ISO_639_1_TO_2.get(raw_lang.lower())
    if code:
        return [normalize_language_code(code)]
    if _is_three_letter_code(raw_lang):
        return [normalize_language_code(raw_lang.lower())]
    return None


_CACHE_MISS = object()


def _check_native_language_cache(
    db: Database | None,
    file_path: Path,
) -> object:
    """Check DB cache for *file_path*; return stored value or ``_CACHE_MISS``."""
    if db is not None:
        cached = db.get_native_language_cache(file_path)
        if cached is not None:
            cached_langs, cached_source, _ = cached
            if cached_langs:
                _msg = "Native language cache hit for '%s': %s (source: %s)"
                logger.debug(_msg, file_path.name, cached_langs, cached_source)
            return cached_langs
    return _CACHE_MISS


def _get_filename_title(file_path: Path) -> tuple[str, str | None]:
    """Extract movie title from the filename stem via parse_movie_title."""
    return parse_movie_title(file_path)


def _get_directory_title(file_path: Path) -> tuple[str, str | None]:
    r"""Extract the show/movie title from the parent directory, walking up past
    one or more consecutive ``Season \d+``, ``Series \d+``, or ``Specials``
    subdirectories to reach the show root.

    For example, ``/data/Westworld/Season 3/file.mkv`` resolves to
    ``("westworld", None)`` instead of ``("season 3", None)``.
    """
    parent = file_path.parent
    while parent.name and _TV_SUBDIR_RE.match(parent.name):
        parent = parent.parent
    return parse_movie_title(parent)


def _lookup_chain(tmdb_api_key: str | None, media_type: str = "movie") -> list[tuple]:
    """Return ordered (lookup_fn, title_fn, source_label) triples.

    Args:
        tmdb_api_key: TMDb API key, or None to skip TMDb entries.
        media_type: ``"movie"`` or ``"tv"`` — selects the TMDb search endpoint.
    """
    chain: list[tuple] = [
        (_lookup_imdbpie, _get_filename_title, "imdbpie_filename"),
        (_lookup_imdbpie, _get_directory_title, "imdbpie_directory"),
    ]
    if tmdb_api_key:
        chain += [
            (partial(_lookup_tmdb, api_key=tmdb_api_key, media_type=media_type), _get_filename_title, "tmdb_filename"),
            (
                partial(_lookup_tmdb, api_key=tmdb_api_key, media_type=media_type),
                _get_directory_title,
                "tmdb_directory",
            ),
        ]
    return chain


def _describe_failure(source_label: str, tmdb_api_key: str | None) -> str:
    """Return an error message for a failed lookup step."""
    if "tmdb" in source_label:
        return "no match from IMDbPie or TMDb (tried filename and directory name)"
    if not tmdb_api_key:
        return "no match from IMDbPie (tried filename and directory name, no TMDb API key configured)"
    return "no match from IMDbPie"


def _upwalk_tvshow_nfo(file_path: Path) -> NfoMetadata | None:
    """Walk up from *file_path* looking for a ``tvshow.nfo`` with usable metadata.

    Delegates discovery to :func:`find_tvshow_nfo` so the depth limit and
    case-insensitive glob live in one place.  Returns the first parsed
    result that contains at least one ID (imdb / tmdb / tvdb) or title
    field, or *None* if none is found.
    """
    current = file_path.parent
    while True:
        nfo_path = find_tvshow_nfo(current)
        if nfo_path is None:
            return None
        meta = parse_nfo(nfo_path)
        if meta is not None:
            return meta
        # Closest tvshow.nfo exists but has no usable metadata — continue
        # the search above it.
        parent_of = nfo_path.parent.parent
        if parent_of == nfo_path.parent:
            return None
        current = parent_of


def _get_nfo_metadata(file_path: Path) -> NfoMetadata | None:
    """Discover and parse an .nfo file for *file_path*.

    Discovery uses same-stem NFO first (standard for movies).  When
    parsing fails (e.g. a Sonarr episode NFO with ``<episodedetails>``
    root, or an NFO whose own ``tvshow.nfo`` has no usable fields) a
    ``tvshow.nfo`` upwalk is attempted to retrieve series-level IMDb /
    TMDb / TVDB IDs.

    Returns structured metadata, or *None* if no suitable .nfo file
    exists or cannot be parsed.
    """
    nfo_path = discover_nfo(file_path)
    if nfo_path is None:
        # No NFO found at all — the tvshow upwalk would repeat the same search.
        return None
    meta = parse_nfo(nfo_path)
    if meta is not None:
        return meta

    # Parse failed (or produced nothing usable) — climb for a tvshow.nfo that
    # does carry series-level IDs.  When discovery already returned a tvshow.nfo
    # this re-parses that one file once before ascending past it, which is the
    # price of keeping a single discovery rule.
    return _upwalk_tvshow_nfo(file_path)


def _lookup_and_cache(
    lookup_fn: Any,
    args: tuple[Any, ...],
    file_path: Path,
    db: Database | None,
    source: str,
) -> list[str] | None:
    """Call *lookup_fn* with *args*; on success log, cache, and return codes."""
    codes = cast("list[str] | None", lookup_fn(*args))
    if codes is None:
        return None
    logger.info(
        "Identified native language(s) for '%s': %s (source=%s).",
        file_path.name,
        codes,
        source,
    )
    _maybe_cache_result(db, file_path, codes, source, None)
    return codes


def _run_nfo_id_lookup_chain(
    tmdb_id: str | None,
    tvdb_id: str | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,
    tmdb_media_type: str,
    file_path: Path,
    db: Database | None,
) -> list[str] | None:
    """Run the NFO ID lookup chain for TMDb/TVDB.

    For TV shows (``tmdb_media_type="tv"``), TVDB is tried first since
    it is TV-specific and more reliable. For movies, TMDb is tried first.
    The correct endpoint is passed to TMDb lookups via 4-tuples that
    encode the exact call args, avoiding fragile identity checks.
    """
    _is_tv = tmdb_media_type == "tv"
    # Each entry: (lookup_fn, api_key, source, call_args)
    _chain: tuple[tuple[Any, str | None, str, tuple[Any, ...]], ...] = (
        (
            (_lookup_tvdb_by_id, tvdb_api_key, "nfo_tvdb_id", (tvdb_id, tvdb_api_key)),
            (_lookup_tmdb_by_id, tmdb_api_key, "nfo_tmdb_id", (tmdb_id, tmdb_api_key, tmdb_media_type)),
        )
        if _is_tv
        else (
            (_lookup_tmdb_by_id, tmdb_api_key, "nfo_tmdb_id", (tmdb_id, tmdb_api_key, tmdb_media_type)),
            (_lookup_tvdb_by_id, tvdb_api_key, "nfo_tvdb_id", (tvdb_id, tvdb_api_key)),
        )
    )
    for lookup_fn, api_key, source, _args in _chain:
        eid = _args[0]
        if eid and api_key:
            result = _lookup_and_cache(
                lookup_fn,
                _args,
                file_path,
                db,
                source,
            )
            if result:
                return result
    return None


def _nfo_media_type(nfo_meta: NfoMetadata, file_path: Path) -> str:
    """Return the TMDb media type, preferring the NFO root over the filename.

    The NFO root is authoritative in both directions: TMDb uses separate ID
    namespaces for movies and TV, so a ``<tvshow>`` NFO must never be queried as
    ``/movie/{id}`` (issue #68) and a ``<movie>`` NFO must never be queried as
    ``/tv/{id}`` just because the filename happens to contain ``SxxExx``.
    """
    return "tv" if nfo_meta.is_tvshow else "movie"


def _resolve_nfo_id_lookups(
    nfo_meta: NfoMetadata,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,
) -> list[str] | None:
    """Try direct IMDb/TMDb/TVDB ID lookups from NFO metadata.

    Returns language codes if any lookup succeeds, or *None* to
    continue to the next fallback phase.

    Lookup order is unified for all media types:
    - **TMDb/TVDB chain first** (``originalLanguage`` — a single production
      language code, more appropriate for ``--keep-native-audio``).
      The chain internally orders by media type: TMDb first for movies,
      TVDB first for TV shows.
    - **IMDb fallback** (``spokenLanguages`` — all languages present in the
      title, broader but less specific). Used when the chain has no API key
      configured or all keyed lookups fail.
    """
    tmdb_media_type = _nfo_media_type(nfo_meta, file_path)

    # Phase 1 — TMDb/TVDB chain (originalLanguage — single production
    # language code).  The chain internally orders by media type:
    # TMDb first for movies, TVDB first for TV shows.
    result = _run_nfo_id_lookup_chain(
        nfo_meta.tmdb_id,
        nfo_meta.tvdb_id,
        tmdb_api_key,
        tvdb_api_key,
        tmdb_media_type,
        file_path,
        db,
    )
    if result:
        return result

    # Phase 2 — IMDb ID fallback (spokenLanguages — broader but
    # includes every language with any dialogue in the title).
    if nfo_meta.imdb_id:
        result = _lookup_and_cache(
            _lookup_imdbpie_by_id,
            (nfo_meta.imdb_id,),
            file_path,
            db,
            "nfo_imdbpie_id",
        )
        if result:
            return result

    return None


def _resolve_nfo_title_search(
    nfo_meta: NfoMetadata,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
) -> list[str] | None:
    """Search IMDbPie/TMDb using the clean title from NFO metadata.

    Falls back from IMDbPie to TMDb when IMDbPie finds no match and a
    TMDb API key is configured.  Returns language codes or *None*.
    """
    search_title = nfo_meta.original_title or nfo_meta.title
    if not search_title:
        return None

    # Sanitize year: NFO may contain air dates ("2019-06-05") — extract
    # just the 4-digit year for API compatibility, or None if unparseable.
    year: str | None = None
    if nfo_meta.year and re.match(r"\d{4}", nfo_meta.year):
        year = nfo_meta.year[:4]

    codes = _lookup_imdbpie(search_title, year)
    if codes is not None:
        _msg = "Identified native language(s) for '%s': %s (source=nfo_imdbpie_title)."
        logger.info(_msg, file_path.name, codes)
        _maybe_cache_result(db, file_path, codes, "nfo_imdbpie_title", None)
        return codes

    if tmdb_api_key:
        _tmdb_media_type = _nfo_media_type(nfo_meta, file_path)
        codes = _lookup_tmdb(search_title, year, tmdb_api_key, _tmdb_media_type)
        if codes is not None:
            _msg = "Identified native language(s) for '%s': %s (source=nfo_tmdb_title)."
            logger.info(_msg, file_path.name, codes)
            _maybe_cache_result(db, file_path, codes, "nfo_tmdb_title", None)
            return codes

    return None


def _resolve_imdb_embedded(
    eid: str,
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None = None,
) -> list[str] | None:
    """Resolve via IMDb ID, falling back to embedded TMDb then TVDB IDs."""
    codes = _lookup_imdbpie_by_id(eid)
    if codes is not None:
        logger.info(
            "Identified native language(s) for '%s': %s (source=imdbpie_embedded_id).",
            file_path.name,
            codes,
        )
        _maybe_cache_result(db, file_path, codes, "imdbpie_embedded_id", None)
        return codes

    # IMDb failed — try the embedded TMDb ID, then the embedded TVDB ID.
    codes = _resolve_embedded_tmdb_fallback(file_stem, file_path, db, tmdb_api_key)
    if codes is not None:
        return codes

    codes = _resolve_embedded_tvdb_fallback(file_stem, file_path, db, tvdb_api_key)
    if codes is None:
        logger.debug(
            "No native language found for '%s' via embedded IMDb ID (%s).",
            file_path.name,
            eid,
        )
    return codes


def _resolve_embedded_tmdb_fallback(
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
) -> list[str] | None:
    """Resolve via an embedded TMDb ID in *file_stem*, if one is present."""
    if not tmdb_api_key:
        return None
    tmdb_m = _EMBEDDED_TMDB_RE.search(file_stem)
    if not tmdb_m:
        return None
    eid = tmdb_m.group(1) or tmdb_m.group(2) or tmdb_m.group(3)
    if not eid:
        return None
    return _lookup_and_cache(
        _lookup_tmdb_by_id,
        (eid, tmdb_api_key, _get_tmdb_endpoint(file_stem)),
        file_path,
        db,
        "tmdb_embedded_id",
    )


def _resolve_embedded_tvdb_fallback(
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tvdb_api_key: str | None,
) -> list[str] | None:
    """Resolve via an embedded TVDB ID in *file_stem*, if one is present."""
    if not tvdb_api_key:
        return None
    tvdb_m = _EMBEDDED_TVDB_RE.search(file_stem)
    if not tvdb_m:
        return None
    eid = tvdb_m.group(1) or tvdb_m.group(2) or tvdb_m.group(3)
    if not eid:
        return None
    return _lookup_and_cache(
        _lookup_tvdb_by_id,
        (eid, tvdb_api_key),
        file_path,
        db,
        "tvdb_embedded_id",
    )


def _resolve_tmdb_embedded(
    eid: str,
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None = None,
) -> list[str] | None:
    """Resolve via a TMDb-only embedded ID, falling back to an embedded TVDB ID."""
    if tmdb_api_key:
        _tmdb_media_type = _get_tmdb_endpoint(file_path.stem)
        codes = _lookup_tmdb_by_id(eid, tmdb_api_key, _tmdb_media_type)
        if codes is not None:
            logger.info(
                "Identified native language(s) for '%s': %s (source=tmdb_embedded_id).",
                file_path.name,
                codes,
            )
            _maybe_cache_result(db, file_path, codes, "tmdb_embedded_id", None)
            return codes

    return _resolve_embedded_tvdb_fallback(file_stem, file_path, db, tvdb_api_key)


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


def _resolve_embedded_id_phase(
    file_stem: str,
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,
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
        codes = _resolve_imdb_embedded(eid, file_stem, file_path, db, tmdb_api_key, tvdb_api_key)
    elif source == "tmdb":
        codes = _resolve_tmdb_embedded(eid, file_stem, file_path, db, tmdb_api_key, tvdb_api_key)
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


def _run_filename_directory_chain(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None,
) -> list[str] | None:
    """Run the existing filename/directory lookup chain.

    Iterates the fallback chain (IMDbPie/TMDb with filename/directory
    titles) and returns the first match or *None*.
    """
    _tmdb_media_type = _get_tmdb_endpoint(file_path.stem)
    last_error = "no match from any source"
    chain = _lookup_chain(tmdb_api_key, _tmdb_media_type)
    for lookup_fn, title_fn, source_label in chain:
        title, year = title_fn(file_path)
        if not title:
            logger.debug(
                "Skipping lookup for '%s' - could not determine title (year=%s).",
                file_path.name,
                year,
            )
            continue
        logger.debug(
            "Looking up native language for '%s' (title=%s, year=%s, source=%s).",
            file_path.name,
            title,
            year,
            source_label,
        )
        codes = lookup_fn(title, year)
        if codes is not None:
            logger.info(
                "Identified native language(s) for '%s': %s (source=%s).",
                file_path.name,
                codes,
                source_label,
            )
            if db is not None:
                db.set_native_language_cache(file_path, codes, source_label, None)
            return cast("list[str]", codes)
        last_error = _describe_failure(source_label, tmdb_api_key)
        logger.debug(
            "No native language found for '%s' via %s.",
            file_path.name,
            source_label,
        )

    logger.debug(
        "No native language found for '%s' after exhausting all sources.",
        file_path.name,
    )
    _maybe_cache_failure(db, file_path, last_error)
    return None


def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
    tvdb_api_key: str | None = None,
) -> list[str] | None:
    """Return ISO 639-2/B native language codes for *file_path*, or None.

    Checks the database cache first (by file_path + fingerprint).  On miss,
    tries an NFO-based fast path (direct ID lookups), then filename-embedded
    ID scan, then NFO-title search, then falls through to the existing
    filename/directory chain.

    The NFO phase supports both ``<movie>`` and ``<tvshow>`` XML formats
    created by Radarr, Sonarr, Kodi, etc.

    Args:
        file_path: Path to the media file.
        db: Optional database instance for caching.
        tmdb_api_key: TMDb API key for TMDb-powered lookups.
        tvdb_api_key: TVDB API key for TVDB-powered lookups.
    """
    cached_langs = _check_native_language_cache(db, file_path)
    if cached_langs is not _CACHE_MISS:
        return cast("list[str] | None", cached_langs)

    # ------------------------------------------------------------------
    # Phase 1 — NFO direct ID lookups (most reliable)
    # ------------------------------------------------------------------
    nfo_meta = _get_nfo_metadata(file_path)
    if nfo_meta is not None:
        result = _resolve_nfo_id_lookups(nfo_meta, file_path, db, tmdb_api_key, tvdb_api_key)
        if result is not None:
            return result

    # ------------------------------------------------------------------
    # Phase 2 — Embedded ID in filename (direct ID, highly reliable)
    # ------------------------------------------------------------------
    result = _resolve_embedded_id_phase(file_path.stem, file_path, db, tmdb_api_key, tvdb_api_key)
    if result is not None:
        return result

    # ------------------------------------------------------------------
    # Phase 3 — NFO title search (fuzzy, less reliable)
    # ------------------------------------------------------------------
    if nfo_meta is not None:
        result = _resolve_nfo_title_search(nfo_meta, file_path, db, tmdb_api_key)
        if result is not None:
            return result

    # ------------------------------------------------------------------
    # Phase 4 — Filename/directory fallback chain (least reliable)
    # ------------------------------------------------------------------
    return _run_filename_directory_chain(file_path, db, tmdb_api_key)


def _maybe_cache_result(
    db: Database | None,
    file_path: Path,
    codes: list[str],
    source: str,
    error: str | None,
) -> None:
    """Store a successful native language lookup result in the cache."""
    if db is not None:
        db.set_native_language_cache(file_path, codes, source, error)


def _maybe_cache_failure(db: Database | None, file_path: Path, error: str) -> None:
    """Store a failed lookup result so we don't retry on every run."""
    if db is not None:
        db.set_native_language_cache(file_path, None, None, error)
