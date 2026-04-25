"""Command-line interface for trimarr."""

import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from trimarr.downloader import get_app_data_dir
from trimarr.logger import create_logger

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


class _CliCommand(click.Command):
    """Custom Click Command subclass that stores CLI examples and renders the epilog without indentation."""

    EXAMPLES = """
\b
Examples:
  Keep only English audio and subtitles:
    {prog} \\
      --language eng \\
      --media-path /mnt/media/movies

\b
  Keep English and French audio and subtitles:
    {prog} \\
      --language eng,fre \\
      --media-path /mnt/media/movies

\b
  Keep only English audio, but retain all subtitle tracks:
    {prog} \\
      --language eng \\
      --keep-subtitles \\
      --media-path /mnt/media/movies

\b
  Dry run to preview changes without modifying files:
    {prog} \\
      --language eng \\
      --dry-run \\
      --media-path /mnt/media/movies

\b
  Keep only French audio and update each file's title metadata to match its filename:
    {prog} \\
      --language fre \\
      --edit-metadata-title \\
      --media-path /mnt/media/movies

\b
  Use a custom mkvmerge binary and database location:
    {prog} \\
      --language eng \\
      --media-path /mnt/media/movies \\
      --mkvmerge-path /usr/bin/mkvmerge \\
      --database-path /var/lib/trimarr/trimarr.db

\b
  Run every 6 hours, processing immediately on startup:
    {prog} \\
      --language eng \\
      --media-path /mnt/media/movies \\
      --schedule 6h \\
      --run-on-start

\b
  Run daily (first run after 24 hours):
    {prog} \\
      --language eng \\
      --media-path /mnt/media/movies \\
      --schedule 1d
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
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    required=True,
    metavar="<media path>",
    help="Path to the directory containing media files.",
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
    metavar="<interval>",
    help=(
        "Run trimarr repeatedly at the given interval rather than once."
        " Format: <N><unit> where unit is m (minutes), h (hours), d (days), or w (weeks)."
        " Examples: 30m, 6h, 1d, 2w."
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
        "When --schedule is set, fire one run immediately on startup before the first timed interval."
        " Cannot be used without --schedule."
    ),
)
@click.version_option(version=_VERSION, prog_name="Trimarr")
def cli(
    language: str,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    keep_subtitles: bool,
    keep_audio: bool,
    media_path: str,
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
) -> None:
    """Trimarr - Removes (trims) unwanted audio and subtitles from matroska container format video files.

    This script will remove unwanted audio and subtitle tracks from matroska container format video files based on
    user-defined criteria. It uses matroska CLI tools for processing the video files and SQLite for tracking which files
    have already been processed to avoid redundant work.
    """
    from trimarr.downloader import download_mkvmerge, get_installed_mkvmerge_tag, get_latest_mkvmerge_tag
    from trimarr.runner import run

    # Parse comma-separated language codes into a normalised list.
    languages = [code.strip().lower() for code in language.split(",") if code.strip()]
    if not languages:
        raise click.UsageError("--language requires at least one non-empty language code, e.g. --language eng")

    if run_on_start and schedule is None:
        raise click.UsageError("--run-on-start requires --schedule.")

    if schedule is not None:
        from trimarr.scheduler import parse_interval

        try:
            interval_seconds = parse_interval(schedule)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--schedule") from exc
    else:
        interval_seconds = None

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level, log_path=log_path)

    if edit_metadata_title and delete_metadata_title:
        raise click.UsageError("--edit-metadata-title and --delete-metadata-title are mutually exclusive.")

    # Determine whether we are managing the binary ourselves or the user supplied their own.
    user_supplied_mkvmerge = mkvmerge_path is not None
    if mkvmerge_path is None:
        mkvmerge_path = _DEFAULT_MKVMERGE_PATH

    if not Path(mkvmerge_path).is_file():
        if user_supplied_mkvmerge:
            raise click.UsageError(f"mkvmerge not found at the specified path: '{mkvmerge_path}'")
        logger.info(f"mkvmerge not found at '{mkvmerge_path}', downloading latest binary...")
        try:
            mkvmerge_path = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
            logger.success(f"mkvmerge installed at: {mkvmerge_path}")
        except Exception as exc:
            raise click.ClickException(f"Could not download mkvmerge: {exc}") from exc
    elif not user_supplied_mkvmerge and not no_update_check:
        # Lightweight update check — only the release tag JSON is fetched (~few KB), no binary download.
        try:
            installed_tag = get_installed_mkvmerge_tag(_APP_DATA_DIR / "bin")
            latest_tag = get_latest_mkvmerge_tag()
            if installed_tag is None:
                # No version file — binary predates version tracking; update to establish the baseline.
                logger.info(f"mkvmerge version unknown (pre-versioning install), updating to {latest_tag}...")
                mkvmerge_path = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
                logger.success(f"mkvmerge updated to {latest_tag}.")
            elif installed_tag != latest_tag:
                logger.info(f"mkvmerge update available ({installed_tag} → {latest_tag}). Updating...")
                mkvmerge_path = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
                logger.success(f"mkvmerge updated to {latest_tag}.")
            else:
                logger.debug(f"mkvmerge is up to date ({installed_tag}).")
        except Exception as exc:
            logger.warning(f"mkvmerge update check failed ({exc}). Proceeding with installed version.")

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
        )

    if interval_seconds is not None:
        from trimarr.scheduler import run_scheduled

        run_scheduled(_run, interval_seconds=interval_seconds, run_on_start=run_on_start, logger=logger)
    else:
        _run()


if __name__ == "__main__":
    cli()
