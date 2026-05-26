"""Unit tests for core.processor."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trimarr.processor import CorruptOutputError, MkvTrack, _spinner, build_mkvmerge_command, probe_file, process_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MKVMERGE = "/usr/bin/mkvmerge"


def _make_tracks(
    audio_langs: list[str | None],
    sub_langs: list[str | None],
    video_count: int = 1,
    audio_names: list[str | None] | None = None,
    sub_names: list[str | None] | None = None,
    audio_defaults: list[bool] | None = None,
    sub_defaults: list[bool] | None = None,
    audio_channels: list[int | None] | None = None,
) -> list[MkvTrack]:
    """Build a minimal track list for testing."""
    tracks: list[MkvTrack] = []
    tid = 0
    for _ in range(video_count):
        tracks.append(MkvTrack(id=tid, type="video", language=None))
        tid += 1
    for i, lang in enumerate(audio_langs):
        name = audio_names[i] if audio_names else None
        default = audio_defaults[i] if audio_defaults else False
        ch = audio_channels[i] if audio_channels else None
        tracks.append(MkvTrack(id=tid, type="audio", language=lang, name=name, default_track=default, channels=ch))
        tid += 1
    for i, lang in enumerate(sub_langs):
        name = sub_names[i] if sub_names else None
        default = sub_defaults[i] if sub_defaults else False
        tracks.append(MkvTrack(id=tid, type="subtitles", language=lang, name=name, default_track=default))
        tid += 1
    return tracks


def _build_cmd(
    tracks: list[MkvTrack],
    language: list[str] | None = None,
    keep_audio: bool = False,
    keep_subtitles: bool = False,
    edit_metadata_title: bool = False,
    delete_metadata_title: bool = False,
    input_path: Path | None = None,
    output_path: Path | None = None,
    logger: MagicMock | None = None,
    strip_lower_channels: bool = False,
    strip_commentary: bool = False,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
) -> list[str] | None:
    """Thin wrapper around build_mkvmerge_command with test-friendly defaults."""
    if language is None:
        language = ["eng"]
    return build_mkvmerge_command(
        mkvmerge_path=MKVMERGE,
        input_path=input_path or Path("/media/Movie.mkv"),
        output_path=output_path or Path("/media/Movie.mkv.tmp"),
        tracks=tracks,
        language=language,
        keep_audio=keep_audio,
        keep_subtitles=keep_subtitles,
        edit_metadata_title=edit_metadata_title,
        delete_metadata_title=delete_metadata_title,
        logger=logger or MagicMock(),
        strip_lower_channels=strip_lower_channels,
        strip_commentary=strip_commentary,
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,
    )


def _proc_logger() -> MagicMock:
    """Return a MagicMock logger with individual method mocks for process_file tests."""
    log = MagicMock()
    for attr in ("debug", "info", "warning", "error", "success"):
        setattr(log, attr, MagicMock())
    return log


def _proc_cmd(input_path: Path, output_path: Path) -> list[str]:
    """Build a minimal mkvmerge command for process_file tests."""
    return [MKVMERGE, "-o", str(output_path), str(input_path)]


def _fake_run_success_with_probe(args: list[str], **kwargs: object) -> MagicMock:
    """Simulate mkvmerge: write processed bytes to output, return exit 0. Also handles -J probes."""
    if args[1] == "-J":
        return MagicMock(returncode=0, stdout="{}", stderr="")
    Path(args[2]).write_bytes(b"processed")
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Strip subtitle by regex
# ---------------------------------------------------------------------------


class TestStripSubtitleByRegex:
    """Tests for _apply_strip_subtitle_regex."""

    def test_drops_subtitle_matching_regex(self) -> None:
        """Subtitle track with a name matching the regex is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")])
        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        # Only track ID 3 (Dialogues) should be kept
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [3]  # only the non-matching track

    def test_no_match_keeps_all_subtitles(self) -> None:
        """When no subtitle name matches, nothing is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="audio", language="fre"),  # dropped by language filter
            MkvTrack(id=3, type="subtitles", language="eng", name="Dialogues"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Sous-titres"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"songs")])
        assert cmd is not None  # audio change generates a command
        # No subtitle tracks were dropped by regex — no --subtitle-tracks or --no-subtitles emitted
        assert "--subtitle-tracks" not in cmd
        assert "--no-subtitles" not in cmd

    def test_case_insensitive_match(self) -> None:
        """Regex with (?i) matches case-insensitively."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="songs & signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="SIGNS & SONGS"),
        ]
        # Pattern matches "songs" OR "signs" (case-insensitive), so both tracks match
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)(songs|signs)")])
        assert cmd is not None
        # All subs matched → --no-subtitles is used instead of --subtitle-tracks
        assert "--no-subtitles" in cmd

    def test_multiple_patterns_all_match(self) -> None:
        """Multiple patterns — tracks matching any pattern are dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Commentary Subs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            strip_subtitle_regex_patterns=[
                re.compile(r"(?i)songs.*signs"),
                re.compile(r"(?i)commentary"),
            ],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [4]  # only Dialogues survives

    def test_no_patterns_is_noop(self) -> None:
        """Empty patterns list = feature disabled."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=None)
        # No audio or subtitle changes needed for a single eng sub track
        assert cmd is None  # nothing to do

    def test_drops_all_subtitles_matched(self) -> None:
        """When all subtitles match the regex, all are dropped safely."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Signs & Songs"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)(songs|signs)")])
        assert cmd is not None
        assert "--no-subtitles" in cmd

    def test_logs_regex_drops(self) -> None:
        """Info-level log is emitted when regex drops subtitle tracks."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
        ]
        logger = MagicMock()
        _ = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs")], logger=logger)
        # Should log info about dropping subtitle track(s) by name regex
        info_calls = [c for c in logger.info.call_args_list if "regex" in str(c)]
        assert len(info_calls) >= 1
        assert "subtitle track(s) by name regex" in str(info_calls[0])

    def test_regex_after_commentary(self) -> None:
        """Regex stripping runs after commentary strip — regex removes what survives commentary."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Director Commentary"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            strip_commentary=True,
            strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [4]  # commentary (2) and songs (3) both dropped

    def test_regex_after_language_filter(self) -> None:
        """Regex stripping operates on language-surviving tracks only."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="jpn", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            language=["eng"],
            strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        # Track 2 (jpn, Songs) was already dropped by language filter
        # Track 3 (eng, Songs) is dropped by regex
        # Track 4 (eng, Dialogues) survives
        assert kept == [4]

    def test_null_name_track_not_matched(self) -> None:
        """Subtitle track with name=None is not matched by regex."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name=None),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r".")])
        assert cmd is None  # no changes — name=None doesn't match "any char"

    def test_keep_subtitles_skips_regex_strip(self) -> None:
        """Regex strip does not fire when --keep-subtitles is set."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
        ]
        cmd = _build_cmd(tracks, keep_subtitles=True, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs")])
        # keep_subtitles overrides regex — all subtitles kept, no changes needed
        assert cmd is None

    def test_subtitle_fallback_fired_skips_regex_strip(self) -> None:
        """Regex strip does not fire when subtitle safety fallback activated."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="audio", language="jpn"),
            MkvTrack(id=3, type="subtitles", language="jpn", name="Songs & Signs"),
        ]
        # --language eng means no sub matches → fallback fires, keeps all subs
        cmd = _build_cmd(tracks, language=["eng"], strip_subtitle_regex_patterns=[re.compile(r"(?i)songs")])
        # Audio changes (jpn audio dropped) trigger a command
        assert cmd is not None
        # Fallback keeps the jpn subtitle even though it matches regex
        assert "--audio-tracks" in cmd
        idx = cmd.index("--audio-tracks") + 1
        kept_audio = [int(x) for x in cmd[idx].split(",")]
        assert kept_audio == [1]  # only eng audio kept
        # The jpn subtitle should still be present (fallback protected it)
        # --subtitle-tracks absent means no subtitle change was needed


# ---------------------------------------------------------------------------
# probe_file()
# ---------------------------------------------------------------------------


class TestProbeFile:
    """Tests for probe_file()."""

    def _mkvmerge_json(self, tracks: list[dict]) -> str:
        return json.dumps({"tracks": tracks})

    def test_parses_tracks_correctly(self, tmp_path: Path) -> None:
        raw_tracks = [
            {"id": 0, "type": "video", "properties": {"language": "und"}},
            {
                "id": 1,
                "type": "audio",
                "properties": {"language": "eng", "track_name": "Main Audio", "default_track": True},
            },
            {"id": 2, "type": "subtitles", "properties": {"language": "fre"}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)

        assert len(result) == 3
        assert result[0] == MkvTrack(id=0, type="video", language=None)  # "und" → None
        assert result[1] == MkvTrack(id=1, type="audio", language="eng", name="Main Audio", default_track=True)
        assert result[2] == MkvTrack(id=2, type="subtitles", language="fre")

    def test_parses_track_name_and_default_flag(self, tmp_path: Path) -> None:
        raw_tracks = [
            {
                "id": 0,
                "type": "audio",
                "properties": {"language": "eng", "track_name": "Commentary 1", "default_track": True},
            },
            {"id": 1, "type": "audio", "properties": {"language": "eng", "default_track": False}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].name == "Commentary 1"
        assert result[0].default_track is True
        assert result[1].name is None
        assert result[1].default_track is False

    def test_empty_track_name_normalised_to_none(self, tmp_path: Path) -> None:
        raw_tracks = [{"id": 0, "type": "audio", "properties": {"language": "eng", "track_name": ""}}]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].name is None

    def test_raises_on_nonzero_exit(self, tmp_path: Path) -> None:
        mkv = tmp_path / "bad.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="error!")
            with pytest.raises(RuntimeError, match="exit 2"):
                probe_file(MKVMERGE, mkv)

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        mkv = tmp_path / "bad.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
            with pytest.raises(RuntimeError, match="parse"):
                probe_file(MKVMERGE, mkv)

    def test_raises_runtime_error_on_timeout(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=MKVMERGE, timeout=60)),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            probe_file(MKVMERGE, mkv)

    def test_raises_runtime_error_on_os_error(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with (
            patch("subprocess.run", side_effect=OSError("permission denied")),
            pytest.raises(RuntimeError, match="Could not execute"),
        ):
            probe_file(MKVMERGE, mkv)

    def test_language_ietf_two_char_normalised_to_iso_639_2(self, tmp_path: Path) -> None:
        """I4: When 'language' is absent but 'language_ietf' is a 2-char code, it must be
        normalised to ISO 639-2 so language filters work correctly (e.g. 'en' → 'eng')."""
        raw_tracks = [
            {"id": 0, "type": "audio", "properties": {"language_ietf": "en"}},
            {"id": 1, "type": "audio", "properties": {"language_ietf": "fr"}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].language == "eng"
        assert result[1].language == "fre"

    def test_language_ietf_bcp47_with_subtag_normalised(self, tmp_path: Path) -> None:
        """I4: BCP-47 tags with subtags (e.g. 'en-US', 'pt-BR') must be normalised to ISO 639-2."""
        raw_tracks = [
            {"id": 0, "type": "audio", "properties": {"language_ietf": "en-US"}},
            {"id": 1, "type": "audio", "properties": {"language_ietf": "pt-BR"}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].language == "eng"
        assert result[1].language == "por"


# ---------------------------------------------------------------------------
# build_mkvmerge_command()
# ---------------------------------------------------------------------------


class TestBuildMkvmergeCommand:
    """Tests for build_mkvmerge_command() — pure function, no subprocess."""

    def test_returns_none_when_no_changes_needed(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=["eng"])
        assert _build_cmd(tracks, language=["eng"]) is None

    def test_returns_none_with_no_tracks_to_remove_and_no_metadata(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=[])
        assert _build_cmd(tracks) is None

    def test_drops_foreign_audio(self) -> None:
        tracks = _make_tracks(audio_langs=["eng", "fre", "ger"], sub_langs=[])
        cmd = _build_cmd(tracks, language=["eng"])
        assert cmd is not None
        assert "--audio-tracks" in cmd
        # Only track 1 (eng) should be kept
        idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[idx]
        assert "2" not in cmd[idx]
        assert "3" not in cmd[idx]

    def test_no_audio_safety_fallback_returns_none_and_warns(self) -> None:
        """Safety fallback: when NO audio tracks match, keep all — returns None and logs a warning."""
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=[])
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        assert result is None  # No other changes → nothing to do
        logger.warning.assert_called_once()
        assert "eng" in logger.warning.call_args[0][0]

    def test_keep_audio_overrides_language_filter(self) -> None:
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=[])
        result = _build_cmd(tracks, language=["eng"], keep_audio=True)
        assert result is None  # Nothing to do

    def test_drops_foreign_subtitles(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=["eng", "fre"])
        cmd = _build_cmd(tracks, language=["eng"])
        assert cmd is not None
        assert "--subtitle-tracks" in cmd

    def test_no_subtitles_safety_fallback_returns_none_and_warns(self) -> None:
        """Safety fallback: when NO subtitle tracks match, keep all — returns None and logs a warning."""
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        assert result is None  # No other changes → nothing to do
        logger.warning.assert_called_once()

    def test_subtitle_fallback_does_not_log_false_drop_when_audio_changes_present(self) -> None:
        """Regression: subtitle fallback + audio changes must not emit a 'Dropping subtitle track(s)' log.

        When no subtitle tracks match the language the safety fallback fires and all subtitles
        are kept.  Previously the summary log block used the pre-fallback snapshot
        (language_sub_drop_ids) without checking needs_sub_change, so it emitted a misleading
        'Dropping N subtitle track(s)' INFO message even though no subtitle filtering happened.
        """
        tracks = [
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="audio", language="fre"),  # dropped — language mismatch
            MkvTrack(id=3, type="subtitles", language="fre"),  # no eng sub → fallback keeps all
            MkvTrack(id=4, type="subtitles", language="ger"),  # no eng sub → fallback keeps all
        ]
        logger = MagicMock()
        cmd = _build_cmd(tracks, language=["eng"], logger=logger)
        # Audio change present → command is not None
        assert cmd is not None
        # Subtitle fallback fired → no --subtitle-tracks / --no-subtitles in command
        assert "--subtitle-tracks" not in cmd
        assert "--no-subtitles" not in cmd
        # No info log mentioning subtitle drops should have been emitted
        for call in logger.info.call_args_list:
            assert "subtitle" not in call.args[0].lower(), (
                f"Unexpected subtitle drop log when fallback should suppress it: {call.args[0]}"
            )
        # Warning about keeping all subtitles must have fired
        logger.warning.assert_called_once()

    def test_keep_subtitles_overrides_language_filter(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        result = _build_cmd(tracks, language=["eng"], keep_subtitles=True)
        assert result is None

    def test_multi_language_keeps_all_matching_tracks(self) -> None:
        """Multiple language codes: tracks in any listed language are retained."""
        tracks = _make_tracks(audio_langs=["eng", "fre", "ger"], sub_langs=["eng", "fre", "jpn"])
        cmd = _build_cmd(tracks, language=["eng", "fre"])
        assert cmd is not None
        # eng(id=1) and fre(id=2) audio kept; ger(id=3) dropped
        idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[idx]
        assert "2" in cmd[idx]
        assert "3" not in cmd[idx]
        # eng(id=4) and fre(id=5) subtitles kept; jpn(id=6) dropped
        idx = cmd.index("--subtitle-tracks") + 1
        assert "4" in cmd[idx]
        assert "5" in cmd[idx]
        assert "6" not in cmd[idx]

    def test_multi_language_no_drop_needed_returns_none(self) -> None:
        """When all tracks match one of the listed languages, no changes are needed."""
        tracks = _make_tracks(audio_langs=["eng", "fre"], sub_langs=["eng"])
        assert _build_cmd(tracks, language=["eng", "fre"]) is None

    def test_edit_metadata_title_included(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[])
        inp = Path("/media/My.Movie.mkv")
        cmd = _build_cmd(tracks, edit_metadata_title=True, input_path=inp)
        assert cmd is not None
        assert "--title" in cmd
        idx = cmd.index("--title") + 1
        assert cmd[idx] == "My.Movie"

    def test_delete_metadata_title_sets_empty_string(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[])
        cmd = _build_cmd(tracks, delete_metadata_title=True)
        assert cmd is not None
        assert "--title" in cmd
        idx = cmd.index("--title") + 1
        assert cmd[idx] == ""

    def test_command_starts_with_mkvmerge_and_output(self) -> None:
        # Use a mixed-language track list so there IS something to drop
        tracks = _make_tracks(audio_langs=["eng", "fre"], sub_langs=[])
        out = Path("/tmp/out.mkv")
        cmd = _build_cmd(tracks, output_path=out)
        assert cmd is not None
        assert cmd[0] == MKVMERGE
        assert cmd[1] == "-o"
        assert cmd[2] == str(out)

    def test_input_path_appended_at_end(self) -> None:
        # Use a mixed-language track list so there IS something to drop
        tracks = _make_tracks(audio_langs=["eng", "fre"], sub_langs=[])
        inp = Path("/media/test.mkv")
        cmd = _build_cmd(tracks, input_path=inp)
        assert cmd is not None
        assert cmd[-1] == str(inp)


# ---------------------------------------------------------------------------
# Audio commentary-only fallback tests
# ---------------------------------------------------------------------------


class TestAudioCommentaryFallback:
    """Verify the secondary audio safety fallback: all-commentary matches → keep all."""

    def test_all_matching_audio_commentary_triggers_fallback(self) -> None:
        """When the only language-matching audio track is commentary, keep all audio."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre"),  # foreign main
            MkvTrack(id=2, type="audio", language="eng", name="Director's Commentary"),
        ]
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        # No audio change applied (fallback fired); no subtitle work either
        assert result is None
        logger.warning.assert_called_once()
        assert "commentary" in logger.warning.call_args[0][0].lower()
        assert "eng" in logger.warning.call_args[0][0]

    def test_all_matching_audio_commentary_no_audio_filter_in_cmd(self) -> None:
        """When fallback fires alongside subtitle changes, no --audio-tracks in the command."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre"),  # foreign main
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
            MkvTrack(id=3, type="subtitles", language="eng"),
            MkvTrack(id=4, type="subtitles", language="fre"),  # to be dropped
        ]
        logger = MagicMock()
        cmd = _build_cmd(tracks, language=["eng"], logger=logger)
        # Subtitle trimming must still apply
        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        # Audio must NOT be filtered
        assert "--audio-tracks" not in cmd
        assert "--no-audio" not in cmd

    def test_mix_of_commentary_and_real_does_not_trigger_fallback(self) -> None:
        """When at least one non-commentary track matches, normal filtering applies."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre"),  # dropped
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),  # kept
            MkvTrack(id=3, type="audio", language="eng", name="Main"),  # kept
        ]
        logger = MagicMock()
        cmd = _build_cmd(tracks, language=["eng"], logger=logger)
        assert cmd is not None
        # French track dropped; both eng tracks kept — no fallback warning
        assert "--audio-tracks" in cmd
        idx = cmd.index("--audio-tracks") + 1
        assert "1" not in cmd[idx]
        assert "2" in cmd[idx]
        assert "3" in cmd[idx]
        logger.warning.assert_not_called()

    def test_no_tracks_to_drop_does_not_trigger_fallback(self) -> None:
        """If there is nothing to drop, the fallback must not fire."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary"),
        ]
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        assert result is None
        logger.warning.assert_not_called()

    def test_multi_language_commentary_only_triggers_fallback(self) -> None:
        """Multi-language filter: fallback fires when every match across all languages is commentary."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="ger"),  # dropped
            MkvTrack(id=2, type="audio", language="eng", name="Director Commentary"),
            MkvTrack(id=3, type="audio", language="fre", name="Commentary 2"),
        ]
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng", "fre"], logger=logger)
        assert result is None
        logger.warning.assert_called_once()

    def test_keep_audio_bypasses_fallback(self) -> None:
        """keep_audio=True means no tracks are dropped so fallback is irrelevant."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre"),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
        ]
        result = _build_cmd(tracks, language=["eng"], keep_audio=True)
        assert result is None  # Nothing to do; no warning


# ---------------------------------------------------------------------------
# Commentary default-track reassignment tests
# ---------------------------------------------------------------------------


class TestCommentaryDefaultTrack:
    """Tests for the commentary --default-track reassignment logic."""

    def test_audio_commentary_default_reassigned_to_non_commentary(self) -> None:
        """Commentary track with default=True is demoted; non-commentary promoted."""
        # tid 0: video, tid 1: eng commentary (default), tid 2: eng normal, tid 3: fre (dropped)
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="eng", name="Main Audio", default_track=False),
            MkvTrack(id=3, type="audio", language="fre"),
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "1:0" in cmd  # commentary tid demoted
        assert "2:1" in cmd  # non-commentary tid promoted

    def test_audio_commentary_no_default_no_flags_emitted(self) -> None:
        """Commentary track present but non-commentary track already default — no change needed."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=False),
            MkvTrack(id=2, type="audio", language="eng", name="Main Audio", default_track=True),
            MkvTrack(id=3, type="audio", language="fre"),
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        # Non-commentary already holds default — no reassignment needed
        assert "--default-track" not in cmd

    def test_audio_all_matching_commentary_triggers_fallback_not_default_demote(self) -> None:
        """When all matching audio is commentary, the new fallback fires (keep all audio).

        Old behaviour: filter audio, then demote the commentary default-track flag.
        New behaviour: skip audio filtering entirely — stripping the foreign track would
        leave only commentary audio, which is almost always wrong.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="fre"),  # would have been dropped
        ]
        logger = MagicMock()
        result = _build_cmd(tracks, logger=logger)
        # Fallback fires → no audio change → no other changes → None
        assert result is None
        # Warning must mention commentary and the language
        logger.warning.assert_called_once()
        assert "commentary" in logger.warning.call_args[0][0].lower()
        assert "eng" in logger.warning.call_args[0][0]

    def test_audio_not_removing_tracks_no_flags_emitted(self) -> None:
        """No audio tracks dropped — commentary default-track logic must not fire."""
        # All audio matches language, so audio_drop is empty
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="eng", name="Main Audio", default_track=False),
        ]
        # Nothing to remove → returns None
        result = _build_cmd(tracks)
        assert result is None

    def test_subtitle_commentary_default_reassigned_to_non_commentary(self) -> None:
        """Commentary subtitle with default=True is demoted; non-commentary promoted."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Commentary", default_track=True),
            MkvTrack(id=3, type="subtitles", language="eng", name="SDH", default_track=False),
            MkvTrack(id=4, type="subtitles", language="fre"),  # dropped
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "2:0" in cmd
        assert "3:1" in cmd

    def test_audio_no_source_default_promotes_non_commentary(self) -> None:
        """Source file has no default audio track — commentary kept → promote non-commentary.

        This is the real-world case seen in the screenshot: all tracks have
        default_track=False, but the non-commentary track should still be
        promoted to default when we are removing tracks.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary 1", default_track=False),
            MkvTrack(id=3, type="audio", language="eng", name="Commentary 2", default_track=False),
            MkvTrack(id=6, type="audio", language="eng", name="Eng", default_track=False),
            MkvTrack(id=7, type="audio", language="fre"),  # dropped
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "6:1" in cmd  # non-commentary promoted
        assert "2:0" not in cmd  # commentary not demoted (was never default)
        assert "3:0" not in cmd

    def test_subtitle_no_source_default_promotes_non_commentary(self) -> None:
        """Source file has no default subtitle track — commentary kept → promote non-commentary."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="English", default_track=False),
            MkvTrack(id=4, type="subtitles", language="eng", name="Commentary 1", default_track=False),
            MkvTrack(id=5, type="subtitles", language="eng", name="Commentary 2", default_track=False),
            MkvTrack(id=8, type="subtitles", language="fre"),  # dropped
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "2:1" in cmd  # English subtitle promoted
        assert "4:0" not in cmd
        assert "5:0" not in cmd

    def test_subtitle_commentary_no_default_no_flags_emitted(self) -> None:
        """Commentary subtitle present but non-commentary already default — no change needed."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Commentary", default_track=False),
            MkvTrack(id=3, type="subtitles", language="eng", name="SDH", default_track=True),
            MkvTrack(id=4, type="subtitles", language="fre"),  # dropped
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" not in cmd

    def test_subtitle_all_kept_commentary_warns_and_demotes_default(self) -> None:
        """All remaining subtitle tracks are commentary — demote default, log warning."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Director Commentary", default_track=True),
            MkvTrack(id=3, type="subtitles", language="fre"),  # dropped
        ]
        logger = MagicMock()
        cmd = _build_cmd(tracks, logger=logger)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "2:0" in cmd
        logger.warning.assert_called()

    def test_subtitle_not_removing_tracks_no_flags_emitted(self) -> None:
        """No subtitle tracks dropped — commentary default-track logic must not fire."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Commentary", default_track=True),
        ]
        result = _build_cmd(tracks)
        assert result is None

    def test_commentary_name_case_insensitive(self) -> None:
        """Commentary matching is case-insensitive; default-track reassignment applies."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Director's Commentary", default_track=True),
            MkvTrack(id=2, type="audio", language="eng", name="Main", default_track=False),
            MkvTrack(id=3, type="audio", language="fre"),
        ]
        cmd = _build_cmd(tracks)
        assert cmd is not None
        assert "--default-track" in cmd
        assert "1:0" in cmd
        assert "2:1" in cmd


