"""Unit tests for trimarr._nfo_parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

from trimarr._nfo_parser import discover_nfo, parse_nfo

if TYPE_CHECKING:
    from pathlib import Path


class TestParseNfo:
    """Tests for parse_nfo()."""

    def test_parse_movie_xml(self, tmp_path: Path) -> None:
        """Full movie XML returns correct NfoMetadata."""
        nfo = tmp_path / "movie.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>The Dark Knight</title>
  <originaltitle>The Dark Knight</originaltitle>
  <year>2008</year>
  <imdbid>tt0468569</imdbid>
  <tmdbid>155</tmdbid>
  <uniqueid type="imdb" default="true">tt0468569</uniqueid>
  <uniqueid type="tmdb">155</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.original_title == "The Dark Knight"
        assert result.year == "2008"
        assert result.imdb_id == "tt0468569"
        assert result.tmdb_id == "155"

    def test_parse_tvshow_xml(self, tmp_path: Path) -> None:
        """Full tvshow XML returns correct NfoMetadata."""
        nfo = tmp_path / "tvshow.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<tvshow>
  <title>Breaking Bad</title>
  <year>2008</year>
  <imdbid>tt0903747</imdbid>
  <tmdbid>1396</tmdbid>
  <uniqueid type="imdb" default="true">tt0903747</uniqueid>
  <uniqueid type="tmdb">1396</uniqueid>
  <uniqueid type="tvdb">81189</uniqueid>
</tvshow>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Breaking Bad"
        assert result.year == "2008"
        assert result.imdb_id == "tt0903747"
        assert result.tmdb_id == "1396"

    def test_parse_minimal_movie(self, tmp_path: Path) -> None:
        """Minimal movie XML with just title works."""
        nfo = tmp_path / "minimal.nfo"
        nfo.write_text("""<?xml version="1.0"?><movie><title>Alien</title></movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Alien"
        assert result.original_title is None
        assert result.year is None
        assert result.imdb_id is None
        assert result.tmdb_id is None

    def test_parse_garbage(self, tmp_path: Path) -> None:
        """Garbage content returns None."""
        nfo = tmp_path / "bad.nfo"
        nfo.write_text("not xml at all <<<<")
        assert parse_nfo(nfo) is None

    def test_parse_empty(self, tmp_path: Path) -> None:
        """Empty file returns None."""
        nfo = tmp_path / "empty.nfo"
        nfo.write_text("")
        assert parse_nfo(nfo) is None

    def test_parse_no_relevant_fields(self, tmp_path: Path) -> None:
        """Valid XML but with only plot/studio fields returns None."""
        nfo = tmp_path / "metadata.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <plot>Some plot</plot>
  <studio>Warner Bros.</studio>
</movie>""")
        assert parse_nfo(nfo) is None

    def test_parse_originaltitle(self, tmp_path: Path) -> None:
        """originaltitle is separate from title."""
        nfo = tmp_path / "foreign.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Wo Hu Cang Long</title>
  <originaltitle>Crouching Tiger, Hidden Dragon</originaltitle>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "Wo Hu Cang Long"
        assert result.original_title == "Crouching Tiger, Hidden Dragon"

    def test_parse_originaltitle_only(self, tmp_path: Path) -> None:
        """NFO with only originaltitle (no title) is valid."""
        nfo = tmp_path / "originalonly.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <originaltitle>Das Boot</originaltitle>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title is None
        assert result.original_title == "Das Boot"

    def test_parse_uniqueid_fallback_no_imdbid(self, tmp_path: Path) -> None:
        """When <imdbid> is missing, uses <uniqueid type='imdb'>."""
        nfo = tmp_path / "uniqueid.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <uniqueid type="imdb" default="true">tt1234567</uniqueid>
  <uniqueid type="tmdb">999</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt1234567"
        assert result.tmdb_id == "999"

    def test_parse_imdbid_takes_precedence(self, tmp_path: Path) -> None:
        """<imdbid> takes precedence over <uniqueid type='imdb'>."""
        nfo = tmp_path / "precedence.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <imdbid>tt0000001</imdbid>
  <uniqueid type="imdb" default="true">tt0000002</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt0000001"

    def test_parse_whitespace_stripped(self, tmp_path: Path) -> None:
        """Values have surrounding whitespace stripped."""
        nfo = tmp_path / "whitespace.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>  The Dark Knight  </title>
  <year>  2008  </year>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.title == "The Dark Knight"
        assert result.year == "2008"

    def test_parse_unexpected_root(self, tmp_path: Path) -> None:
        """Valid XML with unexpected root element returns None."""
        nfo = tmp_path / "weird.nfo"
        nfo.write_text('<?xml version="1.0"?><foo><title>X</title></foo>')
        assert parse_nfo(nfo) is None

    def test_parse_whitespace_uniqueid(self, tmp_path: Path) -> None:
        """Whitespace-only uniqueid is skipped, next one used."""
        nfo = tmp_path / "whitespaceid.nfo"
        nfo.write_text("""<?xml version="1.0"?>
