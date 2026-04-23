"""Unit tests for cli.main (Click CLI entry point)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from trimarr.cli import cli

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
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng"]

    def test_multiple_languages_comma_separated(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
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
        with patch("trimarr.runner.run") as mock_run:
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
        with patch("trimarr.runner.run"):
            result = runner.invoke(cli, _base_args(str(tmp_path)) + ["--edit-metadata-title"])
        assert result.exit_code == 0, result.output

    def test_delete_alone_is_accepted(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("trimarr.runner.run"):
            result = runner.invoke(cli, _base_args(str(tmp_path)) + ["--delete-metadata-title"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Flag options forwarded to run()
# ---------------------------------------------------------------------------


class TestFlagsForwardedToRun:
    """CLI flags must be correctly forwarded to run()."""

    def _invoke_with_flags(self, tmp_path: Path, extra_args: list[str]) -> MagicMock:
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
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

    def test_boolean_flags_default_to_false(self, tmp_path: Path) -> None:
        mock_run = self._invoke_with_flags(tmp_path, [])
        _, kwargs = mock_run.call_args
        assert kwargs["strip_lower_channels"] is False
        assert kwargs["keep_audio"] is False
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
        with patch("trimarr.runner.run") as mock_run:
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


# ---------------------------------------------------------------------------
# Scheduler options
# ---------------------------------------------------------------------------


class TestScheduleOption:
    """--schedule and --run-on-start CLI integration."""

    def test_schedule_calls_run_scheduled(self, tmp_path: Path) -> None:
        """When --schedule is provided, run_scheduled is called instead of runner.run."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.scheduler.run_scheduled") as mock_sched:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path)) + ["--mkvmerge-path", str(fake_mkvmerge), "--schedule", "6h"],
            )

        assert result.exit_code == 0, result.output
        mock_sched.assert_called_once()
        _, kwargs = mock_sched.call_args
        assert kwargs["interval_seconds"] == 21600
        assert kwargs["run_on_start"] is False

    def test_run_on_start_forwarded_to_run_scheduled(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.scheduler.run_scheduled") as mock_sched:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge), "--schedule", "1d", "--run-on-start"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_sched.call_args
        assert kwargs["run_on_start"] is True

    def test_run_on_start_without_schedule_exits_with_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(cli, _base_args(str(tmp_path)) + ["--run-on-start"])
        assert result.exit_code != 0
        assert "schedule" in result.output.lower()

    def test_invalid_schedule_format_exits_with_error(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        result = CliRunner().invoke(
            cli,
            _base_args(str(tmp_path)) + ["--mkvmerge-path", str(fake_mkvmerge), "--schedule", "0h"],
        )

        assert result.exit_code != 0
        assert "schedule" in result.output.lower()

    def test_no_schedule_calls_runner_run_directly(self, tmp_path: Path) -> None:
        """Without --schedule the existing runner.run path is used unchanged."""
        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Version fallback when package metadata is absent
# ---------------------------------------------------------------------------


class TestVersionFallback:
    """_VERSION falls back to 'unknown' when importlib cannot find the package."""

    def test_version_unknown_when_package_not_installed(self) -> None:
        from importlib.metadata import PackageNotFoundError as _PNFError

        import trimarr.cli as cli_module

        with patch("importlib.metadata.version", side_effect=_PNFError):
            importlib.reload(cli_module)
        try:
            assert cli_module._VERSION == "unknown"
        finally:
            importlib.reload(cli_module)


# ---------------------------------------------------------------------------
# Help output / custom epilog
# ---------------------------------------------------------------------------


class TestHelpOutput:
    """--help renders the custom epilog with usage examples."""

    def test_help_shows_examples(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Examples:" in result.output
        assert "{prog}" not in result.output


# ---------------------------------------------------------------------------
# Auto-download when the default managed mkvmerge binary is absent
# ---------------------------------------------------------------------------


class TestAutoDownloadMkvmerge:
    """When the managed mkvmerge binary is absent it is downloaded automatically."""

    def test_downloads_when_default_path_missing(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "mkvmerge"
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", "/nonexistent/mkvmerge"),
            patch("trimarr.downloader.download_mkvmerge", return_value=fake_bin) as mock_dl,
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_dl.assert_called_once()
        assert mock_run.call_args.kwargs["mkvmerge_path"] == str(fake_bin)

    def test_download_failure_exits_with_error(self, tmp_path: Path) -> None:
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", "/nonexistent/mkvmerge"),
            patch("trimarr.downloader.download_mkvmerge", side_effect=RuntimeError("network error")),
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code != 0
        assert "mkvmerge" in result.output.lower()


# ---------------------------------------------------------------------------
# Managed mkvmerge update-check branches
# ---------------------------------------------------------------------------


class TestMkvmergeUpdateCheck:
    """Update-check paths: no tag file, newer tag available, and check failure."""

    def test_updates_when_no_version_tag_present(self, tmp_path: Path) -> None:
        """Binary exists but has no version tag (pre-versioning install) — update is triggered."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        fake_new_bin = tmp_path / "mkvmerge_new"
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", return_value=None),
            patch("trimarr.downloader.get_latest_mkvmerge_tag", return_value="v82.0.0"),
            patch("trimarr.downloader.download_mkvmerge", return_value=fake_new_bin) as mock_dl,
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_dl.assert_called_once()
        assert mock_run.call_args.kwargs["mkvmerge_path"] == str(fake_new_bin)

    def test_updates_when_newer_version_available(self, tmp_path: Path) -> None:
        """When installed tag differs from latest, the binary is updated."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        fake_new_bin = tmp_path / "mkvmerge_new"
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", return_value="v80.0.0"),
            patch("trimarr.downloader.get_latest_mkvmerge_tag", return_value="v82.0.0"),
            patch("trimarr.downloader.download_mkvmerge", return_value=fake_new_bin) as mock_dl,
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_dl.assert_called_once()
        assert mock_run.call_args.kwargs["mkvmerge_path"] == str(fake_new_bin)

    def test_update_check_failure_continues_with_installed_version(self, tmp_path: Path) -> None:
        """A failing update check logs a warning but does not abort the run."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        mock_logger = MagicMock()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.cli.create_logger", return_value=mock_logger),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", side_effect=OSError("no network")),
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_logger.warning.assert_called_once()
        mock_run.assert_called_once()
