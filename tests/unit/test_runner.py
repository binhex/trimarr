"""Unit tests for core.runner."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path

from trimarr.database import Database
from trimarr.runner import (
    _build_profile_hash,
    _dir_has_work,
    _process_one_file,
    _ProcessingConfig,
    _resolve_effective_language,
    _RunCounts,
)


class TestBuildProfileHash:
    """Tests for the _build_profile_hash() function."""

    @staticmethod
    def _compute_old_style_hash(
        *,
        language: list[str],
        keep_audio: bool = False,
        keep_subtitles: bool = False,
        edit_metadata_title: bool = False,
        delete_metadata_title: bool = False,
        strip_lower_channels: bool = False,
        strip_commentary: bool = False,
    ) -> str:
        """Compute the profile hash as it was BEFORE --strip-subtitle-regex was added.

        The old hash never included the "subtitle_regex_patterns" key.
        This lets us assert backward compatibility in tests.
        """
        from trimarr.processor import normalize_language_code

        canonical_language = sorted(normalize_language_code(c) for c in language)
        profile = {
            "delete_metadata_title": delete_metadata_title,
            "edit_metadata_title": edit_metadata_title,
            "keep_audio": keep_audio,
            "keep_subtitles": keep_subtitles,
            "language": canonical_language,
            "strip_commentary": strip_commentary,
            "strip_lower_channels": strip_lower_channels,
        }
        return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()

    def test_backward_compatible_without_subtitle_regex(self) -> None:
        """Profile hash must NOT change when --strip-subtitle-regex is not used.

        Adding the strip_subtitle_regex_patterns parameter must not alter the
        profile hash for existing users who do not use the feature.  Otherwise
        every previously-processed database entry would appear stale and trigger
        a full library re-scan on upgrade (the bug reported in issue commentary).
        """
        # The hash from the new code (with the extra parameter) must match
        # the hash that the old code (pre-feature) would have produced.
        old_hash = self._compute_old_style_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
        )
        new_hash = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
        )

        assert old_hash == new_hash, (
            f"Profile hash changed when no --strip-subtitle-regex is used:\n"
            f"  Old (7-key profile): {old_hash}\n"
            f"  New (8-key profile): {new_hash}\n"
            "This causes a full library re-scan for every existing user on upgrade."
        )

    def test_non_empty_patterns_change_hash(self) -> None:
        """Providing actual regex patterns must produce a different profile hash."""
        hash_no_pattern = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
        )
        hash_with_pattern = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            strip_subtitle_regex_patterns=[re.compile(r"(?i)songs.*signs")],
        )

        assert hash_no_pattern != hash_with_pattern, "Non-empty patterns must change the hash"


class TestResolveEffectiveLanguage:
    """Tests for _resolve_effective_language()."""

    def test_no_flag(self) -> None:
        """keep_native_audio off → effective == cfg.language."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=False,
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng"]

    def test_keep_audio_overrides(self) -> None:
        """--keep-audio causes native lookup to be skipped."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=True,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng"]

    def test_native_added_to_language(self, mocker) -> None:
        """Native language is merged into the effective list."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        mock_resolve = mocker.patch(
            "trimarr.native_language.resolve_native_language",
            return_value=["chi"],
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng", "chi"]
        mock_resolve.assert_called_once()

    def test_native_dedup(self, mocker) -> None:
        """Native language already in --language list is not duplicated."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng", "chi"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        mocker.patch(
            "trimarr.native_language.resolve_native_language",
            return_value=["chi"],
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng", "chi"]
        assert result.count("chi") == 1

    def test_cfg_not_mutated(self, mocker) -> None:
        """_resolve_effective_language does not mutate cfg.language."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
            keep_native_audio=True,
        )
        original = list(cfg.language)
        mocker.patch(
            "trimarr.native_language.resolve_native_language",
            return_value=["chi"],
        )
        _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert cfg.language == original

    def test_native_lookup_returns_none(self, mocker) -> None:
        """When resolve_native_language returns None, fall back to cfg.language."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=True,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
        )
        mocker.patch(
            "trimarr.native_language.resolve_native_language",
            return_value=None,
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng"]

    def test_native_lookup_returns_empty(self, mocker) -> None:
        """When resolve_native_language returns an empty list, fall back to cfg.language."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=True,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
        )
        mocker.patch(
            "trimarr.native_language.resolve_native_language",
            return_value=[],
        )
        result = _resolve_effective_language(
            file_path=Path("/fake/test.mkv"),
            cfg=cfg,
            db=MagicMock(),
            logger=MagicMock(),
        )
        assert result == ["eng"]


