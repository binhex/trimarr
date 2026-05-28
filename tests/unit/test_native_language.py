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
            ("/data/Das.Boot.1981.DC.1080p.BluRay.x264-CtrlHD.mkv", "das boot", "1981"),
            ("/data/2024.Movie.1080p.mkv", "movie", "2024"),
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
            Path("/data/Das Boot (1981).mkv"), ["ger"], "imdbpie_filename", None
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
        """When parse_movie_title returns empty title, chain skips and fails."""
        import trimarr.native_language as nl

        mocker.patch.object(nl, "parse_movie_title", return_value=("", None))
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        result = resolve_native_language(Path("/data/Unknown.mkv"), db=mock_db)
        assert result is None
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Unknown.mkv"), None, None, "no match from any source"
        )

    def test_imdbpie_fails_no_tmdb_key(self, mocker) -> None:
        """IMDbPie returns no data, no TMDb API key configured — hits failure path."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        result = resolve_native_language(Path("/data/Test Movie (2020).mkv"), db=mock_db)
        assert result is None
        mock_db.set_native_language_cache.assert_called_once_with(
            Path("/data/Test Movie (2020).mkv"),
            None,
            None,
            "no match from IMDbPie (tried filename and directory name, no TMDb API key configured)",
        )

    def test_dir_title_fallback(self, mocker) -> None:
        """Filename search returns None, directory search succeeds."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # First call (filename) returns [], second call (directory) succeeds
        instance.search_for_title.side_effect = [
            [],
            [{"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"}],
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        # File with noisy scene name in parent dir with clean name
        path = Path("/media/Das Boot (1981)/Das.Boot.1981.DC.1080p.BluRay.x264-CtrlHD.mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["ger"]
        # Should have called search twice (filename failed, directory succeeded)
        assert instance.search_for_title.call_count == 2
        mock_db.set_native_language_cache.assert_called_once_with(path, ["ger"], "imdbpie_directory", None)

    def test_filename_success_no_fallback(self, mocker) -> None:
        """Filename succeeds - directory fallback is never attempted."""
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
        path = Path("/data/Das Boot (1981).mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["ger"]
        # Should only have called search once (filename succeeded)
        assert instance.search_for_title.call_count == 1
        mock_db.set_native_language_cache.assert_called_once_with(path, ["ger"], "imdbpie_filename", None)

    def test_dir_title_no_year(self, mocker) -> None:
        """Directory has no year, directory step skipped, falls to TMDb."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Some Movie", "original_title": "Some Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "en"}
        """
        mock_urlopen.side_effect = [search_response, detail_response]
        # File in a directory with no year; filename itself has a year
        path = Path("/data/Some Movie (2020).mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result == ["eng"]
        mock_db.set_native_language_cache.assert_called_once_with(path, ["eng"], "tmdb_filename", None)

    def test_all_steps_fail(self, mocker) -> None:
        """All 4 steps return None -> cached failure with combined error."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Connection refused")
        path = Path("/data/Test Movie (2020).mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result is None
        mock_db.set_native_language_cache.assert_called_once()
        args = mock_db.set_native_language_cache.call_args
        assert args[0][3] == "no match from IMDbPie or TMDb (tried filename and directory name)"

    def test_tmdb_fallback_dir(self, mocker) -> None:
        """IMDbPie fails both steps, TMDb with directory succeeds."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = b"""
        {"results": [{"id": 123, "title": "Some Movie", "original_title": "Some Movie"}]}
        """
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b"""
        {"original_language": "fr"}
        """
        mock_urlopen.side_effect = [search_response, search_response, detail_response]
        # IMDbPie fails on both filename and dir; TMDb succeeds on dir
        path = Path("/media/Some Movie (2020)/Noisy.File.GROUP.mkv")
        result = resolve_native_language(path, db=mock_db, tmdb_api_key="fake-key")
        assert result == ["fre"]
        mock_db.set_native_language_cache.assert_called_once_with(path, ["fre"], "tmdb_directory", None)

    def test_no_tmdb_key_dir(self, mocker) -> None:
        """No TMDb key, IMDbPie with directory succeeds (TMDb steps skipped)."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # First call (filename) returns [], second call (directory) succeeds
        instance.search_for_title.side_effect = [
            [],
            [{"title": "Some Movie", "year": 2020, "imdb_id": "tt0000000"}],
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }
        path = Path("/media/Some Movie (2020)/Noisy.File.mkv")
        result = resolve_native_language(path, db=mock_db)
        assert result == ["eng"]
        assert instance.search_for_title.call_count == 2
        mock_db.set_native_language_cache.assert_called_once_with(path, ["eng"], "imdbpie_directory", None)

    def test_integration_embedded_imdb_id(self, mocker) -> None:
        """Embedded IMDb ID in filename triggers direct lookup."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_lookup = mocker.patch(
            "trimarr.native_language._lookup_imdbpie_by_id",
            return_value=["ger", "eng"],
        )
        mock_tmdb = mocker.patch("trimarr.native_language._lookup_tmdb_by_id")
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {imdb-tt0077914} 2024.mkv"), db=mock_db, tmdb_api_key="test-key"
        )

        assert result == ["ger", "eng"]
        mock_lookup.assert_called_once_with("tt0077914")
        mock_tmdb.assert_not_called()

    def test_integration_embedded_tmdb_id(self, mocker) -> None:
        """Embedded TMDb ID in filename triggers TMDb direct lookup."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_lookup = mocker.patch(
            "trimarr.native_language._lookup_tmdb_by_id",
            return_value=["chi"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(Path("/data/Movie {tmdb-77914} 2024.mkv"), db=mock_db, tmdb_api_key="test-key")

        assert result == ["chi"]
        mock_lookup.assert_called_once_with("77914", "test-key")

    def test_integration_embedded_id_fails_fallthrough(self, mocker) -> None:
        """Embedded ID found but API fails; falls through to filename search."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mocker.patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=None)
        mock_tmdb = mocker.patch("trimarr.native_language._lookup_tmdb_by_id")
        mock_search = mocker.patch(
            "trimarr.native_language._lookup_imdbpie",
            return_value=["eng"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {imdb-tt0077914} 2024.mkv"), db=mock_db, tmdb_api_key="test-key"
        )

        assert result == ["eng"]
        mock_tmdb.assert_not_called()
        mock_search.assert_called_once()

    def test_integration_embedded_dual_id_tmdb_fallback(self, mocker) -> None:
        """IMDb ID present but fails; embedded TMDb ID used as fallback."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mocker.patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=None)
        mock_tmdb = mocker.patch(
            "trimarr.native_language._lookup_tmdb_by_id",
            return_value=["chi"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {imdb-tt0077914} {tmdb-77914} 2024.mkv"),
            db=mock_db,
            tmdb_api_key="test-key",
        )

        assert result == ["chi"]
        mock_tmdb.assert_called_once_with("77914", "test-key")

    def test_integration_embedded_dual_id_tmdb_no_key(self, mocker) -> None:
        """IMDb ID fails, TMDb ID present but no API key; falls through."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_imdbpie = mocker.patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=None)
        mock_tmdb = mocker.patch("trimarr.native_language._lookup_tmdb_by_id")
        mock_search = mocker.patch(
            "trimarr.native_language._lookup_imdbpie",
            return_value=["ger"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {imdb-tt0077914} {tmdb-77914} 2024.mkv"),
            db=mock_db,
            tmdb_api_key=None,
        )

        assert result == ["ger"]
        mock_imdbpie.assert_called_once_with("tt0077914")
        mock_tmdb.assert_not_called()
        mock_search.assert_called_once()

    def test_integration_embedded_imdb_only_fails(self, mocker) -> None:
        """IMDb ID only in filename, lookup fails, no TMDb ID to fall back on."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mocker.patch("trimarr.native_language._lookup_imdbpie_by_id", return_value=None)
        mock_tmdb = mocker.patch("trimarr.native_language._lookup_tmdb_by_id")
        mock_search = mocker.patch(
            "trimarr.native_language._lookup_imdbpie",
            return_value=["fra"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {imdb-tt0077914} 2024.mkv"),
            db=mock_db,
            tmdb_api_key="test-key",
        )

        assert result == ["fra"]
        mock_tmdb.assert_not_called()
        mock_search.assert_called_once()

    def test_integration_embedded_tmdb_no_key(self, mocker) -> None:
        """TMDb-only embedded ID but no API key configured; falls through."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_imdbpie = mocker.patch("trimarr.native_language._lookup_imdbpie_by_id")
        mock_tmdb = mocker.patch("trimarr.native_language._lookup_tmdb_by_id")
        mock_search = mocker.patch(
            "trimarr.native_language._lookup_imdbpie",
            return_value=["jpn"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {tmdb-77914} 2024.mkv"),
            db=mock_db,
            tmdb_api_key=None,
        )

        assert result == ["jpn"]
        mock_imdbpie.assert_not_called()
        mock_tmdb.assert_not_called()
        mock_search.assert_called_once()

    def test_integration_embedded_tmdb_fails(self, mocker) -> None:
        """TMDb-only embedded ID present but lookup fails; falls through."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_imdbpie = mocker.patch("trimarr.native_language._lookup_imdbpie_by_id")
        mock_tmdb = mocker.patch(
            "trimarr.native_language._lookup_tmdb_by_id",
            return_value=None,
        )
        mock_search = mocker.patch(
            "trimarr.native_language._lookup_imdbpie",
            return_value=["spa"],
        )
        mocker.patch("trimarr.native_language._get_nfo_metadata", return_value=None)

        result = resolve_native_language(
            Path("/data/Movie {tmdb-77914} 2024.mkv"),
            db=mock_db,
            tmdb_api_key="test-key",
        )

        assert result == ["spa"]
        mock_imdbpie.assert_not_called()
        mock_tmdb.assert_called_once_with("77914", "test-key")
        mock_search.assert_called_once()


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

    def test_three_plus_char_string_names(self) -> None:
        """3+ char strings treated as language names resolve to codes."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["English", "German"])
        assert result == ["eng", "ger"]

    def test_three_plus_char_duplicate(self) -> None:
        """Duplicate 3+ char strings produce a single code."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["English", "English"])
        assert result == ["eng"]

    def test_three_plus_char_unknown_name(self) -> None:
        """Unknown 3+ char string returns None."""
        from trimarr.native_language import _extract_imdb_spoken_codes

        result = _extract_imdb_spoken_codes(["ObscureMadeUpLanguage"])
        assert result is None


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


class TestHelpers:
    """Tests for the new helper functions (Task 1)."""

    def test_get_filename_title(self) -> None:
        """_get_filename_title returns parse_movie_title result for file stem."""
        from trimarr.native_language import _get_filename_title

        result = _get_filename_title(Path("/data/Das Boot (1981).mkv"))
        assert result == ("das boot", "1981")

    def test_get_directory_title(self) -> None:
        """_get_directory_title returns parse_movie_title result for parent dir."""
        from trimarr.native_language import _get_directory_title

        result = _get_directory_title(Path("/data/Great Expectations (1946)/Great Expectations.mkv"))
        assert result == ("great expectations", "1946")

    def test_get_directory_title_no_year(self) -> None:
        """_get_directory_title with no year in dir name returns title only."""
        from trimarr.native_language import _get_directory_title

        result = _get_directory_title(Path("/data/Movie/File.mkv"))
        assert result[0] == "movie"
        assert result[1] is None

    def test_lookup_chain_no_api_key(self) -> None:
        """_lookup_chain without TMDb key returns only IMDbPie entries."""
        from trimarr.native_language import _lookup_chain, _lookup_imdbpie

        chain = _lookup_chain(None)
        assert len(chain) == 2
        for entry in chain:
            assert len(entry) == 3
            assert entry[0] is _lookup_imdbpie
            assert "imdbpie" in entry[2]

    def test_lookup_chain_with_api_key(self) -> None:
        """_lookup_chain with TMDb key includes IMDbPie and TMDb entries."""
        from trimarr.native_language import _lookup_chain, _lookup_imdbpie

        chain = _lookup_chain("fake-key")
        assert len(chain) == 4
        # First two are imdbpie-based
        assert chain[0][0] is _lookup_imdbpie
        assert chain[0][2] == "imdbpie_filename"
        assert chain[1][2] == "imdbpie_directory"
        # Last two are tmdb-based (using partial)
        assert chain[2][2] == "tmdb_filename"
        assert chain[3][2] == "tmdb_directory"

    def test_lookup_chain_title_functions(self) -> None:
        """_lookup_chain title_fns correctly extract titles via Path."""
        from trimarr.native_language import _lookup_chain

        chain = _lookup_chain("fake-key")
        # Test filename title function
        fn_title_fn = chain[0][1]
        dir_title_fn = chain[1][1]
        result_fn = fn_title_fn(Path("/data/Test Movie (2020).mkv"))
        assert result_fn == ("test movie", "2020")
        result_dir = dir_title_fn(Path("/data/Some Dir (1999)/File.mkv"))
        assert result_dir == ("some dir", "1999")

    def test_describe_failure_imdbpie_only(self) -> None:
        """_describe_failure when source is imdbpie and no TMDb key."""
        from trimarr.native_language import _describe_failure

        msg = _describe_failure("imdbpie_filename", None)
        assert "no match from IMDbPie" in msg
        assert "no TMDb API key configured" in msg

    def test_describe_failure_imdbpie_with_key(self) -> None:
        """_describe_failure when source is imdbpie but TMDb key exists."""
        from trimarr.native_language import _describe_failure

        msg = _describe_failure("imdbpie_filename", "fake-key")
        assert msg == "no match from IMDbPie"

    def test_describe_failure_tmdb(self) -> None:
        """_describe_failure when source is tmdb."""
        from trimarr.native_language import _describe_failure

        msg = _describe_failure("tmdb_filename", "fake-key")
        assert "no match from IMDbPie or TMDb" in msg
        assert "tried filename and directory name" in msg


class TestLookupImdbpieById:
    """Tests for _lookup_imdbpie_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct IMDb ID lookup returns spoken languages."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result == ["ger"]
        instance.search_for_title.assert_not_called()

    def test_aux_failure(self, mocker) -> None:
        """When get_title_auxiliary raises, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.side_effect = RuntimeError("API error")
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_no_spoken_languages(self, mocker) -> None:
        """When aux has no spokenLanguages, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None

    def test_imdbpie_not_installed(self, mocker) -> None:
        """When imdbpie is not installed, returns None."""
        from trimarr.native_language import _lookup_imdbpie_by_id

        mocker.patch("trimarr.native_language._HAS_IMDBPIE", False)
        result = _lookup_imdbpie_by_id("tt0082096")
        assert result is None


class TestLookupTmdbById:
    """Tests for _lookup_tmdb_by_id()."""

    def test_success(self, mocker) -> None:
        """Direct TMDb ID lookup returns language codes."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "zh"}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["chi"]

    def test_network_error(self, mocker) -> None:
        """Network error during detail fetch returns None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Connection refused")
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_empty_original_language(self, mocker) -> None:
        """Detail has no original_language -> None."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": ""}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result is None

    def test_three_letter_code(self, mocker) -> None:
        """3-letter original_language is normalised (e.g. deu -> ger)."""
        from trimarr.native_language import _lookup_tmdb_by_id

        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "deu"}'
        mock_urlopen.return_value = detail_response
        result = _lookup_tmdb_by_id("155", "fake-key")
        assert result == ["ger"]


class TestResolveNativeLanguageNfo:
    """Tests for resolve_native_language with NFO integration."""

    def test_nfo_imdbpie_id_success(self, mocker, tmp_path: Path) -> None:
        """NFO with IMDb ID uses direct lookup, no search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><year>2024</year>
<imdbid>tt1234567</imdbid><tmdbid>999</tmdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["eng"]
        instance.search_for_title.assert_not_called()

    def test_nfo_tmdb_id_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IMDb ID fails, TMDb ID succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><year>2024</year>
<imdbid>tt1234567</imdbid><tmdbid>999</tmdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "fr"}'
        mock_urlopen.return_value = detail_response

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["fre"]

    def test_nfo_title_search_fallback(self, mocker, tmp_path: Path) -> None:
        """NFO IDs absent, NFO title used for search succeeds."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (1981)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Das Boot</title><year>1981</year></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]

    def test_nfo_all_fail_then_filename(self, mocker, tmp_path: Path) -> None:
        """NFO steps all fail, falls through to filename search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        nfo = d / "Das Boot.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Das Boot</title><year>1981</year>
<imdbid>tt0000000</imdbid></movie>""")
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value

        def aux_side_effect(imdb_id: str) -> dict:
            return {"spokenLanguages": None} if imdb_id == "tt0000000" else {"spokenLanguages": [{"name": "German"}]}

        instance.get_title_auxiliary.side_effect = aux_side_effect
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        instance.search_for_title.assert_called()

    def test_no_nfo_existing_chain(self, mocker, tmp_path: Path) -> None:
        """No NFO -> existing filename chain behaviour unchanged."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "German"}],
        }
        result = resolve_native_language(mkv, db=None)
        assert result == ["ger"]
        instance.search_for_title.assert_called_once()

    def test_nfo_cache_source_label(self, mocker, tmp_path: Path) -> None:
        """Cache source label starts with 'nfo_' for nfo-based results."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test</title><imdbid>tt1234567</imdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = None
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.get_title_auxiliary.return_value = {"spokenLanguages": [{"name": "English"}]}
        resolve_native_language(mkv, db=mock_db)
        call_args = mock_db.set_native_language_cache.call_args
        assert call_args is not None
        source = call_args[0][2]
        assert source.startswith("nfo_")

    def test_nfo_cache_hit(self, mocker, tmp_path: Path) -> None:
        """DB cache hit returns without NFO re-parse."""
        from trimarr.native_language import resolve_native_language

        mkv = Path("/data/test.mkv")
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "nfo_imdbpie_id", None)
        result = resolve_native_language(mkv, db=mock_db)
        assert result == ["chi"]
        mock_db.get_native_language_cache.assert_called_once()

    def test_nfo_tmdb_title_with_api_key(self, mocker, tmp_path: Path) -> None:
        """NFO title + TMDb search succeeds with API key."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2000)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Wo Hu Cang Long</title><year>2000</year></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        search_response = mocker.MagicMock()
        search_response.__enter__.return_value = search_response
        search_response.read.return_value = (
            b'{"results": [{"id": 123, "title": "Wo Hu Cang Long", "original_title": "Wo Hu Cang Long"}]}'
        )
        detail_response = mocker.MagicMock()
        detail_response.__enter__.return_value = detail_response
        detail_response.read.return_value = b'{"original_language": "zh"}'
        mock_urlopen.side_effect = [search_response, detail_response]

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["chi"]

    def test_nfo_tmdb_id_lookup_fails(self, mocker, tmp_path: Path) -> None:
        """NFO TMDb ID lookup fails, falls through to title search."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2024)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Das Boot</title><year>1981</year>
<imdbid>tt0000000</imdbid><tmdbid>999</tmdbid></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # IMDb ID lookup fails
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        # TMDb ID lookup fails
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Network error")
        # Search succeeds
        instance.search_for_title.return_value = [
            {"title": "Das Boot", "year": 1981, "imdb_id": "tt0082096"},
        ]

        # Use side_effect on aux so the second call (from search) succeeds
        def _aux(imdb_id: str) -> dict:
            return {"spokenLanguages": [{"name": "German"}]} if imdb_id == "tt0082096" else {"spokenLanguages": None}

        instance.get_title_auxiliary.side_effect = _aux

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result == ["ger"]

    def test_nfo_no_title(self, mocker, tmp_path: Path) -> None:
        """NFO direct ID fails (no spoken languages), no embedded ID, NFO has no title, falls to filename chain."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Das Boot (1981)"
        d.mkdir()
        nfo = d / "Das Boot.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><imdbid>tt0000000</imdbid></movie>""")
        mkv = d / "Das Boot (1981).mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # NFO IMDb ID lookup fails
        instance.get_title_auxiliary.return_value = {"spokenLanguages": None}
        # Filename + directory searches also fail (no match)
        instance.search_for_title.return_value = []

        result = resolve_native_language(mkv, db=None)
        assert result is None

    def test_nfo_imdbpie_title_fails_no_api(self, mocker, tmp_path: Path) -> None:
        """NFO title IMDbPie search fails, no TMDb API key, falls through."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Unknown (2020)"
        d.mkdir()
        nfo = d / "Unknown.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Unknown Movie</title><year>2020</year></movie>""")
        mkv = d / "Unknown.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []

        result = resolve_native_language(mkv, db=None)
        assert result is None

    def test_nfo_tmdb_title_search_fails(self, mocker, tmp_path: Path) -> None:
        """NFO title search fails, falls to Phase 4 (filename/directory chain) which also fails."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Unknown (2020)"
        d.mkdir()
        nfo = d / "Unknown.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Unknown Movie</title><year>2020</year></movie>""")
        mkv = d / "Unknown.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.return_value = []
        mock_urlopen = mocker.patch("trimarr.native_language.urllib.request.urlopen")
        mock_urlopen.side_effect = OSError("Network error")

        result = resolve_native_language(mkv, db=None, tmdb_api_key="fake-key")
        assert result is None

    def test_nfo_title_search_with_air_date_year(self, mocker, tmp_path: Path) -> None:
        """NFO with air-date year (2019-06-05) extracts just the 4-digit year."""
        from trimarr.native_language import resolve_native_language

        d = tmp_path / "Test (2019)"
        d.mkdir()
        nfo = d / "Test.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie><title>Test Movie</title><year>2019-06-05</year></movie>""")
        mkv = d / "Test.mkv"
        mkv.write_text("dummy")

        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        # search_for_title should be called with "Test Movie 2019" (not "2019-06-05")
        instance.search_for_title.return_value = [
            {"title": "Test Movie", "year": 2019, "imdb_id": "tt0000001"},
        ]
        instance.get_title_auxiliary.return_value = {
            "spokenLanguages": [{"name": "English"}],
        }

        result = resolve_native_language(mkv, db=None)
        assert result == ["eng"]
        # Verify the search term used only the 4-digit year
        instance.search_for_title.assert_called_once_with("Test Movie 2019")


