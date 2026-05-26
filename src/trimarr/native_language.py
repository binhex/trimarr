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
from typing import TYPE_CHECKING

from trimarr.processor import _ISO_639_1_TO_2, normalize_language_code

if TYPE_CHECKING:
    from pathlib import Path

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

_WORD_SEPARATORS = re.compile(r"[._\-+]")


def _normalise_title(s: str) -> str:
    """Lower-case, collapse whitespace, and strip leading/trailing spaces."""
    return " ".join(s.lower().split())


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
        # Remove the last year occurrence from the title string
        year_pos = cleaned.rfind(year)
        if year_pos != -1:
            cleaned = cleaned[:year_pos] + cleaned[year_pos + len(year) :]

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

    def _strip_parens(s: str) -> str:
        parts = re.split(r"(\([^)]*\))", s)
        result = []
        for part in parts:
            if part.startswith("(") and part.endswith(")") and not _YEAR_RE.search(part):
                continue
            result.append(part)
        return "".join(result)

    cleaned = _strip_parens(cleaned)
    cleaned = _strip_release_tags(cleaned)
    title = _normalise_title(" ".join(cleaned.split()))
    if not title:
        title = _normalise_title(file_path.stem)
    return title, year


def _find_matching_imdb_id(
    hits: list[dict],
    title: str,
    year: str | None,
) -> str | None:
    """Find and return the IMDb ID of the first hit matching *title* and *year*."""
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
        matched_id: str | None = hit.get("imdb_id")
        if matched_id:
            return matched_id
    return None


def _lookup_imdbpie(title: str, year: str | None) -> list[str] | None:
    """Return ISO 639-2/B language codes via IMDbPie, or None on failure."""
    try:
        import imdbpie
    except ImportError:
        logger.warning("imdbpie not installed — cannot perform IMDb lookup.")
        return None
    try:
        client = imdbpie.Imdb()
    except Exception as exc:
        logger.warning("Failed to create IMDbPie client: %s", exc)
        return None
    search_term = title
    if year:
        search_term = f"{title} {year}"
    try:
        hits = client.search_for_title(search_term)
    except Exception as exc:
        logger.debug("IMDbPie search failed for '%s': %s", search_term, exc)
        return None
    if not hits:
        logger.debug("IMDbPie returned no hits for '%s'.", search_term)
        return None
    matched_id = _find_matching_imdb_id(hits, title, year)
    if not matched_id:
        logger.debug("IMDbPie no title+year match for '%s'.", search_term)
        return None
    try:
        aux = client.get_title_auxiliary(matched_id)
    except Exception as exc:
        logger.debug("IMDbPie aux data failed for '%s': %s", matched_id, exc)
        return None
    spoken = aux.get("spokenLanguages") if aux else None
    if not spoken:
        return None
    return _extract_imdb_spoken_codes(spoken)


def _extract_imdb_spoken_codes(spoken: list) -> list[str] | None:
    """Convert IMDb spoken language entries to ISO 639-2/B codes.

    IMDbPie returns ``spokenLanguages`` as a list of ISO 639-1 codes
    (e.g. ``["en", "de"]``).  Map each 2-letter code through the
    existing ``_ISO_639_1_TO_2`` table to get the 3-letter bibliographic
    form.  Fall back to language-name resolution for any entry that is
    not a recognised 2-letter code.
    """
    codes: list[str] = []
    for entry in spoken:
        if isinstance(entry, str) and len(entry) == 2:
            # ISO 639-1 code — map directly to 3-letter form
            code = _ISO_639_1_TO_2.get(entry.lower())
            if code and code not in codes:
                codes.append(code)
        else:
            lang_name = entry.get("name") or entry.get("description") or "" if isinstance(entry, dict) else str(entry)  # noqa: E501
            code = _language_name_to_iso_639_2(lang_name)
            if code and code not in codes:
                codes.append(code)
    return codes or None


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
        alpha_2 = getattr(lang, "alpha_2", None)
        if isinstance(alpha_2, str) and alpha_2:
            code = _ISO_639_1_TO_2.get(alpha_2.lower())
            if code:
                return code
            # alpha_2 not in map — try bibliographic or alpha_3
            biblio = getattr(lang, "bibliographic", None)
            if isinstance(biblio, str) and biblio:
                return normalize_language_code(biblio.lower())
            alpha_3 = getattr(lang, "alpha_3", None)
            if isinstance(alpha_3, str) and alpha_3:
                return normalize_language_code(alpha_3.lower())
        if fallback_code is not None:
            return normalize_language_code(fallback_code)
        alpha_3 = getattr(lang, "alpha_3", None)
        if isinstance(alpha_3, str) and alpha_3:
            return normalize_language_code(alpha_3.lower())
    return None


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
    try:
        import pycountry
    except ImportError:
        return None
    result = _lookup_pycountry_language(pycountry.languages.get(name=name))
    if result:
        return result
    result = _lookup_pycountry_language(pycountry.languages.get(alpha_3=name.lower()), fallback_code=name.lower())
    if result:
        return result
    result = _lookup_pycountry_language(pycountry.languages.get(bibliographic=name.lower()), fallback_code=name.lower())
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


