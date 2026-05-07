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
        channels: Number of audio channels, or *None* for non-audio tracks or
            when the value is absent from the container metadata.
    """

    id: int
    type: str
    language: str | None
    name: str | None = field(default=None)
    default_track: bool = field(default=False)
    channels: int | None = field(default=None)


# Matches track names containing "commentary" (any case, with or without
# surrounding words/numerics, e.g. "Commentary 1", "Director Commentary").
_COMMENTARY_RE: re.Pattern[str] = re.compile(r"commentary", re.IGNORECASE)

# Map ISO 639-1 (2-char) codes to ISO 639-2/B (3-char) codes so that
# mkvmerge files using BCP-47 ``language_ietf`` tags (e.g. "en", "en-US")
# are matched correctly against user-supplied ISO 639-2 language arguments.
_ISO_639_1_TO_2: dict[str, str] = {
    "af": "afr",
    "sq": "alb",
    "ar": "ara",
    "hy": "arm",
    "az": "aze",
    "be": "bel",
    "bs": "bos",
    "bg": "bul",
    "ca": "cat",
    "zh": "chi",
    "hr": "hrv",
    "cs": "cze",
    "da": "dan",
    "nl": "dut",
    "en": "eng",
    "et": "est",
    "fi": "fin",
    "fr": "fre",
    "gl": "glg",
    "ka": "geo",
    "de": "ger",
    "el": "gre",
    "he": "heb",
    "hi": "hin",
    "hu": "hun",
    "is": "ice",
    "id": "ind",
    "it": "ita",
    "ja": "jpn",
    "kn": "kan",
    "kk": "kaz",
    "ko": "kor",
    "lv": "lav",
    "lt": "lit",
    "mk": "mac",
    "ms": "may",
    "ml": "mal",
    "mt": "mlt",
    "nb": "nob",
    "fa": "per",
    "pl": "pol",
    "pt": "por",
    "ro": "rum",
    "ru": "rus",
    "sr": "srp",
    "sk": "slo",
    "sl": "slv",
    "es": "spa",
    "sw": "swa",
    "sv": "swe",
    "tl": "tgl",
    "ta": "tam",
    "te": "tel",
    "th": "tha",
    "tr": "tur",
    "uk": "ukr",
    "ur": "urd",
    "vi": "vie",
    "cy": "wel",
}
# ISO 639-2 terminologic (T) to bibliographic (B) mapping.
# ISO 639-2 defines two sets of 3-letter codes for some languages: the
# bibliographic (B) set and the terminologic (T) set.  mkvmerge metadata may
# contain either form, and users may supply either.  Normalise both to the B
# form so that matching works regardless of which variant is used.
_ISO_639_2_T_TO_B: dict[str, str] = {
    "sqi": "alb",
    "hye": "arm",
    "eus": "baq",
    "mya": "bur",
    "zho": "chi",
    "ces": "cze",
    "nld": "dut",
    "fra": "fre",
    "kat": "geo",
    "deu": "ger",
    "ell": "gre",
    "isl": "ice",
    "mri": "mao",
    "msa": "may",
    "mkd": "mac",
    "fas": "per",
    "ron": "rum",
    "slk": "slo",
    "cym": "wel",
    "bod": "tib",
}


@dataclass
class _FilterResult:
    """Mutable state accumulated during audio/subtitle track filtering.

    Instances are created by :func:`_apply_language_filter` and then modified
    in-place by each subsequent phase of :func:`build_mkvmerge_command`.
    """

    audio_keep: list[int] = field(default_factory=list)
    audio_drop: list[int] = field(default_factory=list)
    sub_keep: list[int] = field(default_factory=list)
    sub_drop: list[int] = field(default_factory=list)
    audio_fallback_fired: bool = False
    sub_fallback_fired: bool = False
    # Immutable snapshots taken right after language filtering, before any fallback
    # modifies the drop lists.  Used only for logging so the summary shows the
    # tracks dropped *because of language* rather than by later phases.
    language_audio_drop_ids: frozenset[int] = field(default_factory=frozenset)
    language_sub_drop_ids: frozenset[int] = field(default_factory=frozenset)
    # Track IDs removed specifically by the strip-commentary phase.
    commentary_audio_drop_ids: set[int] = field(default_factory=set)
    commentary_sub_drop_ids: set[int] = field(default_factory=set)



@dataclass
class _FilterResult:
    """Mutable state accumulated during audio/subtitle track filtering.

    Instances are created by :func:`_apply_language_filter` and then modified
    in-place by each subsequent phase of :func:`build_mkvmerge_command`.
    """

    audio_keep: list[int] = field(default_factory=list)
    audio_drop: list[int] = field(default_factory=list)
    sub_keep: list[int] = field(default_factory=list)
    sub_drop: list[int] = field(default_factory=list)
    audio_fallback_fired: bool = False
    sub_fallback_fired: bool = False
    # Immutable snapshots taken right after language filtering, before any fallback
    # modifies the drop lists.  Used only for logging so the summary shows the
    # tracks dropped *because of language* rather than by later phases.
    language_audio_drop_ids: frozenset[int] = field(default_factory=frozenset)
    language_sub_drop_ids: frozenset[int] = field(default_factory=frozenset)
    # Track IDs removed specifically by the strip-commentary phase.
    commentary_audio_drop_ids: set[int] = field(default_factory=set)
    commentary_sub_drop_ids: set[int] = field(default_factory=set)


# Minimum acceptable output-to-input size ratio.  A legitimate remux strips
# audio/subtitle tracks but the video stream (the bulk of any MKV) is always
# retained — typically 90 %+ of the source size.  50 % is a very conservative
# lower bound that catches catastrophically truncated or partial writes while
# still allowing files with unusually large audio/subtitle payloads.
_MIN_OUTPUT_RATIO: float = 0.5


class CorruptOutputError(BaseException):
    """Raised when the mkvmerge output fails structural (``mkvmerge -J``) validation.

    Inherits from :class:`BaseException` rather than :class:`Exception` so that
    it bypasses the broad ``except Exception`` safety-net inside
    :func:`process_file` and propagates directly to the orchestrating caller,
    halting all further processing immediately.

    The temporary output file is *preserved on disk* at :attr:`tmp_path` so
    the operator can inspect it to diagnose the root cause.

    Attributes:
        file_path: Source MKV file that was being processed.
        tmp_path: Temporary output file retained on disk for inspection.
        probe_returncode: Exit code returned by ``mkvmerge -J``.
        probe_output: Combined stderr / stdout from the ``mkvmerge -J`` probe.
        output_size: Size in bytes of the temporary output file.
        input_size: Size in bytes of the source file.
        mkvmerge_path: Filesystem path to the mkvmerge binary that was used.
    """

    def __init__(
        self,
        file_path: Path,
        tmp_path: Path,
        probe_returncode: int,
        probe_output: str,
        output_size: int,
        input_size: int,
        mkvmerge_path: str,
    ) -> None:
        """Initialise with full diagnostic context."""
        self.file_path = file_path
        self.tmp_path = tmp_path
        self.probe_returncode = probe_returncode
        self.probe_output = probe_output
        self.output_size = output_size
        self.input_size = input_size
        self.mkvmerge_path = mkvmerge_path
        super().__init__(str(file_path))


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
    if t.channels is not None:
        parts.append(f"{t.channels}ch")
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
        # Normalise BCP-47 / ISO 639-1 tags to ISO 639-2 so that files using
        # "language_ietf" (e.g. "en", "en-US") match user-supplied codes like
        # "eng".  3-char codes are already ISO 639-2 and pass through unchanged,
        # but ISO 639-2 terminologic variants (e.g. "fra" for French) are
        # normalised to their bibliographic form ("fre") so either form matches.
        if lang:
            base = lang.split("-")[0]
            if len(base) == 2:
                lang = _ISO_639_1_TO_2.get(base, base)
            elif len(base) == 3:
                lang = _ISO_639_2_T_TO_B.get(base, base)
        tracks.append(
            MkvTrack(
                id=raw["id"],
                type=raw["type"],
                language=lang,
                name=props.get("track_name") or None,
                default_track=bool(props.get("default_track", False)),
                channels=props.get("audio_channels") if raw["type"] == "audio" else None,
            )
        )
    return tracks


# ---------------------------------------------------------------------------
# Internal filtering helpers used by build_mkvmerge_command()
# ---------------------------------------------------------------------------


def _apply_language_filter(
    tracks: list[MkvTrack],
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
) -> _FilterResult:
    """Phase 1 — classify each track as keep or drop based on language.

    Returns a :class:`_FilterResult` with:
    - ``audio_keep`` / ``audio_drop``: track IDs for the audio decision.
    - ``sub_keep`` / ``sub_drop``: track IDs for the subtitle decision.
    - ``language_audio_drop_ids`` / ``language_sub_drop_ids``: immutable
      snapshots of the drop sets *before* any fallback can modify them.
    """
    # Normalise user-supplied language codes: ISO 639-2 terminologic variants
    # (e.g. "fra" for French) are mapped to their bibliographic form ("fre")
    # so they match track languages normalised in probe_file().
    language = [_ISO_639_2_T_TO_B.get(c, c) for c in language]
    result = _FilterResult()
    for track in tracks:
        if track.type == "audio":
            if keep_audio or track.language in language:
                result.audio_keep.append(track.id)
            else:
                result.audio_drop.append(track.id)
        elif track.type == "subtitles":
            if keep_subtitles or track.language in language:
                result.sub_keep.append(track.id)
            else:
                result.sub_drop.append(track.id)
    # Snapshot before fallbacks modify the drop lists (used for logging only).
    result.language_audio_drop_ids = frozenset(result.audio_drop)
    result.language_sub_drop_ids = frozenset(result.sub_drop)
    return result


def _apply_audio_fallbacks(
    result: _FilterResult,
    tracks: list[MkvTrack],
    language: list[str],
    logger: Logger,
    input_path: Path,
) -> None:
    """Phase 2 — apply audio safety fallbacks to prevent a silent output file.

    Primary fallback: if no audio track matches the language, keep every
    audio track.  Secondary fallback: if every *matching* audio track is a
    commentary track, also keep everything (the user almost certainly wants
    the main foreign-language audio rather than commentary-only output).
    Either fallback sets ``result.audio_fallback_fired = True``.
    """
    lang_desc = f"'{language[0]}'" if len(language) == 1 else str(language)
    if result.audio_drop and not result.audio_keep:
        logger.warning(
            f"No audio tracks match language {lang_desc} in '{input_path.name}' "
            f"\u2014 keeping all audio to prevent silent data loss."
        )
        result.audio_drop.clear()
        result.audio_fallback_fired = True
    elif result.audio_drop:
        audio_commentary_ids = {t.id for t in tracks if t.type == "audio" and _is_commentary(t.name)}
        if all(tid in audio_commentary_ids for tid in result.audio_keep):
            logger.warning(
                f"All audio tracks matching language {lang_desc} in '{input_path.name}' are commentary "
                f"\u2014 keeping all audio to avoid commentary-only audio."
            )
            result.audio_drop.clear()
            result.audio_fallback_fired = True


def _apply_subtitle_fallback(
    result: _FilterResult,
    language: list[str],
    logger: Logger,
    input_path: Path,
) -> None:
    """Phase 3 — apply the subtitle safety fallback.

    If filtering would drop every subtitle track (none match the language),
    keep all subtitles and set ``result.sub_fallback_fired = True``.
    """
    lang_desc = f"'{language[0]}'" if len(language) == 1 else str(language)
    if result.sub_drop and not result.sub_keep:
        logger.warning(
            f"No subtitle tracks match language {lang_desc} in '{input_path.name}' \u2014 keeping all subtitles."
        )
        result.sub_drop.clear()
        result.sub_fallback_fired = True


def _apply_strip_commentary(
    result: _FilterResult,
    tracks: list[MkvTrack],
    keep_audio: bool,
    keep_subtitles: bool,
    logger: Logger,
    input_path: Path,
    strip_commentary: bool,
) -> None:
    """Phase 4 — remove commentary tracks after all language/safety fallbacks have resolved.

    Guards (type skipped entirely):
    - Audio: ``keep_audio`` is *True*, or the audio safety fallback fired.
    - Subtitles: ``keep_subtitles`` is *True*, or the subtitle safety fallback fired.

    Audio final gate: if stripping would remove *all* remaining audio, keep
    everything and log a warning instead.  A silent file is never acceptable.
    Subtitles have no equivalent gate (subtitle-free output is acceptable).
    """
    if not strip_commentary:
        return

    if not keep_audio and not result.audio_fallback_fired and result.audio_keep:
        audio_keep_set = set(result.audio_keep)
        audio_commentary_to_drop = [
            t.id for t in tracks if t.type == "audio" and t.id in audio_keep_set and _is_commentary(t.name)
        ]
        if audio_commentary_to_drop:
            remaining = [i for i in result.audio_keep if i not in set(audio_commentary_to_drop)]
            if not remaining:
                logger.warning(
                    f"All audio tracks in '{input_path.name}' are commentary "
                    f"\u2014 keeping all audio to prevent silent output."
                )
            else:
                result.commentary_audio_drop_ids = set(audio_commentary_to_drop)
                for tid in audio_commentary_to_drop:
                    result.audio_keep.remove(tid)
                    result.audio_drop.append(tid)

    if not keep_subtitles and not result.sub_fallback_fired and result.sub_keep:
        sub_keep_set = set(result.sub_keep)
        sub_commentary_to_drop = [
            t.id for t in tracks if t.type == "subtitles" and t.id in sub_keep_set and _is_commentary(t.name)
        ]
        if sub_commentary_to_drop:
            result.commentary_sub_drop_ids = set(sub_commentary_to_drop)
            for tid in sub_commentary_to_drop:
                result.sub_keep.remove(tid)
                result.sub_drop.append(tid)


def _apply_strip_lower_channels(
    result: _FilterResult,
    tracks: list[MkvTrack],
    keep_audio: bool,
    strip_lower_channels: bool,
    logger: Logger,
) -> None:
    """Phase 5 — drop lower-channel-count audio tracks within each language group.

    Skipped when ``keep_audio`` is *True*, the flag is off, there are no kept
    audio tracks, or the audio safety fallback fired.

    Commentary tracks are excluded from the max-channel calculation so that a
    high-channel commentary track never causes the main audio to be dropped.
    Tracks with unknown channel counts (``channels=None``) are always kept.
    """
    if not strip_lower_channels or keep_audio or not result.audio_keep or result.audio_fallback_fired:
        return

    keep_set = set(result.audio_keep)
    surviving = [t for t in tracks if t.type == "audio" and t.id in keep_set]
    lang_groups: dict[str | None, list[MkvTrack]] = {}
    for t in surviving:
        lang_groups.setdefault(t.language, []).append(t)

    channel_drop: list[int] = []
    for group_tracks in lang_groups.values():
        non_commentary = [t for t in group_tracks if not _is_commentary(t.name)]
        known = [t.channels for t in non_commentary if t.channels is not None]
        if not known:
            continue
        max_ch = max(known)
        if any(ch < max_ch for ch in known):
            channel_drop.extend(t.id for t in non_commentary if t.channels is not None and t.channels < max_ch)

    if channel_drop:
        channel_drop_set = set(channel_drop)
        for tid in channel_drop:
            result.audio_keep.remove(tid)
            result.audio_drop.append(tid)
        descs = ", ".join(_fmt_track(t) for t in surviving if t.id in channel_drop_set)
        logger.info(f"  Dropping {len(channel_drop)} audio track(s) with fewer channels than the per-language maximum.")
        logger.debug(f"  Dropping lower-channel audio track(s): {descs}")


def _log_filter_changes(
    result: _FilterResult,
    tracks: list[MkvTrack],
    language: list[str],
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    input_path: Path,
    logger: Logger,
    needs_audio_change: bool,
    needs_sub_change: bool,
) -> None:
    """Log a human-readable summary of the track changes that will be applied."""
    lang_filter = f"\u2260 '{language[0]}'" if len(language) == 1 else f"not in {language}"
    if result.language_audio_drop_ids and needs_audio_change:
        logger.info(f"  Dropping {len(result.language_audio_drop_ids)} audio track(s) (language {lang_filter}).")
        descs = ", ".join(_fmt_track(t) for t in tracks if t.type == "audio" and t.id in result.language_audio_drop_ids)
        logger.debug(f"  Dropping audio track(s): {descs}")
    if result.language_sub_drop_ids and needs_sub_change:
        logger.info(f"  Dropping {len(result.language_sub_drop_ids)} subtitle track(s) (language {lang_filter}).")
        descs = ", ".join(
            _fmt_track(t) for t in tracks if t.type == "subtitles" and t.id in result.language_sub_drop_ids
        )
        logger.debug(f"  Dropping subtitle track(s): {descs}")
    if result.commentary_audio_drop_ids:
        logger.info(f"  Dropping {len(result.commentary_audio_drop_ids)} audio commentary track(s).")
        descs = ", ".join(
            _fmt_track(t) for t in tracks if t.type == "audio" and t.id in result.commentary_audio_drop_ids
        )
        logger.debug(f"  Dropping audio commentary track(s): {descs}")
    if result.commentary_sub_drop_ids:
        logger.info(f"  Dropping {len(result.commentary_sub_drop_ids)} subtitle commentary track(s).")
        descs = ", ".join(
            _fmt_track(t) for t in tracks if t.type == "subtitles" and t.id in result.commentary_sub_drop_ids
        )
        logger.debug(f"  Dropping subtitle commentary track(s): {descs}")
    if edit_metadata_title:
        logger.info(f"  Metadata: setting title to '{input_path.stem}'")
    elif delete_metadata_title:
        logger.info("  Metadata: clearing title")


def _compute_default_track_flags(
    tracks: list[MkvTrack],
    result: _FilterResult,
    needs_audio_change: bool,
    needs_sub_change: bool,
    logger: Logger,
    input_path: Path,
) -> list[str]:
    """Compute ``--default-track`` flags for commentary / non-commentary reassignment.

    When tracks are being removed for a given type, make sure no commentary track
    remains the default.  Promote the first non-commentary track to default (unless
    one already is) and demote any commentary track that holds the default flag.
    Returns a flat list of flag pairs ready for appending to the mkvmerge argv.
    """
    default_flags: list[str] = []
    for track_type, needs_change, keep_ids in (
        ("audio", needs_audio_change, result.audio_keep),
        ("subtitles", needs_sub_change, result.sub_keep),
    ):
        if not needs_change:
            continue
        keep_set = set(keep_ids)
        kept_tracks = [t for t in tracks if t.type == track_type and t.id in keep_set]
        commentary_kept = [t for t in kept_tracks if _is_commentary(t.name)]
        if not commentary_kept:
            continue
        non_commentary = [t for t in kept_tracks if not _is_commentary(t.name)]
        if non_commentary:
            if not any(t.default_track for t in non_commentary):
                default_flags += ["--default-track", f"{non_commentary[0].id}:1"]
            for t in commentary_kept:
                if t.default_track:
                    default_flags += ["--default-track", f"{t.id}:0"]
        else:
            for t in commentary_kept:
                if t.default_track:
                    default_flags += ["--default-track", f"{t.id}:0"]
            logger.warning(
                f"All remaining {track_type} tracks in '{input_path.name}' are commentary "
                f"\u2014 cannot reassign default {track_type} track."
            )
    return default_flags


def build_mkvmerge_command(
    mkvmerge_path: str,
    input_path: Path,
    output_path: Path,
    tracks: list[MkvTrack],
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    logger: Logger,
    strip_lower_channels: bool = False,
    strip_commentary: bool = False,
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
    Audio filtering is also left untouched when every matching audio track is a
    commentary track — stripping non-commentary audio in that case would leave
    the viewer with only director's commentary, which is almost always wrong.
    Video tracks are always kept.

    Args:
        mkvmerge_path: Path to the mkvmerge binary.
        input_path: Source MKV file.
        output_path: Destination path for the trimmed file (usually a temp path).
        tracks: Track list as returned by :func:`probe_file`.
        language: One or more ISO 639-2 language codes to retain (e.g. ``["eng"]``
            or ``["eng", "fre"]``).
        keep_audio: When *True*, retain all audio tracks regardless of language.
        keep_subtitles: When *True*, retain all subtitle tracks regardless of language.
        edit_metadata_title: When *True*, set the container title to the file stem.
        delete_metadata_title: When *True*, clear the container title.
        logger: Optional loguru logger; used to emit warnings when the safety
            fallback is triggered (no tracks match the language filter).
        strip_lower_channels: When *True*, after all other filtering, drop any
            audio tracks whose channel count is strictly below the maximum
            channel count of the surviving audio tracks.  Tracks with unknown
            channel counts (``channels=None``) are always preserved.  Skipped
            when *keep_audio* is *True*.
        strip_commentary: When *True*, audio and subtitle tracks whose name
            contains the word "commentary" (case-insensitive) are removed after
            language filtering and all safety fallbacks have resolved.  Skipped
            for audio when *keep_audio* is *True* or the audio safety fallback
            fired; skipped for subtitles when *keep_subtitles* is *True* or the
            subtitle safety fallback fired.  Audio has a final gate: if stripping
            would leave zero audio tracks, all audio is kept and a warning is
            logged — a silent file is never acceptable.  Subtitles have no such
            gate; commentary-only subtitle tracks are stripped unconditionally.

    Returns:
        A list of strings suitable for :func:`subprocess.run`, or *None* if
        mkvmerge does not need to be invoked.
    """
    result = _apply_language_filter(tracks, language, keep_audio, keep_subtitles)
    _apply_audio_fallbacks(result, tracks, language, logger, input_path)
    _apply_subtitle_fallback(result, language, logger, input_path)
    _apply_strip_commentary(result, tracks, keep_audio, keep_subtitles, logger, input_path, strip_commentary)
    _apply_strip_lower_channels(result, tracks, keep_audio, strip_lower_channels, logger)

    needs_audio_change = bool(result.audio_drop)
    needs_sub_change = bool(result.sub_drop)
    needs_metadata_change = edit_metadata_title or delete_metadata_title

    if not needs_audio_change and not needs_sub_change and not needs_metadata_change:
        return None

    _log_filter_changes(
        result,
        tracks,
        language,
        edit_metadata_title,
        delete_metadata_title,
        input_path,
        logger,
        needs_audio_change,
        needs_sub_change,
    )

    cmd: list[str] = [mkvmerge_path, "-o", str(output_path)]

    if edit_metadata_title:
        cmd += ["--title", input_path.stem]
    elif delete_metadata_title:
        cmd += ["--title", ""]

    if needs_audio_change and result.audio_keep:
        cmd += ["--audio-tracks", ",".join(str(i) for i in result.audio_keep)]

    if needs_sub_change and result.sub_keep:
        cmd += ["--subtitle-tracks", ",".join(str(i) for i in result.sub_keep)]
    elif needs_sub_change and not result.sub_keep:
        cmd += ["--no-subtitles"]

    cmd += _compute_default_track_flags(tracks, result, needs_audio_change, needs_sub_change, logger, input_path)
    cmd.append(str(input_path))
    return cmd