# ---------------------------------------------------------------------------
# process_file()
# ---------------------------------------------------------------------------


class TestProcessFile:
    """Tests for process_file()."""

    def test_success_with_backup(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        out_placeholder = tmp_path / "out.mkv"
        cmd = _proc_cmd(mkv, out_placeholder)

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            # Write to the actual temp path (patched into cmd by process_file)
            out_path = Path(args[2])
            out_path.write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=_proc_logger())

        assert result is None
        assert mkv.read_bytes() == b"processed"
        backup = tmp_path / "movie.mkv.bak"
        assert backup.read_bytes() == b"original"

    def test_success_no_backup(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        out_placeholder = tmp_path / "out.mkv"
        cmd = _proc_cmd(mkv, out_placeholder)

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is None
        assert mkv.read_bytes() == b"processed"
        assert not (tmp_path / "movie.mkv.bak").exists()

    def test_exit_code_1_with_valid_output_succeeds_with_warning(self, tmp_path: Path) -> None:
        """mkvmerge exit 1 means 'completed with warnings' — output is still valid."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            # Structural validation probe (-J) must return 0; only the remux returns 1.
            if args[1] == "-J":
                return MagicMock(returncode=0, stdout="{}", stderr="")
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=1, stdout="", stderr="warning from mkvmerge")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        assert result is None
        assert mkv.read_bytes() == b"processed"
        logger.warning.assert_called_once()

    def test_exit_code_1_with_empty_output_fails(self, tmp_path: Path) -> None:
        """mkvmerge exit 1 with an empty/missing output is still a failure."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")  # Empty — not usable
            return MagicMock(returncode=1, stdout="", stderr="warning from mkvmerge")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is not None
        assert mkv.read_bytes() == b"original"  # Original untouched

    def test_exit_code_2_fails_cleanly_and_cleans_up_temp(self, tmp_path: Path) -> None:
        """mkvmerge exit ≥2 is a hard failure: returns False, logs an error, and removes the temp file."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"partial")
            return MagicMock(returncode=2, stdout="", stderr="fatal error")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is not None
        logger.error.assert_called_once()
        assert not list(tmp_path.glob("*.trimarr_tmp"))  # No leftover temp files

    def test_no_backup_uses_atomic_replace_not_delete_first(self, tmp_path: Path) -> None:
        """Verify no-backup mode does NOT delete the original before renaming.

        In the old (buggy) implementation, `file.unlink()` happened before
        `tmp.rename(file)`.  If the rename failed, the original was gone.
        The fix uses `tmp.replace(file)` (atomic overwrite).  We simulate a
        rename failure to confirm the original survives.
        """
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        call_count = 0

        def failing_replace(self_path: Path, target: Path) -> None:
            nonlocal call_count
            call_count += 1
            raise OSError("simulated rename failure")

        with patch("subprocess.run", side_effect=fake_run), patch.object(Path, "replace", failing_replace):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        # The process should have failed gracefully
        assert result is not None
        # The original MUST still exist — atomic replace never deletes first
        assert mkv.exists()
        assert mkv.read_bytes() == b"original"

    def test_backup_mode_restores_original_when_replace_fails(self, tmp_path: Path) -> None:
        """If tmp→original replace fails after original→.bak, rollback must restore original."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        replace_calls: list[str] = []

        _real_replace = Path.replace

        def selective_replace(self_path: Path, target: Path) -> None:
            replace_calls.append(str(self_path))
            # Allow original→.bak; fail on tmp→original
            if str(self_path).endswith(".trimarr_tmp"):
                raise OSError("simulated tmp→original failure")
            _real_replace(self_path, target)

        with patch("subprocess.run", side_effect=fake_run), patch.object(Path, "replace", selective_replace):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is not None
        # Original must be restored from backup
        assert mkv.exists()
        assert mkv.read_bytes() == b"original"
        # Backup must be cleaned up by the rollback
        backup = tmp_path / "movie.mkv.bak"
        assert not backup.exists()

    def test_backup_mode_logs_critical_when_rollback_also_fails(self, tmp_path: Path) -> None:
        """When both replace and rollback fail, a CRITICAL error must be logged."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        _real_replace = Path.replace

        def always_fail_replace(self_path: Path, target: Path) -> None:
            # Allow original→.bak only; fail everything else
            if str(target).endswith(".bak"):
                _real_replace(self_path, target)
            else:
                raise OSError("simulated failure")

        with patch("subprocess.run", side_effect=fake_run), patch.object(Path, "replace", always_fail_replace):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is not None
        assert logger.critical.call_count >= 1
        critical_calls = " ".join(str(c) for c in logger.critical.call_args_list)
        assert "backup" in critical_calls.lower()


# ---------------------------------------------------------------------------
# I1: Truncated output rejection
# ---------------------------------------------------------------------------


class TestTruncatedOutputRejection:
    """Verify that process_file rejects suspiciously small mkvmerge output."""

    def test_rejects_empty_output(self, tmp_path: Path) -> None:
        """Zero-byte output must be rejected."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is not None
        assert mkv.read_bytes() == b"A" * 10_000

    def test_rejects_empty_output_even_with_skip_size_check(self, tmp_path: Path) -> None:
        """Zero-byte output must still be rejected when skip_size_check=True.

        The zero-byte guard is unconditional — skip_size_check only bypasses the
        50% ratio heuristic, not the hard empty-file check.
        """
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger(), skip_size_check=True)

        assert result is not None
        assert mkv.read_bytes() == b"A" * 10_000

    def test_rejects_suspiciously_small_output(self, tmp_path: Path) -> None:
        """Output that is < 0.1 % of the source must be rejected."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 200_000)  # 200 KB input
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"X")  # 1 byte — well below 0.1 % of 200 KB
            return MagicMock(returncode=0, stdout="", stderr="")

        logger = _proc_logger()
        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        assert result is not None
        assert mkv.read_bytes() == b"A" * 200_000
        logger.error.assert_called()

    def test_skip_size_check_accepts_small_output(self, tmp_path: Path) -> None:
        """When skip_size_check=True, output below the 50 % threshold must be accepted."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 200_000)  # 200 KB input
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            if args[1] == "-J":
                return MagicMock(returncode=0, stdout="{}", stderr="")
            Path(args[2]).write_bytes(b"X")  # 1 byte — well below 50 % of 200 KB
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger(), skip_size_check=True)

        assert result is None

    def test_accepts_output_at_threshold(self, tmp_path: Path) -> None:
        """Output at or above the 50 % threshold must be accepted."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)  # 10 KB input → threshold = 5000 bytes (50 %)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        min_ok = max(1, 10_000 // 2)  # = 5000 bytes

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            if args[1] == "-J":
                return MagicMock(returncode=0, stdout="{}", stderr="")
            Path(args[2]).write_bytes(b"B" * min_ok)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is None

    def test_rejects_output_failing_structural_validation(self, tmp_path: Path) -> None:
        """Output passing size check but rejected by mkvmerge -J must raise CorruptOutputError.

        This halts ALL processing and preserves the temp file for inspection.
        """
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        min_ok = max(1, 10_000 // 2)  # 5000 bytes — passes size check

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            if args[1] == "-J":
                return MagicMock(returncode=1, stdout="", stderr="not a valid MKV container")
            Path(args[2]).write_bytes(b"B" * min_ok)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run), pytest.raises(CorruptOutputError) as exc_info:
            process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        exc = exc_info.value
        assert exc.probe_returncode == 1
        assert "not a valid MKV container" in exc.probe_output
        assert exc.output_size == min_ok
        assert exc.input_size == 10_000
        # Original must be completely untouched
        assert mkv.read_bytes() == b"A" * 10_000
        # Temp file must be PRESERVED on disk for operator inspection
        assert exc.tmp_path.exists()


# ---------------------------------------------------------------------------
# I7: None-language track filtering
# ---------------------------------------------------------------------------


class TestNoneLanguageTrackFiltering:
    """Verify that tracks with language=None are handled correctly."""

    def test_none_language_audio_is_dropped_when_filtering(self) -> None:
        """An audio track with language=None does not match any language code — must be dropped."""
        tracks = _make_tracks(audio_langs=["eng", None], sub_langs=[])
        cmd = _build_cmd(tracks, language=["eng"])
        assert cmd is not None
        # eng track (id=1) kept; None-language track (id=2) dropped
        idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[idx]
        assert "2" not in cmd[idx]

    def test_none_language_subtitle_is_dropped_when_filtering(self) -> None:
        """A subtitle track with language=None does not match — must be dropped."""
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[None, "eng"])
        cmd = _build_cmd(tracks, language=["eng"])
        assert cmd is not None
        # None-language sub (id=2) dropped; eng sub (id=3) kept
        idx = cmd.index("--subtitle-tracks") + 1
        assert "3" in cmd[idx]
        assert "2" not in cmd[idx]

    def test_all_none_language_audio_triggers_safety_fallback(self) -> None:
        """When every audio track has language=None and no track matches, fallback keeps all."""
        tracks = _make_tracks(audio_langs=[None, None], sub_langs=[])
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        # Safety fallback: keeps all audio — no other changes → returns None
        assert result is None
        logger.warning.assert_called_once()

    def test_all_none_language_subtitles_triggers_safety_fallback(self) -> None:
        """When every subtitle track has language=None and no track matches, fallback keeps all."""
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[None, None])
        logger = MagicMock()
        result = _build_cmd(tracks, language=["eng"], logger=logger)
        assert result is None
        logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# I5: Audio safety fallback fires while subtitles still need trimming
# ---------------------------------------------------------------------------


class TestFallbackWithSubtitleTrimming:
    """Verify the safety fallback keeps all audio while still trimming subtitles."""

    def test_audio_fallback_fires_subtitles_still_trimmed(self) -> None:
        """No audio matches language → keep all audio; foreign subtitle still dropped."""
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=["eng", "fre"])
        cmd = _build_cmd(tracks, language=["eng"], logger=MagicMock())
        # Subtitle trimming must still happen even though audio fallback fired
        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        sub_idx = cmd.index("--subtitle-tracks") + 1
        # eng subtitle (id=3) kept; fre subtitle (id=4) dropped
        assert "3" in cmd[sub_idx]
        assert "4" not in cmd[sub_idx]
        # Audio must NOT have a --audio-tracks filter (kept all via fallback)
        assert "--audio-tracks" not in cmd


# ---------------------------------------------------------------------------
# I9: OS-level failure modes in process_file
# ---------------------------------------------------------------------------


class TestProcessFileOsFailures:
    """Verify process_file handles OS-level failures cleanly."""

    def test_timeout_expired_returns_false_and_cleans_up(self, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired must cause False return and no leftover tmp files."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd, 3600)):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is not None
        assert mkv.read_bytes() == b"original"
        assert list(tmp_path.glob("*.trimarr_tmp")) == []

    def test_oserror_from_subprocess_returns_false_and_cleans_up(self, tmp_path: Path) -> None:
        """OSError from subprocess.run must cause False return and no leftover tmp files."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        with patch("subprocess.run", side_effect=OSError("disk full")):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is not None
        assert mkv.read_bytes() == b"original"
        assert list(tmp_path.glob("*.trimarr_tmp")) == []

    def test_tmp_file_cleaned_up_when_mkvmerge_raises_mid_write(self, tmp_path: Path) -> None:
        """Temp file written by mkvmerge must be deleted even if subprocess raises unexpectedly."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run_with_partial_write(args: list[str], **kwargs: object) -> None:
            Path(args[2]).write_bytes(b"partial output")
            raise RuntimeError("unexpected crash after partial write")

        with patch("subprocess.run", side_effect=fake_run_with_partial_write):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is not None
        assert mkv.read_bytes() == b"original"
        assert list(tmp_path.glob("*.trimarr_tmp")) == []

    def test_mkstemp_failure_returns_false(self, tmp_path: Path) -> None:
        """If tempfile.mkstemp raises, process_file must return False gracefully."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        with patch("trimarr.processor.tempfile.mkstemp", side_effect=OSError("no space left on device")):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is not None
        assert mkv.read_bytes() == b"original"

    def test_cleanup_oserror_in_finally_is_suppressed(self, tmp_path: Path) -> None:
        """OSError from temp-file unlink in finally must be swallowed (logged as warning, not propagated)."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")  # Empty output — fails size check before -J
            return MagicMock(returncode=0, stdout="", stderr="")

        logger = _proc_logger()
        with (
            patch("subprocess.run", side_effect=fake_run),
            patch.object(Path, "unlink", side_effect=OSError("permission denied")),
        ):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        # Must return False (not raise), even though cleanup also failed
        assert result is not None
        assert mkv.read_bytes() == b"original"
        # Warning must have been logged about the cleanup failure
        logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# MkvTrack.channels field
# ---------------------------------------------------------------------------


class TestMkvTrackChannels:
    """MkvTrack must carry a channels field for audio channel count."""

    def test_channels_included_in_equality(self) -> None:
        a = MkvTrack(id=0, type="audio", language="eng", channels=8)
        b = MkvTrack(id=0, type="audio", language="eng", channels=6)
        assert a != b

    def test_audio_channels_in_probe_output(self, tmp_path: Path) -> None:
        """probe_file() must populate channels for audio tracks from audio_channels property."""
        raw_tracks = [
            {"id": 0, "type": "audio", "properties": {"language": "eng", "audio_channels": 8}},
            {"id": 1, "type": "subtitles", "properties": {"language": "eng"}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"tracks": raw_tracks}),
                stderr="",
            )
            result = probe_file(MKVMERGE, mkv)
        assert result[0].channels == 8  # audio track
        assert result[1].channels is None  # subtitle track — no channels

    def test_missing_audio_channels_property_gives_none(self, tmp_path: Path) -> None:
        """Audio track without audio_channels in JSON results in channels=None."""
        raw_tracks = [{"id": 0, "type": "audio", "properties": {"language": "eng"}}]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"tracks": raw_tracks}),
                stderr="",
            )
            result = probe_file(MKVMERGE, mkv)
        assert result[0].channels is None


# ---------------------------------------------------------------------------
# Channel filtering in build_mkvmerge_command()
# ---------------------------------------------------------------------------


class TestChannelFiltering:
    """Verify strip_lower_channels behaviour in build_mkvmerge_command()."""

    def test_drops_tracks_below_max_channel_count(self) -> None:
        """8ch tracks kept; 6ch and 2ch tracks dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=8),
            MkvTrack(id=3, type="audio", language="eng", channels=6),
            MkvTrack(id=4, type="audio", language="eng", channels=2),
        ]
        cmd = _build_cmd(tracks, strip_lower_channels=True)
        assert cmd is not None
        idx = cmd.index("--audio-tracks") + 1
        kept = cmd[idx].split(",")
        assert "1" in kept
        assert "2" in kept
        assert "3" not in kept
        assert "4" not in kept

    def test_all_equal_channels_returns_none(self) -> None:
        """When all tracks share the same channel count, nothing is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=6),
            MkvTrack(id=2, type="audio", language="eng", channels=6),
        ]
        assert _build_cmd(tracks, strip_lower_channels=True) is None

    def test_all_none_channels_returns_none(self) -> None:
        """When channel count is unknown for all tracks, nothing is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=None),
            MkvTrack(id=2, type="audio", language="eng", channels=None),
        ]
        assert _build_cmd(tracks, strip_lower_channels=True) is None

    def test_flag_off_does_not_filter_channels(self) -> None:
        """strip_lower_channels=False must leave lower-channel tracks untouched."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=2),
        ]
        assert _build_cmd(tracks, strip_lower_channels=False) is None

    def test_keep_audio_bypasses_channel_filtering(self) -> None:
        """--keep-audio disables channel filtering entirely."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=2),
        ]
        assert _build_cmd(tracks, keep_audio=True, strip_lower_channels=True) is None

    def test_unknown_channel_tracks_preserved_alongside_known(self) -> None:
        """Tracks with channels=None are never dropped — only known-inferior tracks go."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=None),  # unknown → keep
            MkvTrack(id=3, type="audio", language="eng", channels=2),  # inferior → drop
        ]
        cmd = _build_cmd(tracks, strip_lower_channels=True)
        assert cmd is not None
        idx = cmd.index("--audio-tracks") + 1
        kept = cmd[idx].split(",")
        assert "1" in kept
        assert "2" in kept
        assert "3" not in kept

    def test_single_audio_track_not_dropped(self) -> None:
        """A single surviving audio track is always the max — must never be dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=2),
        ]
        assert _build_cmd(tracks, strip_lower_channels=True) is None

    def test_runs_after_language_filter(self) -> None:
        """Channel filtering applies to tracks surviving the language filter, not all tracks."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre", channels=6),  # language-dropped first
            MkvTrack(id=2, type="audio", language="eng", channels=8),  # kept
            MkvTrack(id=3, type="audio", language="eng", channels=2),  # channel-dropped
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)
        assert cmd is not None
        idx = cmd.index("--audio-tracks") + 1
        kept = cmd[idx].split(",")
        assert "2" in kept
        assert "1" not in kept
        assert "3" not in kept

    def test_logs_info_when_dropping_lower_channel_tracks(self) -> None:
        """An INFO log must be emitted for each batch of dropped lower-channel tracks."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=2),
        ]
        logger = MagicMock()
        _build_cmd(tracks, logger=logger, strip_lower_channels=True)
        messages = [c.args[0] for c in logger.info.call_args_list if c.args]
        assert any("channel" in m.lower() for m in messages)


