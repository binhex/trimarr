# Scheduler Design Spec

## Problem

Trimarr is currently a one-shot tool: it runs, processes files, and exits. Users who want
recurring scans must set up external scheduling (cron, systemd timers, etc.). This is
friction — especially for users running trimarr in a container or as a simple background
process. Adding a built-in scheduler removes that friction and makes trimarr self-contained.

## Proposed Approach

Add two new CLI options to run trimarr as a long-lived, drift-corrected scheduling loop.
When `--schedule` is omitted the existing one-shot behaviour is fully preserved.

## CLI Interface

Two new options:

```
--schedule <N><unit>
```

Schedules trimarr to run repeatedly at the given interval. `N` is a positive integer;
supported units are:

| Unit | Meaning   |
|------|-----------|
| `m`  | minutes   |
| `h`  | hours     |
| `d`  | days      |
| `w`  | weeks     |

Examples: `--schedule 30m`, `--schedule 6h`, `--schedule 1d`, `--schedule 2w`.

When `--schedule` is omitted trimarr runs once and exits (current behaviour, backward compatible).

```
--run-on-start
```

Flag. Only valid with `--schedule`. When set, trimarr fires one run immediately on startup
before entering the timed loop. When omitted, trimarr waits for the first interval to elapse
before running.

Using `--run-on-start` without `--schedule` is a usage error.

### Usage Examples

```bash
# Run every 6 hours, fire immediately on startup
trimarr --language eng --media-path /mnt/media --schedule 6h --run-on-start

# Run daily, first run after 24 hours
trimarr --language eng --media-path /mnt/media --schedule 1d

# Run every 30 minutes (useful for active download folders)
trimarr --language eng --media-path /mnt/media --schedule 30m --run-on-start
```

## Architecture

A new `scheduler.py` module is introduced. It has no knowledge of trimarr internals —
it accepts a zero-argument callable and a sleep interval, nothing else.

```
cli.py
  │
  ├─ (no --schedule) ──→ runner.run(...)           ← unchanged path
  │
  └─ (--schedule)   ──→ scheduler.run_scheduled(
                              run_fn=lambda: runner.run(...),
                              interval_seconds=<parsed>,
                              run_on_start=<flag>,
                              logger=logger,
                        )
```

### `scheduler.py` Public API

```python
def parse_interval(interval: str) -> int:
    """Parse '30m', '6h', '2d', '1w' into seconds.

    Raises ValueError for invalid input (bad unit, non-positive N, etc.).
    """

def run_scheduled(
    run_fn: Callable[[], None],
    interval_seconds: int,
    run_on_start: bool,
    logger: Logger,
) -> None:
    """Run run_fn on a drift-corrected schedule until KeyboardInterrupt."""
```

`cli.py` converts `ValueError` from `parse_interval` into a `click.BadParameter`.

## Loop Behaviour

The scheduler uses a **run-then-sleep** pattern to achieve true drift correction.

```
start:
  if not run_on_start:
      log "First run at HH:MM:SS (in Xh Ym)"
      sleep interval_seconds in 1-second ticks

  loop:
      t0 = monotonic()
      call run_fn()
      elapsed = monotonic() - t0

      sleep_secs = max(0, interval_seconds - elapsed)

      if elapsed > interval_seconds:
          log WARNING "Run took Xs > interval Ys, firing next run immediately"
      else:
          log "Next run at HH:MM:SS (in Xh Ym)"

      sleep sleep_secs in 1-second ticks
```

By sleeping for `interval - elapsed` rather than a full `interval`, the scheduler keeps
the cadence fixed relative to when each run *started*, not when it *ended*. A 10-minute
run on a 1-hour schedule results in a 50-minute sleep, so the next run fires exactly 1
hour after the previous one began. Without this correction, drift accumulates every cycle.

If a run overshoots the interval the next run fires immediately with a warning rather than
silently skipping or accumulating lag.

Sleeping in 1-second ticks (rather than one long `time.sleep`) means the process responds
to Ctrl+C within ~1 second at all times.

## Error Handling

| Scenario | Behaviour |
|---|---|
| `--run-on-start` without `--schedule` | `click.UsageError` — clear message shown |
| Invalid interval (e.g. `0h`, `abc`, `-1d`) | `click.BadParameter` — shows format hint |
| `run_fn()` raises an unhandled exception | Log error, continue the loop |
| Ctrl+C during sleep | Log `"Scheduler stopped."`, exit 0 |
| Ctrl+C during a run | `runner.py` handles it — logs partial summary, exits 130 |
| Run duration exceeds interval | Log warning, next sleep = 0 |

Unhandled exceptions from `run_fn` do **not** stop the scheduler. A transient fault
(mkvmerge crash, disk full, network error) should not require a manual restart of a
long-running daemon process.

## Files Changed

| File | Change |
|------|--------|
| `src/trimarr/scheduler.py` | New module: `parse_interval`, `run_scheduled` |
| `src/trimarr/cli.py` | Add `--schedule`, `--run-on-start` options; routing logic |
| `tests/unit/test_scheduler.py` | New unit tests for scheduler module |
| `README.md` | Document new options in Options table and add scheduler section |

## Out of Scope

- Calendar-month scheduling (use `30d` or `31d` instead)
- Cron expression syntax
- Multiple concurrent schedules
- Persisting the schedule across restarts (state is in CLI args only)
