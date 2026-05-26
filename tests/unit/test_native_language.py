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
            ("/data/Das Boot (1981).mkv", "das boot", "1981"),
            ("/data/Some.Movie.2024.2160p.WEBRip.mkv", "some movie", "2024"),
            ("/data/Movie.Name.2022.1080p.BluRay.x265.mkv", "movie name", "2022"),
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

    def test_fallback_to_stem(self) -> None:
        """When the cleaned title is empty (only release tags), fall back to raw stem."""
        result = parse_movie_title(Path("/data/2160p.mkv"))
        assert result == ("2160p", None)
        result2 = parse_movie_title(Path("/data/WEBRip.1080p.mkv"))
        assert result2 == ("webrip.1080p", None)

    def test_year_from_parent_directory(self) -> None:
        """Year in parent directory name when filename has no year."""
        result = parse_movie_title(Path("/data/Great Expectations (1946)/Great Expectations.mkv"))
        assert result == ("great expectations", "1946"), f"Got {result}"


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

    def test_alpha3_code_input(self) -> None:
        """Pass an alpha-3 terminologic code like 'deu' - hits alpha_3 lookup branch."""
        assert _language_name_to_iso_639_2("deu") == "ger"

    def test_bibliographic_code_input(self) -> None:
        """Pass a bibliographic code like 'ger' - hits bibliographic lookup branch."""
        assert _language_name_to_iso_639_2("ger") == "ger"

    def test_name_without_alpha2(self) -> None:
        """Language name whose entry has alpha_3 but no alpha_2."""
        assert _language_name_to_iso_639_2("Ghotuo") == "aaa"

    def test_alpha3_without_alpha2(self, mocker) -> None:
        """Alpha-3 lookup for a language with no alpha_2 hits the fallback return."""
        # 'aaa' is Ghotuo's alpha_3, which has no alpha_2
        result = _language_name_to_iso_639_2("aaa")
        assert result == "aaa"

    def test_pycountry_import_error(self, mocker) -> None:
        """When pycountry is not available, uses fallback dict."""
        import builtins

        real_import = builtins.__import__

        def _block_pycountry(name, *args, **kwargs):
            if name == "pycountry":
                raise ImportError("No module named pycountry")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=_block_pycountry)
        assert _language_name_to_iso_639_2("English") == "eng"
        assert _language_name_to_iso_639_2("Unknown") is None


class TestLookupImdbpie:
    def test_success(self, mocker) -> None:
        """IMDbPie finds the movie and returns languages."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
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
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        result = _lookup_imdbpie("Unknown Movie", None)
        assert result is None

    def test_api_error(self, mocker) -> None:
        """IMDbPie raises an exception during search."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.side_effect = RuntimeError("API error")
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None

    def test_import_error(self, mocker) -> None:
        """When imdbpie is not installed, returns None."""
        import builtins

        real_import = builtins.__import__

        def _block_imdbpie(name, *args, **kwargs):
            if name == "imdbpie":
                raise ImportError("No module named imdbpie")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=_block_imdbpie)
        result = _lookup_imdbpie("Test", None)
        assert result is None

    def test_client_creation_error(self, mocker) -> None:
        """When imdbpie.Imdb() raises an exception, returns None."""
        mocker.patch("imdbpie.Imdb", side_effect=RuntimeError("Client error"))
        result = _lookup_imdbpie("Test", None)
        assert result is None

    def test_no_spoken_languages(self, mocker) -> None:
        """When aux has no spokenLanguages, returns None."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None

    def test_aux_failure(self, mocker) -> None:
        """When get_title_auxiliary raises, returns None."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.side_effect = RuntimeError("Aux failure")
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None

    def test_no_title_year_match(self, mocker) -> None:
        """Hits exist but none match both title and year."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Different Movie", "year": 1999, "imdb_id": "tt9999999"},
        ]
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None

    def test_year_mismatch(self, mocker) -> None:
        """Title matches but year differs, no match found."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1999, "imdb_id": "tt0082096"},
        ]
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None

    def test_year_type_error(self, mocker) -> None:
        """Hit has non-integer year, which raises ValueError on int()."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": "N/A", "imdb_id": "tt0082096"},
        ]
        result = _lookup_imdbpie("Das Boot", "1981")
        assert result is None


class TestLookupTmdb:
    def test_success(self, mocker) -> None:
        """TMDb search + detail returns original_language."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Wo Hu Cang Long", "original_title": "Wo Hu Cang Long"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
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
        response.__enter__.return_value = response
        response.read.return_value = b'{"results": []}'
        mock_urlopen.return_value = response
        result = _lookup_tmdb("Unknown", None, "fake-key")
        assert result is None

    def test_no_api_key(self) -> None:
        """No TMDb API key means no lookup is attempted."""
        result = _lookup_tmdb("Test", "2020", "")
        assert result is None

    def test_search_failure(self, mocker) -> None:
        """TMDb search endpoint raises an exception."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Network error")
        result = _lookup_tmdb("Any Movie", None, "fake-key")
        assert result is None

    def test_detail_fetch_failure(self, mocker) -> None:
        """TMDb detail endpoint raises an exception."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Wo Hu Cang Long", "original_title": "Wo Hu Cang Long"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.side_effect = OSError("Network error")
        mock_urlopen.side_effect = [search_response, detail_response]
        result = _lookup_tmdb("Wo Hu Cang Long", "2000", "fake-key")
        assert result is None

    def test_tmdb_id_none(self, mocker) -> None:
        """Search result has id=None, should be skipped."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        response = mocker.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"""
        {"results": [{"id": None, "title": "Movie", "original_title": "Movie"}]}
        """
        mock_urlopen.return_value = response
        result = _lookup_tmdb("Movie", None, "fake-key")
        assert result is None

    def test_empty_original_language(self, mocker) -> None:
        """Detail has no original_language field."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Test Movie", "original_title": "Test Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": ""}
        """
        mock_urlopen.side_effect = [search_response, detail_response]
        result = _lookup_tmdb("Test Movie", None, "fake-key")
        assert result is None

    def test_three_letter_raw_lang(self, mocker) -> None:
        """When original_language is a 3-letter code not in ISO_639_1_TO_2."""
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Film", "original_title": "Film"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "deu"}
        """
        mock_urlopen.side_effect = [search_response, detail_response]
        result = _lookup_tmdb("Film", None, "fake-key")
        assert result == ["ger"]


