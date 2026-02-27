# Trimarr

This software is under *HEAVY* development right now, expect lack of documentation, major bugs and missing functionality.

## Description

Removes (trims) unrequired audio and subtitles from matroska container format video files.

## Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Astral uv](https://github.com/astral-sh/uv#installation)

## Quick start

### Installation

```bash
git clone https://github.com/binhex/trimarr
cd trimarr
uv venv --quiet
uv sync
```

### Running

```bash
trimarr
```

## Options

WIP

## Development

```bash
git clone https://github.com/binhex/trimarr
cd trimarr
uv venv --quiet
uv sync --extra dev
```

If you wish to perform linting on all files before committing (PR will nt be
accepted if it does not pass all linting) then run `pre-commit run --all-files`.

## FAQ

WIP
