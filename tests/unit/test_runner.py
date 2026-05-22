"""Unit tests for core.runner."""

from __future__ import annotations

import hashlib
import json
import re

from trimarr.runner import _build_profile_hash


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
