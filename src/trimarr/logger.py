"""Logging utilities for trimarr."""

from __future__ import annotations

import logging
import os
import re
import traceback
from typing import TYPE_CHECKING, Any

from loguru import logger as _logger

if TYPE_CHECKING:
    from types import FrameType

    from loguru import Logger


_CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f\x85\u2028\u2029]")


def _sanitize_record(record: Any) -> None:
    """Rewrite a loguru record so untrusted text cannot forge a log line.

    Applied through ``logger.patch`` in :func:`create_logger`, so every message
    from any module (including records bridged from stdlib logging) is covered
    without each call site remembering to sanitise.
    """
    record["message"] = sanitize_for_log(str(record["message"]))
    exception = record["exception"]
    if exception is not None:
        # A traceback is rendered outside the message, so the message sanitiser
        # never sees it.  Fold it into the message (sanitised) and drop the
        # separate exception: a filename inside the traceback must not be able
        # to forge a log record.
        rendered = "".join(traceback.format_exception(*exception))
        record["message"] = sanitize_for_log(f"{record['message']} | {rendered}")
        record["exception"] = None


def sanitize_for_log(text: str) -> str:
    """Replace control characters so an untrusted name cannot forge log lines.

    Media file and directory names come from release sources, so they may contain
    newlines, Unicode line/paragraph separators (which ``str.splitlines`` honours)
    or ANSI escapes.  Rendering them verbatim lets one name inject fake log
    records; this keeps each value on a single, inert line.
    """
    return _CONTROL_CHARS_RE.sub("?", text)


class _InterceptHandler(logging.Handler):
    """Redirect stdlib :mod:`logging` records into loguru.

    Modules that use ``logging.getLogger(__name__)`` (``native_language``,
    ``_nfo_parser``) would otherwise fall through to logging's last-resort
    handler, which prints WARNING and above to bare stderr and never reaches
    the configured console format or the rotating log file.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Forward *record* to loguru at the equivalent level."""
        try:
            level: str | int = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk past logging's own frames so the record is attributed to the
        # module that called the stdlib logger, not to logging.callHandlers.
        frame: FrameType | None = logging.currentframe()
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _bridge_package_logging() -> None:
    """Route the ``trimarr`` package's stdlib logging into loguru.

    Only the package logger is bridged.  Attaching the handler to the root
    logger would also pull third-party INFO chatter into trimarr's console and
    log file (``imdbpie`` logs every API call), which would bury the operator's
    own diagnostics.
    """
    package_logger = logging.getLogger("trimarr")
    package_logger.handlers = [_InterceptHandler()]
    package_logger.setLevel(logging.DEBUG)
    package_logger.propagate = False


def create_logger(log_format: str, log_level: str = "INFO", log_path: str | None = None) -> Logger:
    """Return a configured Loguru logger instance.

    Stdlib ``logging`` records are intercepted and forwarded to loguru, so
    every module's diagnostics reach the same sinks regardless of which
    logging API it uses.

    Args:
        log_format: Loguru format string for console output.
        log_level: Minimum log level for both sinks.
        log_path: Optional path to a log file. The parent directory is created
            automatically if it does not already exist.
    """
    _logger.configure(patcher=_sanitize_record)
    _bridge_package_logging()
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
        # Only call makedirs when the path has a directory component; a bare
        # filename (e.g. "trimarr.log") would produce an empty string which
        # causes os.makedirs to raise FileNotFoundError.
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        _logger.add(
            sink=log_path,
            level=log_level.upper(),
            format=log_format,
            rotation="10 MB",
            retention=3,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
        )

    return _logger
