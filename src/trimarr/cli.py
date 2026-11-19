"""Command-line interface for trimarr."""

from __future__ import annotations

import platform
import re
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


_RELEASE_TAG_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)")
"""Matches the leading numeric core of a release tag (e.g. ``v58.0.0``)."""


def _parse_release_tag(tag: str) -> tuple[int, ...] | None:
    """Parse a release tag such as ``v58.0.0`` into a comparable tuple.

    Returns *None* when the tag is not a dotted numeric version.
    """
    match = _RELEASE_TAG_RE.match(tag.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _is_up_to_date(installed_tag: str, latest_tag: str) -> bool:
    """Return True when *installed_tag* equals or is newer than *latest_tag*.

    Compares parsed versions rather than raw strings so a retracted or rewritten
    "latest" release cannot silently downgrade an already-newer binary.
    """
    if installed_tag == latest_tag:
        return True
    installed = _parse_release_tag(installed_tag)
    latest = _parse_release_tag(latest_tag)
    if installed is None or latest is None:
        return False
    width = max(len(installed), len(latest))
    return installed + (0,) * (width - len(installed)) >= latest + (0,) * (width - len(latest))


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

    if installed_tag is not None and _is_up_to_date(installed_tag, latest_tag):
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
    just_downloaded = False

    if not Path(resolved).is_file():
        if user_supplied:
            raise click.UsageError(f"mkvmerge not found at the specified path: '{resolved}'")
        from trimarr.downloader import download_mkvmerge

        logger.info(f"mkvmerge not found at '{resolved}', downloading latest binary...")
        try:
            resolved = str(download_mkvmerge(dest_dir=_APP_DATA_DIR / "bin"))
            logger.success(f"mkvmerge installed at: {resolved}")
            just_downloaded = True
        except Exception as exc:
            raise click.ClickException(f"Could not download mkvmerge: {exc}") from exc

    if not user_supplied and not no_update_check and not just_downloaded:
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


def _validate_option_combinations(
    *,
    run_on_start: bool,
    schedule: str | None,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
) -> None:
    """Reject mutually exclusive flag combinations before any work starts.

    Raises:
        click.UsageError: when ``--run-on-start`` is used without ``--schedule``,
            the cron expression is invalid, or both metadata-title flags are set.
    """
    if run_on_start and schedule is None:
        raise click.UsageError("--run-on-start requires --schedule.")
    _validate_cron_schedule(schedule)
    if edit_metadata_title and delete_metadata_title:
        raise click.UsageError("--edit-metadata-title and --delete-metadata-title are mutually exclusive.")


def _compile_subtitle_patterns(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    """Compile every ``--strip-subtitle-regex`` value.

    Raises:
        click.UsageError: when a pattern is not a valid Python regex.
    """
    compiled: list[re.Pattern[str]] = []
    for pattern_str in patterns:
        try:
            compiled.append(re.compile(pattern_str))
        except re.error as exc:
            raise click.UsageError(f"Invalid regex in --strip-subtitle-regex: '{pattern_str}' — {exc}") from exc
    return compiled


def _warn_about_redundant_flags(*, keep_audio: bool, keep_undefined_audio: bool, logger: Logger) -> None:
    """Warn when ``--keep-undefined-audio`` cannot have any effect."""
    if keep_audio and keep_undefined_audio:
        logger.warning(
            "--keep-undefined-audio has no effect when --keep-audio is set: every audio track is already kept."
        )


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
    "--keep-undefined-audio",
    is_flag=True,
    default=False,
    help=(
        'If specified, audio tracks with an undefined language code ("und")'
        " are kept rather than dropped by the language filter.  Useful when"
        " source files have missing or incorrect language tags."
        " Ignored when --keep-audio is set."
    ),
)
@click.option(
    "--keep-native-audio",
    is_flag=True,
    default=False,
    help=(
        "If specified, trimarr identifies the film's native/original spoken"
        " language(s) via IMDb, TMDb, and TVDB, and keeps all audio tracks"
        " in those languages alongside your --language preference."
        " Ignored when --keep-audio is set."
        " Requires an internet connection for first-time lookups;"
        " results are cached in the database."
    ),
)
@click.option(
    "--tmdb-api-key",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<key>",
    help=(
        "TMDb API key. The preferred source for NFO/embedded ID lookups"
        " (originalLanguage); IMDb spokenLanguages is the fallback. Optional —"
        " without it, TMDb lookups are skipped."
    ),
)
@click.option(
    "--tvdb-api-key",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<key>",
    help=(
        "TVDB API key.  For TV shows TVDB's originalLanguage is tried before"
        " TMDb in NFO ID lookups; for movies it is the fallback after TMDb."
        "  Needed for TV shows whose NFO files carry only a TVDB ID."
        "  Optional — without it, TVDB lookups are silently skipped."
    ),
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
    help="Logging level for the console and file sinks",
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
    help=(
        "If specified, the script will perform a dry run without modifying any media file"
        " and without writing cache rows.  An existing tracking database may still be"
        " opened and upgraded."
    ),
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
        " the maximum channel count among the surviving audio tracks *of the same language* will be"
        " removed.  For example, with --language eng,fre a file holding 8-channel English and"
        " 2-channel French keeps the French track because it is the best French track."
        " Tracks with an unknown channel count are always preserved, and commentary tracks are"
        " exempt from the filter (use --strip-commentary to remove those)."
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
        " and a warning is logged. Subtitle commentary tracks are stripped unless the subtitle"
        " language fallback fired (no match for --language keeps every subtitle)."
        " Has no effect on audio when --keep-audio is set, or on subtitles when --keep-subtitles is set."
        " Disabled by default."
    ),
)
@click.option(
    "--strip-subtitle-regex",
    type=click.STRING,
    required=False,
    multiple=True,
    default=(),
    metavar="<regex>",
    help=(
        "One or more regex patterns. Any subtitle track whose name matches any pattern"
        " will be removed, regardless of language.  Patterns use Python re syntax."
        " Not applied when the subtitle language fallback triggers (no subtitle track"
        " matches --language, in which case all subtitles are kept) or when"
        " --keep-subtitles is set."
        " Specify multiple times for multiple patterns."
        " Example: --strip-subtitle-regex '(?i)songs.*signs' --strip-subtitle-regex '(?i)signs.*songs'"
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
        "Command to run before processing files in a directory."
        " The command is executed directly, without a shell: it is split POSIX-style"
        " and then run, so pipes, redirection, globbing and variable expansion are"
        " literal arguments rather than shell syntax."
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
        "Command to run after processing files in a directory."
        " The command is executed directly, without a shell: it is split POSIX-style"
        " and then run, so pipes, redirection, globbing and variable expansion are"
        " literal arguments rather than shell syntax."
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
    keep_undefined_audio: bool,
    keep_native_audio: bool,
    tmdb_api_key: str | None,
    tvdb_api_key: str | None,
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
    strip_subtitle_regex: tuple[str, ...],
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
    _validate_option_combinations(
        run_on_start=run_on_start,
        schedule=schedule,
        edit_metadata_title=edit_metadata_title,
        delete_metadata_title=delete_metadata_title,
    )
    subtitle_regex_patterns = _compile_subtitle_patterns(strip_subtitle_regex)

    logger = create_logger(log_format=_LOG_FORMAT, log_level=log_level, log_path=log_path)
    _warn_about_redundant_flags(keep_audio=keep_audio, keep_undefined_audio=keep_undefined_audio, logger=logger)

    mkvmerge_path = _resolve_mkvmerge_path(mkvmerge_path, no_update_check, logger)

    def _run() -> None:
        run(
            language=languages,
            edit_metadata_title=edit_metadata_title,
            delete_metadata_title=delete_metadata_title,
            keep_subtitles=keep_subtitles,
            keep_audio=keep_audio,
            keep_undefined_audio=keep_undefined_audio,
            keep_native_audio=keep_native_audio,
            tmdb_api_key=tmdb_api_key,
            tvdb_api_key=tvdb_api_key,
            media_path=media_path,
            mkvmerge_path=mkvmerge_path,
            database_path=database_path,
            no_backup=no_backup,
            dry_run=dry_run,
            logger=logger,
            strip_lower_channels=strip_lower_channels,
            strip_commentary=strip_commentary,
            strip_subtitle_regex_patterns=subtitle_regex_patterns,
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
