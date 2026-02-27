"""Command-line interface for trimarr."""

from importlib.metadata import version, PackageNotFoundError

import click

from core.logging import create_logger
from utils.utils import get_project_root

try:
    _VERSION = version("Trimarr")
except PackageNotFoundError:
    _VERSION = "unknown"


# Compute default database path (project_root/db/trimarr.db)
_PROJECT_ROOT = get_project_root()
_DEFAULT_DB_PATH = f"{_PROJECT_ROOT}/db/trimarr.db"

@click.command()
@click.option(
    "--database-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_DB_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to SQLite database file for tracking processed files.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    metavar="<level>",
    show_default=True,
    help="Logging level for console output",
)
@click.version_option(version=_VERSION, prog_name="Trimarr")
def cli(
    database_path: str | None,
    log_level: str,
) -> None:
    """Trimarr - Removes (trims) unwanted audio and subtitles from matroska container format video files.

    This script will remove unwanted audio and subtitle tracks from matroska container format video files based on
    user-defined criteria. It uses matroska CLI tools for processing the video files and SQLite for tracking which files
    have already been processed to avoid redundant work.
    """

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level)

    logger.info("Trimarr CLI is not yet implemented. Please run `trimarr --help` for usage instructions.")

if __name__ == "__main__":
    cli()
