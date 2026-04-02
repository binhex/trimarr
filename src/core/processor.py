"""MKV file analysis and processing via mkvmerge."""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from loguru import Logger


@dataclass
class MkvTrack:
    """Represents a single track inside an MKV container.

    Attributes:
        id: mkvmerge track ID (zero-based for each track type but stored as the
            global integer ID returned by ``mkvmerge -J``).
        type: Track type string as returned by mkvmerge, e.g. ``"audio"``,
            ``"subtitles"``, or ``"video"``.
        language: ISO 639-2 language tag, or *None* when the track carries no
            language information.
        name: Human-readable track name as stored in the container, or *None*
            when the track has no name.
        default_track: Whether this track is flagged as the default for its
            type in the source container.
    """

    id: int
    type: str
    language: str | None
    name: str | None = field(default=None)
    default_track: bool = field(default=False)


# Matches track names containing "commentary" (any case, with or without
# surrounding words/numerics, e.g. "Commentary 1", "Director Commentary").
_COMMENTARY_RE: re.Pattern[str] = re.compile(r"commentary", re.IGNORECASE)


def _is_commentary(name: str | None) -> bool:
    """Return *True* if *name* looks like a commentary track."""
    if not name:
        return False
    return bool(_COMMENTARY_RE.search(name))


def _fmt_track(t: MkvTrack) -> str:
    """Format a track as a short string for log messages.

    Track names come from untrusted MKV metadata, so control characters
    (newlines, ANSI escapes, etc.) are stripped to prevent log injection.
    """
    parts = [f"ID {t.id}"]
    if t.language:
        parts.append(f"[{t.language}]")
    if t.name:
        safe_name = "".join(c for c in t.name if c.isprintable())
        parts.append(f"'{safe_name}'")
    return " ".join(parts)


