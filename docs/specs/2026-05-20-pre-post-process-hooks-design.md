# Pre/Post Process Hooks — Design Spec

**Goal:** Allow users to define shell commands that run before and after trimarr processes files in a directory, so that external operations (e.g. flipping immutable flags via `chattr`) can be performed around processing.

**Architecture:** Two new optional CLI options (`--pre-process`, `--post-process`) accept a shell command template with `{leaf}` and `{dir}` substitution variables. The runner groups files needing processing by their parent directory and fires pre/post hooks around each directory's batch. A third option (`--command-timeout-mins`) controls the per-command timeout.

---

## 1. CLI Options

### `--pre-process <command template>`

Shell command to execute **before** processing files in a directory. The command template supports `{leaf}` and `{dir}` substitution.

- **Type:** `click.STRING`
- **Required:** No
- **Default:** None (disabled)
- **Metavar:** `<command>`
- **Help text:**
  ```
  Shell command to run before processing files in a directory.
  Use {leaf} for the directory basename and {dir} for the full
  directory path. Example: --pre-process 'no_ransom.sh --unlock yes {leaf}'
  ```

### `--post-process <command template>`

Shell command to execute **after** processing files in a directory. Same substitution variables as `--pre-process`.

- **Type:** `click.STRING`
- **Required:** No
- **Default:** None (disabled)
- **Metavar:** `<command>`
- **Help text:**
  ```
  Shell command to run after processing files in a directory.
  Use {leaf} for the directory basename and {dir} for the full
  directory path. Example: --post-process 'no_ransom.sh --unlock no {leaf}'
  ```

### `--command-timeout-mins <minutes>`

Maximum time in minutes that each `--pre-process` or `--post-process` command is allowed to run before being killed.

- **Type:** `click.IntRange(min=0)`
- **Required:** No
- **Default:** `5`
- **Metavar:** `<minutes>`
- **Help text:**
  ```
  Timeout in minutes for each pre/post process command.
  Set to 0 to disable timeout entirely. Default: 5 minutes.
  ```

---

## 2. Variable Substitution

Both `--pre-process` and `--post-process` support the following template variables:

| Variable | Resolves to | Example |
|---|---|---|
| `{leaf}` | Directory basename (last path component) | `The Matrix (1999)` |
| `{dir}` | Full absolute path to the containing directory | `/mnt/user/Movies/The Matrix (1999)` |

**Substitution rules:**

- Simple string replacement using `str.replace()` for each variable.
- Variables can appear zero, once, or multiple times in the template.
- No nesting or recursive expansion.
- If a file sits directly in a media path root (no subdirectory), `{leaf}` resolves to the media path directory's basename and `{dir}` to that directory's absolute path. Example: file `/mnt/user/Movies/some_movie.mkv` → `{leaf}=Movies`, `{dir}=/mnt/user/Movies`.

---

## 3. Execution Model

**Per-directory batching:**

Trimarr groups files that need processing by their parent directory. Hooks fire once per unique directory that has work:

```
discover MKV files across all media paths
for each file, check fingerprint → collect files needing work
group needing-work files by parent directory
for each directory with work:
    run --pre-process command (with substituted variables)
    for each file in that directory:
        process file (probe → mkvmerge → replace)
    run --post-process command (with substituted variables)
print summary
```

**Fire conditions:**

- Hooks fire **only** for directories where at least one file actually needs processing (not skipped by fingerprint).
- Pre fires immediately before the first file in the directory is processed.
- Post fires after **all** files in that directory have been processed — including files that failed, were skipped mid-loop, or raised errors.
- If a directory has no files needing processing, no hooks fire for it.

**Independence:**

- `--pre-process` can be specified without `--post-process` and vice versa.
- Neither implies nor requires `--schedule` — they also work in one-shot mode.

---

## 4. Execution & Error Handling

**Command execution:**

