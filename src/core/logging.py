"""Logging utilities for trimarr."""

from __future__ import annotations

import os

from loguru import logger as _logger


def create_logger(log_format: str, log_level: str = "INFO", log_path: str | None = None):
    """Return a configured Loguru logger instance.

    Args:
        log_format: Loguru format string for console output.
        log_level: Minimum log level for both sinks.
        log_path: Optional path to a log file. The parent directory is created
            automatically if it does not already exist.
    """
    _logger.remove()

    # Console sink
    _logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper(),
        format=log_format,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # File sink
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"
        _logger.add(
            sink=log_path,
            level=log_level.upper(),
            format=file_format,
            rotation="10 MB",
            retention=3,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
        )

    return _logger
