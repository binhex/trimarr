# Strip Subtitle by Regex — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--strip-subtitle-regex <pattern>` CLI flag that force-deletes subtitle tracks whose `name` matches the user-provided regex.

**Architecture:** New Phase 4b in `build_mkvmerge_command()`, between commentary stripping (Phase 4a) and channel stripping (Phase 5). Each user-supplied regex is compiled at CLI parse time. The new `_apply_strip_subtitle_regex()` iterates surviving subtitle tracks in `result.sub_keep`, moves matches to `result.sub_drop`. No safety guard — matched tracks are always dropped.

**Tech Stack:** Python 3.12+, Click, loguru, re

**Spec:** `docs/superpowers/specs/2026-05-21-strip-subtitle-by-regex-design.md`

---

### Task 1: Add CLI option (cli.py)

**Files:**
- Modify: `src/trimarr/cli.py`

- [ ] **Step 1: Add the `--strip-subtitle-regex` Click option**

Insert after the `--strip-commentary` option block (around line 165):

```python
@click.option(
    "--strip-subtitle-regex",
    type=click.STRING,
    required=False,
    multiple=True,
    default=(),
    metavar="<regex>",
    help=(
        "One or more regex patterns. Any subtitle track whose name matches any pattern"
        " will be removed, regardless of language.  Patterns use Python re syntax."
        " Specify multiple times for multiple patterns."
        " Example: --strip-subtitle-regex '(?i)songs.*signs' --strip-subtitle-regex '(?i)signs.*songs'"
    ),
)
```

- [ ] **Step 2: Add `strip_subtitle_regex` param to the `cli()` function signature**

Add after `strip_commentary: bool`:

```python
strip_subtitle_regex: tuple[str, ...],
```

- [ ] **Step 3: Compile regexes and pass to `run()`**

After the existing variable assignments in `cli()`, add compilation (around where validations happen, before `_run` definition):

```python
import re  # at top of file

# After languages parsing, before the schedule validation
subtitle_regex_patterns: list[re.Pattern] = []
for pattern_str in strip_subtitle_regex:
    try:
        subtitle_regex_patterns.append(re.compile(pattern_str))
    except re.error as exc:
        raise click.UsageError(
            f"Invalid regex in --strip-subtitle-regex: '{pattern_str}' — {exc}"
        ) from exc
```

Then pass it to the `run()` call inside `_run()`:

```python
def _run() -> None:
    run(
        language=languages,
        ...
        strip_commentary=strip_commentary,
        strip_subtitle_regex_patterns=subtitle_regex_patterns,  # NEW
        ...
    )
```

- [ ] **Step 4: Add `import re` at top of cli.py**

If not already present.

- [ ] **Step 5: Run existing tests to confirm no regression**

```bash
cd /data/trimarr && uv run pytest --no-header -q
```
Expected: 336 passed.

- [ ] **Step 6: Commit**

```bash
git add src/trimarr/cli.py
git commit -m "feat(cli): add --strip-subtitle-regex option"
```

---

### Task 2: Add regex stripping logic (processor.py)

**Files:**
- Modify: `src/trimarr/processor.py`

- [ ] **Step 1: Add `subtitle_regex_drop_ids` to `_FilterResult`**

Add after `commentary_sub_drop_ids`:

```python
@dataclass
class _FilterResult:
    ...
    commentary_sub_drop_ids: set[int] = field(default_factory=set)
    # Track IDs removed specifically by the subtitle regex phase.
    subtitle_regex_drop_ids: set[int] = field(default_factory=set)
```

- [ ] **Step 2: Write `_apply_strip_subtitle_regex()` function**

Insert after `_apply_strip_commentary()` (around line 550) and before `_group_kept_audio_by_language()`:

