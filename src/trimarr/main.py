"""Trimarr core orchestration: scan, analyse, and trim MKV files."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.database import Database
from core.processor import build_mkvmerge_command, probe_file, process_file

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


def run(
    language: str,
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
) -> None:
    """Scan *media_path* and trim unwanted tracks from every MKV file found.

    Files that have already been processed (same path **and** same content
    fingerprint) are silently skipped.  On dry-run the planned mkvmerge command
    is logged but nothing on disk is modified.

    Args:
        language: ISO 639-2 language code to retain (e.g. ``"eng"``).
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
    """
    root = Path(media_path)
    mkv_files = sorted(root.rglob("*.mkv"))

    if not mkv_files:
        logger.warning(f"No .mkv files found under '{media_path}'.")
        return

    logger.info(f"Found {len(mkv_files)} .mkv file(s) under '{media_path}'.")

    if dry_run:
        logger.opt(colors=True).info(
            "<green>DRY-RUN</green>  | No files will be modified — logging planned changes only."
        )

    total = len(mkv_files)
    counts: dict[str, int] = {"processed": 0, "skipped": 0, "failed": 0, "no_change": 0}
    session_bytes_saved: int = 0
    interrupted = False

    try:
        with Database(database_path) as db:
            for idx, file_path in enumerate(mkv_files, 1):
                # Skip unchanged files
                if db.is_processed(file_path):
                    logger.debug(f"Already processed (unchanged): {file_path}")
                    counts["skipped"] += 1
                    continue

                logger.info(f"  [{idx}/{total}] Checking '{file_path.relative_to(root)}'...")

                # Probe tracks
                try:
                    tracks = probe_file(mkvmerge_path, file_path)
                except RuntimeError as exc:
                    logger.error(f"Could not probe '{file_path}': {exc}")
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
                )

                if cmd is None:
                    if not dry_run:
                        logger.info(f"No changes needed for '{file_path.name}' — marking as processed.")
                        db.mark_processed(file_path, bytes_saved=0)
                    else:
                        logger.info(f"No changes needed for '{file_path.name}'.")
                    counts["no_change"] += 1
                    continue

                if dry_run:
                    logger.opt(colors=True).info(f"<green>DRY-RUN</green>  | Would run: {' '.join(cmd)}")
                    counts["processed"] += 1
                    continue

                # Process the file
                size_before = file_path.stat().st_size
                success = process_file(
                    mkvmerge_path=mkvmerge_path,
                    file_path=file_path,
                    command=cmd,
                    no_backup=no_backup,
                    logger=logger,
                )
                if success:
                    bytes_saved = size_before - file_path.stat().st_size
                    session_bytes_saved += bytes_saved
                    db.mark_processed(file_path, bytes_saved=bytes_saved)
                    logger.success(f"Processed: {file_path.name}")
                    counts["processed"] += 1
                else:
                    counts["failed"] += 1

    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted — showing partial results.")

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
        with Database(database_path) as db_summary:
            all_time_saved = db_summary.total_bytes_saved()
        logger.info(f"Space saved (all sessions): {_fmt_bytes(all_time_saved)}.")

    if interrupted:
        sys.exit(130)
