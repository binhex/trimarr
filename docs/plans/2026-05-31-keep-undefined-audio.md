# `--keep-undefined-audio` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--keep-undefined-audio` flag so audio tracks with `und` (undefined) language codes are preserved instead of silently dropped by the language filter.

**Architecture:** A new `keep_undefined_audio: bool = False` parameter flows from the CLI flag through `_ProcessingConfig` → `build_mkvmerge_command()` → `_apply_language_filter()` → `_classify_track_by_language()`. In the classifier, a new `elif` branch checks for `track.language is None and keep_undefined_audio` and keeps the track. The parameter is also added to `_build_profile_hash()` so toggling the flag reprocesses files.

**Tech Stack:** Python 3.12+, Click (CLI), pytest (tests)

---

### Task 1: Processor — Add `keep_undefined_audio` parameter to filtering pipeline

**Files:**
- Modify: `src/trimarr/processor.py`
- Test: `tests/unit/test_processor.py`

- [ ] **Step 1: Write failing test for `_classify_track_by_language` with `keep_undefined_audio`**

Add to `tests/unit/test_processor.py` inside (or after) the existing `TestNoneLanguageTrackFiltering` class (around line 1267):

```python
def test_keep_undefined_audio_keeps_none_language_track(self) -> None:
    """Undefined audio track (language=None) is kept when keep_undefined_audio=True."""
    from trimarr.processor import _classify_track_by_language, _FilterResult, _TRACK_AUDIO, MkvTrack

    result = _FilterResult()
    track = MkvTrack(id=1, type=_TRACK_AUDIO, language=None, name=None, default_track=False, channels=2)
    _classify_track_by_language(track, ["eng"], keep_audio=False, keep_subtitles=False, keep_undefined_audio=True, result=result)
    assert 1 in result.audio_keep
    assert 1 not in result.audio_drop

def test_keep_undefined_audio_false_drops_none_language_track(self) -> None:
    """Undefined audio track (language=None) is dropped when keep_undefined_audio=False (default)."""
    from trimarr.processor import _classify_track_by_language, _FilterResult, _TRACK_AUDIO, MkvTrack

    result = _FilterResult()
    track = MkvTrack(id=1, type=_TRACK_AUDIO, language=None, name=None, default_track=False, channels=2)
    _classify_track_by_language(track, ["eng"], keep_audio=False, keep_subtitles=False, keep_undefined_audio=False, result=result)
    assert 1 in result.audio_drop
    assert 1 not in result.audio_keep

def test_keep_undefined_audio_ignores_subtitle_tracks(self) -> None:
    """Undefined subtitle track is still dropped — flag is audio-only."""
    from trimarr.processor import _classify_track_by_language, _FilterResult, _TRACK_SUBTITLES, MkvTrack

    result = _FilterResult()
    track = MkvTrack(id=1, type=_TRACK_SUBTITLES, language=None, name=None, default_track=False, channels=None)
    _classify_track_by_language(track, ["eng"], keep_audio=False, keep_subtitles=False, keep_undefined_audio=True, result=result)
    assert 1 in result.sub_drop

def test_keep_undefined_audio_noop_when_keep_audio_set(self) -> None:
    """keep_undefined_audio has no effect when keep_audio=True (all audio kept anyway)."""
    from trimarr.processor import _classify_track_by_language, _FilterResult, _TRACK_AUDIO, MkvTrack

    result = _FilterResult()
    track = MkvTrack(id=1, type=_TRACK_AUDIO, language="und", name=None, default_track=False, channels=2)
    _classify_track_by_language(track, ["eng"], keep_audio=True, keep_subtitles=False, keep_undefined_audio=False, result=result)
    assert 1 in result.audio_keep
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_processor.py::TestNoneLanguageTrackFilterling -v`

Expected: FAIL with TypeError — `_classify_track_by_language()` got an unexpected keyword argument `keep_undefined_audio`

- [ ] **Step 3: Add `keep_undefined_audio` parameter to `_classify_track_by_language()`**

In `src/trimarr/processor.py`, update the function (around line 371):

```python
def _classify_track_by_language(
    track: MkvTrack,
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    keep_undefined_audio: bool,  # NEW
    result: _FilterResult,
) -> None:
    """Classify a single track as keep or drop based on language rules.

    Mutates *result* in place by appending the track ID to the appropriate
    keep/drop list.

    When *keep_undefined_audio* is *True* and the track's language is *None*
    (from an ``und`` MKV tag), the track is kept rather than dropped,
    preventing accidental loss of audio whose language is simply unknown.
    Only applies to audio tracks.
    """
    if track.type == _TRACK_AUDIO:
        if keep_audio or track.language in language:
            result.audio_keep.append(track.id)
        elif track.language is None and keep_undefined_audio:
            result.audio_keep.append(track.id)
        else:
            result.audio_drop.append(track.id)
    elif track.type == _TRACK_SUBTITLES:
        if keep_subtitles or track.language in language:
            result.sub_keep.append(track.id)
        else:
            result.sub_drop.append(track.id)
```

- [ ] **Step 4: Add `keep_undefined_audio` parameter to `_apply_language_filter()`**

Update `_apply_language_filter()` (around line 395) to accept and pass through the new parameter:

```python
def _apply_language_filter(
    tracks: list[MkvTrack],
    language: list[str],
    keep_audio: bool,
    keep_subtitles: bool,
    keep_undefined_audio: bool = False,  # NEW
) -> _FilterResult:
```

And update the call to `_classify_track_by_language` inside it (around line 405):

```python
    for track in tracks:
        _classify_track_by_language(
            track, language, keep_audio, keep_subtitles,
            keep_undefined_audio,  # NEW
            result,
        )
```

