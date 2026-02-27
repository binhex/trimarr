"""Logging utilities for seakarr."""

from __future__ import annotations

from loguru import logger as _logger


def create_logger(log_format: str, log_level: str = "INFO"):
    """Return a configured Loguru logger instance."""
    _logger.remove()
    _logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper(),
        format=log_format,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )
    return _logger