@contextlib.contextmanager
def _spinner(message: str) -> Iterator[None]:
    """Show a braille spinner on stderr while the body executes (TTY only)."""
    if not sys.stderr.isatty():
        yield
        return

    stop = threading.Event()

    def _run() -> None:
        for char in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if stop.is_set():
                break
            sys.stderr.write(f"\r  {char} {message}")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write(f"\r{' ' * (len(message) + 5)}\r")
        sys.stderr.flush()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def probe_file(mkvmerge_path: str, file_path: Path) -> list[MkvTrack]:
    """Return the list of tracks inside *file_path* by running ``mkvmerge -J``.

    Args:
        mkvmerge_path: Path to the mkvmerge binary.
        file_path: Path to the MKV file to inspect.

    Returns:
        List of :class:`MkvTrack` objects, one per track.

    Raises:
        RuntimeError: If mkvmerge exits with a non-zero code, times out, raises
            an OS-level error, or its output cannot be parsed as JSON.
    """
    try:
        result = subprocess.run(
            [mkvmerge_path, "-J", str(file_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"mkvmerge -J timed out (>60s) for '{file_path}'.") from None
    except OSError as exc:
        raise RuntimeError(f"Could not execute mkvmerge for '{file_path}': {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(f"mkvmerge -J failed for '{file_path}' (exit {result.returncode}): {result.stderr.strip()}")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse mkvmerge JSON output for '{file_path}': {exc}") from exc

    tracks: list[MkvTrack] = []
    for raw in info.get("tracks", []):
        props = raw.get("properties", {})
        lang = props.get("language") or props.get("language_ietf")
        # Normalise the mkvmerge "und" (undetermined) sentinel to None so
        # callers can treat it the same as missing language information.
        if lang == "und":
            lang = None
        tracks.append(
            MkvTrack(
                id=raw["id"],
                type=raw["type"],
                language=lang,
                name=props.get("track_name") or None,
                default_track=bool(props.get("default_track", False)),
            )
        )
    return tracks


def build_mkvmerge_command(
    mkvmerge_path: str,
    input_path: Path,
    output_path: Path,
    tracks: list[MkvTrack],
    language: str,
    keep_audio: bool,
    keep_subtitles: bool,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    logger: Logger | None = None,
) -> list[str] | None:
    """Build the mkvmerge argv needed to produce a trimmed copy of *input_path*.

    The function returns *None* when no changes are required (the file already
    contains only the desired tracks and no metadata operations are requested).
    Callers should skip mkvmerge and mark the file as processed when *None* is
    returned.

    Audio and subtitle tracks that do **not** match the requested language are
    dropped, unless the ``keep_audio`` / ``keep_subtitles`` overrides are set.
    If filtering would remove **all** tracks of a given type (i.e. no track
    matches the language), that type is left untouched and a warning is logged.
    Video tracks are always kept.

    Args:
        mkvmerge_path: Path to the mkvmerge binary.
        input_path: Source MKV file.
        output_path: Destination path for the trimmed file (usually a temp path).
        tracks: Track list as returned by :func:`probe_file`.
        language: ISO 639-2 language code to retain (e.g. ``"eng"``).
        keep_audio: When *True*, retain all audio tracks regardless of language.
        keep_subtitles: When *True*, retain all subtitle tracks regardless of language.
        edit_metadata_title: When *True*, set the container title to the file stem.
        delete_metadata_title: When *True*, clear the container title.
        logger: Optional loguru logger; used to emit warnings when the safety
            fallback is triggered (no tracks match the language filter).

    Returns:
        A list of strings suitable for :func:`subprocess.run`, or *None* if
        mkvmerge does not need to be invoked.
    """
    audio_keep: list[int] = []
    audio_drop: list[int] = []
    sub_keep: list[int] = []
    sub_drop: list[int] = []

    for track in tracks:
        if track.type == "audio":
            if keep_audio or track.language == language:
                audio_keep.append(track.id)
            else:
                audio_drop.append(track.id)
        elif track.type == "subtitles":
            if keep_subtitles or track.language == language:
                sub_keep.append(track.id)
            else:
                sub_drop.append(track.id)
        # Video tracks are always kept; no action needed.

    # Safety fallback: if filtering would drop ALL audio tracks (none match the
    # language), keep everything rather than produce a silent file.
    if audio_drop and not audio_keep:
        if logger is not None:
            logger.warning(
                f"No audio tracks match language '{language}' in '{input_path.name}' "
                f"— keeping all audio to prevent silent data loss."
            )
        audio_drop.clear()

    # Same safety fallback for subtitles.
    if sub_drop and not sub_keep:
        if logger is not None:
            logger.warning(
                f"No subtitle tracks match language '{language}' in '{input_path.name}' — keeping all subtitles."
            )
        sub_drop.clear()

    needs_audio_change = bool(audio_drop)
    needs_sub_change = bool(sub_drop)
    needs_metadata_change = edit_metadata_title or delete_metadata_title

    if not needs_audio_change and not needs_sub_change and not needs_metadata_change:
        return None

    # Log what is being changed and why, so the user has full visibility.
    if logger is not None:
        audio_drop_set = set(audio_drop)
        sub_drop_set = set(sub_drop)
        if audio_drop:
            logger.info(f"  Dropping {len(audio_drop)} audio track(s) (language ≠ '{language}').")
            descs = ", ".join(_fmt_track(t) for t in tracks if t.type == "audio" and t.id in audio_drop_set)
            logger.debug(f"  Dropping audio track(s): {descs}")
        if sub_drop:
            logger.info(f"  Dropping {len(sub_drop)} subtitle track(s) (language ≠ '{language}').")
            descs = ", ".join(_fmt_track(t) for t in tracks if t.type == "subtitles" and t.id in sub_drop_set)
            logger.debug(f"  Dropping subtitle track(s): {descs}")
        if edit_metadata_title:
            logger.info(f"  Metadata: setting title to '{input_path.stem}'")
        elif delete_metadata_title:
            logger.info("  Metadata: clearing title")

    cmd: list[str] = [mkvmerge_path, "-o", str(output_path)]

    # Metadata title edit
    if edit_metadata_title:
        cmd += ["--title", input_path.stem]
    elif delete_metadata_title:
        cmd += ["--title", ""]

    # Audio track selection
    if needs_audio_change and audio_keep:
        cmd += ["--audio-tracks", ",".join(str(i) for i in audio_keep)]
    elif needs_audio_change and not audio_keep:
        cmd += ["--no-audio"]

    # Subtitle track selection
    if needs_sub_change and sub_keep:
        cmd += ["--subtitle-tracks", ",".join(str(i) for i in sub_keep)]
    elif needs_sub_change and not sub_keep:
        cmd += ["--no-subtitles"]

    # Commentary default-track reassignment.
    # When we ARE removing tracks of a type, make sure no commentary track
    # remains (or becomes) the default.  If commentary tracks are kept alongside
    # non-commentary tracks, promote the first non-commentary track to default
    # (unless one already is).  Always demote any commentary track that currently
    # holds the default flag.
    default_flags: list[str] = []
    for track_type, needs_change, keep_ids in (
        ("audio", needs_audio_change, audio_keep),
        ("subtitles", needs_sub_change, sub_keep),
    ):
        if not needs_change:
            continue
        keep_set = set(keep_ids)
        kept_tracks = [t for t in tracks if t.type == track_type and t.id in keep_set]
        commentary_kept = [t for t in kept_tracks if _is_commentary(t.name)]
        if not commentary_kept:
            continue  # No commentary tracks among the kept set — nothing to do.
        non_commentary = [t for t in kept_tracks if not _is_commentary(t.name)]
        if non_commentary:
            # Promote the first non-commentary track to default, unless one
            # already holds that flag in the source file.
            if not any(t.default_track for t in non_commentary):
                default_flags += ["--default-track", f"{non_commentary[0].id}:1"]
            # Demote any commentary track that is incorrectly flagged as default.
            for t in commentary_kept:
                if t.default_track:
                    default_flags += ["--default-track", f"{t.id}:0"]
        else:
            # All remaining tracks are commentary — still unset their default
            # flags so no commentary track is marked as default in the output.
            for t in commentary_kept:
                if t.default_track:
                    default_flags += ["--default-track", f"{t.id}:0"]
            if logger is not None:
                logger.warning(
                    f"All remaining {track_type} tracks in '{input_path.name}' are commentary "
                    f"— cannot reassign default {track_type} track."
                )

    cmd += default_flags
    cmd.append(str(input_path))
    return cmd


def process_file(
    mkvmerge_path: str,
    file_path: Path,
    command: list[str],
    no_backup: bool,
    logger: Logger,
) -> bool:
    """Execute a pre-built mkvmerge command and safely replace *file_path*.

    The workflow is:
    1. Run mkvmerge to a temporary file in the same directory.
    2. Validate the output (non-zero size; exit code 0 only — exit code 1 from
       mkvmerge is treated as a warning/failure per project policy).
    3. On success: rename original to ``<path>.bak`` (unless *no_backup*) then
       rename temp to original.
    4. On any failure: remove the temp file and leave the original untouched.

    Args:
        mkvmerge_path: Path to the mkvmerge binary (used only for logging).
        file_path: Path to the MKV file being processed.
        command: Full mkvmerge argv as returned by :func:`build_mkvmerge_command`.
        no_backup: When *True*, delete the original instead of renaming to ``.bak``.
        logger: Loguru logger instance.

    Returns:
        *True* on success, *False* on failure.
    """
    tmp_path: Path | None = None
    try:
        # Write temp file next to the original so the rename is atomic on most filesystems.
        tmp_fd, tmp_str = tempfile.mkstemp(dir=file_path.parent, suffix=".trimarr_tmp")
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)

        # Patch the output path in the command to the actual temp file.
        patched_cmd = list(command)
        out_idx = patched_cmd.index("-o") + 1
        patched_cmd[out_idx] = str(tmp_path)

        logger.debug(f"Running: {' '.join(patched_cmd)}")
        with _spinner(f"Remuxing '{file_path.name}'..."):
            result = subprocess.run(patched_cmd, capture_output=True, text=True, timeout=3600)

        if result.returncode == 1:
            logger.warning(
                f"mkvmerge reported warnings for '{file_path}' (exit 1) — keeping original.\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return False

        if result.returncode != 0:
            logger.error(
                f"mkvmerge failed for '{file_path}' (exit {result.returncode}).\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return False

        # Validate output file
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            logger.error(f"mkvmerge produced an empty or missing output for '{file_path}'.")
            return False

        # Replace original atomically.
        # For backup mode: rename original → .bak, then atomically replace with temp.
        # If the second step fails, roll back the backup rename.
        # For no-backup mode: directly replace the original with the temp in one atomic step.
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        if no_backup:
            # os.replace / Path.replace is atomic on POSIX; overwrites the destination.
            tmp_path.replace(file_path)
        else:
            file_path.rename(backup_path)
            try:
                tmp_path.replace(file_path)
            except BaseException:
                # Roll back: restore original from backup — but only if the replace did not
                # complete.  os.rename() is atomic on POSIX, so file_path either exists (replace
                # succeeded) or doesn't (replace was not reached / was interrupted before the
                # syscall returned).  Catching BaseException here ensures KeyboardInterrupt is
                # also covered.
                if not file_path.exists():
                    try:
                        backup_path.rename(file_path)
                    except Exception as restore_exc:
                        logger.error(
                            f"CRITICAL: Could not restore original from backup '{backup_path}': {restore_exc}. "
                            f"Original is at '{backup_path}'."
                        )
                raise
            logger.debug(f"Original backed up to '{backup_path}'.")
        tmp_path = None  # Ownership transferred; do not delete in finally block.

        return True

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error processing '{file_path}': {exc}")
        return False
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
