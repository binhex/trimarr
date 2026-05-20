"""Command-line interface for trimarr."""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

import click

from trimarr.downloader import get_app_data_dir
from trimarr.logger import create_logger

if TYPE_CHECKING:
    from loguru import Logger

try:
    _VERSION = version("Trimarr")
except PackageNotFoundError:
    _VERSION = "unknown"


# Compute default paths under the user application data directory so they are
# correct both during development and when installed via pip/uv.
_APP_DATA_DIR = get_app_data_dir()
_MKVMERGE_BIN = "mkvmerge.exe" if platform.system() == "Windows" else "mkvmerge"
_DEFAULT_MKVMERGE_PATH = str(_APP_DATA_DIR / "bin" / _MKVMERGE_BIN)
_DEFAULT_DB_PATH = str(_APP_DATA_DIR / "db" / "trimarr.db")
_DEFAULT_LOGS_PATH = str(_APP_DATA_DIR / "logs" / "trimarr.log")
_LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
_ISO_639_2_CODES_URL = "https://en.wikipedia.org/wiki/List_of_ISO_639-2_codes"


def _check_for_mkvmerge_update(current_path: str, logger: Logger) -> str:
    """Check for a newer managed mkvmerge release and update if one is available.

    Args:
        current_path: Filesystem path to the currently installed binary.
        logger: Loguru logger for status messages.

    Returns:
        Path to the binary to use (updated path on success, *current_path* if
        the check fails or the installed version is already up to date).
    """
    from trimarr.downloader import get_installed_mkvmerge_tag, get_latest_mkvmerge_tag

    try:
        installed_tag = get_installed_mkvmerge_tag(_APP_DATA_DIR / "bin")
        latest_tag = get_latest_mkvmerge_tag()
    except Exception as exc:
        logger.warning(f"mkvmerge update check failed ({exc}). Proceeding with installed version.")
        return current_path

    if installed_tag is not None and installed_tag == latest_tag:
        logger.debug(f"mkvmerge is up to date ({installed_tag}).")
        return current_path

    if installed_tag is None:
        # No version file: binary predates version tracking; update to establish the baseline.
        logger.info(f"mkvmerge version unknown (pre-versioning install), updating to {latest_tag}...")
    else:
        logger.info(f"mkvmerge update available ({installed_tag} \u2192 {latest_tag}). Updating...")

    from trimarr.downloader import download_mkvmerge

    try:
        new_path = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
    except Exception as exc:
        logger.warning(f"mkvmerge download failed ({exc}). Proceeding with installed version.")
        return current_path
    logger.success(f"mkvmerge updated to {latest_tag}.")
    return new_path


def _resolve_mkvmerge_path(
    mkvmerge_path: str | None,
    no_update_check: bool,
    logger: Logger,
) -> str:
    """Resolve the mkvmerge binary path; download or update if the managed binary is used.

    When *mkvmerge_path* is *None*, uses the managed binary at ``_DEFAULT_MKVMERGE_PATH``,
    downloading it on first run and updating it unless *no_update_check* is *True*.
    Raises :exc:`click.UsageError` if a user-supplied path does not exist, or
    :exc:`click.ClickException` if a required download fails.
    """
    user_supplied = mkvmerge_path is not None
    resolved = mkvmerge_path if mkvmerge_path is not None else _DEFAULT_MKVMERGE_PATH

    if not Path(resolved).is_file():
        if user_supplied:
            raise click.UsageError(f"mkvmerge not found at the specified path: '{resolved}'")
        from trimarr.downloader import download_mkvmerge

        logger.info(f"mkvmerge not found at '{resolved}', downloading latest binary...")
        try:
            resolved = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
            logger.success(f"mkvmerge installed at: {resolved}")
        except Exception as exc:
            raise click.ClickException(f"Could not download mkvmerge: {exc}") from exc
    elif not user_supplied and not no_update_check:
        resolved = _check_for_mkvmerge_update(resolved, logger)

    return resolved


