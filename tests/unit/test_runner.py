"""Unit tests for core.runner."""

from __future__ import annotations

import hashlib
import json
import re
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
        assert result is True


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