- Commands are executed via `subprocess.run(cmd, shell=True)`.
- The command string is the template with variables substituted — no additional quoting or escaping of the user's input.
- stdout and stderr from the command are captured but not logged at INFO level (use DEBUG for output).

**Timeout:**

- `--command-timeout-mins` controls the timeout per command invocation, converted to seconds (`value * 60`).
- A value of `0` disables the timeout entirely.
- If a command times out, `subprocess.TimeoutExpired` is caught, the process is killed, and a warning is logged.
- **Default:** 5 minutes if the option is not specified.

**Error handling (non-zero exit):**

- If the command exits with a non-zero return code, a warning is logged:
  ```
  Pre-process command for 'The Matrix (1999)' exited with code 1:
  <stderr content>
  ```
- Processing **continues** regardless of hook failure.
- Post-process fires regardless of whether pre-process succeeded or failed.

**Error handling (exceptions):**

- `OSError`, `subprocess.CalledProcessError`, or any other execution error is caught, logged as a warning, and processing continues.
- No hook failure ever changes trimarr's exit code.

**Safety:**

- `{leaf}` and `{dir}` are substituted directly into a shell command string. Users are responsible for ensuring their command templates are safe. This is consistent with the power-user nature of the feature.
- No attempt is made to validate or sanitize the command template — incorrect shell syntax will produce a shell error, which is caught and logged.

---

## 5. Architecture

### New module: `src/trimarr/hooks.py`

A small module with a single public helper:

```python
def _run_hook(
    cmd_template: str,
    leaf: str,
    dir_path: str,
    logger: Logger,
    timeout_seconds: int | None = 300,
) -> None:
    """Execute a shell command hook with {leaf} and {dir} substitution.

    Args:
        cmd_template: Shell command template containing {leaf} and/or {dir}.
        leaf: Directory basename to substitute for {leaf}.
        dir_path: Absolute directory path to substitute for {dir}.
        logger: Loguru logger instance for warning messages.
        timeout_seconds: Max seconds to wait before killing the command.
                         None disables the timeout.
    """
```

### Integration in `run()`

The `run()` function in `runner.py` currently:

1. Discovers MKV files
2. Filters already-processed files
3. Iterates over files needing processing, calling `process_file()` for each
4. Prints summary

The change groups step 3 by directory and wraps each group with hook calls. The change is contained entirely within `runner.py`.

### New file: `src/trimarr/hooks.py`

`runner.py` is 491 lines, so hook logic lives in its own module:

---

## 6. Testing

### Unit tests for `_run_hook`

- Substitutes `{leaf}` and `{dir}` correctly in the command string
- Runs the substituted command via shell
- Non-zero exit logs warning and does not raise
- Timeout logs warning and does not raise
- OSError during execution logs warning and does not raise
- Timeout_seconds=None disables timeout
- Empty command template does nothing (no shell invocation)

### Integration tests for CLI

- `--pre-process` with valid command is executed before processing
- `--post-process` with valid command is executed after processing
- `--command-timeout-mins` is forwarded correctly
- `--pre-process` without `--post-process` works (only pre fires)
- `--post-process` without `--pre-process` works (only post fires)
- Hook errors do not affect process exit code
- Commands are only fired for directories with actual work

### Test fixtures

- Use `subprocess.run` mocking to avoid actual shell execution in unit tests
- Use a real echo/true command for integration-level verification

---

## 7. Files Changed

| File | Action | What |
|---|---|---|
| `src/trimarr/cli.py` | Modify | Add `--pre-process`, `--post-process`, `--command-timeout-mins` options |
| `src/trimarr/hooks.py` | **Create** | `_run_hook()` helper for shell command execution and variable substitution |
| `src/trimarr/runner.py` | Modify | Group files by directory, call `_run_hook` before/after each directory batch |
| `tests/unit/test_runner.py` | Modify | Tests for hook execution, substitution, error handling |
| `tests/unit/test_cli.py` | Modify | Tests for new CLI options |
