"""Shell command hook execution for pre/post processing."""

from __future__ import annotations

import platform
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Logger


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
    if not cmd_template.strip():
        return

    # Strip user-supplied quote wrapping around {leaf}/{dir} markers —
    # the quoting fix from commit 4a8f86c prevents double-quoting when
    # users write ``--include-folders '{leaf}'``.
    # Handle both balanced (``'{leaf}'``) and unbalanced (``'{leaf}``)
    # wrapping so that shlex.split does not fail on a lone quote.
    template = cmd_template
    for marker in ("{leaf}", "{dir}"):
        for q in ("'", '"'):
            template = template.replace(f"{q}{marker}", marker)
            template = template.replace(f"{marker}{q}", marker)

    # Parse the template into a list of arguments using shlex.split() so
    # shell metacharacters (|, >, $, etc.) in argument values are treated
    # as literal characters, not interpreted by the shell.  Without this,
    # ``--media-shares Movies|TV`` would be split by the shell into two
    # commands at the pipe.
    try:
        posix = platform.system() != "Windows"
        args = shlex.split(template, posix=posix)
    except ValueError as exc:
        logger.warning(f"Hook command template is malformed: {exc}")
        return
    # Substitute placeholders directly into the arg list (no shell
    # involvement, so no quoting needed).
    args = [arg.replace("{leaf}", leaf).replace("{dir}", dir_path) for arg in args]

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
        logger.warning(f"Hook command timed out after {timeout_seconds}s: {cmd_display}")
        return
    except OSError as exc:
        logger.warning(f"Hook command failed: {cmd_display}: {exc}")
        return

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning(f"Hook command exited with code {result.returncode}: {stderr}")
