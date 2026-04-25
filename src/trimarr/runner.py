"""Trimarr core orchestration: scan, analyse, and trim MKV files."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from trimarr.database import Database
from trimarr.processor import CorruptOutputError, build_mkvmerge_command, probe_file, process_file

if TYPE_CHECKING:
    from loguru import Logger


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
    profile = {
        "delete_metadata_title": delete_metadata_title,
        "edit_metadata_title": edit_metadata_title,
        "keep_audio": keep_audio,
        "keep_subtitles": keep_subtitles,
        "language": sorted(language),
        "strip_commentary": strip_commentary,
        "strip_lower_channels": strip_lower_channels,
    }
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def _print_summary(
    counts: dict[str, int],
    session_bytes_saved: int,
    dry_run: bool,
    interrupted: bool,
    database_path: str,
    logger: Logger,
) -> None:
    """Log the run summary.

    Centralises the summary message so both the normal and interrupted exit paths
    produce consistent output and adding a new counter only requires one change.

    Args:
        counts: Dict with keys ``processed``, ``no_change``, ``skipped``, ``failed``.
        session_bytes_saved: Bytes reclaimed during this run (real mode only).
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
            f"Would have processed: {counts['processed']}, "
            f"no change needed: {counts['no_change']}, "
            f"skipped (already done): {counts['skipped']}, "
            f"failed: {counts['failed']}."
        )
    else:
        logger.info(
            f"{status} processed: {counts['processed']}, "
            f"no change needed: {counts['no_change']}, "
            f"skipped (already done): {counts['skipped']}, "
            f"failed: {counts['failed']}."
        )
        if counts["processed"] > 0:
            logger.info(
                f"Space saved this session: {_fmt_bytes(session_bytes_saved)} ({counts['processed']} file(s) remuxed)."
            )
        try:
            with Database(database_path) as db_summary:
                all_time_saved = db_summary.total_bytes_saved()
            logger.info(f"Space saved (all sessions): {_fmt_bytes(all_time_saved)}.")
        except sqlite3.Error as exc:
            logger.warning(f"Could not retrieve all-time savings from database: {exc}")


