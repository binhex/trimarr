"""Unit tests for cli.main (Click CLI entry point)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli.main import cli

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_args(media_path: str) -> list[str]:
    """Minimal valid CLI args."""
    return ["--language", "eng", "--media-path", media_path]


# ---------------------------------------------------------------------------
# Language option parsing
# ---------------------------------------------------------------------------


class TestLanguageOption:
    """--language parsing and validation."""

    def test_single_language_accepted(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.main.run") as mock_run:
            result = runner.invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng"]

    def test_multiple_languages_comma_separated(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.main.run") as mock_run:
            result = runner.invoke(cli, ["--language", "eng,fre", "--media-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng", "fre"]

    def test_empty_language_exits_with_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", "", "--media-path", str(tmp_path)])
        assert result.exit_code != 0
        assert "language" in result.output.lower()

    def test_language_codes_normalised_to_lowercase(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.main.run") as mock_run:
            result = runner.invoke(cli, ["--language", "ENG,FRE", "--media-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng", "fre"]

    def test_missing_language_option_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--media-path", str(tmp_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Mutually exclusive metadata flags
# ---------------------------------------------------------------------------


class TestMutuallyExclusiveMetadataFlags:
    """--edit-metadata-title and --delete-metadata-title are mutually exclusive."""

    def test_both_flags_exits_with_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--language",
                "eng",
                "--media-path",
                str(tmp_path),
                "--edit-metadata-title",
                "--delete-metadata-title",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_edit_alone_is_accepted(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.main.run"):
            result = runner.invoke(cli, _base_args(str(tmp_path)) + ["--edit-metadata-title"])
        assert result.exit_code == 0, result.output

    def test_delete_alone_is_accepted(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.main.run"):
            result = runner.invoke(cli, _base_args(str(tmp_path)) + ["--delete-metadata-title"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Flag options forwarded to run()
# ---------------------------------------------------------------------------


class TestFlagsForwardedToRun:
    """CLI flags must be correctly forwarded to run()."""

    def _invoke_with_flags(self, tmp_path: Path, extra_args: list[str]) -> MagicMock:
        runner = CliRunner()
        with patch("trimarr.main.run") as mock_run:
            result = runner.invoke(cli, _base_args(str(tmp_path)) + extra_args)
        assert result.exit_code == 0, result.output
        return mock_run

    def test_keep_audio_forwarded(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, ["--keep-audio"])
        _, kwargs = mock_run.call_args
        assert kwargs["keep_audio"] is True

    def test_keep_subtitles_forwarded(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, ["--keep-subtitles"])
        _, kwargs = mock_run.call_args
        assert kwargs["keep_subtitles"] is True

    def test_dry_run_forwarded(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, ["--dry-run"])
        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True

    def test_no_backup_forwarded(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, ["--no-backup"])
        _, kwargs = mock_run.call_args
        assert kwargs["no_backup"] is True

    def test_strip_lower_channels_forwarded(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, ["--strip-lower-channels"])
        _, kwargs = mock_run.call_args
        assert kwargs["strip_lower_channels"] is True

    def test_strip_lower_channels_default_is_false(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, [])
        _, kwargs = mock_run.call_args
        assert kwargs["strip_lower_channels"] is False

    def test_keep_audio_default_is_false(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, [])
        _, kwargs = mock_run.call_args
        assert kwargs["keep_audio"] is False

    def test_dry_run_default_is_false(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, [])
        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is False


# ---------------------------------------------------------------------------
# mkvmerge path handling
# ---------------------------------------------------------------------------


class TestMkvmergePath:
    """--mkvmerge-path behaviour: user-supplied vs auto-managed."""

    def test_nonexistent_user_supplied_path_exits_with_error(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            _base_args(str(tmp_path)) + ["--mkvmerge-path", "/nonexistent/mkvmerge"],
        )
        assert result.exit_code != 0
        assert "mkvmerge" in result.output.lower()

    def test_existing_mkvmerge_path_is_used_directly(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        fake_mkvmerge.chmod(0o755)
        runner = CliRunner()
        with patch("trimarr.main.run") as mock_run:
            result = runner.invoke(
                cli,
                _base_args(str(tmp_path)) + ["--mkvmerge-path", str(fake_mkvmerge)],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["mkvmerge_path"] == str(fake_mkvmerge)


# ---------------------------------------------------------------------------
# Version flag
# ---------------------------------------------------------------------------


class TestVersionFlag:
    def test_version_flag_outputs_version_string(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "trimarr" in result.output.lower()