```python
def _apply_strip_subtitle_regex(
    result: _FilterResult,
    tracks: list[MkvTrack],
    patterns: list[re.Pattern],
) -> None:
    """Phase 4b — drop subtitle tracks whose name matches any user-supplied regex.

    Operates on the subtitle tracks that survived language filtering and commentary
    stripping.  Each pattern is tested against each kept subtitle track's ``name``
    field.  Matched tracks are moved from ``sub_keep`` to ``sub_drop``.
    There is no safety guard — if all subtitles match, all are dropped.
    """
    if not patterns:
        return

    keep_set = set(result.sub_keep)
    for track in tracks:
        if track.type != _TRACK_SUBTITLES:
            continue
        if track.id not in keep_set:
            continue
        if track.name is None:
            continue
        for pattern in patterns:
            if pattern.search(track.name):
                result.sub_keep.remove(track.id)
                result.sub_drop.append(track.id)
                result.subtitle_regex_drop_ids.add(track.id)
                break  # matched one pattern, no need to check others
```

- [ ] **Step 3: Add `strip_subtitle_regex_patterns` param to `build_mkvmerge_command()`**

Update the function signature:

```python
def build_mkvmerge_command(
    ...
    strip_commentary: bool = False,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,  # NEW
) -> list[str] | None:
```

Insert the new phase call after `_apply_strip_commentary(...)` and before `_apply_strip_lower_channels(...)`:

```python
    _apply_strip_commentary(result, tracks, keep_audio, keep_subtitles, logger, input_path, strip_commentary)
    _apply_strip_subtitle_regex(result, tracks, strip_subtitle_regex_patterns or [])
    _apply_strip_lower_channels(result, tracks, keep_audio, strip_lower_channels, logger)
```

- [ ] **Step 4: Add regex drop logging to `_log_filter_changes()`**

Add after the commentary drop log lines:

```python
def _log_filter_changes(...):
    ...
    _log_commentary_drops(result.commentary_sub_drop_ids, _TRACK_SUBTITLES, tracks, logger)
    # NEW: log regex-triggered drops
    if result.subtitle_regex_drop_ids:
        logger.info(f"  Dropping {len(result.subtitle_regex_drop_ids)} subtitle track(s) by name regex.")
        descs = ", ".join(
            _fmt_track(t) for t in tracks if t.type == _TRACK_SUBTITLES and t.id in result.subtitle_regex_drop_ids
        )
        logger.debug(f"  Dropping subtitle track(s) by name regex: {descs}")
    ...
```

- [ ] **Step 5: Verify `needs_sub_change` still works**

The regex drops are appended to `result.sub_drop` inside `_apply_strip_subtitle_regex`, and `needs_sub_change = bool(result.sub_drop)` runs after all phases — so this is already correct. No code change needed.

- [ ] **Step 6: Run existing tests to confirm no regression**

```bash
cd /data/trimarr && uv run pytest --no-header -q
```
Expected: 336 passed.

- [ ] **Step 7: Commit**

```bash
git add src/trimarr/processor.py
git commit -m "feat(processor): add _apply_strip_subtitle_regex phase"
```

---

### Task 3: Plumb through runner.py

**Files:**
- Modify: `src/trimarr/runner.py`

- [ ] **Step 1: Add `strip_subtitle_regex_patterns` to `_ProcessingConfig`**

```python
@dataclass(frozen=True)
class _ProcessingConfig:
    ...
    strip_commentary: bool
    strip_subtitle_regex_patterns: list[re.Pattern] | None  # NEW
    ...
```

Add `import re` at the top of the file if not already present.

- [ ] **Step 2: Add `strip_subtitle_regex_patterns` to `_build_profile_hash()`**

Update the function signature and the profile dict:

```python
def _build_profile_hash(
    ...
    strip_commentary: bool,
    strip_subtitle_regex_patterns: list[re.Pattern] | None,  # NEW
) -> str:
    ...
    profile = {
        ...
        "strip_commentary": strip_commentary,
        "strip_subtitle_regex": [p.pattern for p in (strip_subtitle_regex_patterns or [])],  # NEW
        ...
    }
```

- [ ] **Step 3: Pass the parameter through `_process_one_file()`**

Update the `build_mkvmerge_command()` call:

```python
    cmd = build_mkvmerge_command(
        ...
        strip_commentary=cfg.strip_commentary,
        strip_subtitle_regex_patterns=cfg.strip_subtitle_regex_patterns,  # NEW
    )
```

