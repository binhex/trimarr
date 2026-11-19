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
from trimarr.logger import sanitize_for_log
from trimarr.processor import (
    CorruptOutputError,
    build_mkvmerge_command,
    probe_container_title,
    probe_file,
    process_file,
)

# Width of the separator line used in critical diagnostic messages.
_LOG_SEPARATOR_WIDTH = 80

if TYPE_CHECKING:
    import re

    from loguru import Logger


@dataclass(frozen=True)
class _ProcessingConfig:
    """Immutable bundle of all per-run processing options."""

    mkvmerge_path: str
    language: list[str]
    keep_audio: bool
    keep_native_audio: bool
    keep_subtitles: bool
    edit_metadata_title: bool
    delete_metadata_title: bool
    strip_lower_channels: bool
    strip_commentary: bool
    skip_size_check: bool
    dry_run: bool
    no_backup: bool
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None
    tmdb_api_key: str | None = None
    tvdb_api_key: str | None = None
    keep_undefined_audio: bool = False


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


def _run_dir_hook(
    cmd_template: str | None,
    dir_has_work: bool,
    dry_run: bool,
    leaf: str,
    dir_path: str,
    logger: Logger,
    timeout_seconds: int | None,
) -> None:
    """Run a pre or post process hook for a directory group.

    The hook only fires when all of these hold:

    * *cmd_template* is not *None* (the user configured a hook).
    * *dir_has_work* is *True* (at least one file needs processing).
    * *dry_run* is *False* (we are in normal mode).

    Args:
        cmd_template: The user-supplied hook command template (may be ``None``).
        dir_has_work: Whether any file in the directory group needs processing.
        dry_run: Whether the run is in dry-run mode.
        leaf: The leaf directory name (for ``{leaf}`` substitution).
        dir_path: The full directory path (for ``{dir}`` substitution).
        logger: Loguru logger instance.
        timeout_seconds: Hook execution timeout (passed through to :func:`_run_hook`).
    """
    if cmd_template is not None and dir_has_work and not dry_run:
        _run_hook(
            cmd_template=cmd_template,
            leaf=leaf,
            dir_path=dir_path,
            logger=logger,
            timeout_seconds=timeout_seconds,
        )


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
        logger.warning(f"  {sanitize_for_log(str(file_path))}: {sanitize_for_log(reason)}")


