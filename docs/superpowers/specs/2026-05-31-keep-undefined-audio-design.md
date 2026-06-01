# `--keep-undefined-audio` — Preserving Undefined Language Audio Tracks

**Date:** 2026-05-31
**Status:** Design approved
**Issue:** [binhex/trimarr#60](https://github.com/binhex/trimarr/issues/60)

## Problem

The MKV spec requires a language field on all audio tracks, but many tools
set it to `und` (undefined) when the language is unknown.

Currently `_normalize_language_code()` converts `und` to `None`, and
`_classify_track_by_language()` drops any audio track where
`track.language is None` — because `None` never matches a user-specified
ISO 639-2 language code in the keep list.

**Risk:** In a file with one properly-labeled track and one `und` track,
the `und` track is silently dropped. This can mean losing audio that may
actually be in the desired language, or losing the only high-quality
audio track (surround sound, highest bitrate), simply because its language
tag is missing.

## Design

### 1. CLI Flag — `--keep-undefined-audio`

```python
@click.option(
    "--keep-undefined-audio",
    is_flag=True,
    default=False,
    help=(
        "If specified, audio tracks with an undefined language code (\"und\")"
        " are kept rather than dropped by the language filter.  Useful when"
        " source files have missing or incorrect language tags."
        " Ignored when --keep-audio is set."
    ),
)
```

- `is_flag=True, default=False` — consistent with all other trimarr flags
- No `--no-` prefix variant needed
- Threaded through: `cli.py` → `run()` → `_ProcessingConfig` →
  `build_mkvmerge_command()` → filtering pipeline

### 2. Profile Hash

`_build_profile_hash()` in `runner.py` must include `keep_undefined_audio`
so that changing this flag triggers reprocessing of previously-scanned files.

### 3. Filtering Pipeline — `_classify_track_by_language()`

A new `keep_undefined_audio: bool = False` parameter is added to
`_classify_track_by_language()`, `_apply_language_filter()`, and
`build_mkvmerge_command()`.

The classification logic becomes:

```python
def _classify_track_by_language(
    track: MkvTrack,
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    keep_undefined_audio: bool,  # NEW
    result: _FilterResult,
) -> None:
    if track.type == _TRACK_AUDIO:
        if keep_audio or track.language in language:
            result.audio_keep.append(track.id)
        elif track.language is None and keep_undefined_audio:
            result.audio_keep.append(track.id)
        else:
            result.audio_drop.append(track.id)
    elif track.type == _TRACK_SUBTITLES:
        # undefined subtitle tracks are NOT preserved by this flag
        if keep_subtitles or track.language in language:
            result.sub_keep.append(track.id)
        else:
            result.sub_drop.append(track.id)
```

### 4. Interaction With Other Flags

| Flag | Interaction |
|---|---|
| `--keep-audio` | Takes precedence — all audio kept, `--keep-undefined-audio` ignored |
| `--strip-commentary` | Orthogonal — commentary strip checks **track name**, not language. An `und` track with a commentary-sounding name is still removed after language filtering |
| Audio safety fallback | Unchanged — only fires when ALL audio tracks would be dropped. With `--keep-undefined-audio`, mixed files (one labeled + one `und`) will keep the `und` track, so the fallback won't trigger |
| `--strip-lower-channels` | Still applies — an `und` track with low channels may be dropped after language filtering |

### 5. Error Handling / Edge Cases

| Scenario | Behaviour |
|---|---|
| `--keep-undefined-audio` not set (default) | Current behaviour: `und` tracks dropped |
| `--keep-undefined-audio` set, no `und` tracks | No effect |
| `--keep-undefined-audio` and `--keep-audio` both set | `--keep-audio` takes precedence, warning logged |
| All tracks are `und` | Kept (if flag set), then audio safety fallback still protects against silent file |

### 6. Testing

**Test file:** `tests/unit/test_processor.py`

| Test | What it covers |
|---|---|
| `test_keep_undefined_audio_keeps_und_track` | `und` audio track is kept when flag set |
| `test_keep_undefined_audio_default_drops_und_track` | Default (False) drops `und` as before |
| `test_keep_undefined_audio_with_keep_audio` | No effect when `--keep-audio` is set |
| `test_keep_undefined_audio_subtitle_not_affected` | Undefined subtitle tracks still dropped |
| `test_keep_undefined_audio_profile_hash` | Hash changes when flag changes |

### 7. Files Changed

| File | Change |
|---|---|
| `src/trimarr/cli.py` | Add `--keep-undefined-audio` option |
| `src/trimarr/runner.py` | Add to `_ProcessingConfig`, thread through `run()`, add to `_build_profile_hash()` |
| `src/trimarr/processor.py` | Add parameter to `_classify_track_by_language()`, `_apply_language_filter()`, `build_mkvmerge_command()` |
| `tests/unit/test_processor.py` | Add test cases |
