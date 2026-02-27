"""Command-line interface for trimarr."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from core.logging import create_logger
from utils.utils import download_mkvmerge, get_project_root

try:
    _VERSION = version("Trimarr")
except PackageNotFoundError:
    _VERSION = "unknown"


# Compute default database path (project_root/db/trimarr.db)
_PROJECT_ROOT = get_project_root()
_DEFAULT_MKVMERGE_PATH = f"{_PROJECT_ROOT}/bin/mkvmerge"
_DEFAULT_DB_PATH = f"{_PROJECT_ROOT}/db/trimarr.db"
_DEFAULT_LOGS_PATH = f"{_PROJECT_ROOT}/logs/trimarr.log"


@click.command()
@click.option(
    "--language",
    type=click.STRING,
    required=True,
    metavar="<language code>",
    help=(
        "Specify the language code for the audio/subtitle tracks to keep, e.g. 'eng' for English."
        "Language codes: http://en.wikipedia.org/wiki/List_of_ISO_639-2_codes"
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
    default=_DEFAULT_MKVMERGE_PATH,
    show_default=True,
    metavar="<mkvmerge path>",
    help="Path to mkvmerge executable.",
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
    "--dry-run",
    is_flag=True,
    default=False,
    help="If specified, the script will perform a dry run without making any changes.",
)
@click.version_option(version=_VERSION, prog_name="Trimarr")
def cli(
    language: str,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    keep_subtitles: bool,
    keep_audio: bool,
    media_path: str,
    mkvmerge_path: str,
    database_path: str,
    log_path: str,
    log_level: str,
    dry_run: bool,
) -> None:
    """Trimarr - Removes (trims) unwanted audio and subtitles from matroska container format video files.

    This script will remove unwanted audio and subtitle tracks from matroska container format video files based on
    user-defined criteria. It uses matroska CLI tools for processing the video files and SQLite for tracking which files
    have already been processed to avoid redundant work.
    """

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level, log_path=log_path)

    if not Path(mkvmerge_path).is_file():
        logger.info(f"mkvmerge not found at '{mkvmerge_path}', downloading latest binary...")
        mkvmerge_path = str(download_mkvmerge(dest_dir=_PROJECT_ROOT / "bin"))
        logger.success(f"mkvmerge installed at: {mkvmerge_path}")

    logger.info("Trimarr CLI is not yet implemented. Please run `trimarr --help` for usage instructions.")


if __name__ == "__main__":
    cli()
