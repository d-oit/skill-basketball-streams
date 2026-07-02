"""Unit tests for scripts/bump_version.py."""
import tempfile
from pathlib import Path

import pytest

from scripts.bump_version import (
    parse_version,
    format_version,
    bump_version,
    read_skill_version,
)


class TestParseVersion:
    """Test version parsing."""

    def test_parse_simple_version(self):
        assert parse_version("1.2.3") == (1, 2, 3)

    def test_parse_version_with_quotes(self):
        assert parse_version('"1.2.3"') == (1, 2, 3)
        assert parse_version("'1.2.3'") == (1, 2, 3)

    def test_parse_version_with_whitespace(self):
        assert parse_version("  1.2.3  ") == (1, 2, 3)

    def test_parse_invalid_version(self):
        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("1.2")

    def test_parse_invalid_version_non_numeric(self):
        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("1.2.x")


class TestFormatVersion:
    """Test version formatting."""

    def test_format_version(self):
        assert format_version(1, 2, 3) == "1.2.3"
        assert format_version(0, 0, 1) == "0.0.1"
        assert format_version(10, 20, 30) == "10.20.30"


class TestBumpVersion:
    """Test version bumping."""

    def test_bump_patch(self):
        assert bump_version((1, 2, 3), "patch") == (1, 2, 4)
        assert bump_version((1, 2, 9), "patch") == (1, 2, 10)

    def test_bump_minor(self):
        assert bump_version((1, 2, 3), "minor") == (1, 3, 0)
        assert bump_version((1, 9, 9), "minor") == (1, 10, 0)

    def test_bump_major(self):
        assert bump_version((1, 2, 3), "major") == (2, 0, 0)
        assert bump_version((9, 9, 9), "major") == (10, 0, 0)

    def test_bump_invalid_type(self):
        with pytest.raises(ValueError, match="Invalid bump type"):
            bump_version((1, 2, 3), "invalid")


class TestReadSkillVersion:
    """Test reading version from SKILL.md."""

    def test_read_version_from_skill_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_file = root / "SKILL.md"
            skill_file.write_text(
                "---\n"
                "name: test\n"
                'version: "1.2.3"\n'
                "category: workflow\n"
                "---\n\n"
                "# Test\n",
                encoding="utf-8",
            )
            version_str, frontmatter_lines, version_line_idx = read_skill_version(root)
            assert version_str == "1.2.3"
            assert version_line_idx == 2  # Line 2 (0-indexed from frontmatter start)

    def test_read_version_without_quotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_file = root / "SKILL.md"
            skill_file.write_text(
                "---\n"
                "name: test\n"
                "version: 1.2.3\n"
                "category: workflow\n"
                "---\n\n"
                "# Test\n",
                encoding="utf-8",
            )
            version_str, frontmatter_lines, version_line_idx = read_skill_version(root)
            assert version_str == "1.2.3"

    def test_read_version_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_file = root / "SKILL.md"
            skill_file.write_text(
                "---\n"
                "name: test\n"
                "category: workflow\n"
                "---\n\n"
                "# Test\n",
                encoding="utf-8",
            )
            with pytest.raises(ValueError, match="'version' field not found"):
                read_skill_version(root)

    def test_read_version_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skill_file = root / "SKILL.md"
            skill_file.write_text("# Test\n", encoding="utf-8")
            with pytest.raises(ValueError, match="No YAML frontmatter"):
                read_skill_version(root)

    def test_read_version_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with pytest.raises(FileNotFoundError, match="SKILL.md not found"):
                read_skill_version(root)
