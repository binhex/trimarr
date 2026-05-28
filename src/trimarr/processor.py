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


def normalize_language_code(code: str) -> str:
    """Normalise an ISO 639-2 code to its bibliographic (B) form."""
    return _ISO_639_2_T_TO_B.get(code, code)


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
    # Track IDs removed specifically by the subtitle regex phase.
    subtitle_regex_drop_ids: set[int] = field(default_factory=set)


# Minimum acceptable output-to-input size ratio.  A legitimate remux strips
# audio/subtitle tracks but the video stream (the bulk of any MKV) is always
# retained — typically 90 %+ of the source size.  50 % is a very conservative
# lower bound that catches catastrophically truncated or partial writes while
# still allowing files with unusually large audio/subtitle payloads.
_MIN_OUTPUT_RATIO: float = 0.5


# Singular display names for log messages (track type → operator-facing label).
_TRACK_LOG_NAME: dict[str, str] = {
    "audio": "audio",
    "subtitles": "subtitle",
}

# Track type string constants matching mkvmerge -J output.
_TRACK_AUDIO = "audio"
_TRACK_SUBTITLES = "subtitles"
_TRACK_VIDEO = "video"

# Timeout in seconds for mkvmerge -J probing (per file).
_PROBE_TIMEOUT = 60
# Timeout in seconds for the full mkvmerge remux (generous for large files).
_PROCESS_TIMEOUT = 3600


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


# ---------------------------------------------------------------------------
# Language normalisation helpers
# ---------------------------------------------------------------------------


def _normalize_language_code(lang: str | None) -> str | None:
    """Normalise a raw mkvmerge language tag to an ISO 639-2/B code.

    Converts the mkvmerge ``"und"`` sentinel to *None*, maps ISO 639-1 2-char
    codes to their ISO 639-2 equivalents, and converts ISO 639-2 terminologic
    (T) codes to bibliographic (B) form.  Unknown codes are returned unchanged.

    The tag is lower-cased before lookup so that uppercase codes (e.g. ``"EN"``,
    ``"FRA"``) are matched correctly against the dict keys.
    """
    if lang is None or lang == "und":
        return None
    base = lang.split("-")[0].lower()
    if len(base) == 2:
        return _ISO_639_1_TO_2.get(base, base)
    if len(base) == 3:
        return _ISO_639_2_T_TO_B.get(base, base)
    return lang


def _parse_track(raw: dict) -> MkvTrack:
    """Parse a single track entry from ``mkvmerge -J`` JSON output."""
    props = raw.get("properties", {})
    lang_raw = props.get("language") or props.get("language_ietf")
    lang = _normalize_language_code(lang_raw)
    return MkvTrack(
        id=raw["id"],
        type=raw["type"],
        language=lang,
        name=props.get("track_name") or None,
        default_track=bool(props.get("default_track", False)),
        channels=props.get("audio_channels") if raw["type"] == _TRACK_AUDIO else None,
    )