class TestResolveNativeLanguage:
    def test_cache_hit(self, mocker) -> None:
        """Database cache hit returns stored languages without API call."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "imdbpie", None)
        result = resolve_native_language(Path("/data/test.mkv"), db=mock_db)
        assert result == ["chi"]
        mock_db.get_native_language_cache.assert_called_once()

    def test_cache_hit_empty_langs(self, mocker) -> None:
        """Cache hit with empty languages list (falsy) still returns empty list."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = ([], "imdbpie", None)
        result = resolve_native_language(Path("/data/test.mkv"), db=mock_db)
        assert result == []

    def test_cache_miss_then_imdbpie_success(self, mocker) -> None:
        """Cache miss triggers IMDbPie lookup, result is cached."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(Path("/data/Das Boot (1981).mkv"), db=mock_db)
        assert result == ["ger"]
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Das Boot (1981).mkv"), ["ger"], "imdbpie", None
        )

    def test_cache_miss_all_fail(self, mocker) -> None:
        """Cache miss + all APIs fail returns None and caches failure."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        result = resolve_native_language(Path("/data/Unknown.mkv"), db=mock_db)
        assert result is None
        mock_db.set_native_language_cache.assert_called_once()

    def test_no_db_provided(self, mocker) -> None:
        """When db is None, lookup still works but results aren't cached."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Test", "year": 2020, "imdb_id": "tt0000000"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }
        result = resolve_native_language(Path("/data/Test (2020).mkv"), db=None)
        assert result == ["eng"]

    def test_tmdb_fallback(self, mocker) -> None:
        """IMDbPie returns None, TMDb fallback succeeds with api_key."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Unknown Movie", "original_title": "Unknown Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "en"}
        """
        mock_urlopen.side_effect = [search_response, detail_response]
        result = resolve_native_language(Path("/data/Unknown Movie (2020).mkv"), db=mock_db, tmdb_api_key="fake-key")
        assert result == ["eng"]
        mock_db.set_native_language_cache.assert_called_once()

    def test_empty_title(self, mocker) -> None:
        """When parse_movie_title returns empty title, returns None and caches failure."""
        import trimarr.native_language as nl

        mocker.patch.object(nl, "parse_movie_title", return_value=("", None))
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        result = resolve_native_language(Path("/data/Unknown.mkv"), db=mock_db)
        assert result is None
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Unknown.mkv"), None, None, "unable to parse title"
        )


class TestNormaliseForCompare:
    """Tests for _normalise_for_compare()."""

    def test_accents_folded(self) -> None:
        """Accented characters are NFKD-folded to ASCII."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Amélie") == "amelie"
        assert _normalise_for_compare("José") == "jose"
        assert _normalise_for_compare("Café Society") == "cafesociety"

    def test_ampersand_to_and(self) -> None:
        """& is converted to 'and'."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Tom & Jerry") == "tomandjerry"
        assert _normalise_for_compare("Pride & Prejudice") == "prideandprejudice"

    def test_written_numerals_to_digits(self) -> None:
        """Written number words are converted to digits (including at start of title)."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Twelve Years a Slave") == "12yearsaslave"
        assert _normalise_for_compare("Seven Samurai") == "7samurai"

    def test_roman_numerals_to_digits(self) -> None:
        """Roman numerals in titles are converted to digits."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Rocky IV") == "rocky4"
        assert _normalise_for_compare("Rocky II") == "rocky2"

    def test_possessive_s_stripped(self) -> None:
        """Possessive 's after s-ending words is stripped; other possessives pass through."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Jones's") == "jones"
        # "Alex's" does NOT end in 's' so the possessive pattern doesn't apply.
        # The apostrophe is stripped by the punctuation cleaner at the end.
        assert _normalise_for_compare("Alex's") == "alexs"

    def test_punctuation_stripped(self) -> None:
        """Colons, apostrophes, quotes, commas are stripped (comparison is a single token)."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("Harry, Ron and Hermione") == "harryronandhermione"
        assert _normalise_for_compare("Don't Look Up") == "dontlookup"

    def test_imdb_keyword_stripped(self) -> None:
        """The word 'imdb' is stripped from titles."""
        from trimarr.native_language import _normalise_for_compare

        assert _normalise_for_compare("IMDb Top 250") == "top250"

    def test_combined_with_parse(self) -> None:
        """Full pipeline: parse_movie_title + normalise_for_compare produces clean search tokens."""
        from trimarr.native_language import _normalise_for_compare

        title, year = parse_movie_title(Path("/data/Amélie (2001).mkv"))
        assert year == "2001"
        assert _normalise_for_compare(title) == "amelie"

        title2, year2 = parse_movie_title(Path("/data/Pride & Prejudice (2005).mkv"))
        assert year2 == "2005"
        assert _normalise_for_compare(title2) == "prideandprejudice"


class TestExtractImdbSpokenCodes:
    """Direct tests for _extract_imdb_spoken_codes()."""

    def test_string_format_codes(self) -> None:
        """String entries (ISO 639-1 codes) are mapped through _ISO_639_1_TO_2."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["en", "de"])
        assert result == ["eng", "ger"]

    def test_string_format_duplicate(self) -> None:
        """Duplicate string entries produce a single code."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["en", "en"])
        assert result == ["eng"]

    def test_dict_format_unknown_name(self) -> None:
        """Dict entry with empty/unrecognised name returns None."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes([{}])
        assert result is None

        result2 = _extract_imdb_spoken_codes([{"name": ""}])
        assert result2 is None

    def test_mixed_format_string_and_dict(self) -> None:
        """Mixed string and dict entries are handled in the same list."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["en", {"name": "German"}])
        assert result == ["eng", "ger"]

    def test_dict_format_duplicate_code(self) -> None:
        """Duplicate dict entries produce a single code."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes([{"name": "German"}, {"name": "German"}])
        assert result == ["ger"]


