"""Unit tests for core.runner."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from trimarr.runner import _build_profile_hash, _ProcessingConfig, _resolve_effective_language


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