def probe_file(mkvmerge_path: str, file_path: Path) -> list[MkvTrack]:
    """Run ``mkvmerge -J`` on *file_path* and return the list of :class:`MkvTrack` objects.

    Raises :exc:`RuntimeError` on non-zero exit, timeout, OS error, or JSON parse failure.
    """
    try:
        result = subprocess.run(
            [mkvmerge_path, "-J", str(file_path)],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
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

    return [_parse_track(raw) for raw in info.get("tracks", [])]


# ---------------------------------------------------------------------------
# Internal filtering helpers used by build_mkvmerge_command()
# ---------------------------------------------------------------------------


def _classify_track_by_language(
    track: MkvTrack,
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    result: _FilterResult,
) -> None:
    """Classify a single track as keep or drop based on language rules.

    Mutates *result* in place by appending the track ID to the appropriate
    keep/drop list.
    """
    if track.type == _TRACK_AUDIO:
        if keep_audio or track.language in language:
            result.audio_keep.append(track.id)
        else:
            result.audio_drop.append(track.id)
    elif track.type == _TRACK_SUBTITLES:
        if keep_subtitles or track.language in language:
            result.sub_keep.append(track.id)
        else:
            result.sub_drop.append(track.id)


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
        _classify_track_by_language(track, language, keep_audio, keep_subtitles, result)
    # Snapshot before fallbacks modify the drop lists (used for logging only).
    result.language_audio_drop_ids = frozenset(result.audio_drop)
    result.language_sub_drop_ids = frozenset(result.sub_drop)
    return result


def _all_kept_audio_is_commentary(result: _FilterResult, tracks: list[MkvTrack]) -> bool:
    """Return *True* when every kept audio track is a commentary track."""
    audio_commentary_ids = {t.id for t in tracks if t.type == _TRACK_AUDIO and _is_commentary(t.name)}
    return all(tid in audio_commentary_ids for tid in result.audio_keep)


def _lang_desc(language: list[str]) -> str:
    """Format language filter description for log messages."""
    return f"'{language[0]}'" if len(language) == 1 else str(language)


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
    lang_desc = _lang_desc(language)
    if result.audio_drop and not result.audio_keep:
        logger.warning(
            f"No audio tracks match language {lang_desc} in '{input_path.name}' "
            f"\u2014 keeping all audio to prevent silent data loss."
        )
        result.audio_drop.clear()
        result.audio_fallback_fired = True
    elif result.audio_drop and _all_kept_audio_is_commentary(result, tracks):
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
    lang_desc = _lang_desc(language)
    if result.sub_drop and not result.sub_keep:
        logger.warning(
            f"No subtitle tracks match language {lang_desc} in '{input_path.name}' \u2014 keeping all subtitles."
        )
        result.sub_drop.clear()
        result.sub_fallback_fired = True


def _find_audio_commentary_to_drop(tracks: list[MkvTrack], audio_keep_set: set[int]) -> list[int]:
    """Return IDs of kept audio tracks that are commentary tracks."""
    return [t.id for t in tracks if t.type == _TRACK_AUDIO and t.id in audio_keep_set and _is_commentary(t.name)]


def _strip_audio_commentary_unsafe(
    result: _FilterResult,
    tracks: list[MkvTrack],
    logger: Logger,
    input_path: Path,
) -> None:
    """Strip audio commentary tracks after all safety guards have been checked.

    Guards are verified by the caller (:func:`_apply_strip_commentary`).
    Applies the final gate: if stripping would remove all remaining audio,
    keep everything and log a warning instead.
    """
    audio_keep_set = set(result.audio_keep)
    audio_commentary_to_drop = _find_audio_commentary_to_drop(tracks, audio_keep_set)
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


def _strip_subtitle_commentary_unsafe(
    result: _FilterResult,
    tracks: list[MkvTrack],
) -> None:
    """Strip subtitle commentary tracks after all safety guards have been checked.

    Guards are verified by the caller (:func:`_apply_strip_commentary`).
    Unlike audio, there is no final gate — subtitle-free output is acceptable.
    """
    sub_keep_set = set(result.sub_keep)
    sub_commentary_to_drop = [
        t.id for t in tracks if t.type == _TRACK_SUBTITLES and t.id in sub_keep_set and _is_commentary(t.name)
    ]
    if sub_commentary_to_drop:
        result.commentary_sub_drop_ids = set(sub_commentary_to_drop)
        for tid in sub_commentary_to_drop:
            result.sub_keep.remove(tid)
            result.sub_drop.append(tid)


def _apply_strip_commentary(
    result: _FilterResult,
    tracks: list[MkvTrack],
    keep_audio: bool,
    keep_subtitles: bool,
    logger: Logger,
    input_path: Path,
    strip_commentary: bool,
) -> None:
    """Phase 4 — strip commentary tracks after all language/safety fallbacks have resolved."""
    if not strip_commentary:
        return

    if not keep_audio and not result.audio_fallback_fired and result.audio_keep:
        _strip_audio_commentary_unsafe(result, tracks, logger, input_path)

    if not keep_subtitles and not result.sub_fallback_fired and result.sub_keep:
        _strip_subtitle_commentary_unsafe(result, tracks)


def _match_subtitle_regex_patterns(
    track: MkvTrack,
    patterns: list[re.Pattern],
    result: _FilterResult,
) -> None:
    """If *track* name matches any *pattern*, move it to sub_drop."""
    for pattern in patterns:
        if pattern.search(track.name):
            result.sub_keep.remove(track.id)
            result.sub_drop.append(track.id)
            result.subtitle_regex_drop_ids.add(track.id)
            return


def _apply_strip_subtitle_regex(
    result: _FilterResult,
    tracks: list[MkvTrack],
    patterns: list[re.Pattern],
    keep_subtitles: bool = False,
) -> None:
    """Phase 4b — drop subtitle tracks whose name matches any user-supplied regex.

    Operates on the subtitle tracks that survived language filtering and commentary
    stripping.  Each pattern is tested against each kept subtitle track's ``name``
    field.  Matched tracks are moved from ``sub_keep`` to ``sub_drop``.
    There is no safety guard — if all subtitles match, all are dropped.
    """
    if not patterns or keep_subtitles or result.sub_fallback_fired:
        return

    keep_set = set(result.sub_keep)
    for track in tracks:
        if track.type != _TRACK_SUBTITLES:
            continue
        if track.id not in keep_set:
            continue
        if track.name is None:
            continue
        _match_subtitle_regex_patterns(track, patterns, result)


def _group_kept_audio_by_language(
    tracks: list[MkvTrack],
    keep_set: set[int],
) -> tuple[dict[str | None, list[MkvTrack]], list[MkvTrack]]:
    """Group kept audio tracks by language and return (lang_groups, surviving).

    Returns both the per-language groups and the flat list of all surviving
    audio tracks so callers avoid recomputing the filter.
    """
    surviving = [t for t in tracks if t.type == _TRACK_AUDIO and t.id in keep_set]
    lang_groups: dict[str | None, list[MkvTrack]] = {}
    for t in surviving:
        lang_groups.setdefault(t.language, []).append(t)
    return lang_groups, surviving


def _find_non_commentary_with_known_channels(
    group_tracks: list[MkvTrack],
) -> tuple[list[MkvTrack], list[int]]:
    """Return (non_commentary_tracks, known_channel_counts) for *group_tracks*.

    Commentary tracks are excluded so they do not inflate the max-channel
    calculation used by :func:`_compute_channel_drops_per_group`.
    """
    non_commentary = [t for t in group_tracks if not _is_commentary(t.name)]
    known = [t.channels for t in non_commentary if t.channels is not None]
    return non_commentary, known


def _compute_channel_drops_per_group(group_tracks: list[MkvTrack]) -> list[int]:
    """Return IDs of lower-channel non-commentary tracks to drop within *group_tracks*."""
    non_commentary, known = _find_non_commentary_with_known_channels(group_tracks)
    if not known:
        return []
    max_ch = max(known)
    if any(ch < max_ch for ch in known):
        return [t.id for t in non_commentary if t.channels is not None and t.channels < max_ch]
    return []


def _apply_channel_drop_changes(
    channel_drop: list[int],
    result: _FilterResult,
    surviving: list[MkvTrack],
    logger: Logger,
) -> None:
    """Apply channel-based drop decisions to *result* and emit log messages."""
    if channel_drop:
        channel_drop_set = set(channel_drop)
        for tid in channel_drop:
            result.audio_keep.remove(tid)
            result.audio_drop.append(tid)
        descs = ", ".join(_fmt_track(t) for t in surviving if t.id in channel_drop_set)
        logger.info(f"  Dropping {len(channel_drop)} audio track(s) with fewer channels than the per-language maximum.")
        logger.debug(f"  Dropping lower-channel audio track(s): {descs}")


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
    if not strip_lower_channels:
        return
    if keep_audio:
        return
    if not result.audio_keep:
        return
    if result.audio_fallback_fired:
        return

    keep_set = set(result.audio_keep)
    lang_groups, surviving = _group_kept_audio_by_language(tracks, keep_set)

    channel_drop: list[int] = []
    for group_tracks in lang_groups.values():
        channel_drop.extend(_compute_channel_drops_per_group(group_tracks))

    _apply_channel_drop_changes(channel_drop, result, surviving, logger)


# ---------------------------------------------------------------------------
# Logging helpers used by _log_filter_changes()
# ---------------------------------------------------------------------------


def _log_language_drops(
    drop_ids: frozenset[int],
    track_type: str,
    tracks: list[MkvTrack],
    language: list[str],
    needs_change: bool,
    logger: Logger,
) -> None:
    """Log language-filtered track drops for one track type."""
    if drop_ids and needs_change:
        lang_filter = f"\u2260 {_lang_desc(language)}" if len(language) == 1 else f"not in {language}"
        display = _TRACK_LOG_NAME.get(track_type, track_type)
        logger.info(f"  Dropping {len(drop_ids)} {display} track(s) (language {lang_filter}).")
        descs = ", ".join(_fmt_track(t) for t in tracks if t.type == track_type and t.id in drop_ids)
        logger.debug(f"  Dropping {display} track(s): {descs}")


def _log_commentary_drops(
    drop_ids: set[int],
    track_type: str,
    tracks: list[MkvTrack],
    logger: Logger,
) -> None:
    """Log commentary track drops for one track type."""
    if drop_ids:
        display = _TRACK_LOG_NAME.get(track_type, track_type)
        logger.info(f"  Dropping {len(drop_ids)} {display} commentary track(s).")
        descs = ", ".join(_fmt_track(t) for t in tracks if t.type == track_type and t.id in drop_ids)
        logger.debug(f"  Dropping {display} commentary track(s): {descs}")


def _log_metadata_title_change(
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    input_path: Path,
    logger: Logger,
) -> None:
    """Log the metadata title operation that will be applied."""
    if edit_metadata_title:
        logger.info(f"  Metadata: setting title to '{input_path.stem}'")
    elif delete_metadata_title:
        logger.info("  Metadata: clearing title")


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
    _log_language_drops(result.language_audio_drop_ids, _TRACK_AUDIO, tracks, language, needs_audio_change, logger)
    _log_language_drops(result.language_sub_drop_ids, _TRACK_SUBTITLES, tracks, language, needs_sub_change, logger)
    _log_commentary_drops(result.commentary_audio_drop_ids, _TRACK_AUDIO, tracks, logger)
    _log_commentary_drops(result.commentary_sub_drop_ids, _TRACK_SUBTITLES, tracks, logger)
    if result.subtitle_regex_drop_ids:
        logger.info(f"  Dropping {len(result.subtitle_regex_drop_ids)} subtitle track(s) by name regex.")
        descs = ", ".join(
            _fmt_track(t) for t in tracks if t.type == _TRACK_SUBTITLES and t.id in result.subtitle_regex_drop_ids
        )
        logger.debug(f"  Dropping subtitle track(s) by name regex: {descs}")
    _log_metadata_title_change(edit_metadata_title, delete_metadata_title, input_path, logger)


# ---------------------------------------------------------------------------
# Default-track flag helpers used by _compute_default_track_flags()
# ---------------------------------------------------------------------------


def _find_commentary_and_non(
    track_type: str,
    keep_ids: list[int],
    tracks: list[MkvTrack],
) -> tuple[list[MkvTrack], list[MkvTrack]]:
    """Return (commentary_kept, non_commentary_kept) for *track_type* among *keep_ids*."""
    keep_set = set(keep_ids)
    kept_tracks = [t for t in tracks if t.type == track_type and t.id in keep_set]
    commentary_kept = [t for t in kept_tracks if _is_commentary(t.name)]
    non_commentary = [t for t in kept_tracks if not _is_commentary(t.name)]
    return commentary_kept, non_commentary


def _default_flags_for_commentary_tracks(
    commentary_kept: list[MkvTrack],
    non_commentary: list[MkvTrack],
    track_type: str,
    logger: Logger,
    input_path: Path,
) -> list[str]:
    """Build ``--default-track`` flags; demotes commentary defaults, promotes non-commentary."""
    flags: list[str] = []
    if non_commentary:
        if not any(t.default_track for t in non_commentary):
            flags += ["--default-track", f"{non_commentary[0].id}:1"]
        for t in commentary_kept:
            if t.default_track:
                flags += ["--default-track", f"{t.id}:0"]
    else:
        for t in commentary_kept:
            if t.default_track:
                flags += ["--default-track", f"{t.id}:0"]
        logger.warning(
            f"All remaining {track_type} tracks in '{input_path.name}' are commentary "
            f"\u2014 cannot reassign default {track_type} track."
        )
    return flags


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
        (_TRACK_AUDIO, needs_audio_change, result.audio_keep),
        (_TRACK_SUBTITLES, needs_sub_change, result.sub_keep),
    ):
        if not needs_change:
            continue
        commentary_kept, non_commentary = _find_commentary_and_non(track_type, keep_ids, tracks)
        if not commentary_kept:
            continue
        default_flags += _default_flags_for_commentary_tracks(
            commentary_kept, non_commentary, track_type, logger, input_path
        )
    return default_flags


