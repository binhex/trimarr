"""Trimarr core orchestration: scan, analyse, and trim MKV files."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from trimarr.database import Database
from trimarr.hooks import _run_hook
from trimarr.processor import CorruptOutputError, build_mkvmerge_command, probe_file, process_file

# Width of the separator line used in critical diagnostic messages.
_LOG_SEPARATOR_WIDTH = 80

if TYPE_CHECKING:
    from loguru import Logger


@dataclass(frozen=True)
class _ProcessingConfig:
    """Immutable bundle of all per-run processing options."""

    mkvmerge_path: str
    language: list[str]
    keep_audio: bool
    keep_subtitles: bool
    edit_metadata_title: bool
    delete_metadata_title: bool
    strip_lower_channels: bool
    strip_commentary: bool
    skip_size_check: bool
    dry_run: bool
    no_backup: bool


@dataclass
class _RunCounts:
    """Mutable run counters updated in place during the processing loop."""

    processed: int = 0
    skipped: int = 0
    failed: int = 0
    no_change: int = 0
    bytes_saved: int = 0


def _fmt_bytes(n: int) -> str:
    """Return a human-readable string for a byte count.

    Args:
        n: Number of bytes (may be negative).

    Returns:
        A string such as ``"1.23 GB"`` or ``"512.00 KB"``.
    """
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TB"


def _print_failure_report(failures: list[tuple[Path, str]], logger: Logger) -> None:
    """Log a consolidated list of all files that failed processing.

    Called after the run summary to provide a single point of reference for
    any files that could not be processed, regardless of the reason.  Each
    entry is the file path paired with a concise single-line error reason.

    Args:
        failures: List of ``(file_path, reason)`` pairs collected during the run.
        logger: Loguru logger instance.
    """
    if not failures:
        return
    logger.warning(f"Failed files ({len(failures)}):")
    for file_path, reason in failures:
        logger.warning(f"  {file_path}: {reason}")


def _build_profile_hash(
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    strip_lower_channels: bool,
    strip_commentary: bool,
) -> str:
    """Return a stable SHA-256 hex digest encoding the current processing profile.

    Only parameters that affect *which tracks are kept or modified* are included.
    Run-time options (``dry_run``, ``no_backup``, paths) are deliberately excluded.

    Args:
        language: List of ISO 639-2 language codes to retain (sorted internally).
        keep_audio: Retain all audio tracks regardless of language.
        keep_subtitles: Retain all subtitle tracks regardless of language.
        edit_metadata_title: Set container title to filename stem.
        delete_metadata_title: Clear container title.
        strip_lower_channels: Drop audio tracks below the max channel count.
        strip_commentary: Drop tracks whose name contains "commentary".

    Returns:
        64-character lowercase hex string.
    """
    # Normalise language codes to their bibliographic ISO 639-2 form so that
    # equivalent aliases (e.g. "fre" and "fra") produce the same profile hash.
    from trimarr.processor import normalize_language_code

    canonical_language = sorted(normalize_language_code(c) for c in language)
    profile = {
        "delete_metadata_title": delete_metadata_title,
        "edit_metadata_title": edit_metadata_title,
        "keep_audio": keep_audio,
        "keep_subtitles": keep_subtitles,
        "language": canonical_language,
        "strip_commentary": strip_commentary,
        "strip_lower_channels": strip_lower_channels,
    }
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def _print_summary(
    counts: _RunCounts,
    dry_run: bool,
    interrupted: bool,
    database_path: str,
    logger: Logger,
) -> None:
    """Log the run summary.

    Centralises the summary message so both the normal and interrupted exit paths
    produce consistent output and adding a new counter only requires one change.

    Args:
        counts: _RunCounts with processed, no_change, skipped, failed, bytes_saved.
        dry_run: Whether the run was in dry-run mode.
        interrupted: Whether the run was cut short by :exc:`KeyboardInterrupt`.
        database_path: Path to the tracking DB (used to query all-time savings).
        logger: Loguru logger instance.
    """
    status = "Interrupted after" if interrupted else "Done —"
    if dry_run:
        prefix = "<green>DRY-RUN</green>  | "
        suffix = "Interrupted — no files were modified." if interrupted else "Complete — no files were modified."
        logger.opt(colors=True).info(
            f"{prefix}{suffix} "
            f"Would have processed: {counts.processed}, "
            f"no change needed: {counts.no_change}, "
            f"skipped (already done): {counts.skipped}, "
            f"failed: {counts.failed}."
        )
    else:
        logger.info(
            f"{status} processed: {counts.processed}, "
            f"no change needed: {counts.no_change}, "
            f"skipped (already done): {counts.skipped}, "
            f"failed: {counts.failed}."
        )
        if counts.processed > 0:
            logger.info(
                f"Space saved this session: {_fmt_bytes(counts.bytes_saved)} ({counts.processed} file(s) remuxed)."
            )
        try:
            with Database(database_path) as db_summary:
                all_time_saved = db_summary.total_bytes_saved()
            logger.info(f"Space saved (all sessions): {_fmt_bytes(all_time_saved)}.")
        except sqlite3.Error as exc:
            logger.warning(f"Could not retrieve all-time savings from database: {exc}")


def _discover_mkv_files(media_path: list[str], logger: Logger) -> list[tuple[Path, Path]]:
    """Scan *media_path* roots for .mkv files. Returns deduplicated (mkv_file, root) pairs."""
    all_files: list[tuple[Path, Path]] = []
    for path_str in media_path:
        root = Path(path_str)
        if not root.exists():
            logger.error(f"Media path '{path_str}' does not exist.")
            continue
        if not root.is_dir():
            logger.error(f"Media path '{path_str}' is not a directory.")
            continue
        files = sorted(p for p in root.rglob("*") if p.suffix.lower() == ".mkv")
        if not files:
            logger.warning(f"No .mkv files found under '{path_str}'.")
        else:
            logger.info(f"Found {len(files)} .mkv file(s) under '{path_str}'.")
            all_files.extend((f, root) for f in files)

    return _deduplicate_file_list(all_files)


def _deduplicate_file_list(
    all_files: list[tuple[Path, Path]],
) -> list[tuple[Path, Path]]:
    """Deduplicate *all_files* by resolved path to handle symlinks and overlapping roots."""
    seen: set[Path] = set()
    unique: list[tuple[Path, Path]] = []
    for file_path, root in all_files:
        resolved = file_path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append((file_path, root))
    return unique


def _handle_corrupt_output(
    exc: CorruptOutputError,
    counts: _RunCounts,
    logger: Logger,
) -> None:
    """Log the critical diagnostic report for a :exc:`CorruptOutputError` and exit 2."""
    ratio_pct = (exc.output_size / exc.input_size * 100) if exc.input_size else 0.0
    try:
        disk = shutil.disk_usage(exc.tmp_path.parent)
        free_str = f"{disk.free:,} bytes ({_fmt_bytes(disk.free)}) on '{exc.tmp_path.parent}'"
    except OSError:
        free_str = "unavailable"
    sep = "=" * _LOG_SEPARATOR_WIDTH
    logger.opt(colors=False).critical(
        f"\n{sep}\n"
        "TRIMARR ABORTED — CORRUPT OUTPUT DETECTED\n"
        f"{sep}\n"
        "mkvmerge produced a structurally invalid MKV that failed the integrity check.\n"
        "All processing has been halted immediately to prevent further data loss.\n\n"
        f"  Source file  : {exc.file_path}\n"
        f"  Source size  : {exc.input_size:,} bytes ({_fmt_bytes(exc.input_size)})\n"
        f"  Output file  : {exc.tmp_path}  <-- RETAINED for inspection\n"
        f"  Output size  : {exc.output_size:,} bytes ({_fmt_bytes(exc.output_size)}, {ratio_pct:.1f}% of source)\n"
        f"  Free space   : {free_str}\n"
        f"  mkvmerge     : {exc.mkvmerge_path}\n"
        f"  Probe exit   : {exc.probe_returncode}\n"
        f"  Probe output : {exc.probe_output or '(none)'}\n\n"
        f"  Progress before halt: "
        f"{counts.processed} processed, {counts.skipped} skipped, "
        f"{counts.failed} failed, {counts.no_change} unchanged.\n\n"
        "Possible causes:\n"
        "  * Disk full or near-full during the mkvmerge write\n"
        "  * Network / FUSE filesystem error (e.g. Unraid mergerfs, Samba, NFS)\n"
        "  * Insufficient write permissions on the temp directory\n"
        "  * mkvmerge bug or unsupported container variant\n\n"
        "The ORIGINAL source file has NOT been modified.\n"
        "The corrupt temp file has been retained at the path shown above.\n"
        f'  Inspect with: {exc.mkvmerge_path} -J "{exc.tmp_path}"\n'
        "Delete the temp file and resolve the root cause before re-running trimarr.\n"
        f"{sep}"
    )
    sys.exit(2)


def _process_one_file(
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    db: Database,
    profile_hash: str,
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
) -> None:
    """Process a single MKV file and update the shared run counters.

    Handles database skip-check, probing, command building, dry-run display,
    and actual processing.  Mutates *counts* and *failures*
    in place.  Raises :exc:`CorruptOutputError` without catching it so the outer
    loop can halt all processing immediately.
    """
    # Skip unchanged files processed with the same profile
    if db.is_processed(file_path, profile_hash=profile_hash):
        logger.debug(f"Already processed (unchanged): {file_path}")
        counts.skipped += 1
        return

    logger.info(f"  [{idx}/{total}] Checking '{file_path.relative_to(root)}'...")

    # Probe tracks
    try:
        tracks = probe_file(cfg.mkvmerge_path, file_path)
    except RuntimeError as exc:
        reason = f"probe failed: {str(exc).splitlines()[0].strip()}"
        logger.error(f"Could not probe '{file_path}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1
        return

    # Build the mkvmerge command (returns None if nothing to do)
    cmd = build_mkvmerge_command(
        mkvmerge_path=cfg.mkvmerge_path,
        input_path=file_path,
        output_path=file_path,  # placeholder; process_file patches this
        tracks=tracks,
        language=cfg.language,
        keep_audio=cfg.keep_audio,
        keep_subtitles=cfg.keep_subtitles,
        edit_metadata_title=cfg.edit_metadata_title,
        delete_metadata_title=cfg.delete_metadata_title,
        logger=logger,
        strip_lower_channels=cfg.strip_lower_channels,
        strip_commentary=cfg.strip_commentary,
    )

    if cmd is None:
        if not cfg.dry_run:
            logger.info(f"No changes needed for '{file_path.name}' — marking as processed.")
            db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=0)
        else:
            logger.info(f"No changes needed for '{file_path.name}'.")
        counts.no_change += 1
        return

    if cfg.dry_run:
        # Patch the displayed -o argument to reflect the actual temp-file-then-rename
        # execution strategy; mkvmerge refuses input == output, so showing the raw
        # command (where placeholder output_path == input_path) would be misleading.
        display_cmd = list(cmd)
        with contextlib.suppress(ValueError):
            display_cmd[display_cmd.index("-o") + 1] = "[tmpfile]"
        # Escape angle brackets in dynamic content (file paths, titles) so loguru's
        # colour parser does not mistake them for markup tags.
        cmd_str = " ".join(display_cmd).replace("<", r"\<").replace(">", r"\>")
        logger.opt(colors=True).info(f"<green>DRY-RUN</green>  | Would run: {cmd_str}")
        counts.processed += 1
        return

    # Process the file
    size_before = file_path.stat().st_size
    error = process_file(
        mkvmerge_path=cfg.mkvmerge_path,
        file_path=file_path,
        command=cmd,
        no_backup=cfg.no_backup,
        logger=logger,
        skip_size_check=cfg.skip_size_check,
    )
    if error is None:
        bytes_saved = max(0, size_before - file_path.stat().st_size)
        counts.bytes_saved += bytes_saved
        db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=bytes_saved)
        logger.success(f"Processed: {file_path.name}")
        counts.processed += 1
    else:
        failures.append((file_path, error))
        counts.failed += 1


def _process_one_file_guarded(
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    db: Database,
    profile_hash: str,
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
) -> None:
    """Wrap :func:`_process_one_file` with per-file OSError and sqlite3.Error guards.

    OSError and sqlite3.Error are caught, logged, and counted as failures so
    that a single bad file never aborts the entire batch.  :exc:`CorruptOutputError`
    is intentionally not caught here — it propagates to halt all processing.
    """
    try:
        _process_one_file(
            file_path=file_path,
            root=root,
            idx=idx,
            total=total,
            db=db,
            profile_hash=profile_hash,
            cfg=cfg,
            counts=counts,
            failures=failures,
            logger=logger,
        )
    except OSError as exc:
        reason = f"filesystem error: {str(exc).splitlines()[0].strip()}"
        logger.error(f"File system error processing '{file_path}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1
    except sqlite3.Error as exc:
        reason = f"database error: {str(exc).splitlines()[0].strip()}"
        logger.error(f"Database error processing '{file_path}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1


def _dir_has_work(
    files_in_dir: list[tuple[Path, Path]],
    db: Database,
    profile_hash: str,
    logger: Logger,
) -> bool:
    """Check if any file in *files_in_dir* needs processing (not already processed).

    Returns True at the first file that is not yet processed, or if a
    filesystem or database error occurs (the error is logged and work is
    assumed so that pre hooks fire).
    """
    for fp, _ in files_in_dir:
        try:
            if not db.is_processed(fp, profile_hash=profile_hash):
                return True
        except OSError as exc:
            logger.error(f"File system error processing '{fp}': {exc}")
            return True
        except sqlite3.Error as exc:
            logger.error(f"Database error processing '{fp}': {exc}")
            return True
    return False


def _process_directory_groups(
    dir_groups: OrderedDict[Path, list[tuple[Path, Path]]],
    database_path: str,
    profile_hash: str,
    cfg: _ProcessingConfig,
    total: int,
    pre_process: str | None,
    post_process: str | None,
    command_timeout_seconds: int | None,
    logger: Logger,
) -> tuple[_RunCounts, bool, list[tuple[Path, str]]]:
    """Process files grouped by directory, firing pre/post hooks around each group.

    Returns (counts, interrupted, failures).
    """
    counts = _RunCounts()
    failures: list[tuple[Path, str]] = []
    interrupted = False

    try:
        with Database(database_path) as db:
            global_idx = 0

            for dir_path, files_in_dir in dir_groups.items():
                dir_has_work = _dir_has_work(files_in_dir, db, profile_hash, logger)

                if dir_has_work:
                    leaf = dir_path.name

                    if pre_process is not None:
                        _run_hook(
                            cmd_template=pre_process,
                            leaf=leaf,
                            dir_path=str(dir_path),
                            logger=logger,
                            timeout_seconds=command_timeout_seconds,
                        )

                for file_path, root in files_in_dir:
                    global_idx += 1
                    _process_one_file_guarded(
                        file_path=file_path,
                        root=root,
                        idx=global_idx,
                        total=total,
                        db=db,
                        profile_hash=profile_hash,
                        cfg=cfg,
                        counts=counts,
                        failures=failures,
                        logger=logger,
                    )

                if dir_has_work and post_process is not None:
                    _run_hook(
                        cmd_template=post_process,
                        leaf=leaf,
                        dir_path=str(dir_path),
                        logger=logger,
                        timeout_seconds=command_timeout_seconds,
                    )

    except CorruptOutputError as exc:
        _handle_corrupt_output(exc, counts, logger)
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted — showing partial results.")

    return counts, interrupted, failures


def run(
    language: list[str],
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    keep_subtitles: bool,
    keep_audio: bool,
    media_path: list[str] | str | os.PathLike[str],
    mkvmerge_path: str,
    database_path: str,
    no_backup: bool,
    dry_run: bool,
    logger: Logger,
    strip_lower_channels: bool = False,
    strip_commentary: bool = False,
    skip_size_check: bool = False,
    pre_process: str | None = None,
    post_process: str | None = None,
    command_timeout_mins: int = 5,
) -> None:
    """Scan *media_path* directories and process every MKV file found.

    Already-processed files (same fingerprint and profile) are skipped silently.
    On dry-run the planned command is logged; nothing on disk is modified.
    Raises :exc:`SystemExit` (code 130) on :exc:`KeyboardInterrupt`.
    """
    if isinstance(media_path, (str, os.PathLike)):
        media_path = [str(media_path)]

    unique_files = _discover_mkv_files(media_path, logger)

    if not unique_files:
        return

    if dry_run:
        logger.opt(colors=True).info(
            "<green>DRY-RUN</green>  | No files will be modified — logging planned changes only."
        )

    profile_hash = _build_profile_hash(
        language=language,
        keep_audio=keep_audio,
        keep_subtitles=keep_subtitles,
        edit_metadata_title=edit_metadata_title,
        delete_metadata_title=delete_metadata_title,
        strip_lower_channels=strip_lower_channels,
        strip_commentary=strip_commentary,
    )

    cfg = _ProcessingConfig(
        mkvmerge_path=mkvmerge_path,
        language=list(language),
        keep_audio=keep_audio,
        keep_subtitles=keep_subtitles,
        edit_metadata_title=edit_metadata_title,
        delete_metadata_title=delete_metadata_title,
        strip_lower_channels=strip_lower_channels,
        strip_commentary=strip_commentary,
        skip_size_check=skip_size_check,
        dry_run=dry_run,
        no_backup=no_backup,
    )

    command_timeout_seconds: int | None = command_timeout_mins * 60 if command_timeout_mins > 0 else None

    total = len(unique_files)
    # Group files by their parent directory for hook support
    dir_groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict()
    for file_path, root in unique_files:
        dir_groups.setdefault(file_path.parent, []).append((file_path, root))

    counts, interrupted, failures = _process_directory_groups(
        dir_groups=dir_groups,
        database_path=database_path,
        profile_hash=profile_hash,
        cfg=cfg,
        total=total,
        pre_process=pre_process,
        post_process=post_process,
        command_timeout_seconds=command_timeout_seconds,
        logger=logger,
    )

    _print_summary(counts, dry_run, interrupted, database_path, logger)
    _print_failure_report(failures, logger)

    if interrupted:
        sys.exit(130)
