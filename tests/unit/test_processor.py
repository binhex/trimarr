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

ENG = "eng"
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
        tracks.append(MkvTrack(id=tid, type="audio", language=lang, name=name, default_track=default))
        tid += 1
    for i, lang in enumerate(sub_langs):
        name = sub_names[i] if sub_names else None
        default = sub_defaults[i] if sub_defaults else False
        tracks.append(MkvTrack(id=tid, type="subtitles", language=lang, name=name, default_track=default))
        tid += 1
    return tracks


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


# ---------------------------------------------------------------------------
# build_mkvmerge_command()
# ---------------------------------------------------------------------------


class TestBuildMkvmergeCommand:
    """Tests for build_mkvmerge_command() — pure function, no subprocess."""

    def _build(
        self,
        tracks: list[MkvTrack],
        language: str = ENG,
        keep_audio: bool = False,
        keep_subtitles: bool = False,
        edit_metadata_title: bool = False,
        delete_metadata_title: bool = False,
        input_path: Path | None = None,
        output_path: Path | None = None,
        logger: MagicMock | None = None,
    ) -> list[str] | None:
        inp = input_path or Path("/media/Movie.Name.mkv")
        out = output_path or Path("/media/Movie.Name.mkv.tmp")
        return build_mkvmerge_command(
            mkvmerge_path=MKVMERGE,
            input_path=inp,
            output_path=out,
            tracks=tracks,
            language=language,
            keep_audio=keep_audio,
            keep_subtitles=keep_subtitles,
            edit_metadata_title=edit_metadata_title,
            delete_metadata_title=delete_metadata_title,
            logger=logger,
        )

    def test_returns_none_when_no_changes_needed(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=["eng"])
        assert self._build(tracks, language="eng") is None

    def test_returns_none_with_no_tracks_to_remove_and_no_metadata(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=[])
        assert self._build(tracks) is None

    def test_drops_foreign_audio(self) -> None:
        tracks = _make_tracks(audio_langs=["eng", "fre", "ger"], sub_langs=[])
        cmd = self._build(tracks, language="eng")
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
        result = self._build(tracks, language="eng", logger=logger)
        assert result is None  # No other changes → nothing to do
        logger.warning.assert_called_once()
        assert "eng" in logger.warning.call_args[0][0]

    def test_keep_audio_overrides_language_filter(self) -> None:
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=[])
        result = self._build(tracks, language="eng", keep_audio=True)
        assert result is None  # Nothing to do

    def test_drops_foreign_subtitles(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=["eng", "fre"])
        cmd = self._build(tracks, language="eng")
        assert cmd is not None
        assert "--subtitle-tracks" in cmd

    def test_no_subtitles_safety_fallback_returns_none_and_warns(self) -> None:
        """Safety fallback: when NO subtitle tracks match, keep all — returns None and logs a warning."""
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        logger = MagicMock()
        result = self._build(tracks, language="eng", logger=logger)
        assert result is None  # No other changes → nothing to do
        logger.warning.assert_called_once()

    def test_keep_subtitles_overrides_language_filter(self) -> None:
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        result = self._build(tracks, language="eng", keep_subtitles=True)
        assert result is None

    def test_edit_metadata_title_included(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[])
        inp = Path("/media/My.Movie.mkv")
        cmd = self._build(tracks, edit_metadata_title=True, input_path=inp)
        assert cmd is not None
        assert "--title" in cmd
        idx = cmd.index("--title") + 1
        assert cmd[idx] == "My.Movie"

    def test_delete_metadata_title_sets_empty_string(self) -> None:
        tracks = _make_tracks(audio_langs=["eng"], sub_langs=[])
        cmd = self._build(tracks, delete_metadata_title=True)
        assert cmd is not None
        assert "--title" in cmd
        idx = cmd.index("--title") + 1
        assert cmd[idx] == ""

    def test_command_starts_with_mkvmerge_and_output(self) -> None:
        # Use a mixed-language track list so there IS something to drop
        tracks = _make_tracks(audio_langs=["eng", "fre"], sub_langs=[])
        out = Path("/tmp/out.mkv")
        cmd = self._build(tracks, output_path=out)
        assert cmd is not None
        assert cmd[0] == MKVMERGE
        assert cmd[1] == "-o"
        assert cmd[2] == str(out)

    def test_input_path_appended_at_end(self) -> None:
        # Use a mixed-language track list so there IS something to drop
        tracks = _make_tracks(audio_langs=["eng", "fre"], sub_langs=[])
        inp = Path("/media/test.mkv")
        cmd = self._build(tracks, input_path=inp)
        assert cmd is not None
        assert cmd[-1] == str(inp)