# ---------------------------------------------------------------------------
# Multi-language + channel strip (I1 regression)
# ---------------------------------------------------------------------------


class TestChannelFilteringMultiLanguage:
    """strip_lower_channels must not drop tracks from other language groups."""

    def test_lower_channel_track_in_other_language_is_not_dropped(self) -> None:
        """English 8ch + French 2ch, language=[eng,fre] → French must NOT be dropped.

        The French 2ch track was explicitly requested via --language fre; dropping
        it because English happens to have more channels is wrong.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="fre", channels=2),
        ]
        # Both eng and fre are in language list; fre 2ch must survive
        cmd = _build_cmd(tracks, language=["eng", "fre"], strip_lower_channels=True)
        # No tracks to drop → should return None (no change needed)
        assert cmd is None, "French 2ch audio was incorrectly dropped even though 'fre' is in --language list"

    def test_lower_channel_within_same_language_is_still_dropped(self) -> None:
        """English 8ch + English 2ch, language=[eng] → 2ch track dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="eng", channels=2),
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)
        assert cmd is not None
        idx = cmd.index("--audio-tracks") + 1
        kept = cmd[idx].split(",")
        assert "1" in kept
        assert "2" not in kept

    def test_each_language_group_keeps_its_own_max_channel_track(self) -> None:
        """English 8ch + French 6ch + French 2ch, language=[eng,fre].

        Within the French group the 2ch track should be dropped; English 8ch
        must not affect French's own max (6ch).
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=8),
            MkvTrack(id=2, type="audio", language="fre", channels=6),
            MkvTrack(id=3, type="audio", language="fre", channels=2),
        ]
        cmd = _build_cmd(tracks, language=["eng", "fre"], strip_lower_channels=True)
        assert cmd is not None
        idx = cmd.index("--audio-tracks") + 1
        kept = cmd[idx].split(",")
        assert "1" in kept  # eng 8ch → kept
        assert "2" in kept  # fre 6ch (max for fre group) → kept
        assert "3" not in kept  # fre 2ch (below fre max) → dropped


# ---------------------------------------------------------------------------
# Commentary fallback + channel strip interaction (I6)
# ---------------------------------------------------------------------------


class TestCommentaryFallbackChannelStripInteraction:
    """Channel-strip must behave correctly when commentary fallback has fired."""

    def test_commentary_fallback_then_no_channel_drop(self) -> None:
        """When commentary fallback fires, channel-strip is skipped entirely.

        Scenario: eng commentary 6ch + fre main 8ch, language=[eng]
          Language filter → eng commentary (6ch) kept, fre main (8ch) dropped
          Fallback fires  → audio_drop cleared, audio_fallback_fired=True
          Channel-strip   → SKIPPED (fallback active)
          Expected        → no audio change, cmd is None
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary", channels=6),
            MkvTrack(id=2, type="audio", language="fre", channels=8),
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)
        assert cmd is None  # No changes needed (no sub drops, no metadata changes)

    def test_commentary_fallback_with_channel_strip_preserves_foreign_track(self) -> None:
        """C1 regression: two eng commentary tracks (8ch + 2ch) plus one fre main track.

        When the commentary fallback fires, channel-strip must be skipped entirely.
        There is no benefit in pruning lower-channel commentary tracks when we are
        already in 'keep everything' fallback mode, and doing so would silently drop
        the fre main track (which was never in audio_keep).

        Scenario: eng_commentary_8ch + eng_commentary_2ch + fre_main_6ch, language=[eng]
          Language filter  → audio_keep=[1,2], audio_drop=[3]
          Fallback fires   → audio_drop=[], audio_fallback_fired=True
          Channel-strip    → SKIPPED (fallback active)
          Expected         → no audio change (all tracks kept), cmd is None
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary", channels=8),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary", channels=2),
            MkvTrack(id=3, type="audio", language="fre", channels=6),
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)
        # Fallback fires → channel-strip skipped → audio_drop empty → no change needed
        assert cmd is None

    def test_channel_strip_never_drops_main_audio_in_favour_of_commentary(self) -> None:
        """Regression: commentary track with MORE channels than main audio must not
        cause the main audio track to be dropped.

        Real-world case: Across 110th Street (1972) — eng FLAC 1.0 (1ch main) +
        eng commentary (2ch) + non-eng track, with --strip-lower-channels.

        Without the fix, max_ch=2 caused the 1ch main audio to be dropped,
        leaving only the commentary track in the output.

        With the fix, commentary tracks are excluded from the max-channel
        calculation, so max_ch=1 (from non-commentary tracks only) and the
        main audio is retained.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None, channels=1),  # main FLAC 1.0
            MkvTrack(id=2, type="audio", language="eng", name="Commentary", channels=2),
            MkvTrack(id=3, type="audio", language="fre", channels=6),
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)

        # Non-eng track must be dropped, eng main + commentary both kept.
        assert cmd is not None
        assert "--audio-tracks" in cmd
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]  # main audio kept
        assert "2" in cmd[audio_idx]  # commentary kept
        assert "3" not in cmd[audio_idx]  # non-eng dropped

    def test_channel_strip_still_drops_lower_non_commentary_tracks(self) -> None:
        """Commentary exclusion must not disable stripping of lower-channel main tracks.

        When multiple non-commentary tracks exist at different channel counts,
        the lower-channel non-commentary tracks must still be dropped.
        Commentary tracks must be preserved regardless of channel count.

        Scenario: eng 7.1 main + eng 5.1 secondary + eng 2ch commentary + fre 6ch
        Expected: 5.1 secondary dropped (5 < 7), commentary kept, fre dropped.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None, channels=8),  # 7.1
            MkvTrack(id=2, type="audio", language="eng", name=None, channels=6),  # 5.1
            MkvTrack(id=3, type="audio", language="eng", name="Commentary", channels=2),
            MkvTrack(id=4, type="audio", language="fre", channels=6),
        ]
        cmd = _build_cmd(tracks, language=["eng"], strip_lower_channels=True)

        assert cmd is not None
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]  # 7.1 kept
        assert "2" not in cmd[audio_idx]  # 5.1 dropped (lower than 7.1)
        assert "3" in cmd[audio_idx]  # commentary kept (exempt from strip)
        assert "4" not in cmd[audio_idx]  # fre dropped


# ---------------------------------------------------------------------------
# TestStripCommentary – strip_commentary flag behaviour
# ---------------------------------------------------------------------------


class TestStripCommentary:
    """Verify --strip-commentary behaviour in build_mkvmerge_command()."""

    # ------------------------------------------------------------------
    # Happy-path: tracks ARE dropped
    # ------------------------------------------------------------------

    def test_audio_commentary_track_is_dropped(self) -> None:
        """An audio track named 'Commentary' is removed when strip_commentary=True."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True)

        assert cmd is not None
        assert "--audio-tracks" in cmd
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]
        assert "2" not in cmd[audio_idx]

    def test_subtitle_commentary_track_is_dropped(self) -> None:
        """A subtitle track named 'Commentary' is removed when strip_commentary=True."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name=None),
            MkvTrack(id=3, type="subtitles", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True)

        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        sub_idx = cmd.index("--subtitle-tracks") + 1
        assert "2" in cmd[sub_idx]
        assert "3" not in cmd[sub_idx]

    def test_multiple_commentary_tracks_all_dropped(self) -> None:
        """Multiple commentary audio tracks are all removed in a single pass."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Director Commentary"),
            MkvTrack(id=3, type="audio", language="eng", name="Cast Commentary"),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True)

        assert cmd is not None
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]
        assert "2" not in cmd[audio_idx]
        assert "3" not in cmd[audio_idx]

    def test_commentary_match_is_case_insensitive(self) -> None:
        """'COMMENTARY', 'Commentary', 'commentary' all match."""
        for name in ("COMMENTARY", "Commentary", "commentary", "Audio Commentary Track"):
            tracks = [
                MkvTrack(id=0, type="video", language=None),
                MkvTrack(id=1, type="audio", language="eng", name=None),
                MkvTrack(id=2, type="audio", language="eng", name=name),
            ]
            cmd = _build_cmd(tracks, strip_commentary=True)
            assert cmd is not None, f"Expected change for name={name!r}"
            audio_idx = cmd.index("--audio-tracks") + 1
            assert "2" not in cmd[audio_idx], f"Track with name={name!r} should be dropped"

    def test_info_logged_when_audio_commentary_dropped(self) -> None:
        """An INFO message is emitted when commentary audio tracks are dropped."""
        logger = MagicMock()
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
        ]
        _build_cmd(tracks, logger=logger, strip_commentary=True)

        info_calls = [str(c) for c in logger.info.call_args_list]
        assert any("commentary" in msg.lower() for msg in info_calls), (
            "Expected INFO log about dropping commentary audio track"
        )

    def test_info_logged_when_subtitle_commentary_dropped(self) -> None:
        """An INFO message is emitted when commentary subtitle tracks are dropped."""
        logger = MagicMock()
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name=None),
            MkvTrack(id=3, type="subtitles", language="eng", name="Commentary"),
        ]
        _build_cmd(tracks, logger=logger, strip_commentary=True)

        info_calls = [str(c) for c in logger.info.call_args_list]
        assert any("commentary" in msg.lower() for msg in info_calls), (
            "Expected INFO log about dropping commentary subtitle track"
        )

    # ------------------------------------------------------------------
    # Default disabled
    # ------------------------------------------------------------------

    def test_strip_commentary_disabled_keeps_commentary_tracks(self) -> None:
        """strip_commentary=False (default) must leave commentary tracks untouched."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
        ]
        # No language drops, no changes -> should return None
        assert _build_cmd(tracks, strip_commentary=False) is None

    def test_no_commentary_found_produces_no_log(self) -> None:
        """When no commentary tracks exist, no INFO or WARNING is emitted."""
        logger = MagicMock()
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="subtitles", language="eng", name=None),
        ]
        _build_cmd(tracks, logger=logger, strip_commentary=True)

        # No INFO/WARNING about commentary should be emitted when none found
        for call in logger.info.call_args_list + logger.warning.call_args_list:
            assert "commentary" not in str(call).lower()

    # ------------------------------------------------------------------
    # Safety fallback: keep_audio / keep_subtitles overrides
    # ------------------------------------------------------------------

    def test_keep_audio_skips_strip_commentary_for_audio(self) -> None:
        """--keep-audio prevents commentary audio from being stripped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
            MkvTrack(id=3, type="subtitles", language="eng", name=None),
            MkvTrack(id=4, type="subtitles", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, keep_audio=True, strip_commentary=True)
        # Subtitle commentary still dropped, audio untouched (keep_audio=True)
        assert cmd is not None
        assert "--audio-tracks" not in cmd  # keep_audio -> no audio manipulation
        assert "--subtitle-tracks" in cmd
        sub_idx = cmd.index("--subtitle-tracks") + 1
        assert "3" in cmd[sub_idx]
        assert "4" not in cmd[sub_idx]

    def test_keep_subtitles_skips_strip_commentary_for_subtitles(self) -> None:
        """--keep-subtitles prevents commentary subtitle tracks from being stripped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary"),
            MkvTrack(id=3, type="subtitles", language="eng", name=None),
            MkvTrack(id=4, type="subtitles", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, keep_subtitles=True, strip_commentary=True)
        # Audio commentary still dropped, subtitles untouched (keep_subtitles=True)
        assert cmd is not None
        assert "--audio-tracks" in cmd
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]
        assert "2" not in cmd[audio_idx]
        assert "--subtitle-tracks" not in cmd  # keep_subtitles -> no sub manipulation

    # ------------------------------------------------------------------
    # Safety fallback: language fallbacks take priority
    # ------------------------------------------------------------------

    def test_audio_fallback_fired_skips_strip_commentary(self) -> None:
        """When the audio language fallback fires, strip_commentary must not apply.

        If there are no matching-language audio tracks, we already keep everything.
        Adding a commentary strip on top would silently drop audio in a fallback scenario.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="fre", name="Commentary"),
            MkvTrack(id=2, type="audio", language="fre", name=None),
        ]
        # Language=eng -> no eng tracks -> audio fallback fires -> strip_commentary skipped
        cmd = _build_cmd(tracks, language=["eng"], strip_commentary=True)
        assert cmd is None  # fallback kept all audio, no sub changes

    def test_subtitle_fallback_fired_skips_strip_commentary_for_subs(self) -> None:
        """When the subtitle language fallback fires, strip_commentary must not apply to subs."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="subtitles", language="fre", name="Commentary"),
            MkvTrack(id=3, type="subtitles", language="fre", name=None),
        ]
        # Eng audio fine; no eng subs -> subtitle fallback fires -> sub strip skipped
        cmd = _build_cmd(tracks, language=["eng"], strip_commentary=True)
        assert cmd is None  # fallback kept all subs, no audio changes needed

    def test_secondary_audio_fallback_fired_skips_strip_commentary(self) -> None:
        """Secondary fallback (all matching audio are commentary) blocks strip_commentary.

        Scenario: only eng track is a commentary; non-eng main track exists.
        Secondary fallback fires to keep the non-eng main audio.
        strip_commentary must not then strip audio.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary"),
            MkvTrack(id=2, type="audio", language="fre", name=None),
        ]
        # Secondary fallback fires -> audio_fallback_fired=True -> strip_commentary skipped
        cmd = _build_cmd(tracks, language=["eng"], strip_commentary=True)
        assert cmd is None  # all audio kept

    # ------------------------------------------------------------------
    # Safety fallback: stripping would leave zero tracks
    # ------------------------------------------------------------------

    def test_all_audio_commentary_keeps_all_audio_and_warns(self) -> None:
        """Final gate: if stripping would leave zero audio, keep all and warn.

        A silent file is never acceptable, so audio stripping is skipped when
        ALL surviving audio tracks are commentary.
        """
        logger = MagicMock()
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, logger=logger, strip_commentary=True)
        assert cmd is None  # commentary audio kept; no change

        warning_calls = [str(c) for c in logger.warning.call_args_list]
        assert any("commentary" in msg.lower() for msg in warning_calls), "Expected WARNING about all-commentary audio"

    def test_all_subtitles_commentary_strips_all(self) -> None:
        """Subtitle-free output is acceptable — all commentary subtitles ARE stripped.

        Unlike audio, there is no final gate for subtitles.
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None),
            MkvTrack(id=2, type="subtitles", language="eng", name="Commentary"),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True)
        assert cmd is not None
        assert "--no-subtitles" in cmd

    # ------------------------------------------------------------------
    # Interaction with --strip-lower-channels
    # ------------------------------------------------------------------

    def test_strip_commentary_before_strip_lower_channels(self) -> None:
        """Commentary tracks are removed before strip-lower-channels runs.

        strip-commentary must run first so that a high-channel commentary track
        does not inflate the max-channel calculation and cause main audio to be dropped.

        Scenario: eng main (6ch) + eng commentary (8ch) + fre (4ch)
          Language filter  -> audio_keep=[1,2], audio_drop=[3]
          strip-commentary -> audio_keep=[1], commentary_drop={2}
          strip-lower-channels -> max=6 (only main), nothing else to drop
          Expected         -> only eng main (6ch) kept
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None, channels=6),
            MkvTrack(id=2, type="audio", language="eng", name="Commentary", channels=8),
            MkvTrack(id=3, type="audio", language="fre", channels=4),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True, strip_lower_channels=True)

        assert cmd is not None
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]  # eng main kept
        assert "2" not in cmd[audio_idx]  # commentary dropped by strip-commentary
        assert "3" not in cmd[audio_idx]  # fre dropped by language filter

    def test_strip_commentary_combined_with_language_and_channel_strip(self) -> None:
        """Full pipeline: language filter + strip-commentary + strip-lower-channels.

        Scenario: eng 7.1 main + eng 2ch secondary + eng commentary (6ch) + fre 6ch + fre commentary
          Language filter  -> audio_keep=[1,2,3], audio_drop=[4,5]
          strip-commentary -> removes eng commentary (3)
          strip-lower-channels -> max=8 among non-commentary; drops 2ch secondary (2)
          Expected         -> only eng 7.1 main (1) kept
        """
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name=None, channels=8),  # 7.1
            MkvTrack(id=2, type="audio", language="eng", name=None, channels=2),  # stereo
            MkvTrack(id=3, type="audio", language="eng", name="Commentary", channels=6),
            MkvTrack(id=4, type="audio", language="fre", channels=6),
            MkvTrack(id=5, type="audio", language="fre", name="Commentary", channels=2),
        ]
        cmd = _build_cmd(tracks, strip_commentary=True, strip_lower_channels=True)

        assert cmd is not None
        audio_idx = cmd.index("--audio-tracks") + 1
        assert "1" in cmd[audio_idx]  # eng 7.1 kept
        assert "2" not in cmd[audio_idx]  # eng stereo dropped (lower channels)
        assert "3" not in cmd[audio_idx]  # eng commentary dropped
        assert "4" not in cmd[audio_idx]  # fre dropped by language
        assert "5" not in cmd[audio_idx]  # fre commentary dropped by language