# ---------------------------------------------------------------------------
# mkvmerge command assembly helpers used by build_mkvmerge_command()
# ---------------------------------------------------------------------------


def _assemble_metadata_args(
    edit_metadata_title: bool,
    delete_metadata_title: bool,
    input_path: Path,
) -> list[str]:
    """Return the ``--title`` argv fragment for the requested metadata operation."""
    if edit_metadata_title:
        return ["--title", input_path.stem]
    if delete_metadata_title:
        return ["--title", ""]
    return []


def _format_track_ids(ids: list[int]) -> str:
    """Format a list of track IDs as a comma-separated string."""
    return ",".join(str(i) for i in ids)


def _assemble_track_args(
    result: _FilterResult,
    needs_audio_change: bool,
    needs_sub_change: bool,
) -> list[str]:
    """Return audio and subtitle inclusion/exclusion argv fragments."""
    args: list[str] = []
    if needs_audio_change and result.audio_keep:
        args += ["--audio-tracks", _format_track_ids(result.audio_keep)]
    if needs_sub_change and result.sub_keep:
        args += ["--subtitle-tracks", _format_track_ids(result.sub_keep)]
    elif needs_sub_change and not result.sub_keep:
        args += ["--no-subtitles"]
    return args


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
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
) -> list[str] | None:
    """Build the mkvmerge argv for *input_path*; return *None* if no changes are needed.

    Runs all filtering phases (language, fallbacks, commentary, regex, channel), then assembles
    the final command.  Returns *None* when no track or metadata changes are required
    so callers can skip mkvmerge and mark the file as already processed.
    """
    result = _apply_language_filter(tracks, language, keep_audio, keep_subtitles)
    _apply_audio_fallbacks(result, tracks, language, logger, input_path)
    _apply_subtitle_fallback(result, language, logger, input_path)
    _apply_strip_commentary(result, tracks, keep_audio, keep_subtitles, logger, input_path, strip_commentary)
    _apply_strip_subtitle_regex(result, tracks, strip_subtitle_regex_patterns or [], keep_subtitles=keep_subtitles)
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
    cmd += _assemble_metadata_args(edit_metadata_title, delete_metadata_title, input_path)
    cmd += _assemble_track_args(result, needs_audio_change, needs_sub_change)
    cmd += _compute_default_track_flags(tracks, result, needs_audio_change, needs_sub_change, logger, input_path)
    cmd.append(str(input_path))
    return cmd


