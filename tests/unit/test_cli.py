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

    def test_two_letter_code_exits_with_error(self, tmp_path: Path) -> None:
        """ISO 639-1 two-letter codes like 'en' are rejected; users must use 3-letter 639-2 codes."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", "en", "--media-path", str(tmp_path)])
        assert result.exit_code != 0
        assert "3-letter" in result.output.lower() or "language" in result.output.lower()

    def test_terminologic_code_accepted(self, tmp_path: Path) -> None:
        """ISO 639-2 terminologic codes (e.g. 'fra', 'deu') must be accepted."""
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(cli, ["--language", "fra", "--media-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["fra"]


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
        _, kwargs = mock_run.call_args
        assert kwargs["mkvmerge_path"] == str(fake_mkvmerge)

    def test_update_check_failure_on_latest_tag_call_continues(self, tmp_path: Path) -> None:
        """A failure from get_latest_mkvmerge_tag also triggers the 'check failed' fallback."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        mock_logger = MagicMock()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.cli.create_logger", return_value=mock_logger),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", return_value="v80.0.0"),
            patch("trimarr.downloader.get_latest_mkvmerge_tag", side_effect=OSError("DNS failure")),
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_logger.warning.assert_called_once()
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["mkvmerge_path"] == str(fake_mkvmerge)

    def test_no_update_check_flag_skips_update_check(self, tmp_path: Path) -> None:
        """--no-update-check must prevent _check_for_mkvmerge_update from running at all."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.downloader.get_installed_mkvmerge_tag") as mock_tag,
            patch("trimarr.downloader.get_latest_mkvmerge_tag") as mock_latest,
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)) + ["--no-update-check"])
        assert result.exit_code == 0, result.output
        mock_tag.assert_not_called()
        mock_latest.assert_not_called()
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["mkvmerge_path"] == str(fake_mkvmerge)

    def test_no_update_when_already_up_to_date(self, tmp_path: Path) -> None:
        """When installed tag equals the latest tag, no download occurs."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", return_value="v82.0.0"),
            patch("trimarr.downloader.get_latest_mkvmerge_tag", return_value="v82.0.0"),
            patch("trimarr.downloader.download_mkvmerge") as mock_dl,
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        mock_dl.assert_not_called()
        mock_run.assert_called_once()

    def test_download_failure_continues_with_installed_version(self, tmp_path: Path) -> None:
        """If the download itself fails (not the check), a warning is logged and run proceeds."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()
        mock_logger = MagicMock()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.cli.create_logger", return_value=mock_logger),
            patch("trimarr.downloader.get_installed_mkvmerge_tag", return_value="v80.0.0"),
            patch("trimarr.downloader.get_latest_mkvmerge_tag", return_value="v82.0.0"),
            patch("trimarr.downloader.download_mkvmerge", side_effect=RuntimeError("network timeout")),
            patch("trimarr.runner.run") as mock_run,
        ):
            result = CliRunner().invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        warning_calls = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        assert any("download failed" in m.lower() for m in warning_calls), (
            "Expected a warning about download failure, not update check failure"
        )
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["mkvmerge_path"] == str(fake_mkvmerge)


# ---------------------------------------------------------------------------
# --media-path option: pipe-separated multi-path support
# ---------------------------------------------------------------------------


class TestMediaPathOption:
    """--media-path parsing, validation, and forwarding."""

    def test_single_path_forwarded_as_list(self, tmp_path: Path) -> None:
        """A single path is forwarded to run() as a one-element list."""
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["media_path"] == [str(tmp_path)]

    def test_pipe_separated_paths_forwarded_as_list(self, tmp_path: Path) -> None:
        """Pipe-separated paths are split and forwarded as a list."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(
                cli,
                ["--language", "eng", "--media-path", f"{dir_a}|{dir_b}"],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["media_path"] == [str(dir_a), str(dir_b)]

    def test_whitespace_trimmed_around_pipes(self, tmp_path: Path) -> None:
        """Whitespace around pipe-separated entries is stripped."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(
                cli,
                ["--language", "eng", "--media-path", f"  {dir_a}  |  {dir_b}  "],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["media_path"] == [str(dir_a), str(dir_b)]

    def test_file_path_rejected_at_cli(self, tmp_path: Path) -> None:
        """A path pointing to a file (not a directory) should fail at CLI parse time."""
        file_path = tmp_path / "movie.mkv"
        file_path.write_bytes(b"data")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--language", "eng", "--media-path", str(file_path)],
        )
        assert result.exit_code != 0

    def test_blank_only_segments_filtered(self, tmp_path: Path) -> None:
        """Blank segments between commas are silently ignored."""
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(
                cli,
                ["--language", "eng", "--media-path", f"{tmp_path}| |"],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["media_path"] == [str(tmp_path)]

    def test_all_blank_segments_rejected(self) -> None:
        """When every pipe-separated entry is blank, fail with a usage error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", "eng", "--media-path", "  |  |  "])
        assert result.exit_code != 0

    def test_already_list_passthrough(self) -> None:
        """When convert() receives an already-converted list, it returns it unchanged."""
        from trimarr.cli import _PipeSeparatedPaths

        param_type = _PipeSeparatedPaths()
        paths = ["/tmp/dir1", "/tmp/dir2"]
        result = param_type.convert(paths, None, None)
        assert result is paths


