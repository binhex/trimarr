# Trimarr

This software is under *HEAVY* development right now, expect lack of documentation, major bugs and missing functionality.

## Description

Removes (trims) unwanted audio and subtitles from matroska container format video files.

## Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Astral uv](https://github.com/astral-sh/uv#installation) (optional)

## Quick start

### Installation using uv (recommended)

```bash
git clone https://github.com/binhex/trimarr
cd trimarr
uv venv --quiet
uv sync
```

### Installation using pip

```bash
git clone https://github.com/binhex/trimarr
cd trimarr
python -m venv .venv
source .venv/bin/activate
pip install .
```

### Usage

```bash
trimarr --help
```

## Options

| Option | Description | Default | Example | Type |
| ------ | ----------- | ------- | ------- | ---- |
| `--language` | Language code for the audio/subtitle tracks to keep. See [ISO 639-2 codes](http://en.wikipedia.org/wiki/List_of_ISO_639-2_codes). | — | `eng` | `string` |
| `--media-path` | Path to the directory containing media files. | — | `/mnt/media/movies` | `path` |
| `--mkvmerge-path` | Path to the mkvmerge executable. | `<project root>/bin/mkvmerge` | `/usr/bin/mkvmerge` | `path` |
| `--database-path` | Path to the SQLite database file used for tracking processed files. | `<project root>/db/trimarr.db` | `/var/lib/trimarr/trimarr.db` | `path` |
| `--log-path` | Path to the log file for tracking application events. | `<project root>/logs/trimarr.log` | `/var/log/trimarr.log` | `path` |
| `--log-level` | Logging level for console output. Choices: `DEBUG`, `INFO`, `SUCCESS`, `WARNING`, `ERROR`. | `INFO` | `DEBUG` | `choice` |
| `--edit-metadata-title` | If specified, the title metadata of each file will be updated to match its filename. | `false` | — | `flag` |
| `--delete-metadata-title` | If specified, the title metadata of each file will be deleted. | `false` | — | `flag` |
| `--keep-subtitles` | If specified, all subtitle tracks will be kept regardless of language. | `false` | — | `flag` |
| `--keep-audio` | If specified, all audio tracks will be kept regardless of language. | `false` | — | `flag` |
| `--dry-run` | If specified, performs a dry run without making any changes. | `false` | — | `flag` |

## Development

```bash
git clone https://github.com/binhex/trimarr
cd trimarr
uv venv --quiet
uv sync --extra dev
```

If you wish to perform linting on all files before committing (PR will not be
accepted if it does not pass all linting) then run `pre-commit run --all-files`.

## FAQ

WIP