- [ ] **Step 5: Add `keep_undefined_audio` parameter to `build_mkvmerge_command()`**

Update the function signature (around line 879) and the call to `_apply_language_filter` inside it:

```python
def build_mkvmerge_command(
    ...
    strip_commentary: bool = False,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
    keep_undefined_audio: bool = False,  # NEW
) -> list[str] | None:
```

Find the call to `_apply_language_filter` inside this function (around line 899):

```python
    result = _apply_language_filter(tracks, language, keep_audio, keep_subtitles, keep_undefined_audio)
```

- [ ] **Step 6: Run all new tests to verify pass**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_processor.py::TestNoneLanguageTrackFiltering -v`

Expected: All 4 new tests PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -10`

Expected: All tests pass (no regressions)

- [ ] **Step 8: Commit**

```bash
cd /data/trimarr && git add src/trimarr/processor.py tests/unit/test_processor.py
git commit -m "feat: add keep_undefined_audio parameter to filtering pipeline"
```

---

### Task 2: Runner — Thread `keep_undefined_audio` through config and profile hash

**Files:**
- Modify: `src/trimarr/runner.py`

- [ ] **Step 1: Add to `_ProcessingConfig` dataclass**

In `src/trimarr/runner.py`, add to `_ProcessingConfig` (around line 45):

```python
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None
    tmdb_api_key: str | None = None
    tvdb_api_key: str | None = None
    keep_undefined_audio: bool = False  # NEW
```

- [ ] **Step 2: Add parameter to `run()` function**

Update the `run()` function signature (around line 613) to accept `keep_undefined_audio: bool = False`. Place it after `strip_commentary` to group with other audio-related flags.

- [ ] **Step 3: Add to `_ProcessingConfig` construction**

Inside `run()`, find the `_ProcessingConfig(...)` call (around line 665) and add:

```python
        keep_undefined_audio=keep_undefined_audio,
```

- [ ] **Step 4: Add to `_build_profile_hash()`**

Update the function signature (around line 133) and the profile dict:

```python
def _build_profile_hash(
    ...
    strip_commentary: bool,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,
    keep_undefined_audio: bool = False,  # NEW
) -> str:
```

Inside, add the key to the `profile` dict (around line 167):

```python
    profile = {
        "delete_metadata_title": delete_metadata_title,
        "edit_metadata_title": edit_metadata_title,
        "keep_audio": keep_audio,
        "keep_subtitles": keep_subtitles,
        "language": canonical_language,
        "strip_commentary": strip_commentary,
        "strip_lower_channels": strip_lower_channels,
        "keep_undefined_audio": keep_undefined_audio,  # NEW
    }
```

- [ ] **Step 5: Update `_process_one_file()` to pass `keep_undefined_audio`**

In `_process_one_file()`, find:

1. The `_build_profile_hash()` call (around line 372) — add `keep_undefined_audio=cfg.keep_undefined_audio`
2. The `build_mkvmerge_command()` call (around line 403) — add `keep_undefined_audio=cfg.keep_undefined_audio`
3. The `_build_profile_hash()` call in the outer `run()` function (around line 644) — add `keep_undefined_audio=keep_undefined_audio`

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -5`

Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
cd /data/trimarr && git add src/trimarr/runner.py
git commit -m "feat: thread keep_undefined_audio through runner config and profile hash"
```

---

### Task 3: CLI — Add `--keep-undefined-audio` flag

**Files:**
- Modify: `src/trimarr/cli.py`

- [ ] **Step 1: Add the CLI option**

In `src/trimarr/cli.py`, after the `--keep-subtitles` option block (around line 196), add:

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

- [ ] **Step 2: Add parameter to `cli()` function signature**

Add `keep_undefined_audio: bool,` to the `cli()` function (around line 517), grouping it with other `keep_*` audio flags.

- [ ] **Step 3: Thread into `run()` call**

Inside `cli()`, find the `run(...)` call (around line 574) and add `keep_undefined_audio=keep_undefined_audio`.

- [ ] **Step 4: Run tests**

Run: `cd /data/trimarr && uv run pytest --no-header -q 2>&1 | tail -5`

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
cd /data/trimarr && git add src/trimarr/cli.py
git commit -m "feat: add --keep-undefined-audio CLI flag"
```

---

### Task 4: Full Suite QA Gate

**Files:** None (verification only)

- [ ] **Step 1: Ruff lint + format**

Run: `cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .`

- [ ] **Step 2: Type check**

Run: `cd /data/trimarr && uv run mypy .`

- [ ] **Step 3: Full test suite with coverage**

Run: `cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr --cov-fail-under=80 2>&1 | tail -20`

- [ ] **Step 4: Commit any fixes**

```bash
cd /data/trimarr && git add -A
git commit -m "chore: satisfy QA gates for keep-undefined-audio feature"
```

---

## File Change Summary

| File | What changed |
|---|---|
| `src/trimarr/processor.py` | `_classify_track_by_language()`: new `keep_undefined_audio` param + `elif` branch for None-language tracks; `_apply_language_filter()`: pass through; `build_mkvmerge_command()`: new param + pass through |
| `src/trimarr/runner.py` | `_ProcessingConfig`: new field; `_build_profile_hash()`: new param + profile key; `run()`: new param + threading; `_process_one_file()`: pass to both `_build_profile_hash()` and `build_mkvmerge_command()` |
| `src/trimarr/cli.py` | New `--keep-undefined-audio` option + param threading |
| `tests/unit/test_processor.py` | 4 new tests for `_classify_track_by_language` with `keep_undefined_audio` |