- [ ] **Step 4: Update `run()` function signature**

```python
def run(
    ...
    strip_commentary: bool = False,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,  # NEW
    ...
```

Add to `_build_profile_hash()` call:

```python
    profile_hash = _build_profile_hash(
        ...
        strip_commentary=strip_commentary,
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,  # NEW
    )
```

Add to `_ProcessingConfig` instantiation:

```python
    cfg = _ProcessingConfig(
        ...
        strip_commentary=strip_commentary,
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,  # NEW
        ...
    )
```

- [ ] **Step 5: Verify the parameter flows through all call sites**

Search for `_process_one_file_guarded` and `_process_directory_groups` to confirm `cfg` is passed (it is — `cfg` is passed as a dataclass, so all fields are available automatically).

- [ ] **Step 6: Run existing tests**

```bash
cd /data/trimarr && uv run pytest --no-header -q
```
Expected: 336 passed.

- [ ] **Step 7: Commit**

```bash
git add src/trimarr/runner.py
git commit -m "feat(runner): plumb strip_subtitle_regex_patterns through config"
```

---

### Task 4: Write processor-level tests (test_processor.py)

**Files:**
- Modify: `tests/unit/test_processor.py`

- [ ] **Step 1: Update `_build_cmd` helper to support the new parameter**

```python
def _build_cmd(
    ...
    strip_commentary: bool = False,
    strip_subtitle_regex_patterns: list[re.Pattern] | None = None,  # NEW
) -> list[str] | None:
    return build_mkvmerge_command(
        ...
        strip_commentary=strip_commentary,
        strip_subtitle_regex_patterns=strip_subtitle_regex_patterns,  # NEW
    )
```

Add `import re` at the top of `test_processor.py`.

- [ ] **Step 2: Add a new test class**

Insert before `TestProbeFile` or after the `_build_cmd` helper definition:

```python
# ---------------------------------------------------------------------------
# Strip subtitle by regex
# ---------------------------------------------------------------------------

class TestStripSubtitleByRegex:
    """Tests for _apply_strip_subtitle_regex."""

    def test_drops_subtitle_matching_regex(self) -> None:
        """Subtitle track with a name matching the regex is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")])
        assert cmd is not None
        assert "--subtitle-tracks" in cmd
        # Only track ID 3 (Dialogues) should be kept
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [3]  # only the non-matching track

    def test_no_match_keeps_all_subtitles(self) -> None:
        """When no subtitle name matches, nothing is dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Dialogues"),
            MkvTrack(id=3, type="subtitles", language="fre", name="Sous-titres"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"songs")])
        # No subtitle changes needed, but audio changes may trigger a command
        assert cmd is not None
        # Verify no subtitle tracks were dropped (both still present)
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert set(kept) == {2, 3}

    def test_case_insensitive_match(self) -> None:
        """Regex with (?i) matches case-insensitively."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="songs & signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="SIGNS & SONGS"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")])
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert len(kept) == 0  # both matched
        assert "--no-subtitles" in cmd  # no subs left → --no-subtitles

    def test_multiple_patterns_all_match(self) -> None:
        """Multiple patterns — tracks matching any pattern are dropped."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Commentary Subs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            strip_subtitle_regex_patterns=[
                re.compile(r"(?i)songs.*signs"),
                re.compile(r"(?i)commentary"),
            ],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [4]  # only Dialogues survives

    def test_no_patterns_is_noop(self) -> None:
        """Empty patterns list = feature disabled."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=None)
        # No audio or subtitle changes needed for a single eng sub track
        assert cmd is None  # nothing to do

    def test_drops_all_subtitles_matched(self) -> None:
        """When all subtitles match the regex, all are dropped safely."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Signs & Songs"),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)(songs|signs)")])
        assert cmd is not None
        assert "--no-subtitles" in cmd

    def test_logs_regex_drops(self) -> None:
        """Info-level log is emitted when regex drops subtitle tracks."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Songs & Signs"),
        ]
        logger = MagicMock()
        _ = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r"(?i)songs")], logger=logger)
        # Should log info about dropping subtitle track(s) by name regex
        info_calls = [c for c in logger.info.call_args_list if "regex" in str(c)]
        assert len(info_calls) >= 1
        assert "subtitle track(s) by name regex" in str(info_calls[0])

    def test_regex_after_commentary(self) -> None:
        """Regex stripping runs after commentary strip — regex removes what survives commentary."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name="Director Commentary"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            strip_commentary=True,
            strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        assert kept == [4]  # commentary (2) and songs (3) both dropped

    def test_regex_after_language_filter(self) -> None:
        """Regex stripping operates on language-surviving tracks only."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="jpn", name="Songs & Signs"),
            MkvTrack(id=3, type="subtitles", language="eng", name="Songs & Signs"),
            MkvTrack(id=4, type="subtitles", language="eng", name="Dialogues"),
        ]
        cmd = _build_cmd(
            tracks,
            language=["eng"],
            strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")],
        )
        assert cmd is not None
        idx = cmd.index("--subtitle-tracks") + 1
        kept = [int(x) for x in cmd[idx].split(",")]
        # Track 2 (jpn, Songs) was already dropped by language filter
        # Track 3 (eng, Songs) is dropped by regex
        # Track 4 (eng, Dialogues) survives
        assert kept == [4]

    def test_null_name_track_not_matched(self) -> None:
        """Subtitle track with name=None is not matched by regex."""
        tracks = [
            MkvTrack(id=0, type="video", language=None),
            MkvTrack(id=1, type="audio", language="eng"),
            MkvTrack(id=2, type="subtitles", language="eng", name=None),
        ]
        cmd = _build_cmd(tracks, strip_subtitle_regex_patterns=[re.compile(r".")])
        assert cmd is None  # no changes — name=None doesn't match "any char"
```

