# Pre/Post Process Hooks — Design Spec

**Goal:** Allow users to define external commands that run before and after trimarr processes files in a directory, so that external operations (e.g. flipping immutable flags via `chattr`) can be performed around processing.

**Architecture:** Two new optional CLI options (`--pre-process`, `--post-process`) accept a command template with `{leaf}` and `{dir}` substitution variables. The runner groups files needing processing by their parent directory and fires pre/post hooks around each directory's batch. A third option (`--command-timeout-mins`) controls the per-command timeout.

---

## 1. CLI Options

### `--pre-process <command template>`

Command to execute **before** processing files in a directory. The command template supports `{leaf}` and `{dir}` substitution.

- **Type:** `click.STRING`
- **Required:** No
- **Default:** None (disabled)
- **Metavar:** `<command>`
- **Help text:**

  ```
  Command to run before processing files in a directory.
  The command is executed directly, without a shell: it is split POSIX-style
  and then run, so pipes, redirection, globbing and variable expansion are
  literal arguments rather than shell syntax.
  Use {leaf} for the directory basename and {dir} for the full
  directory path. Example: --pre-process 'no_ransom.sh --unlock yes {leaf}'
  ```

### `--post-process <command template>`

Command to execute **after** processing files in a directory. Same substitution variables as `--pre-process`.

- **Type:** `click.STRING`
- **Required:** No
- **Default:** None (disabled)
- **Metavar:** `<command>`
- **Help text:**

  ```
  Command to run after processing files in a directory.
  The command is executed directly, without a shell: it is split POSIX-style
  and then run, so pipes, redirection, globbing and variable expansion are
  literal arguments rather than shell syntax.
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
| --- | --- | --- |
| `{leaf}` | Directory basename (last path component) | `The Matrix (1999)` |
| `{dir}` | Full absolute path to the containing directory | `/mnt/user/Movies/The Matrix (1999)` |

**Substitution rules:**

- Single-pass regex substitution per tokenised argument, after `shlex.split`.
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
  Exception: with `--keep-native-audio` (and without `--keep-audio`) the per-file profile cannot be known without
  probing, so every directory is treated as having work and the hooks fire for all of them.
- Hooks are **skipped entirely** in `--dry-run`: nothing on disk may be modified, including by a hook.
- Pre fires immediately before the first file in the directory is processed.
- Post fires after **all** files in that directory have been processed — including files that failed, were skipped mid-loop, or raised errors.
- If a directory has no files needing processing, no hooks fire for it.

**Independence:**

- `--pre-process` can be specified without `--post-process` and vice versa.
- Neither implies nor requires `--schedule` — they also work in one-shot mode.

---

## 4. Execution & Error Handling

**Command execution:**

- Commands are executed via `subprocess.run(argv, shell=False)`. The template is split
  POSIX-style with `shlex.split` (Windows uses `posix=False`, so quotes are
  kept in tokens) and executed directly, so shell metacharacters (`|`,
  `>`, `&&`, `$VAR`, globs) are passed as literal arguments. This deviates from the
  original `shell=True` design and was chosen deliberately to remove the shell-injection
  surface: `{leaf}`/`{dir}` come from filesystem names and must never be interpreted.
- Placeholders are substituted **after** tokenisation, so a directory name containing
  spaces or quotes stays a single argv element.
- Only quote wrapping directly around a marker (`'{leaf}'`, `"{dir}"`) is stripped;
  unbalanced quotes make `shlex.split` raise and the hook is skipped with a warning.
- The command string is otherwise used verbatim — no additional quoting or escaping of the user's input.
- stdout and stderr are captured; stderr is included in the warning when the command exits non-zero.
  Captured output is not otherwise logged — only the resolved command line is logged at DEBUG.

**Timeout:**

- `--command-timeout-mins` controls the timeout per command invocation, converted to seconds (`value * 60`).
- A value of `0` disables the timeout entirely.
- If a command times out, `subprocess.TimeoutExpired` is caught, the process is killed, and a warning is logged.
- **Default:** 5 minutes if the option is not specified.

**Error handling (non-zero exit):**

- If the command exits with a non-zero return code, a warning is logged:

  ```
  Hook command exited with code 1: <resolved command>: <stderr content>
  ```

  The resolved command carries the substituted `{leaf}`/`{dir}` values, so the
  warning identifies the directory without needing the DEBUG line.

- Processing **continues** regardless of hook failure.
- Post-process fires regardless of whether pre-process succeeded or failed.

**Error handling (exceptions):**

- `OSError`, a non-zero exit code, or any other execution error is caught, logged as a warning, and processing continues.
- No hook failure ever changes trimarr's exit code.

**Safety:**

- `{leaf}` and `{dir}` are substituted as whole argv elements after tokenisation. Users are
  responsible for ensuring their command templates are safe. This is consistent with the
  power-user nature of the feature.
- No attempt is made to validate or sanitize the command template — incorrect quoting makes
  `shlex.split` raise, which is caught and logged.

---

## 5. Architecture

### New module: `src/trimarr/hooks.py`

A small module with one entry point (`_run_hook`), split internally into
`_resolve_hook_template` (quote stripping + `shlex` tokenisation) and
`_run_hook_command` (subprocess execution + error logging):

```python
def _run_hook(
    cmd_template: str,
    leaf: str,
    dir_path: str,
    logger: Logger,
    timeout_seconds: int | None = 300,
) -> None:
    """Execute a command hook with {leaf} and {dir} substitution.

    Args:
        cmd_template: Command template containing {leaf} and/or {dir}.
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

---

## 6. Testing

### Unit tests for `_run_hook`

- Substitutes `{leaf}` and `{dir}` correctly in the command string
- Runs the substituted command with `shell=False` (no shell involved)
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
| --- | --- | --- |
| `src/trimarr/cli.py` | Modify | Add `--pre-process`, `--post-process`, `--command-timeout-mins` options |
| `src/trimarr/hooks.py` | **Create** | `_run_hook()` helper for direct command execution and variable substitution |
| `src/trimarr/runner.py` | Modify | Group files by directory, call `_run_hook` before/after each directory batch |
| `tests/unit/test_hooks.py` | **Create** | Substitution, quoting, sanitisation and error-handling tests for `_run_hook` |
| `tests/unit/test_runner.py` | Modify | Hook firing order, dry-run suppression, pre/post pairing |
| `tests/unit/test_cli.py` | Modify | Tests for the new CLI options and their validation |
| `README.md` | Modify | Option table, no-shell semantics, dry-run and keep-native-audio caveats |
