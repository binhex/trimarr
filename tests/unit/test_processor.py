"""Unit tests for core.processor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.processor import MkvTrack, build_mkvmerge_command, probe_file, process_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENG: list[str] = ["eng"]
FRE = "fre"
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
    language: list[str] = ENG,
    keep_audio: bool = False,
    keep_subtitles: bool = False,
    edit_metadata_title: bool = False,
    delete_metadata_title: bool = False,
    input_path: Path | None = None,
    output_path: Path | None = None,
    logger: MagicMock | None = None,
    strip_lower_channels: bool = False,
) -> list[str] | None:
    """Thin wrapper around build_mkvmerge_command with test-friendly defaults."""
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
        logger=logger,
        strip_lower_channels=strip_lower_channels,
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
        """Commentary matching is case-insensitive (e.g. 'COMMENTARY', 'Commentary 2')."""
        for name in ("COMMENTARY", "commentary", "Director's Commentary", "Commentary 2"):
            tracks = [
                MkvTrack(id=0, type="video", language=None),
                MkvTrack(id=1, type="audio", language="eng", name=name, default_track=True),
                MkvTrack(id=2, type="audio", language="eng", name="Main", default_track=False),
                MkvTrack(id=3, type="audio", language="fre"),
            ]
            cmd = _build_cmd(tracks)
            assert cmd is not None, f"Expected command for name={name!r}"
            assert "--default-track" in cmd, f"Expected flags for name={name!r}"
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

        assert result is True
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

        assert result is True
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

        assert result is True
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

        assert result is False
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

        assert result is False
        logger.error.assert_called_once()
        assert not list(tmp_path.glob("*.trimarr_tmp"))  # No leftover temp files

    def test_empty_output_treated_as_failure(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")  # Empty file
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=_proc_logger())

        assert result is False
        assert mkv.read_bytes() == b"original"

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
        assert result is False
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

        assert result is False
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

        assert result is False
        error_calls = " ".join(str(c) for c in logger.error.call_args_list)
        assert "CRITICAL" in error_calls


# ---------------------------------------------------------------------------
# I1: Truncated output rejection
# ---------------------------------------------------------------------------


class TestTruncatedOutputRejection:
    """Verify that process_file rejects suspiciously small mkvmerge output."""

    def test_rejects_empty_output(self, tmp_path: Path) -> None:
        """Zero-byte output must be rejected and return False."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is False
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

        assert result is False
        assert mkv.read_bytes() == b"A" * 200_000
        logger.error.assert_called()

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

        assert result is True

    def test_rejects_output_failing_structural_validation(self, tmp_path: Path) -> None:
        """Output passing size check but rejected by mkvmerge -J must be treated as corrupt."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 10_000)
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")
        min_ok = max(1, 10_000 // 2)  # 5000 bytes — passes size check

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            if args[1] == "-J":
                return MagicMock(returncode=1, stdout="", stderr="not a valid MKV container")
            Path(args[2]).write_bytes(b"B" * min_ok)
            return MagicMock(returncode=0, stdout="", stderr="")

        logger = _proc_logger()
        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=logger)

        assert result is False
        assert mkv.read_bytes() == b"A" * 10_000
        logger.error.assert_called()


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

        assert result is False
        assert mkv.read_bytes() == b"original"
        assert list(tmp_path.glob("*.trimarr_tmp")) == []

    def test_oserror_from_subprocess_returns_false_and_cleans_up(self, tmp_path: Path) -> None:
        """OSError from subprocess.run must cause False return and no leftover tmp files."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        with patch("subprocess.run", side_effect=OSError("disk full")):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is False
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

        assert result is False
        assert mkv.read_bytes() == b"original"
        assert list(tmp_path.glob("*.trimarr_tmp")) == []

    def test_mkstemp_failure_returns_false(self, tmp_path: Path) -> None:
        """If tempfile.mkstemp raises, process_file must return False gracefully."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = _proc_cmd(mkv, tmp_path / "out.mkv")

        with patch("core.processor.tempfile.mkstemp", side_effect=OSError("no space left on device")):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=_proc_logger())

        assert result is False
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
        assert result is False
        assert mkv.read_bytes() == b"original"
        # Warning must have been logged about the cleanup failure
        logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# MkvTrack.channels field
# ---------------------------------------------------------------------------


class TestMkvTrackChannels:
    """MkvTrack must carry a channels field for audio channel count."""

    def test_channels_defaults_to_none(self) -> None:
        t = MkvTrack(id=0, type="audio", language="eng")
        assert t.channels is None

    def test_channels_can_be_set(self) -> None:
        t = MkvTrack(id=0, type="audio", language="eng", channels=8)
        assert t.channels == 8

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