- [ ] **Step 3: Run all tests**

```bash
cd /data/trimarr && uv run pytest --no-header -q
```
Expected: 336 + 10 new = 346 passed.

- [ ] **Step 4: Run coverage check**

```bash
cd /data/trimarr && uv run pytest --cov=src/trimarr --no-header -q
```
Expected: coverage ≥ 99%.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_processor.py
git commit -m "test: add processor-level tests for strip-subtitle-regex"
```

---

### Task 5: Add integration test (test_main.py)

**Files:**
- Modify: `tests/unit/test_main.py`

- [ ] **Step 1: Find the right location for a CLI flag acceptance test**

Look for the test class that tests `--strip-commentary` CLI pass-through:

```bash
cd /data/trimarr && grep -n 'strip.commentary\|strip_commentary' tests/unit/test_main.py
```

- [ ] **Step 2: Add an integration test verifying the CLI option parses and compiles regex**

```python
def test_strip_subtitle_regex_parsed(self, runner: CliRunner, monkeypatch: MonkeyPatch) -> None:
    """--strip-subtitle-regex is accepted and regex is compiled."""
    from click.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--language", "eng",
            "--media-path", "/tmp",
            "--strip-subtitle-regex", "(?i)songs.*signs",
            "--dry-run",
        ],
    )
    # Should not error on regex compilation
    assert result.exit_code != 2, f"Unexpected exit 2: {result.output}"
```

- [ ] **Step 3: Run tests**

```bash
cd /data/trimarr && uv run pytest --no-header -q
```
Expected: 347 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_main.py
git commit -m "test: add integration test for --strip-subtitle-regex CLI flag"
```

---

### Task 6: Quality gates

**Files:**
- All modified files

- [ ] **Step 1: Ruff lint + format**

```bash
cd /data/trimarr && uv run ruff check --fix . && uv run ruff format .
```

- [ ] **Step 2: Mypy**

```bash
cd /data/trimarr && uv run mypy src/trimarr/ tests/
```

- [ ] **Step 3: Full test suite**

```bash
cd /data/trimarr && uv run pytest --no-header -q --cov=src/trimarr
```

- [ ] **Step 4: Pre-commit**

```bash
cd /data/trimarr && pre-commit run --all-files
```

- [ ] **Step 5: Final commit (if lint fixes needed)**

```bash
git add -A
git commit -m "chore: quality gate fixes for strip-subtitle-regex"
```