def _build_profile_hash(
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    strip_lower_channels: bool,
    strip_commentary: bool,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
    keep_undefined_audio: bool = False,
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
        strip_subtitle_regex_patterns: Drop subtitle tracks whose name matches any of these regexes.
        keep_undefined_audio: Keep audio tracks whose language is unknown (hashed only when *True*).

    Returns:
        64-character lowercase hex string.
    """
    # Normalise language codes to their bibliographic ISO 639-2 form so that
    # equivalent aliases (e.g. "fre" and "fra") produce the same profile hash.
    from trimarr.processor import normalize_language_code

    canonical_language = sorted({normalize_language_code(c) for c in language})
    profile = {
        "delete_metadata_title": delete_metadata_title,
        "edit_metadata_title": edit_metadata_title,
        "keep_audio": keep_audio,
        "keep_subtitles": keep_subtitles,
        "language": canonical_language,
        "strip_commentary": strip_commentary,
        "strip_lower_channels": strip_lower_channels,
    }
    # Conditionally include optional features so the hash stays
    # backward-compatible with databases created before this feature existed.
    # When no patterns are configured the profile dict has exactly the same
    # keys as the pre-feature code, avoiding a full re-scan on upgrade.
    if strip_subtitle_regex_patterns:
        profile["subtitle_regex_patterns"] = sorted(p.pattern for p in strip_subtitle_regex_patterns)
    if keep_undefined_audio:
        profile["keep_undefined_audio"] = True
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
        except (sqlite3.Error, OSError) as exc:
            # The database may be unreachable for the same reason the run failed;
            # the summary is best-effort and must not raise out of run().
            logger.warning(f"Could not retrieve all-time savings from database: {exc}")


def _discover_mkv_files(media_path: list[str], logger: Logger) -> list[tuple[Path, Path]]:
    """Scan *media_path* roots for .mkv files. Returns deduplicated (mkv_file, root) pairs.

    Uses ``glob("**/*.[Mm][Kk][Vv]")`` instead of ``sorted(rglob("*"))`` with a suffix filter.
    The glob pattern is evaluated at the ``os.scandir`` selector level, so
    non-MKV entries (thumbnails, subtitles, metadata) are never wrapped in
    Python :class:`~pathlib.Path` objects.  This avoids the pymalloc arena
    fragmentation that caused unbounded RSS growth (~1 GB/min) when the
    scheduler repeatedly iterated millions of directory entries.
    """
    seen: set[Path] = set()
    result: list[tuple[Path, Path]] = []
    for path_str in media_path:
        root = Path(path_str)
        if not root.exists():
            logger.error(f"Media path '{path_str}' does not exist.")
            continue
        if not root.is_dir():
            logger.error(f"Media path '{path_str}' is not a directory.")
            continue
        files = sorted(root.glob("**/*.[Mm][Kk][Vv]"))
        if not files:
            logger.warning(f"No .mkv files found under '{path_str}'.")
        else:
            logger.info(f"Found {len(files)} .mkv file(s) under '{path_str}'.")
            for file_path in files:
                resolved = file_path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    result.append((file_path, root))
    return result


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
    # Sanitise the untrusted fields BEFORE composing the multi-line report: a
    # newline inside a filename or probe log must not become an extra line.
    safe_file = sanitize_for_log(str(exc.file_path))
    safe_tmp = sanitize_for_log(str(exc.tmp_path))
    safe_probe = sanitize_for_log(exc.probe_output or "(none)")
    safe_mkvmerge = sanitize_for_log(exc.mkvmerge_path)
    report = (
        f"{sep}\n"
        "TRIMARR ABORTED — CORRUPT OUTPUT DETECTED\n"
        f"{sep}\n"
        "mkvmerge produced a structurally invalid MKV that failed the integrity check.\n"
        "All processing has been halted immediately to prevent further data loss.\n\n"
        f"  Source file  : {safe_file}\n"
        f"  Source size  : {exc.input_size:,} bytes ({_fmt_bytes(exc.input_size)})\n"
        f"  Output file  : {safe_tmp}  <-- RETAINED for inspection\n"
        f"  Output size  : {exc.output_size:,} bytes ({_fmt_bytes(exc.output_size)}, {ratio_pct:.1f}% of source)\n"
        f"  Free space   : {free_str}\n"
        f"  mkvmerge     : {safe_mkvmerge}\n"
        f"  Probe exit   : {exc.probe_returncode}\n"
        f"  Probe output : {safe_probe}\n\n"
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
        f'  Inspect with: {safe_mkvmerge} -J "{safe_tmp}"\n'
        "Delete the temp file and resolve the root cause before re-running trimarr.\n"
        f"{sep}"
    )
    # One record per line: the central log sanitiser flattens newlines, so a
    # multi-line message would otherwise render as a single '?'-joined line.
    for line in report.splitlines():
        if line:
            logger.opt(colors=False).critical(sanitize_for_log(line))
    sys.exit(2)


def _resolve_effective_language(
    file_path: Path,
    cfg: _ProcessingConfig,
    db: Database | None,
    logger: Logger,
) -> list[str]:
    """Return the effective language list for *file_path*.

    If ``keep_native_audio`` is set and ``keep_audio`` is not, merges
    the film's native language(s) into the user's ``--language`` list.
    The original ``cfg.language`` list is never mutated.
    """
    if not cfg.keep_native_audio or cfg.keep_audio:
        return cfg.language

    from trimarr.native_language import resolve_native_language  # noqa: PLC0415
    from trimarr.processor import normalize_language_code  # noqa: PLC0415

    native = resolve_native_language(
        file_path=file_path,
        db=db,
        tmdb_api_key=cfg.tmdb_api_key,
        tvdb_api_key=cfg.tvdb_api_key,
    )
    if not native:
        return cfg.language

    # Canonicalize and deduplicate
    canonical_user = [normalize_language_code(c) for c in cfg.language]
    canonical_native = [normalize_language_code(c) for c in native]
    seen = set(canonical_user)
    result = canonical_user + [c for c in canonical_native if c not in seen]
    return result


def _probe_file_safe(
    mkvmerge_path: str,
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    cfg: _ProcessingConfig,
    logger: Logger,
    failures: list[tuple[Path, str]],
    counts: _RunCounts,
) -> tuple[list, str] | None:
    """Probe MKV tracks and container title.

    Returns ``(tracks, current_title)`` on success, or *None* on failure
    (logs, records the failure, and lets the caller return early).
    """
    logger.info(f"  [{idx}/{total}] Checking '{file_path.relative_to(root)}'...")

    try:
        tracks = probe_file(mkvmerge_path, file_path)
    except RuntimeError as exc:
        reason = f"probe failed: {str(exc).splitlines()[0].strip()}"
        logger.error(f"Could not probe '{sanitize_for_log(str(file_path))}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1
        return None

    current_title = ""
    if cfg.edit_metadata_title or cfg.delete_metadata_title:
        try:
            current_title = probe_container_title(mkvmerge_path, file_path)
        except RuntimeError as exc:
            reason = f"probe title failed: {str(exc).splitlines()[0].strip()}"
            logger.error(f"Could not probe container title for '{sanitize_for_log(str(file_path))}': {exc}")
            failures.append((file_path, reason))
            counts.failed += 1
            return None

    return tracks, current_title


def _process_one_file(
    file_path: Path,
    root: Path,
    idx: int,
    total: int,
    db: Database,
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
    confirmed_processed: frozenset[Path] = frozenset(),
) -> None:
    """Process a single MKV file and update the shared run counters.

    Handles database skip-check, probing, command building, dry-run display,
    and actual processing.  Mutates *counts* and *failures*
    in place.  Raises :exc:`CorruptOutputError` without catching it so the outer
    loop can halt all processing immediately.

    *confirmed_processed* holds paths :func:`_dir_has_work` already verified as
    processed against the same profile hash, so they are not fingerprinted again.
    """
    # Compute effective language for this file.  Dry-run keeps the cache handle
    # (so already-resolved files are not re-queried) but the database is opened
    # with cache writes disabled, so nothing is stored.
    effective_language = _resolve_effective_language(
        file_path=file_path,
        cfg=cfg,
        db=db,
        logger=logger,
    )

    # Build per-file profile hash
    profile_hash = _build_profile_hash(
        language=effective_language,
        keep_audio=cfg.keep_audio,
        keep_subtitles=cfg.keep_subtitles,
        edit_metadata_title=cfg.edit_metadata_title,
        delete_metadata_title=cfg.delete_metadata_title,
        strip_lower_channels=cfg.strip_lower_channels,
        strip_commentary=cfg.strip_commentary,
        strip_subtitle_regex_patterns=cfg.strip_subtitle_regex_patterns,
        keep_undefined_audio=cfg.keep_undefined_audio,
    )

    # Skip unchanged files processed with the same profile
    if file_path in confirmed_processed:
        logger.debug(f"Already processed (unchanged): {sanitize_for_log(str(file_path))}")
        counts.skipped += 1
        return
    if db.is_processed(file_path, profile_hash=profile_hash):
        logger.debug(f"Already processed (unchanged): {sanitize_for_log(str(file_path))}")
        counts.skipped += 1
        return

    # Probe tracks and container title
    probed = _probe_file_safe(
        mkvmerge_path=cfg.mkvmerge_path,
        file_path=file_path,
        root=root,
        idx=idx,
        total=total,
        cfg=cfg,
        logger=logger,
        failures=failures,
        counts=counts,
    )
    if probed is None:
        return
    tracks, current_title = probed

    # Build the mkvmerge command (returns None if nothing to do)
    cmd = build_mkvmerge_command(
        mkvmerge_path=cfg.mkvmerge_path,
        input_path=file_path,
        output_path=file_path,  # placeholder; process_file patches this
        tracks=tracks,
        language=effective_language,
        keep_audio=cfg.keep_audio,
        keep_subtitles=cfg.keep_subtitles,
        edit_metadata_title=cfg.edit_metadata_title,
        delete_metadata_title=cfg.delete_metadata_title,
        logger=logger,
        strip_lower_channels=cfg.strip_lower_channels,
        strip_commentary=cfg.strip_commentary,
        strip_subtitle_regex_patterns=cfg.strip_subtitle_regex_patterns,
        keep_undefined_audio=cfg.keep_undefined_audio,
        current_title=current_title,
    )

    if cmd is None:
        # A file that needs no changes is already "correct".  In a real run it
        # is recorded so subsequent runs skip the mkvmerge -J probe (which
        # inflates the kernel page cache, visible as Docker container memory).
        # Dry-run writes nothing: the README and --help both promise that.
        if cfg.dry_run:
            logger.info(f"No changes needed for '{sanitize_for_log(file_path.name)}'.")
            counts.no_change += 1
            return
        logger.info(f"No changes needed for '{sanitize_for_log(file_path.name)}' — marking as processed.")
        db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=0)
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
    logger.info(f"  [{idx}/{total}] Remuxing '{sanitize_for_log(file_path.name)}'...")

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
        logger.success(f"Processed: {sanitize_for_log(file_path.name)}")
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
    cfg: _ProcessingConfig,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
    confirmed_processed: frozenset[Path] = frozenset(),
) -> None:
    """Wrap :func:`_process_one_file` with per-file OSError and sqlite3.Error guards.

    OSError and sqlite3.Error are caught, logged, and counted as failures so
    that a single bad file never aborts the entire batch.  :exc:`CorruptOutputError`
    is intentionally not caught here — it propagates to halt all processing.

    *confirmed_processed* carries the paths ``_dir_has_work`` already verified
    against the same profile hash, so they are not fingerprinted a second time.
    """
    try:
        _process_one_file(
            file_path=file_path,
            root=root,
            idx=idx,
            total=total,
            db=db,
            cfg=cfg,
            counts=counts,
            failures=failures,
            logger=logger,
            confirmed_processed=confirmed_processed,
        )
    except OSError as exc:
        reason = f"filesystem error: {str(exc).splitlines()[0].strip()}"
        logger.error(f"File system error processing '{sanitize_for_log(str(file_path))}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1
    except sqlite3.Error as exc:
        reason = f"database error: {str(exc).splitlines()[0].strip()}"
        logger.error(f"Database error processing '{sanitize_for_log(str(file_path))}': {exc}")
        failures.append((file_path, reason))
        counts.failed += 1


def _dir_has_work(
    files_in_dir: list[tuple[Path, Path]],
    db: Database,
    profile_hash: str,
    cfg: _ProcessingConfig,
    logger: Logger,
) -> tuple[bool, frozenset[Path]]:
    """Check if any file in *files_in_dir* needs processing (not already processed).

    Returns ``(has_work, confirmed_processed)``.  ``has_work`` is True at the
    first file that is not yet processed, or if a filesystem or database error
    occurs (the error is logged and work is assumed so that pre hooks fire).
    It is False only when every file in *files_in_dir* is confirmed already
    processed and no errors occurred.

    ``confirmed_processed`` lists the files this check already verified against
    *profile_hash*, so the per-file pass need not fingerprint them again.
    """
    # When keep_native_audio is active, conservatively fire hooks.
    # The *profile_hash* passed to this function is the global pre-native-language
    # hash, so ``is_processed(fp, profile_hash=profile_hash)`` would not reflect
    # the per-file effective language merged by ``_process_one_file``.
    # Rather than probing every file just to decide hooks, always assume work.
    if cfg.keep_native_audio and not cfg.keep_audio:
        return True, frozenset()

    confirmed: set[Path] = set()
    for fp, _ in files_in_dir:
        try:
            if not db.is_processed(fp, profile_hash=profile_hash):
                return True, frozenset(confirmed)
            confirmed.add(fp)
        except OSError as exc:
            logger.error(f"File system error processing '{sanitize_for_log(str(fp))}': {exc}")
            return True, frozenset()
        except sqlite3.Error as exc:
            logger.error(f"Database error processing '{sanitize_for_log(str(fp))}': {exc}")
            return True, frozenset()
    return False, frozenset(confirmed)


def _process_all_groups(
    db: Database,
    dir_groups: OrderedDict[Path, list[tuple[Path, Path]]],
    profile_hash: str,
    cfg: _ProcessingConfig,
    total: int,
    pre_process: str | None,
    post_process: str | None,
    command_timeout_seconds: int | None,
    counts: _RunCounts,
    failures: list[tuple[Path, str]],
    logger: Logger,
) -> None:
    """Run the per-directory hook/file loop over *dir_groups*.

    Mutates *counts* and *failures* in place.  Kept separate from
    :func:`_process_directory_groups` so the database-error handling and the
    loop body stay individually simple.
    """
    global_idx = 0
    for dir_path, files_in_dir in dir_groups.items():
        dir_has_work, confirmed_processed = _dir_has_work(files_in_dir, db, profile_hash, cfg, logger)
        leaf = dir_path.name

        _run_dir_hook(
            cmd_template=pre_process,
            dir_has_work=dir_has_work,
            dry_run=cfg.dry_run,
            leaf=leaf,
            dir_path=str(dir_path),
            logger=logger,
            timeout_seconds=command_timeout_seconds,
        )

        if pre_process and dir_has_work and not cfg.dry_run:
            # A pre-process hook can rewrite or delete files, so the
            # confirmation set gathered before it may be stale.  Drop it and
            # let the per-file pass re-check those files.  (In dry-run the hook
            # does not run, so the set stays valid.)
            confirmed_processed = frozenset()

        try:
            for file_path, root in files_in_dir:
                global_idx += 1
                _process_one_file_guarded(
                    file_path=file_path,
                    root=root,
                    idx=global_idx,
                    total=total,
                    db=db,
                    cfg=cfg,
                    counts=counts,
                    failures=failures,
                    logger=logger,
                    confirmed_processed=confirmed_processed,
                )
        finally:
            # The post hook must run even when a file raises (Ctrl+C,
            # CorruptOutputError): it is the paired undo of the pre hook, so
            # skipping it can leave a directory unlocked or half-prepared.
            _run_dir_hook(
                cmd_template=post_process,
                dir_has_work=dir_has_work,
                dry_run=cfg.dry_run,
                leaf=leaf,
                dir_path=str(dir_path),
                logger=logger,
                timeout_seconds=command_timeout_seconds,
            )


def _open_run_database(database_path: str, dry_run: bool) -> Database:
    """Open the tracking database, keeping a preview run from creating one.

    A dry run must not write anything, including the SQLite file, its WAL
    sidecars and the parent directory.  When the database does not exist yet, a
    dry run uses an in-memory database instead, so the preview still reports
    "nothing processed" without leaving a file behind.

    An existing database is still opened (read-only for cache purposes) because
    the preview has to know which files are already done.  Opening runs the
    schema migration and enables WAL, so a dry run over a *legacy* database
    upgrades its schema — the smallest write that keeps the preview accurate.
    """
    if dry_run and not Path(database_path).exists():
        return Database(":memory:", cache_writes_enabled=False)
    return Database(database_path, cache_writes_enabled=not dry_run)


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
) -> tuple[_RunCounts, bool, list[tuple[Path, str]], bool]:
    """Process files grouped by directory, firing pre/post hooks around each group.

    Returns (counts, interrupted, failures, database_unavailable).  The last
    element is True when the tracking database could not be opened at all, so
    the caller can exit non-zero instead of reporting a successful run.
    """
    counts = _RunCounts()
    failures: list[tuple[Path, str]] = []
    interrupted = False
    database_unavailable = False

    try:
        with _open_run_database(database_path, cfg.dry_run) as db:
            _process_all_groups(
                db=db,
                dir_groups=dir_groups,
                profile_hash=profile_hash,
                cfg=cfg,
                total=total,
                pre_process=pre_process,
                post_process=post_process,
                command_timeout_seconds=command_timeout_seconds,
                counts=counts,
                failures=failures,
                logger=logger,
            )

    except CorruptOutputError as exc:
        _handle_corrupt_output(exc, counts, logger)
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted — showing partial results.")
    except (sqlite3.Error, OSError) as exc:
        # Opening (or migrating) the tracking database failed.  Every other
        # database error path is survivable per file; this one is not, so it is
        # reported as a run-level failure.  Without the tracking database no
        # file can be skipped or recorded, so the run is also marked as failed
        # for anything watching the exit code.
        reason = f"database unavailable: {str(exc).splitlines()[0].strip()}"
        logger.error(f"Cannot open tracking database '{database_path}': {exc}")
        failures.append((Path(database_path), reason))
        counts.failed += 1
        database_unavailable = True

    return counts, interrupted, failures, database_unavailable


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
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
    skip_size_check: bool = False,
    keep_native_audio: bool = False,
    tmdb_api_key: str | None = None,
    tvdb_api_key: str | None = None,
    keep_undefined_audio: bool = False,
    pre_process: str | None = None,
    post_process: str | None = None,
    command_timeout_mins: int = 5,
) -> None:
    """Scan *media_path* directories and process every MKV file found.

    Already-processed files (same fingerprint and profile) are skipped silently.
    On dry-run the planned command is logged; nothing on disk is modified.
    Raises :exc:`SystemExit` with code 130 on :exc:`KeyboardInterrupt`, and with
    code 1 when the tracking database could not be opened.
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
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,
        keep_undefined_audio=keep_undefined_audio,
    )

    cfg = _ProcessingConfig(
        mkvmerge_path=mkvmerge_path,
        language=list(language),
        keep_audio=keep_audio,
        keep_native_audio=keep_native_audio,
        keep_subtitles=keep_subtitles,
        edit_metadata_title=edit_metadata_title,
        delete_metadata_title=delete_metadata_title,
        strip_lower_channels=strip_lower_channels,
        strip_commentary=strip_commentary,
        skip_size_check=skip_size_check,
        dry_run=dry_run,
        no_backup=no_backup,
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,
        tmdb_api_key=tmdb_api_key,
        tvdb_api_key=tvdb_api_key,
        keep_undefined_audio=keep_undefined_audio,
    )

    command_timeout_seconds: int | None = command_timeout_mins * 60 if command_timeout_mins > 0 else None

    total = len(unique_files)
    # Group files by their parent directory for hook support
    dir_groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict()
    for file_path, root in unique_files:
        dir_groups.setdefault(file_path.parent, []).append((file_path, root))

    counts, interrupted, failures, database_unavailable = _process_directory_groups(
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

    if database_unavailable:
        # Nothing could be recorded or skipped, so the run did not do its job.
        # Exit non-zero so cron/CI/health checks can see the failure.
        sys.exit(1)