# ---------------------------------------------------------------------------
# _spinner
# ---------------------------------------------------------------------------


class TestSpinner:
    """Tests for the _spinner context manager."""

    def test_noop_when_not_a_tty(self) -> None:
        """When stderr is not a TTY, _spinner is a pure no-op — no thread is started."""

        class FakeStderr:
            def isatty(self) -> bool:
                return False

        with patch("trimarr.processor.sys.stderr", FakeStderr()), _spinner("testing"):
            pass  # must not raise

    def test_tty_branch_starts_thread_and_cleans_up(self) -> None:
        """When stderr is a TTY, the spinner writes to stderr and emits a cleanup \\r line."""

        class FakeTTY:
            def __init__(self) -> None:
                self.written: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, s: str) -> None:
                self.written.append(s)

            def flush(self) -> None:
                pass

        fake_stderr = FakeTTY()
        with (
            patch("trimarr.processor.sys.stderr", fake_stderr),
            patch("trimarr.processor.time.sleep"),  # instant — no real sleeping
            _spinner("working..."),
        ):
            pass

        # thread.join() was called, so all writes are complete
        assert any("\r" in s for s in fake_stderr.written), "Expected at least one \\r write; got: " + repr(
            fake_stderr.written
        )


# ---------------------------------------------------------------------------
# Missing output file (process_file)
# ---------------------------------------------------------------------------