<movie>
  <title>Test</title>
  <uniqueid type="imdb">   </uniqueid>
  <uniqueid type="imdb" default="true">tt1234567</uniqueid>
</movie>""")
        result = parse_nfo(nfo)
        assert result is not None
        assert result.imdb_id == "tt1234567"

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


class TestDiscoverNfo:
    """Tests for discover_nfo()."""

    def test_stem_match(self, tmp_path: Path) -> None:
        """Same-stem .nfo is found."""
        movie_dir = tmp_path / "Movie (2024)"
        movie_dir.mkdir()
        nfo = movie_dir / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = movie_dir / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_any_nfo_in_dir(self, tmp_path: Path) -> None:
        """No stem match, any .nfo in dir found."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.nfo"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Some.Other.File.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo

    def test_no_nfo(self, tmp_path: Path) -> None:
        """No .nfo anywhere returns None."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_tvshow_upwalk(self, tmp_path: Path) -> None:
        """tvshow.nfo found by walking up from episode directory."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == tvshow_nfo

    def test_tvshow_upwalk_uppercase(self, tmp_path: Path) -> None:
        """tvshow.NFO (uppercase) found by walking up."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        tvshow_nfo = series / "tvshow.NFO"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == tvshow_nfo

    def test_tvshow_upwalk_mixed_case(self, tmp_path: Path) -> None:
        """TvShow.Nfo (mixed case) found by upwalk glob."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        tvshow_nfo = series / "TvShow.Nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == tvshow_nfo

    def test_episode_nfo_takes_priority(self, tmp_path: Path) -> None:
        """Episode-level NFO found before tvshow.nfo upwalk."""
        series = tmp_path / "Breaking Bad"
        season = series / "Season 1"
        season.mkdir(parents=True)
        episode_nfo = season / "Breaking Bad S01E01.nfo"
        episode_nfo.write_text("<movie><title>Episode</title></movie>")
        tvshow_nfo = series / "tvshow.nfo"
        tvshow_nfo.write_text("<tvshow><title>Breaking Bad</title></tvshow>")
        mkv = season / "Breaking Bad S01E01.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == episode_nfo

    def test_stem_match_over_any_nfo(self, tmp_path: Path) -> None:
        """Same-stem .nfo is preferred over other .nfo files in dir."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        other_nfo = d / "other.nfo"
        other_nfo.write_text("<movie><title>Other</title></movie>")
        stem_nfo = d / "Movie.nfo"
        stem_nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == stem_nfo

    def test_no_tvshow_nfo_upwalk(self, tmp_path: Path) -> None:
        """Walk up does not find tvshow.nfo -> None."""
        season = tmp_path / "Series" / "Season 1"
        season.mkdir(parents=True)
        mkv = season / "episode.mkv"
        mkv.write_text("dummy")
        assert discover_nfo(mkv) is None

    def test_any_nfo_mixed_case(self, tmp_path: Path) -> None:
        """Discovery handles .nfo, .NFO, .nfo case-insensitively."""
        d = tmp_path / "Movie (2024)"
        d.mkdir()
        nfo = d / "Movie.NFO"
        nfo.write_text("<movie><title>Movie</title></movie>")
        mkv = d / "Movie.mkv"
        mkv.write_text("dummy")
        result = discover_nfo(mkv)
        assert result == nfo