def _try_tmdb_detail(hit: dict, title: str, api_key: str) -> list[str] | None:
    """If *hit* matches *title*, fetch TMDb detail and extract language codes."""
    for field in ("title", "original_title"):
        hit_title = (hit.get(field) or "").strip().lower()
        if _normalise_title(hit_title) == _normalise_title(title):
            tmdb_id = hit.get("id")
            if tmdb_id is None:
                continue
            encoded_key = urllib.parse.quote(api_key, safe="")
            detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={encoded_key}"
            try:
                with urllib.request.urlopen(detail_url, timeout=15) as resp2:
                    detail = json.loads(resp2.read().decode())
            except Exception as exc:
                logger.debug("TMDb detail failed for id %s: %s", tmdb_id, exc)
                continue
            raw_lang = detail.get("original_language")
            if not raw_lang:
                continue
            code = _ISO_639_1_TO_2.get(raw_lang.lower())
            if code:
                return [normalize_language_code(code)]
            if _is_three_letter_code(raw_lang):
                return [normalize_language_code(raw_lang.lower())]
    return None


def _lookup_tmdb(title: str, year: str | None, api_key: str) -> list[str] | None:
    """Return ISO 639-2/B language codes via TMDb, or None on failure."""
    encoded_title = urllib.parse.quote(title)
    encoded_key = urllib.parse.quote(api_key, safe="")
    search_url = f"https://api.themoviedb.org/3/search/movie?query={encoded_title}&api_key={encoded_key}"
    if year:
        search_url += f"&year={year}"
    try:
        with urllib.request.urlopen(search_url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("TMDb search failed for '%s': %s", title, exc)
        return None
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        logger.debug("TMDb no results for '%s'.", title)
        return None
    for hit in results:
        codes = _try_tmdb_detail(hit, title, api_key)
        if codes:
            return codes
    return None


def resolve_native_language(
    file_path: Path,
    db: Database | None,
    tmdb_api_key: str | None = None,
) -> list[str] | None:
    """Return ISO 639-2/B native language codes for *file_path*, or None.

    Checks the database cache first (by file_path + fingerprint).  On miss,
    attempts IMDbPie lookup followed by TMDb fallback.  Caches the result
    (including failures) so subsequent runs are fast.
    """
    if db is not None:
        cached = db.get_native_language_cache(file_path)
        if cached is not None:
            cached_langs, cached_source, cached_error = cached
            if cached_langs:
                logger.debug(
                    "Native language cache hit for '%s': %s (source: %s)", file_path.name, cached_langs, cached_source
                )
            return cached_langs
    title, year = parse_movie_title(file_path)
    if not title:
        logger.debug("Could not parse movie title from '%s'.", file_path.name)
        _maybe_cache_failure(db, file_path, "unable to parse title")
        return None
    if not year:
        logger.debug(
            "Could not determine year for '%s' — skipping native language lookup (without a year the wrong film may be matched).",
            file_path.name,
        )
        _maybe_cache_failure(db, file_path, "unable to determine year from filename or parent directory")
        return None
    logger.debug("Looking up native language for '%s' (title=%s, year=%s).", file_path.name, title, year)
    codes = _lookup_imdbpie(title, year)
    source = "imdbpie"
    error = "no match from API"
    if not codes:
        if tmdb_api_key:
            codes = _lookup_tmdb(title, year, tmdb_api_key)
            source = "tmdb"
        else:
            error = "IMDbPie returned no data and no TMDb API key configured"
    if not codes:
        logger.debug("No native language found for '%s'.", file_path.name)
        _maybe_cache_failure(db, file_path, error)
        return None
    logger.info("Identified native language(s) for '%s': %s (source: %s).", file_path.name, codes, source)
    if db is not None:
        db.set_native_language_cache(file_path, codes, source, None)
    return codes


def _maybe_cache_failure(db: Database | None, file_path: Path, error: str) -> None:
    """Store a failed lookup result so we don't retry on every run."""
    if db is not None:
        db.set_native_language_cache(file_path, None, None, error)
