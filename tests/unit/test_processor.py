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
) -> list[MkvTrack]:
    """Build a minimal track list for testing."""
    tracks: list[MkvTrack] = []
    tid = 0
    for _ in range(video_count):
        tracks.append(MkvTrack(id=tid, type="video", language=None))
        tid += 1
    for lang in audio_langs:
        tracks.append(MkvTrack(id=tid, type="audio", language=lang))
        tid += 1
    for lang in sub_langs:
        tracks.append(MkvTrack(id=tid, type="subtitles", language=lang))
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
            {"id": 1, "type": "audio", "properties": {"language": "eng"}},
            {"id": 2, "type": "subtitles", "properties": {"language": "fre"}},
        ]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)

        assert len(result) == 3
        assert result[0] == MkvTrack(id=0, type="video", language=None)  # "und" → None
        assert result[1] == MkvTrack(id=1, type="audio", language="eng")
        assert result[2] == MkvTrack(id=2, type="subtitles", language="fre")

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

    def test_und_language_normalised_to_none(self, tmp_path: Path) -> None:
        raw_tracks = [{"id": 0, "type": "video", "properties": {"language": "und"}}]
        mkv = tmp_path / "movie.mkv"
        mkv.touch()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=self._mkvmerge_json(raw_tracks), stderr="")
            result = probe_file(MKVMERGE, mkv)
        assert result[0].language is None

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

    def test_no_audio_flag_when_none_match(self) -> None:
        """Safety fallback: when NO audio tracks match, keep all — don't emit --no-audio."""
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=[])
        # No matching audio + no other changes → nothing to do
        assert self._build(tracks, language="eng") is None

    def test_no_audio_safety_fallback_logs_warning(self) -> None:
        """A logger-provided warning is emitted when the audio safety fallback fires."""
        tracks = _make_tracks(audio_langs=["fre", "ger"], sub_langs=[])
        logger = MagicMock()
        self._build(tracks, language="eng", logger=logger)
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

    def test_no_subtitles_flag_when_none_match(self) -> None:
        """Safety fallback: when NO subtitle tracks match, keep all — don't emit --no-subtitles."""
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        # No matching subs + no other changes → nothing to do
        assert self._build(tracks, language="eng") is None

    def test_no_subtitles_safety_fallback_logs_warning(self) -> None:
        """A logger-provided warning is emitted when the subtitle safety fallback fires."""
        tracks = _make_tracks(audio_langs=[], sub_langs=["fre"])
        logger = MagicMock()
        self._build(tracks, language="eng", logger=logger)
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

    def test_exit_code_1_treated_as_failure(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"partial")
            return MagicMock(returncode=1, stdout="", stderr="warning from mkvmerge")

        with patch("subprocess.run", side_effect=fake_run):
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is False
        assert mkv.read_bytes() == b"original"  # Original untouched
        logger.warning.assert_called_once()

    def test_exit_code_2_treated_as_failure(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = self._cmd(mkv, tmp_path / "out.mkv")
        logger = self._make_logger()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="fatal error")
            result = process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=logger)

        assert result is False
        logger.error.assert_called_once()

    def test_temp_file_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original")
        cmd = self._cmd(mkv, tmp_path / "out.mkv")

        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            Path(args[2]).write_bytes(b"partial")
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            process_file(MKVMERGE, mkv, cmd, no_backup=False, logger=self._make_logger())

        # No leftover .trimarr_tmp files
        assert not list(tmp_path.glob("*.trimarr_tmp"))

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