class TestMissingOutputFile:
    """Verify that process_file handles mkvmerge producing no output file."""

    def test_returns_error_when_output_file_absent_after_exit_0(self, tmp_path: Path) -> None:
        """If mkvmerge exits 0 but writes no output file, process_file must return an error string."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            # Simulate mkvmerge exiting successfully but not creating the output
            Path(args[2]).unlink(missing_ok=True)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        assert result == "mkvmerge produced no output file"
        assert mkv.read_bytes() == b"A" * 10_000
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# Branch coverage: probe_file non-standard language passthrough
# ---------------------------------------------------------------------------


class TestNonStandardLanguageCode:
    """Tracks with language tags that are neither 2-char nor 3-char pass through unchanged."""

    def test_single_char_base_bcp47_language_passed_through(self, tmp_path: Path) -> None:
        """A language tag with a 1-char base (e.g. 'x-private') is returned verbatim."""
        raw_tracks = [{"id": 0, "type": "audio", "properties": {"language_ietf": "x-private"}}]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"tracks": raw_tracks}), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].language == "x-private"

    def test_four_char_language_code_passed_through(self, tmp_path: Path) -> None:
        """A 4-character language code that doesn't map 2-char or 3-char is returned as-is."""
        raw_tracks = [{"id": 0, "type": "audio", "properties": {"language": "abcd"}}]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"tracks": raw_tracks}), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].language == "abcd"