# ---------------------------------------------------------------------------
# Branch coverage: _parse_and_validate_languages missing branches
# ---------------------------------------------------------------------------


class TestParseAndValidateLanguagesBranches:
    """Branch-coverage tests for _parse_and_validate_languages."""

    def test_trailing_comma_filtered_out(self, tmp_path: Path) -> None:
        """A trailing comma produces an empty entry that is filtered by the comprehension."""
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(cli, ["--language", "eng,", "--media-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng"]

    def test_all_whitespace_language_raises(self, tmp_path: Path) -> None:
        """A language value containing only commas/spaces produces an empty list -> UsageError."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", " , ", "--media-path", str(tmp_path)])
        assert result.exit_code != 0
        assert "language" in result.output.lower()

    def test_non_ascii_three_char_code_rejected(self, tmp_path: Path) -> None:
        """A 3-char code with non-ASCII characters is rejected."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", "\u00e9ng", "--media-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_numeric_three_char_code_rejected(self, tmp_path: Path) -> None:
        """A 3-char code containing digits is rejected."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--language", "en1", "--media-path", str(tmp_path)])
        assert result.exit_code != 0
        assert "3-letter" in result.output or "language" in result.output.lower()

    def test_multiple_valid_codes_all_forwarded(self, tmp_path: Path) -> None:
        """Multiple valid codes all pass the for-loop validation."""
        runner = CliRunner()
        with patch("trimarr.runner.run") as mock_run:
            result = runner.invoke(cli, ["--language", "eng,fre,ger", "--media-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["language"] == ["eng", "fre", "ger"]


# ---------------------------------------------------------------------------
# Branch coverage: _resolve_mkvmerge_path missing branches
# ---------------------------------------------------------------------------


class TestResolveMkvmergePathBranches:
    """Branch-coverage tests for _resolve_mkvmerge_path."""

    def test_download_failure_raises_click_exception(self, tmp_path: Path) -> None:
        """When the managed binary is absent and download fails, CLI exits non-zero."""
        runner = CliRunner()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", "/nonexistent/mkvmerge"),
            patch("trimarr.downloader.download_mkvmerge", side_effect=RuntimeError("network error")),
        ):
            result = runner.invoke(cli, _base_args(str(tmp_path)))
        assert result.exit_code != 0
        assert "mkvmerge" in result.output.lower() or "network" in result.output.lower()

    def test_no_update_check_flag_skips_update(self, tmp_path: Path) -> None:
        """--no-update-check with an existing managed binary skips the update check."""
        fake_mkvmerge = tmp_path / "mkvmerge_managed"
        fake_mkvmerge.touch()
        runner = CliRunner()
        with (
            patch("trimarr.cli._DEFAULT_MKVMERGE_PATH", str(fake_mkvmerge)),
            patch("trimarr.cli._check_for_mkvmerge_update") as mock_update,
            patch("trimarr.runner.run"),
        ):
            result = runner.invoke(cli, _base_args(str(tmp_path)) + ["--no-update-check"])
        assert result.exit_code == 0, result.output
        mock_update.assert_not_called()