def process_file(
    mkvmerge_path: str,
    file_path: Path,
    command: list[str],
    no_backup: bool,
    logger: Logger,
    skip_size_check: bool = False,
) -> str | None:
    """Execute a pre-built mkvmerge command and safely replace *file_path*.

    The workflow is:
    1. Run mkvmerge to a temporary file in the same directory.
    2. Validate the output (non-zero size; exit codes 0 and 1 are both
       accepted — 0 means success, 1 means "completed with warnings" and
       the output is still considered valid; anything else is a failure).
    3. On success: rename original to ``<path>.bak`` (unless *no_backup*) then
       rename temp to original.
    4. On any failure: remove the temp file and leave the original untouched.

    Args:
        mkvmerge_path: Path to the mkvmerge binary (used only for logging).
        file_path: Path to the MKV file being processed.
        command: Full mkvmerge argv as returned by :func:`build_mkvmerge_command`.
        no_backup: When *True*, delete the original instead of renaming to ``.bak``.
        logger: Loguru logger instance.
        skip_size_check: When *True*, bypass the suspiciously-small output size
            guard.  Use when legitimate remuxes are expected to produce output
            significantly smaller than 50 % of the source (e.g. files with very
            large audio/subtitle payloads relative to video).

    Returns:
        *None* on success, or a concise single-line error reason string on failure.
    """
    tmp_path: Path | None = None
    try:
        input_size = file_path.stat().st_size

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

        if result.returncode not in (0, 1):
            stderr = result.stderr.strip() or result.stdout.strip()
            logger.error(f"mkvmerge failed for '{file_path}' (exit {result.returncode}).\n{stderr}")
            first_stderr = stderr.splitlines()[0].strip() if stderr else ""
            reason = f"mkvmerge failed (exit {result.returncode})"
            if first_stderr:
                reason += f": {first_stderr}"
            return reason

        # mkvmerge exit 1 means "completed with warnings" — the output is still valid.
        # Log the warning but continue; the output file check below confirms usability.
        if result.returncode == 1:
            logger.warning(
                f"mkvmerge reported warnings for '{file_path}' (exit 1) — output validated and accepted.\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        # Validate output file: must exist, be non-empty, and not suspiciously small.
        # A zero-size or heavily truncated file indicates a crashed/partial write.
        if not tmp_path.exists():
            logger.error(f"mkvmerge produced no output file for '{file_path}'.")
            return "mkvmerge produced no output file"
        output_size = tmp_path.stat().st_size
        # Zero-byte guard is unconditional — an empty file is never valid regardless
        # of --skip-size-check.  The ratio check below is the heuristic that flag bypasses.
        if output_size == 0:
            logger.error(f"mkvmerge produced an empty output file for '{file_path}'; rejecting to avoid data loss.")
            return "mkvmerge produced empty output file"
        min_acceptable = max(1, int(input_size * _MIN_OUTPUT_RATIO))
        if not skip_size_check and output_size < min_acceptable:
            logger.error(
                f"mkvmerge output for '{file_path}' is suspiciously small "
                f"({output_size} B vs {input_size} B input); rejecting to avoid data loss."
            )
            return f"mkvmerge output suspiciously small ({output_size} B vs {input_size} B input)"

        # Structural validation: probe the output with mkvmerge -J to confirm
        # it is a well-formed MKV container.  This catches partial writes and
        # internally inconsistent files that pass the size check but are
        # unplayable (e.g. due to disk-full mid-write on a FUSE/network share).
        # If validation fails, CorruptOutputError is raised to halt ALL further
        # processing — the temp file is preserved for operator inspection.
        probe = subprocess.run(
            [mkvmerge_path, "-J", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode != 0:
            corrupt_tmp = tmp_path
            tmp_path = None  # Prevent finally-block cleanup; file kept for inspection.
            raise CorruptOutputError(
                file_path=file_path,
                tmp_path=corrupt_tmp,
                probe_returncode=probe.returncode,
                probe_output=probe.stderr.strip() or probe.stdout.strip(),
                output_size=output_size,
                input_size=input_size,
                mkvmerge_path=mkvmerge_path,
            )

        # Replace original atomically.
        # For backup mode: rename original → .bak, then atomically replace with temp.
        # If the second step fails, roll back the backup rename.
        # For no-backup mode: directly replace the original with the temp in one atomic step.
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        if no_backup:
            # os.replace / Path.replace is atomic on POSIX; overwrites the destination.
            tmp_path.replace(file_path)
        else:
            # Path.replace() overwrites an existing destination on all platforms.
            # Path.rename() would raise FileExistsError on Windows if a .bak
            # file already exists from a previous run.
            file_path.replace(backup_path)
            try:
                tmp_path.replace(file_path)
            except BaseException:
                # Always attempt rollback when the backup exists.  The original
                # condition (`if not file_path.exists()`) was unsafe on Windows where
                # a partial write can leave a corrupt file at file_path.  Using
                # Path.replace() (not rename) overwrites any partial file atomically.
                if backup_path.exists():
                    try:
                        backup_path.replace(file_path)
                    except Exception as restore_exc:
                        logger.critical(
                            f"Could not restore original from backup '{backup_path}': {restore_exc}. "
                            f"Original is at '{backup_path}'."
                        )
                raise
            logger.debug(f"Original backed up to '{backup_path}'.")
        tmp_path = None  # Ownership transferred; do not delete in finally block.

        return None

    except Exception as exc:
        logger.error(f"Unexpected error processing '{file_path}': {exc}")
        logger.debug("", exc_info=True)
        return f"unexpected error: {str(exc).splitlines()[0].strip()}"
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning(f"Could not remove temp file '{tmp_path}': {cleanup_exc}")
