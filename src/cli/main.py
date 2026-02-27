"""Command-line interface for AppName."""

import click

from core.logging import create_logger
from utils.utils import get_project_root


# Compute default database path (project_root/db/AppName.db)
_PROJECT_ROOT = get_project_root()
_DEFAULT_DB_PATH = f"{_PROJECT_ROOT}/db/AppName.db"

@click.command()
@click.option(
    "--database-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_DB_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to SQLite database file for tracking processed albums in automatic mode",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    metavar="<level>",
    show_default=True,
    help="Logging level for console output",
)
@click.version_option()
def cli(
    database_path: str | None,
    log_level: str,
) -> None:
    """AppName - Short description.

    Long description
    """

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level)


if __name__ == "__main__":
    cli()