class TestDirHasWork:
    """Tests for _dir_has_work()."""

    def test_keep_native_audio_assumes_work(self) -> None:
        """With keep_native_audio active, _dir_has_work always returns True."""
        from unittest.mock import MagicMock

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=True,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,
            no_backup=True,
        )
        result = _dir_has_work(
            files_in_dir=[],
            db=MagicMock(),
            profile_hash="",
            cfg=cfg,
            logger=MagicMock(),
        )
        assert result[0] is True


class TestProcessOneFileDryRun:
    """Tests for _process_one_file() in dry-run mode."""

    def test_dry_run_does_not_mark_processed(self, mocker, tmp_path) -> None:
        """Dry-run must NOT mark files as processed in the database.

        The bug: dry-run called ``db.mark_processed()``, persisting the file
        fingerprint and profile hash into the SQLite DB.  On the next real
        (non-dry-run) run, ``db.is_processed()`` returned ``True`` and every
        file was skipped — zero files processed.
        """
        from unittest.mock import MagicMock

        file = tmp_path / "test.mkv"
        file.write_bytes(b"\x00" * 70000)  # larger than PARTIAL_HASH_BYTES (65536)

        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.open()

        cfg = _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=True,  # KEY: dry-run is on
            no_backup=True,
        )

        profile_hash = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
        )

        # Mock probe + build so we enter the dry-run path (cmd is not None)
        mocker.patch("trimarr.runner.probe_file", return_value=[])
        mocker.patch(
            "trimarr.runner.build_mkvmerge_command",
            return_value=["mkvmerge", "-o", "out", "in"],
        )

        counts = _RunCounts()
        failures: list[tuple[Path, str]] = []

        _process_one_file(
            file_path=file,
            root=tmp_path,
            idx=1,
            total=1,
            db=db,
            cfg=cfg,
            counts=counts,
            failures=failures,
            logger=MagicMock(),
        )

        # THE ASSERTION: after dry-run the file must NOT be in the DB
        assert not db.is_processed(file, profile_hash=profile_hash), (
            "Dry-run must not mark files as processed in the database"
        )
        db.close()