class TestLookupPycountryLanguage:
    """Direct tests for _lookup_pycountry_language()."""

    def test_lookup_none(self) -> None:
        """When lang is None, returns None."""
        from trimarr.native_language import _lookup_pycountry_language

        assert _lookup_pycountry_language(None) is None

    def test_alpha2_in_map_returns_direct(self) -> None:
        """alpha_2 present in _ISO_639_1_TO_2 returns mapped code immediately."""
        import pycountry

        from trimarr.native_language import _lookup_pycountry_language

        # German has alpha_2='de' which IS in _ISO_639_1_TO_2
        lang = pycountry.languages.get(alpha_2="de")
        result = _lookup_pycountry_language(lang)
        assert result == "ger"

    def test_bibliographic_fallback(self) -> None:
        """alpha_2 not in map but bibliographic exists — returns bibliographic."""
        import pycountry

        from trimarr.native_language import _lookup_pycountry_language

        # Tibetan has alpha_2='bo' (NOT in _ISO_639_1_TO_2) and bibliographic='tib'
        lang = pycountry.languages.get(alpha_2="bo")
        result = _lookup_pycountry_language(lang)
        assert result == "tib"

    def test_alpha3_fallback_inner(self) -> None:
        """alpha_2 not in map, no bibliographic — falls back to inner alpha_3."""
        import pycountry

        from trimarr.native_language import _lookup_pycountry_language

        # Abkhazian has alpha_2='ab' (NOT in _ISO_639_1_TO_2), no bibliographic, alpha_3='abk'
        lang = pycountry.languages.get(alpha_3="abk")
        result = _lookup_pycountry_language(lang)
        assert result == "abk"

    def test_no_alpha2_alpha3_fallback(self) -> None:
        """No alpha_2 — falls back to outer alpha_3."""
        import pycountry

        from trimarr.native_language import _lookup_pycountry_language

        # Ghotuo has no alpha_2, alpha_3='aaa'
        lang = pycountry.languages.get(alpha_3="aaa")
        result = _lookup_pycountry_language(lang)
        assert result == "aaa"

    def test_no_alpha2_fallback_code(self) -> None:
        """No alpha_2 with fallback_code — returns fallback_code."""
        import pycountry

        from trimarr.native_language import _lookup_pycountry_language

        # Ghotuo has no alpha_2 — with fallback_code, returns fallback_code
        lang = pycountry.languages.get(alpha_3="aaa")
        result = _lookup_pycountry_language(lang, fallback_code="zzz")
        assert result == "zzz"

    def test_no_alpha3(self) -> None:
        """Lang object with no alpha_3 attribute returns None."""
        from trimarr.native_language import _lookup_pycountry_language

        class _NoAlpha3:
            """Fake language object with no alpha_3."""

            alpha_2 = None

        result = _lookup_pycountry_language(_NoAlpha3())
        assert result is None