# ---------------------------------------------------------------------------
# Commentary default-track reassignment tests
# ---------------------------------------------------------------------------


class TestCommentaryDefaultTrack:
    """Tests for the commentary --default-track reassignment logic."""

    def _build(
        self,
        tracks: list[MkvTrack],
        language: str = ENG,
        logger: MagicMock | None = None,
    ) -> list[str] | None:
        return build_mkvmerge_command(
            mkvmerge_path=MKVMERGE,
            input_path=Path("/media/Movie.mkv"),
            output_path=Path("/media/Movie.mkv.tmp"),
            tracks=tracks,
            language=language,
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            logger=logger,
        )

    def test_audio_commentary_default_reassigned_to_non_commentary(self) -> None:
        """Commentary track with default=True is demoted; non-commentary promoted."""
        # tid 0: video, tid 1: eng commentary (default), tid 2: eng normal, tid 3: fre (dropped)
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="eng", name="Main Audio", default_track=False),
            MkvTrack(id=3, type="audio", language="fre"),
        ]
        cmd = self._build(tracks)
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
        cmd = self._build(tracks)
        assert cmd is not None
        # Non-commentary already holds default — no reassignment needed
        assert "--default-track" not in cmd

    def test_audio_all_kept_commentary_warns_and_demotes_default(self) -> None:
        """All remaining audio tracks are commentary — demote default, log warning."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="fre"),  # dropped
        ]
        logger = MagicMock()
        cmd = self._build(tracks, logger=logger)
        assert cmd is not None
        # Commentary default must still be unset even though there's nothing to promote
        assert "--default-track" in cmd
        assert "1:0" in cmd
        logger.warning.assert_called()
        assert "commentary" in logger.warning.call_args[0][0].lower()

    def test_audio_not_removing_tracks_no_flags_emitted(self) -> None:
        """No audio tracks dropped — commentary default-track logic must not fire."""
        # All audio matches language, so audio_drop is empty
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng", name="Commentary 1", default_track=True),
            MkvTrack(id=2, type="audio", language="eng", name="Main Audio", default_track=False),
        ]
        # Nothing to remove → returns None
        result = self._build(tracks)
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
        cmd = self._build(tracks)
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
        cmd = self._build(tracks)
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
        cmd = self._build(tracks)
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
        cmd = self._build(tracks)
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
        cmd = self._build(tracks, logger=logger)
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
        result = self._build(tracks)
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
            cmd = self._build(tracks)
            assert cmd is not None, f"Expected command for name={name!r}"
            assert "--default-track" in cmd, f"Expected flags for name={name!r}"
            assert "1:0" in cmd
            assert "2:1" in cmd


# ---------------------------------------------------------------------------
# process_file()
# ---------------------------------------------------------------------------


class TestProcessFile:
    """Tests for process_file()."""

    def _make_logger(self) -> MagicMock:
        log = MagicMock()
        log.debug = MagicMock()
        log.info = MagicMock()
        log.warning = MagicMock()
        log.error = MagicMock()
        log.success = MagicMock()
        return log

    def _cmd(self, input_path: Path, output_path: Path) -> list[str]:
        return [MKVMERGE, "-o", str(output_path), str(input_path)]

    def test_success_with_backup(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        out_placeholder = tmp_path / "out.mkv"
        cmd = self._cmd(mkv, out_placeholder)

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            # Write to the actual temp path (patched into cmd by process_file)
            out_path = Path(args[2])
            out_path.write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=self._make_logger())

        assert result is True
        assert mkv.read_bytes() == b"processed"
        backup = tmp_path / "movie.mkv.bak"
        assert backup.read_bytes() == b"original"

    def test_success_no_backup(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        out_placeholder = tmp_path / "out.mkv"
        cmd = self._cmd(mkv, out_placeholder)

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"processed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=True, logger=self._make_logger())

        assert result is True
        assert mkv.read_bytes() == b"processed"
        assert not (tmp_path / "movie.mkv.bak").exists()

    def test_exit_code_1_with_valid_output_succeeds_with_warning(self, tmp_path: Path) -> None:
        """mkvmerge exit 1 means 'completed with warnings' — output is still valid."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
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
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

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
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

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
        cmd = self._cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"")  # Empty file
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=self._make_logger())

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
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

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