class TestDirHasWorkConfirmed:
    """_dir_has_work() reports which files it already verified as processed."""

    @staticmethod
    def _cfg() -> _ProcessingConfig:
        return _ProcessingConfig(
            mkvmerge_path="/fake/mkvmerge",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=False,
            no_backup=True,
        )

    def test_all_processed_returns_confirmed_paths(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        files = [(tmp_path / "a.mkv", tmp_path), (tmp_path / "b.mkv", tmp_path)]
        db = MagicMock()
        db.is_processed.return_value = True

        has_work, confirmed = _dir_has_work(files, db, "hash", self._cfg(), MagicMock())

        assert has_work is False
        assert confirmed == frozenset({tmp_path / "a.mkv", tmp_path / "b.mkv"})

    def test_unprocessed_file_returns_work_and_partial_confirmed(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        files = [(tmp_path / "a.mkv", tmp_path), (tmp_path / "b.mkv", tmp_path)]
        db = MagicMock()
        db.is_processed.side_effect = [True, False]

        has_work, confirmed = _dir_has_work(files, db, "hash", self._cfg(), MagicMock())

        assert has_work is True
        assert confirmed == frozenset({tmp_path / "a.mkv"}), "only the verified file may be reused"

    def test_error_returns_no_confirmed_paths(self, tmp_path: Path) -> None:
        import sqlite3
        from unittest.mock import MagicMock

        files = [(tmp_path / "a.mkv", tmp_path)]
        db = MagicMock()
        db.is_processed.side_effect = sqlite3.Error("boom")

        has_work, confirmed = _dir_has_work(files, db, "hash", self._cfg(), MagicMock())

        assert has_work is True
        assert confirmed == frozenset()


class TestDatabaseOpenFailure:
    """A locked or unwritable tracking database must not abort with a traceback."""

    def test_run_reports_failure_instead_of_raising(self, tmp_path: Path) -> None:
        import sqlite3
        from unittest.mock import MagicMock, patch

        from trimarr.runner import _process_directory_groups

        cfg = TestDirHasWorkConfirmed._cfg()
        groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict({tmp_path: [(tmp_path / "a.mkv", tmp_path)]})

        with (
            patch("trimarr.runner.Database", side_effect=sqlite3.Error("database is locked")),
            patch("trimarr.runner._handle_corrupt_output"),
        ):
            counts, interrupted, failures, database_unavailable = _process_directory_groups(
                groups,
                database_path=str(tmp_path / "trimarr.db"),
                profile_hash="hash",
                cfg=cfg,
                total=1,
                pre_process=None,
                post_process=None,
                command_timeout_seconds=5,
                logger=MagicMock(),
            )

        assert interrupted is False
        assert database_unavailable is True
        assert counts.failed == 1
        assert failures and "database unavailable" in failures[0][1]


class TestConfirmedProcessedShortcut:
    """A confirmed path must skip the per-file DB check; an unconfirmed one must not."""

    @staticmethod
    def _run_one(mkv: Path, db, confirmed: frozenset[Path], tmp_path: Path):
        from unittest.mock import MagicMock

        from trimarr.runner import _process_one_file

        counts = _RunCounts()
        failures: list[tuple[Path, str]] = []
        _process_one_file(
            file_path=mkv,
            root=tmp_path,
            idx=1,
            total=1,
            db=db,
            cfg=TestDirHasWorkConfirmed._cfg(),
            counts=counts,
            failures=failures,
            logger=MagicMock(),
            confirmed_processed=confirmed,
        )
        return counts, db

    def test_confirmed_path_skips_db_check(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mkv = tmp_path / "a.mkv"
        mkv.touch()
        db = MagicMock()
        confirmed = frozenset({mkv})

        counts, db = self._run_one(mkv, db, confirmed, tmp_path)

        assert counts.skipped == 1
        db.is_processed.assert_not_called()

    def test_unconfirmed_path_still_checks_db(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mkv = tmp_path / "a.mkv"
        mkv.touch()
        db = MagicMock()
        db.is_processed.return_value = True

        counts, db = self._run_one(mkv, db, frozenset(), tmp_path)

        assert counts.skipped == 1
        db.is_processed.assert_called_once()


class TestDatabaseUnavailableExit:
    """A run-level database failure must exit non-zero, not report success."""

    def test_run_exits_one_when_database_cannot_open(self, tmp_path: Path) -> None:
        import sqlite3
        from unittest.mock import MagicMock, patch

        import pytest

        from trimarr.runner import run

        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")

        with (
            patch("trimarr.runner.Database", side_effect=sqlite3.Error("database is locked")),
            patch("trimarr.runner._print_summary"),
            pytest.raises(SystemExit) as exc_info,
        ):
            run(
                language=["eng"],
                edit_metadata_title=False,
                delete_metadata_title=False,
                keep_subtitles=False,
                keep_audio=False,
                media_path=[str(tmp_path)],
                mkvmerge_path="/usr/bin/mkvmerge",
                database_path=str(tmp_path / "trimarr.db"),
                no_backup=True,
                dry_run=False,
                logger=MagicMock(),
            )

        assert exc_info.value.code == 1


class TestPreHookInvalidatesConfirmation:
    """A pre-process hook can mutate files, so confirmed paths must be re-checked."""

    @staticmethod
    def _run_groups(tmp_path: Path, pre_process: str | None, dry_run: bool = False):
        from unittest.mock import MagicMock, patch

        from trimarr.runner import _process_directory_groups

        first = tmp_path / "a.mkv"
        second = tmp_path / "b.mkv"
        first.touch()
        second.touch()
        groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict(
            {tmp_path: [(first, tmp_path), (second, tmp_path)]}
        )

        db = MagicMock()
        # `a.mkv` is already processed, `b.mkv` needs work — so the directory has
        # work, the pre-hook fires, and `a.mkv` must then be re-checked.
        db.is_processed.side_effect = [True, False, False]

        with (
            patch("trimarr.runner.Database") as mock_db_cls,
            patch("trimarr.runner._run_dir_hook"),
            patch("trimarr.runner._process_one_file_guarded") as mock_process,
        ):
            mock_db_cls.return_value.__enter__.return_value = db
            mock_db_cls.return_value.__exit__.return_value = False
            _process_directory_groups(
                groups,
                database_path=str(tmp_path / "trimarr.db"),
                profile_hash="hash",
                cfg=_ProcessingConfig(
                    mkvmerge_path="/fake",
                    language=["eng"],
                    keep_audio=False,
                    keep_native_audio=False,
                    keep_subtitles=False,
                    edit_metadata_title=False,
                    delete_metadata_title=False,
                    strip_lower_channels=False,
                    strip_commentary=False,
                    skip_size_check=False,
                    dry_run=dry_run,
                    no_backup=True,
                ),
                total=1,
                pre_process=pre_process,
                post_process=None,
                command_timeout_seconds=5,
                logger=MagicMock(),
            )
        return mock_process.call_args.kwargs["confirmed_processed"]

    def test_pre_hook_drops_confirmation(self, tmp_path: Path) -> None:
        assert self._run_groups(tmp_path, pre_process="touch {dir}/x") == frozenset()

    def test_no_pre_hook_keeps_confirmation(self, tmp_path: Path) -> None:
        confirmed = self._run_groups(tmp_path, pre_process=None)
        assert confirmed == frozenset({tmp_path / "a.mkv"})

    def test_dry_run_keeps_confirmation_even_with_pre_hook(self, tmp_path: Path) -> None:
        """Hooks do not run in dry-run, so the confirmation set is still valid."""
        confirmed = self._run_groups(tmp_path, pre_process="touch {dir}/x", dry_run=True)
        assert confirmed == frozenset({tmp_path / "a.mkv"})


class TestHooksSkippedInDryRun:
    """--dry-run must not execute user hooks (they mutate the filesystem)."""

    def test_hooks_do_not_fire_in_dry_run(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from trimarr.runner import _process_directory_groups

        mkv = tmp_path / "a.mkv"
        mkv.touch()
        groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict({tmp_path: [(mkv, tmp_path)]})

        with (
            patch("trimarr.runner.Database") as mock_db_cls,
            patch("trimarr.runner._run_hook") as mock_hook,
            patch("trimarr.runner._process_one_file_guarded"),
        ):
            mock_db = MagicMock()
            mock_db.is_processed.return_value = False
            mock_db_cls.return_value.__enter__.return_value = mock_db
            mock_db_cls.return_value.__exit__.return_value = False
            _process_directory_groups(
                groups,
                database_path=str(tmp_path / "trimarr.db"),
                profile_hash="hash",
                cfg=_ProcessingConfig(
                    mkvmerge_path="/fake",
                    language=["eng"],
                    keep_audio=False,
                    keep_native_audio=False,
                    keep_subtitles=False,
                    edit_metadata_title=False,
                    delete_metadata_title=False,
                    strip_lower_channels=False,
                    strip_commentary=False,
                    skip_size_check=False,
                    dry_run=True,
                    no_backup=True,
                ),
                total=1,
                pre_process="touch {dir}/x",
                post_process="touch {dir}/y",
                command_timeout_seconds=5,
                logger=MagicMock(),
            )

        mock_hook.assert_not_called()


class TestPostHookRunsOnInterrupt:
    """The post hook is the paired undo of the pre hook and must always fire."""

    def test_post_hook_fires_when_processing_raises(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from trimarr.runner import _process_directory_groups

        mkv = tmp_path / "a.mkv"
        mkv.touch()
        groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict({tmp_path: [(mkv, tmp_path)]})

        hook_templates: list[str] = []

        def _record(**kwargs: object) -> None:
            hook_templates.append(str(kwargs["cmd_template"]))

        with (
            patch("trimarr.runner.Database") as mock_db_cls,
            patch("trimarr.runner._run_dir_hook", side_effect=_record),
            patch("trimarr.runner._process_one_file_guarded", side_effect=KeyboardInterrupt),
        ):
            mock_db = MagicMock()
            mock_db.is_processed.return_value = False
            mock_db_cls.return_value.__enter__.return_value = mock_db
            mock_db_cls.return_value.__exit__.return_value = False
            counts, interrupted, _failures, _db_unavailable = _process_directory_groups(
                groups,
                database_path=str(tmp_path / "trimarr.db"),
                profile_hash="hash",
                cfg=_TestCfg.build(),
                total=1,
                pre_process="unlock {dir}",
                post_process="lock {dir}",
                command_timeout_seconds=5,
                logger=MagicMock(),
            )

        assert interrupted is True
        assert hook_templates == ["unlock {dir}", "lock {dir}"], "post hook must run after an interrupt"


class _TestCfg:
    @staticmethod
    def build():
        return _ProcessingConfig(
            mkvmerge_path="/fake",
            language=["eng"],
            keep_audio=False,
            keep_native_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            skip_size_check=False,
            dry_run=False,
            no_backup=True,
        )


class TestCorruptOutputBanner:
    """The abort banner must survive the central log sanitiser line by line."""

    def test_banner_is_logged_per_line_without_forging(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        import pytest

        from trimarr.processor import CorruptOutputError
        from trimarr.runner import _handle_corrupt_output

        tmp_file = tmp_path / "movie.tmp.mkv"
        tmp_file.write_bytes(b"x")
        exc = CorruptOutputError(
            file_path=tmp_path / "movie.mkv",
            tmp_path=tmp_file,
            probe_returncode=2,
            probe_output="bad",
            output_size=10,
            input_size=100,
            mkvmerge_path="/usr/bin/mkvmerge",
        )
        logger = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            _handle_corrupt_output(exc, _RunCounts(), logger)

        assert exc_info.value.code == 2
        messages = [str(call.args[0]) for call in logger.opt.return_value.critical.call_args_list]
        assert any("CORRUPT OUTPUT DETECTED" in m for m in messages)
        assert all("\n" not in m for m in messages), "each banner line must be its own record"
        assert len(messages) > 5, "the banner must not be flattened into one record"
        assert messages[0] == "=" * 80, "the banner must start with its separator line"


class TestProfileHashKeepUndefinedAudio:
    """keep_undefined_audio is hashed only when True, preserving existing rows."""

    def test_true_changes_hash(self) -> None:
        base = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            strip_subtitle_regex_patterns=None,
            keep_undefined_audio=True,
        )
        without = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            strip_subtitle_regex_patterns=None,
            keep_undefined_audio=False,
        )
        assert base != without

    def test_false_matches_legacy_hash(self) -> None:
        """False must hash exactly like the pre-flag profile (no mass re-scan)."""
        legacy = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
        )
        with_flag = _build_profile_hash(
            language=["eng"],
            keep_audio=False,
            keep_subtitles=False,
            edit_metadata_title=False,
            delete_metadata_title=False,
            strip_lower_channels=False,
            strip_commentary=False,
            keep_undefined_audio=False,
        )
        assert legacy == with_flag


class TestBannerInjectionResistance:
    """A newline inside a path or probe output must not create extra records."""

    def test_newline_in_path_and_output_cannot_forge_records(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        import pytest

        from trimarr.processor import CorruptOutputError
        from trimarr.runner import _handle_corrupt_output

        tmp_file = tmp_path / "movie.tmp.mkv"
        tmp_file.write_bytes(b"x")
        exc = CorruptOutputError(
            file_path=tmp_path / "movie\nFORGED | ERROR | line.mkv",
            tmp_path=tmp_file,
            probe_returncode=2,
            probe_output="bad\nANOTHER FORGED LINE",
            output_size=10,
            input_size=100,
            mkvmerge_path="/usr/bin/mkvmerge",
        )
        logger = MagicMock()

        with pytest.raises(SystemExit):
            _handle_corrupt_output(exc, _RunCounts(), logger)

        messages = [str(call.args[0]) for call in logger.opt.return_value.critical.call_args_list]
        assert all("\n" not in m for m in messages)
        assert not any(m.startswith("FORGED") for m in messages)
        assert not any(m.startswith("ANOTHER FORGED") for m in messages)
        assert len(messages) > 5, "the banner must keep its line structure"
        # The forged text stays embedded in its field, on the field's own line.
        assert sum("FORGED" in m for m in messages) <= 2
