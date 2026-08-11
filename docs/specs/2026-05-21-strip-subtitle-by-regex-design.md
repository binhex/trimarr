# Strip Subtitle by Regex — Design Spec

> **Status:** Approved
> **Date:** 2026-05-21
> **Issue:** https://github.com/binhex/trimarr/issues/47

## Problem

Users need to delete subtitle tracks whose **name** field matches specific text patterns — for example, tracks named "Songs & Signs", "Signs & Songs", or "Songs / Signs" that contain on-screen sign/song translations but no dialogue subtitles. These tracks survive language-based filtering (they are often tagged as English), so users need a way to force-delete them by name.

## Design Decisions (Approved)

| Decision | Choice |
|----------|--------|
| Approach | Generic regex flag (user supplies any pattern) |
| Track scope | Subtitles only |
| Safety guard | None — force-delete all matched tracks |
| Order in pipeline | After language filtering (Phase 4b — right after commentary stripping) |
| Multiple patterns | Yes — repeatable flag, one regex per invocation |
| Match field | Track `name` only |

## Flag

```
--strip-subtitle-by-regex <pattern>
```

- Repeatable (`click.STRING`, `multiple=True`)
- User supplies a Python-compatible `re` pattern (compiled at parse time — fail fast on bad regex)
- If specified zero times, the feature is disabled
- Runs after `--strip-commentary` (independently — both can be used together)

**Recommended regex for the issue's use case:**

```
--strip-subtitle-regex '(?i)(?:songs?\s*[/&]\s*signs?|signs?\s*[/&]\s*songs?)'
```

## Architecture

The new phase is **Phase 4b** in `build_mkvmerge_command()`, inserted immediately after Phase 4 (commentary stripping):

```
Phase 1: Language filter           (_apply_language_filter)
Phase 2: Audio fallbacks           (_apply_audio_fallbacks)
Phase 3: Subtitle fallback         (_apply_subtitle_fallback)
Phase 4a: Commentary strip         (_apply_strip_commentary)
Phase 4b: Subtitle regex strip     (_apply_strip_subtitle_regex)   ← NEW
Phase 5: Channel strip             (_apply_strip_lower_channels)
```

`_apply_strip_subtitle_regex()` operates on `result.sub_keep` — the pool of subtitle tracks that survived all previous phases. For each compiled pattern, it checks every kept subtitle track's `name` field. Any match moves the track from `sub_keep` to `sub_drop`. There is no safety guard — if all subtitles match, all are dropped.

## Files Changed

| File | Change |
|------|--------|
| `src/trimarr/cli.py` | Add `--strip-subtitle-by-regex` option, parse/compile regex, pass to `run()` |
| `src/trimarr/processor.py` | Add `strip_subtitle_regex_patterns` param to `build_mkvmerge_command()`, add `_apply_strip_subtitle_regex()` function, add `_FilterResult.subtitle_regex_drop_ids` for logging, log matched drops |
| `src/trimarr/runner.py` | Add `strip_subtitle_regex_patterns` to `_ProcessingConfig` and `_build_profile_hash()`, plumb through `run()` |
| `tests/unit/test_processor.py` | Add test class for regex stripping: basic match, no match, case-insensitive, multiple patterns, interaction with commentary, interaction with language filter |
| `tests/unit/test_main.py` | Integration test for CLI flag |
