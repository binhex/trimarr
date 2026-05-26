"""Shell command hook execution for pre/post processing."""

from __future__ import annotations

import platform
import re
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Logger


def _resolve_hook_template(
    cmd_template: str,
    leaf: str,
    dir_path: str,
    logger: Logger,
) -> list[str] | None:
    """Parse and substitute a hook command template, returning arg list or None."""
    if not cmd_template.strip():
        return None

    # Strip user-supplied quote wrapping around {leaf}/{dir} markers.
    # Use word-boundary assertions so that only standalone quote-wrapped
    # markers are stripped (e.g. ``'{leaf}'``), NOT quotes inside larger
    # arguments such as ``--file="{leaf}"``.
    template = cmd_template
    for marker in ("{leaf}", "{dir}"):
        for q in ("'", '"'):
            escaped_q = re.escape(q)
            escaped_marker = re.escape(marker)
            # Balanced: q+marker+q -> marker
            template = template.replace(f"{q}{marker}{q}", marker)
            # Unbalanced: q+marker (only when q is preceded by start/whitespace)
            template = re.sub(
                rf"(^|\s){escaped_q}{escaped_marker}",
                rf"\1{marker}",
                template,
            )
            # Unbalanced: marker+q (only when marker is at word boundary
            # AND q is followed by whitespace/end).  This avoids stripping
            # quotes inside arguments like ``--file="{leaf}"`` where the
            # closing quote is structural, not user-added wrapping.
            template = re.sub(
                rf"(?:^|(?<=\s)|(?<=['\"])){escaped_marker}{escaped_q}(\s|$)",
                rf"{marker}\1",
                template,
            )

    try:
        posix = platform.system() != "Windows"
        args = shlex.split(template, posix=posix)
    except ValueError as exc:
        logger.warning(f"Hook command template is malformed: {exc}")
        return None

    return [arg.replace("{leaf}", leaf).replace("{dir}", dir_path) for arg in args]


def _run_hook_command(
    args: list[str],
    timeout_seconds: int | None,
    logger: Logger,
) -> None:
    """Execute *args* via subprocess and log any errors."""
    kwargs: dict = {
        "shell": False,
        "capture_output": True,
        "text": True,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds

    cmd_display = " ".join(args)
    logger.debug(f"Running hook: {cmd_display}")

    try:
        result = subprocess.run(args, **kwargs)
    except subprocess.TimeoutExpired:
        timeout_msg = f"{timeout_seconds}s" if timeout_seconds is not None else "(no timeout set)"
        logger.warning(f"Hook command timed out after {timeout_msg}: {cmd_display}")
        return
    except OSError as exc:
        logger.warning(f"Hook command failed: {cmd_display}: {exc}")
        return

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning(f"Hook command exited with code {result.returncode}: {stderr}")


def _run_hook(
    cmd_template: str,
    leaf: str,
    dir_path: str,
    logger: Logger,
    timeout_seconds: int | None = 300,
) -> None:
    """Execute a hook command with ``{leaf}`` and ``{dir}`` variable substitution.

    The template is split into arguments using :func:`shlex.split` (POSIX shell
    rules on Linux/macOS, Windows rules on Windows) and executed directly via
    :func:`subprocess.run` with ``shell=False``.  No shell is involved, so
    shell metacharacters (``|``, ``>``, ``$()``) in argument values are treated
    as literal characters.

    Args:
        cmd_template: The command template, which may contain ``{leaf}`` and
            ``{dir}`` placeholders.
        leaf: The file or directory name to substitute into ``{leaf}``.
        dir_path: The directory path to substitute into ``{dir}``.
        logger: A ``loguru.Logger`` instance used for warning messages.
        timeout_seconds: Maximum execution time in seconds. Passed as the
            ``timeout`` kwarg to :func:`subprocess.run`. If ``None``, no
            timeout is applied.

    Raises:
        None. All expected errors (ValueError from template parsing,
        TimeoutExpired, OSError, non-zero exit) are caught and logged as
        warnings.
    """
    args = _resolve_hook_template(cmd_template, leaf, dir_path, logger)
    if args is None:
        return
    _run_hook_command(args, timeout_seconds, logger)