def run(
    language: list[str],
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    keep_subtitles: bool,
    keep_audio: bool,
    media_path: str,
    mkvmerge_path: str,
    database_path: str,
    no_backup: bool,
    dry_run: bool,
    logger: Logger,
    strip_lower_channels: bool = False,
    strip_commentary: bool = False,
    skip_size_check: bool = False,
) -> None:
    """Scan *media_path* and trim unwanted tracks from every MKV file found.

    Files that have already been processed (same path **and** same content
    fingerprint) are silently skipped.  On dry-run the planned mkvmerge command
    is logged but nothing on disk is modified.

    Args:
        language: One or more ISO 639-2 language codes to retain (e.g. ``["eng"]``
            or ``["eng", "fre"]``).
        edit_metadata_title: Set each file's container title to its filename stem.
        delete_metadata_title: Clear each file's container title.
        keep_subtitles: Retain all subtitle tracks regardless of language.
        keep_audio: Retain all audio tracks regardless of language.
        media_path: Root directory to search for ``.mkv`` files recursively.
        mkvmerge_path: Path to the mkvmerge binary.
        database_path: Path to the SQLite tracking database.
        no_backup: When *True*, delete the original instead of renaming it to
            ``<name>.bak`` before replacing it with the processed copy.
        dry_run: When *True*, log planned changes without modifying any files.
        logger: Loguru logger instance.
        strip_lower_channels: When *True*, after language filtering, drop any
            audio tracks with a channel count below the maximum among surviving
            audio tracks.
        strip_commentary: When *True*, audio and subtitle tracks whose name
            contains "commentary" (case-insensitive) are removed after language
            filtering and safety fallbacks.  Standard safety fallbacks apply.
        skip_size_check: When *True*, bypass the suspiciously-small output size
            guard that rejects files whose mkvmerge output is less than 50 % of
            the source size.
    """
    root = Path(media_path)

    if not root.exists():
        logger.error(f"Media path '{media_path}' does not exist.")
        return
    if not root.is_dir():
        logger.error(f"Media path '{media_path}' is not a directory.")
        return

    mkv_files = sorted(p for p in root.rglob("*") if p.suffix.lower() == ".mkv")

    if not mkv_files:
        logger.warning(f"No .mkv files found under '{media_path}'.")
        return

    logger.info(f"Found {len(mkv_files)} .mkv file(s) under '{media_path}'.")

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

    total = len(mkv_files)
    counts: dict[str, int] = {"processed": 0, "skipped": 0, "failed": 0, "no_change": 0}
    session_bytes_saved: int = 0
    interrupted = False
    failures: list[tuple[Path, str]] = []

    try:
        with Database(database_path) as db:
            for idx, file_path in enumerate(mkv_files, 1):
                try:
                    # Skip unchanged files processed with the same profile
                    if db.is_processed(file_path, profile_hash=profile_hash):
                        logger.debug(f"Already processed (unchanged): {file_path}")
                        counts["skipped"] += 1
                        continue

                    logger.info(f"  [{idx}/{total}] Checking '{file_path.relative_to(root)}'...")

                    # Probe tracks
                    try:
                        tracks = probe_file(mkvmerge_path, file_path)
                    except RuntimeError as exc:
                        reason = f"probe failed: {str(exc).splitlines()[0].strip()}"
                        logger.error(f"Could not probe '{file_path}': {exc}")
                        failures.append((file_path, reason))
                        counts["failed"] += 1
                        continue

                    # Build the mkvmerge command (returns None if nothing to do)
                    cmd = build_mkvmerge_command(
                        mkvmerge_path=mkvmerge_path,
                        input_path=file_path,
                        output_path=file_path,  # placeholder; process_file patches this
                        tracks=tracks,
                        language=language,
                        keep_audio=keep_audio,
                        keep_subtitles=keep_subtitles,
                        edit_metadata_title=edit_metadata_title,
                        delete_metadata_title=delete_metadata_title,
                        logger=logger,
                        strip_lower_channels=strip_lower_channels,
                        strip_commentary=strip_commentary,
                    )

                    if cmd is None:
                        if not dry_run:
                            logger.info(f"No changes needed for '{file_path.name}' — marking as processed.")
                            db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=0)
                        else:
                            logger.info(f"No changes needed for '{file_path.name}'.")
                        counts["no_change"] += 1
                        continue

                    if dry_run:
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
                        counts["processed"] += 1
                        continue

                    # Process the file
                    size_before = file_path.stat().st_size
                    error = process_file(
                        mkvmerge_path=mkvmerge_path,
                        file_path=file_path,
                        command=cmd,
                        no_backup=no_backup,
                        logger=logger,
                        skip_size_check=skip_size_check,
                    )
                    if error is None:
                        bytes_saved = max(0, size_before - file_path.stat().st_size)
                        session_bytes_saved += bytes_saved
                        db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=bytes_saved)
                        logger.success(f"Processed: {file_path.name}")
                        counts["processed"] += 1
                    else:
                        failures.append((file_path, error))
                        counts["failed"] += 1

                except OSError as exc:
                    reason = f"filesystem error: {str(exc).splitlines()[0].strip()}"
                    logger.error(f"File system error processing '{file_path}': {exc}")
                    failures.append((file_path, reason))
                    counts["failed"] += 1
                except sqlite3.Error as exc:
                    reason = f"database error: {str(exc).splitlines()[0].strip()}"
                    logger.error(f"Database error processing '{file_path}': {exc}")
                    failures.append((file_path, reason))
                    counts["failed"] += 1

    except CorruptOutputError as exc:
        ratio_pct = (exc.output_size / exc.input_size * 100) if exc.input_size else 0.0
        try:
            disk = shutil.disk_usage(exc.tmp_path.parent)
            free_str = f"{disk.free:,} bytes ({_fmt_bytes(disk.free)}) on '{exc.tmp_path.parent}'"
        except OSError:
            free_str = "unavailable"
        sep = "=" * 80
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
            f"{counts['processed']} processed, {counts['skipped']} skipped, "
            f"{counts['failed']} failed, {counts['no_change']} unchanged.\n\n"
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

    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted — showing partial results.")

    _print_summary(counts, session_bytes_saved, dry_run, interrupted, database_path, logger)
    _print_failure_report(failures, logger)

    if interrupted:
        sys.exit(130)
