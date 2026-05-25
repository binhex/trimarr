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
        """IMDbPie raises an exception."""
        mock_client = mocker.patch("imdbpie.Imdb", autospec=True)
        instance = mock_client.return_value
        instance.search_for_title.side_effect = RuntimeError("API error")
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


class TestResolveNativeLanguage:
    def test_cache_hit(self, mocker) -> None:
        """Database cache hit returns stored languages without API call."""
        mock_db = mocker.MagicMock()
        mock_db.get_native_language_cache.return_value = (["chi"], "imdbpie", None)
        result = resolve_native_language(Path("/data/test.mkv"), db=mock_db)
        assert result == ["chi"]
        mock_db.get_native_language_cache.assert_called_once()

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