# ---------------------------------------------------------------------------
# Branch coverage: _apply_strip_lower_channels with empty audio_keep
# ---------------------------------------------------------------------------


class TestStripLowerChannelsNoAudio:
    """strip_lower_channels=True with no audio tracks must be a no-op."""

    def test_subtitle_only_file_not_affected_by_strip_lower_channels(self) -> None:
        """When there are no audio tracks, strip_lower_channels guard exits early."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="subtitles", language="eng"),
            MkvTrack(id=2, type="subtitles", language="fre"),
        ]
        cmd = _build_cmd(tracks, strip_lower_channels=True)
        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        assert "--audio-tracks" not in cmd


# ---------------------------------------------------------------------------
# Branch coverage: _default_flags_for_commentary_tracks else-branch false default
# ---------------------------------------------------------------------------


class TestCommentaryDefaultTrackFalseInElse:
    """All remaining tracks are commentary, none has default_track=True — warning only, no flags."""

    def test_all_subtitle_commentary_no_default_warns_but_no_flag_emitted(self) -> None:
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Director Commentary", default_track=False),
            MkvTrack(id=3, type="subtitles", language="fre"),
        ]
        logger = MagicMock()
        cmd = _build_cmd(tracks, logger=logger)
        assert cmd is not None
        assert "--default-track" not in cmd
        logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Branch coverage: process_file empty stderr on failure
# ---------------------------------------------------------------------------


class TestProcessFileEmptyStderr:
    """mkvmerge exit >=2 with empty stderr must still return a reason string."""

    def test_exit_code_2_empty_stderr_returns_reason_without_colon(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        logger = _proc_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"partial")
            return MagicMock(returncode=2, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is not None
        assert "exit 2" in result
        assert not result.endswith(": ")  # no trailing colon when stderr empty
        logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# Branch coverage: build_mkvmerge_command needs_audio_change with empty audio_keep
# ---------------------------------------------------------------------------


class TestBuildMkvmergeCommandAudioDropNoKeep:
    """When audio_drop is non-empty but audio_keep is empty (fallback fired), no --audio-tracks arg."""

    def test_metadata_only_change_triggers_command_without_audio_or_sub_args(self) -> None:
        """edit_metadata_title=True with no track changes produces a command with only --title."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
        ]
        inp = Path("/media/Movie.mkv")
        cmd = _build_cmd(tracks, edit_metadata_title=True, input_path=inp)
        assert cmd is not None
        assert "--title" in cmd
        assert "--audio-tracks" not in cmd
        assert "--subtitle-tracks" not in cmd

    def test_needs_metadata_change_true_via_delete_title(self) -> None:
        """delete_metadata_title=True also sets needs_metadata_change."""
        tracks = [MkvTrack(id=0, type="video", language=None)]
        cmd = _build_cmd(tracks, delete_metadata_title=True)
        assert cmd is not None
        assert "--title" in cmd
        idx = cmd.index("--title") + 1
        assert cmd[idx] == ""


# ---------------------------------------------------------------------------
# Branch coverage: _compute_channel_drops_per_group all-unknown channels
# ---------------------------------------------------------------------------


class TestComputeChannelDropsAllUnknown:
    """When all non-commentary tracks have channels=None, no drops are computed."""

    def test_strip_lower_channels_all_unknown_no_drop(self) -> None:
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=None),
            MkvTrack(id=2, type="audio", language="eng", channels=None),
        ]
        cmd = _build_cmd(tracks, strip_lower_channels=True)
        assert cmd is None

    def test_strip_lower_channels_all_equal_no_drop(self) -> None:
        """All non-commentary tracks at same channel count → no drop needed."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", channels=6),
            MkvTrack(id=2, type="audio", language="eng", channels=6),
        ]
        cmd = _build_cmd(tracks, strip_lower_channels=True)
        assert cmd is None