class TestExtractEmbeddedId:
    """Tests for _extract_embedded_id()."""

    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            # IMDb: curly, square, bare
            ("Martin (1977) {imdb-tt0077914} 2160p", ("imdb", "tt0077914")),
            ("Martin [imdb-tt0077914] 2160p", ("imdb", "tt0077914")),
            ("Martin imdb-tt0077914 2160p", ("imdb", "tt0077914")),
            # IMDb: case insensitive
            ("Martin {IMDB-tt0077914} 2160p", ("imdb", "tt0077914")),
            # TMDb: curly, square, bare
            ("Martin (1977) {tmdb-77914} 2160p", ("tmdb", "77914")),
            ("Martin [tmdb-77914] 2160p", ("tmdb", "77914")),
            ("Martin tmdb-77914 2160p", ("tmdb", "77914")),
        ],
    )
    def test_extract_embedded_id(self, stem: str, expected: tuple[str, str]) -> None:
        from trimarr.native_language import _extract_embedded_id

        result = _extract_embedded_id(stem)
        assert result == expected

    @pytest.mark.parametrize(
        "stem",
        [
            "Martin (1977) 2160p",
            "Martin [some-other-id] 2160p",
            "tmdb-abc",  # TMDb ID must be numeric
            "imdb-movie",  # IMDb ID must start with tt
        ],
    )
    def test_extract_embedded_id_no_match(self, stem: str) -> None:
        from trimarr.native_language import _extract_embedded_id

        assert _extract_embedded_id(stem) is None

    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            # Both present: IMDb wins (checked first)
            ("{imdb-tt0077914} {tmdb-77914}", ("imdb", "tt0077914")),
            ("[tmdb-77914] [imdb-tt0077914]", ("imdb", "tt0077914")),
        ],
    )
    def test_extract_embedded_id_both_present(self, stem: str, expected: tuple[str, str]) -> None:
        from trimarr.native_language import _extract_embedded_id

        result = _extract_embedded_id(stem)
        assert result == expected