def _parse_and_validate_languages(language: str) -> list[str]:
    languages = [code.strip().lower() for code in language.split(",") if code.strip()]
    if not languages:
        raise click.UsageError("--language requires at least one non-empty language code, e.g. --language eng")
    for code in languages:
        if not (len(code) == 3 and code.isascii() and code.isalpha()):
            raise click.UsageError(
                f"Language codes must be 3-letter ISO 639-2 values, got '{code}'. See {_ISO_639_2_CODES_URL} for valid codes."
            )
    return languages


def _validate_cron_schedule(schedule: str | None) -> None:
    """Validate the ``--schedule`` cron expression argument.

    Args:
        schedule: A cron expression string, or *None* for one-shot mode.

    Raises:
        click.BadParameter: If the expression is not a valid cron pattern.
    """
    if schedule is None:
        return
    from trimarr.scheduler import validate_cron_expr

    try:
        validate_cron_expr(schedule)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--schedule") from exc


class _PipeSeparatedPaths(click.ParamType):
    """Accept one or more pipe-separated directory paths.

    Each entry is trimmed of whitespace and validated as a directory using
    ``click.Path(file_okay=False, dir_okay=True, resolve_path=True)``.
    """

    name = "path"

    def convert(
        self,
        value: str | list[str],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> list[str]:
        """Split *value* on pipes and resolve each entry as a directory path."""
        if isinstance(value, list):
            return value
        path_type = click.Path(file_okay=False, dir_okay=True, resolve_path=True)
        results: list[str] = []
        for entry in value.split("|"):
            entry = entry.strip()
            if not entry:
                continue
            results.append(str(path_type.convert(entry, param, ctx)))
        if not results:
            self.fail("at least one non-empty path is required", param, ctx)
        return results


class _CliCommand(click.Command):
    """Custom Click Command subclass that stores CLI examples and renders the epilog without indentation."""

    EXAMPLES = """
\b
Examples:
  Keep only English audio and subtitles, strip commentary and lower-channel tracks, and delete original files after successful processing:
    {prog} \\
      --language eng \\
      --strip-commentary \\
      --strip-lower-channels \\
      --no-backup \\
      --media-path /mnt/user/Movies

\b
  Keep only English audio and subtitles:
    {prog} \\
      --language eng \\
      --media-path /mnt/user/Movies

\b
  Keep English and French audio and subtitles:
    {prog} \\
      --language eng,fre \\
      --media-path /mnt/user/Movies

\b
  Keep only English audio, but retain all subtitle tracks:
    {prog} \\
      --language eng \\
      --keep-subtitles \\
      --media-path /mnt/user/Movies

\b
  Dry run to preview changes without modifying files:
    {prog} \\
      --language eng \\
      --dry-run \\
      --media-path /mnt/user/Movies

\b
  Keep only French audio and update each file's title metadata to match its filename:
    {prog} \\
      --language fre \\
      --edit-metadata-title \\
      --media-path /mnt/user/Movies

\b
  Use a custom mkvmerge binary and database location:
    {prog} \\
      --language eng \\
      --media-path /mnt/user/Movies \\
      --mkvmerge-path /usr/bin/mkvmerge \\
      --database-path /var/lib/trimarr/trimarr.db

\b
  Process multiple media directories in a single run:
    {prog} \\
      --language eng \\
      --media-path /mnt/user/Movies|/mnt/media/tv

\b
  Run every 30 minutes, processing immediately on startup:
    {prog} \\
      --language eng \\
      --media-path /mnt/user/Movies \\
      --schedule "*/30 * * * *" \\
      --run-on-start

\b
  Run daily at 2 AM:
    {prog} \\
      --language eng \\
      --media-path /mnt/user/Movies \\
      --schedule "0 2 * * *"
\b
"""

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if self.epilog:
            formatter.write_paragraph()
            formatter.write_text(self.epilog.replace("{prog}", ctx.info_name or ""))


@click.command(cls=_CliCommand, epilog=_CliCommand.EXAMPLES)
@click.option(
    "--language",
    type=click.STRING,
    required=True,
    metavar="<code[,code...]>",
    help=(
        "One or more ISO 639-2 language codes (comma-separated) for the audio/subtitle tracks to keep,"
        " e.g. 'eng' for English only or 'eng,fre' for English and French."
        " Language codes: http://en.wikipedia.org/wiki/List_of_ISO_639-2_codes"
    ),
)
@click.option(
    "--edit-metadata-title",
    is_flag=True,
    default=False,
    help="If specified, the title metadata of each file will be updated to match its filename.",
)
@click.option(
    "--delete-metadata-title",
    is_flag=True,
    default=False,
    help="If specified, the title metadata of each file will be deleted.",
)
@click.option(
    "--keep-subtitles",
    is_flag=True,
    default=False,
    help="If specified, all subtitle tracks will be kept regardless of language.",
)
@click.option(
    "--keep-audio",
    is_flag=True,
    default=False,
    help="If specified, all audio tracks will be kept regardless of language.",
)
@click.option(
    "--media-path",
    type=_PipeSeparatedPaths(),
    required=True,
    metavar="<path[|path...]>",
    help="Path(s) to directory/directories containing media files. Accepts a single path or a pipe-separated list of paths (use | as delimiter, which is not valid in directory names).",
)
@click.option(
    "--mkvmerge-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=None,
    show_default=False,
    metavar="<mkvmerge path>",
    help=(
        f"Path to mkvmerge executable.  When omitted, trimarr manages its own"
        f" binary at '{_DEFAULT_MKVMERGE_PATH}' (auto-downloaded and kept up to date)."
    ),
)
@click.option(
    "--database-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_DB_PATH,
    show_default=True,
    metavar="<db path>",
    help="Path to SQLite database file for tracking processed files.",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_LOGS_PATH,
    show_default=True,
    metavar="<logpath>",
    help="Path to log file for tracking application events.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    metavar="<level>",
    show_default=True,
    help="Logging level for console output",
)
@click.option(
    "--no-backup",
    is_flag=True,
    default=False,
    help=(
        "If specified, the original file is deleted instead of being renamed to '<name>.bak'"
        " after successful processing.  By default a backup is always created."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="If specified, the script will perform a dry run without making any changes.",
)
@click.option(
    "--no-update-check",
    is_flag=True,
    default=False,
    help=(
        "Skip the automatic check for a newer mkvmerge version."
        " Has no effect when --mkvmerge-path is specified (user-managed binaries are never auto-updated)."
    ),
)
@click.option(
    "--strip-lower-channels",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "If specified, after language filtering, audio tracks with a channel count strictly below"
        " the maximum channel count among the surviving audio tracks will be removed."
        " For example, if a file has both 8-channel and 2-channel English audio, the 2-channel track"
        " is dropped.  Tracks with an unknown channel count are always preserved."
        " Has no effect when --keep-audio is set.  Disabled by default."
    ),
)
@click.option(
    "--strip-commentary",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "If specified, audio and subtitle tracks whose name contains 'commentary'"
        " (case-insensitive) will be removed after language filtering."
        " Audio safety fallback: if stripping commentary would leave no audio tracks, all audio is kept"
        " and a warning is logged. Subtitle commentary tracks are stripped unconditionally"
        " (a subtitle-free file is acceptable)."
        " Has no effect on audio when --keep-audio is set, or on subtitles when --keep-subtitles is set."
        " Disabled by default."
    ),
)
@click.option(
    "--schedule",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<cron>",
    help=(
        "Run trimarr repeatedly on a cron schedule rather than once."
        " Standard 5-field POSIX cron expression: minute hour day month weekday."
        " Also accepts @hourly, @daily, @weekly, @monthly, @yearly."
        " Examples: '0 2 * * *' (daily at 2am), '*/30 * * * *' (every 30 min),"
        " '@daily' (once per day)."
        " Omit to run once and exit (default behaviour)."
    ),
)
@click.option(
    "--skip-size-check",
    is_flag=True,
    default=False,
    help=(
        "If specified, bypass the output size guard that rejects mkvmerge results smaller than"
        " 50 % of the source file.  Use when legitimate remuxes are expected to produce"
        " significantly smaller output (e.g. files with very large audio/subtitle payloads)."
    ),
)
@click.option(
    "--run-on-start",
    is_flag=True,
    default=False,
    help=(
        "When --schedule is set, fire one run immediately on startup"
        " before the first scheduled cron fire."
        " Cannot be used without --schedule."
    ),
)
@click.option(
    "--pre-process",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<command>",
    help=(
        "Shell command to run before processing files in a directory."
        " Use {leaf} for the directory basename and {dir} for the full"
        " directory path."
        " Example: --pre-process 'no_ransom.sh --unlock yes {leaf}'"
    ),
)
@click.option(
    "--post-process",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<command>",
    help=(
        "Shell command to run after processing files in a directory."
        " Use {leaf} for the directory basename and {dir} for the full"
        " directory path."
        " Example: --post-process 'no_ransom.sh --unlock no {leaf}'"
    ),
)
@click.option(
    "--command-timeout-mins",
    type=click.IntRange(min=0),
    required=False,
    default=5,
    metavar="<minutes>",
    show_default=True,
    help=("Timeout in minutes for each pre/post process command. Set to 0 to disable timeout entirely."),
)
@click.version_option(version=_VERSION, prog_name="Trimarr")
def cli(
    language: str,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    keep_subtitles: bool,
    keep_audio: bool,
    media_path: list[str],
    mkvmerge_path: str | None,
    database_path: str,
    log_path: str,
    log_level: str,
    no_backup: bool,
    dry_run: bool,
    no_update_check: bool,
    strip_lower_channels: bool,
    strip_commentary: bool,
    skip_size_check: bool,
    schedule: str | None,
    run_on_start: bool,
    pre_process: str | None,
    post_process: str | None,
    command_timeout_mins: int,
) -> None:
    """Trimarr - Removes (trims) unwanted audio and subtitles from matroska container format video files.

    This script will remove unwanted audio and subtitle tracks from matroska container format video files based on
    user-defined criteria. It uses matroska CLI tools for processing the video files and SQLite for tracking which files
    have already been processed to avoid redundant work.
    """
    from trimarr.runner import run

    languages = _parse_and_validate_languages(language)

    if run_on_start and schedule is None:
        raise click.UsageError("--run-on-start requires --schedule.")

    _validate_cron_schedule(schedule)

    if edit_metadata_title and delete_metadata_title:
        raise click.UsageError("--edit-metadata-title and --delete-metadata-title are mutually exclusive.")

    logger = create_logger(log_format=_LOG_FORMAT, log_level=log_level, log_path=log_path)

    mkvmerge_path = _resolve_mkvmerge_path(mkvmerge_path, no_update_check, logger)

    def _run() -> None:
        run(
            language=languages,
            edit_metadata_title=edit_metadata_title,
            delete_metadata_title=delete_metadata_title,
            keep_subtitles=keep_subtitles,
            keep_audio=keep_audio,
            media_path=media_path,
            mkvmerge_path=mkvmerge_path,
            database_path=database_path,
            no_backup=no_backup,
            dry_run=dry_run,
            logger=logger,
            strip_lower_channels=strip_lower_channels,
            strip_commentary=strip_commentary,
            skip_size_check=skip_size_check,
            pre_process=pre_process,
            post_process=post_process,
            command_timeout_mins=command_timeout_mins,
        )

    if schedule is not None:
        from trimarr.scheduler import run_scheduled

        run_scheduled(_run, cron_expr=schedule, run_on_start=run_on_start, logger=logger)
    else:
        _run()


if __name__ == "__main__":
    cli()