# ---------------------------------------------------------------------------
# process_file() helpers
# ---------------------------------------------------------------------------


def _check_exit_code(
    result: subprocess.CompletedProcess[str],
    file_path: Path,
    logger: Logger,
) -> str | None:
    """Validate the mkvmerge exit code and log accordingly.

    Returns an error reason string when the remux failed (exit code >= 2),
    *None* when the exit code indicates success (0) or warnings (1).
    Logs a warning for exit code 1 (completed with warnings).
    """
    if result.returncode not in (0, 1):
        stderr = result.stderr.strip() or result.stdout.strip()
        logger.error(f"mkvmerge failed for '{file_path}' (exit {result.returncode}).\n{stderr}")
        first_stderr = stderr.splitlines()[0].strip() if stderr else ""
        reason = f"mkvmerge failed (exit {result.returncode})"
        if first_stderr:
            reason += f": {first_stderr}"
        return reason
    if result.returncode == 1:
        logger.warning(
            f"mkvmerge reported warnings for '{file_path}' (exit 1) — output validated and accepted.\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return None


def _check_output_size(
    tmp_path: Path,
    input_size: int,
    skip_size_check: bool,
    file_path: Path,
    logger: Logger,
) -> tuple[str | None, int]:
    """Validate the output file exists and has an acceptable size.

    Returns ``(error_reason, output_size)`` where *error_reason* is *None* on
    success or a non-empty string describing the rejection reason on failure.
    *output_size* is 0 when an error is returned.
    """
    if not tmp_path.exists():
        logger.error(f"mkvmerge produced no output file for '{file_path}'.")
        return "mkvmerge produced no output file", 0
    output_size = tmp_path.stat().st_size
    # Zero-byte guard is unconditional — an empty file is never valid regardless
    # of --skip-size-check.  The ratio check below is the heuristic that flag bypasses.
    if output_size == 0:
        logger.error(f"mkvmerge produced an empty output file for '{file_path}'; rejecting to avoid data loss.")
        return "mkvmerge produced empty output file", 0
    min_acceptable = max(1, int(input_size * _MIN_OUTPUT_RATIO))
    if not skip_size_check and output_size < min_acceptable:
        logger.error(
            f"mkvmerge output for '{file_path}' is suspiciously small "
            f"({output_size} B vs {input_size} B input); rejecting to avoid data loss."
        )
        return f"mkvmerge output suspiciously small ({output_size} B vs {input_size} B input)", 0
    return None, output_size


def _atomic_file_replace(
    tmp_path: Path,
    file_path: Path,
    no_backup: bool,
    logger: Logger,
) -> None:
    """Atomically replace *file_path* with *tmp_path*, optionally keeping a backup.

    For backup mode: rename original → .bak, then atomically replace with temp.
    If the second step fails, roll back the backup rename.
    For no-backup mode: directly replace the original with the temp atomically.

    Raises:
        Any :class:`BaseException` raised by the file operations, after attempting
        rollback when in backup mode.
    """
    # Capture the original file's permission bits before the rename so they
    # can be applied to the temp file.  ``tempfile.mkstemp()`` creates files
    # with mode 0o600 (masked by umask), which would otherwise replace the
    # original's permissions with a restrictive set after the atomic rename.
    original_mode = file_path.stat().st_mode

    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    if no_backup:
        # os.replace / Path.replace is atomic on POSIX; overwrites the destination.
        tmp_path.chmod(original_mode & 0o777)
        tmp_path.replace(file_path)
    else:
        # Path.replace() overwrites an existing destination on all platforms.
        # Path.rename() would raise FileExistsError on Windows if a .bak
        # file already exists from a previous run.
        file_path.replace(backup_path)
        try:
            tmp_path.chmod(original_mode & 0o777)
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


def _cleanup_tmp_file(tmp_path: Path | None, logger: Logger) -> None:
    """Remove *tmp_path* if it still exists (best-effort; logs on failure)."""
    if tmp_path is not None and tmp_path.exists():
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning(f"Could not remove temp file '{tmp_path}': {cleanup_exc}")


def process_file(
    mkvmerge_path: str,
    file_path: Path,
    command: list[str],
    no_backup: bool,
    logger: Logger,
    skip_size_check: bool = False,
) -> str | None:
    """Execute *command* and atomically replace *file_path* with the result.

    Writes to a temp file, validates exit code, size, and structure, then
    renames into place.  Returns *None* on success or an error reason string.
    Original is never touched on failure; temp file is always cleaned up.
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
            result = subprocess.run(patched_cmd, capture_output=True, text=True, timeout=_PROCESS_TIMEOUT)

        exit_error = _check_exit_code(result, file_path, logger)
        if exit_error is not None:
            return exit_error

        size_error, output_size = _check_output_size(tmp_path, input_size, skip_size_check, file_path, logger)
        if size_error is not None:
            return size_error

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
            timeout=_PROBE_TIMEOUT,
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
        _atomic_file_replace(tmp_path, file_path, no_backup, logger)
        tmp_path = None  # Ownership transferred; do not delete in finally block.

        return None

    except OSError as exc:
        logger.error(f"File system error processing '{file_path}': {exc}")
        logger.debug("", exc_info=True)
        return f"filesystem error: {str(exc).splitlines()[0].strip()}"
    except subprocess.TimeoutExpired as exc:
        logger.error(f"mkvmerge timed out processing '{file_path}': {exc}")
        logger.debug("", exc_info=True)
        return f"mkvmerge timeout: {str(exc).splitlines()[0].strip()}"
    except Exception as exc:
        logger.error(f"Unexpected error processing '{file_path}': {exc}")
        logger.debug("", exc_info=True)
        return f"unexpected error: {str(exc).splitlines()[0].strip()}"
    finally:
        _cleanup_tmp_file(tmp_path, logger)
