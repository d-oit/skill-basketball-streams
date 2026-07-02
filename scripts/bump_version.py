#!/usr/bin/env python3
"""bump_version.py — Helper script to automate version bump sync between SKILL.md and CHANGELOG.md.

This script reads the current version from SKILL.md frontmatter, bumps it
(major/minor/patch flag), writes the new version back to SKILL.md, and appends
a templated entry to CHANGELOG.md automatically.

Usage:
    python3 scripts/bump_version.py [--bump {major,minor,patch}] [--dry-run] [--root PATH]

Examples:
    # Bump patch version (1.1.2 -> 1.1.3)
    python3 scripts/bump_version.py --bump patch

    # Bump minor version (1.1.2 -> 1.2.0)
    python3 scripts/bump_version.py --bump minor

    # Bump major version (1.1.2 -> 2.0.0)
    python3 scripts/bump_version.py --bump major

    # Dry run (show what would change without modifying files)
    python3 scripts/bump_version.py --bump patch --dry-run

Exit codes:
    0  SUCCESS — version bumped successfully
    1  FAIL    — error occurred (file not found, invalid version, etc.)
    2  USAGE   — bad arguments
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


def parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse a version string into (major, minor, patch) tuple.
    
    Args:
        version_str: Version string like "1.1.2" or "1.1.2"
    
    Returns:
        Tuple of (major, minor, patch) as integers.
    
    Raises:
        ValueError: If version string doesn't match semver pattern.
    """
    version_str = version_str.strip().strip('"').strip("'")
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)$', version_str)
    if not match:
        raise ValueError(f"Invalid version string: {version_str!r}. Expected format: X.Y.Z")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def format_version(major: int, minor: int, patch: int) -> str:
    """Format version tuple into a version string."""
    return f"{major}.{minor}.{patch}"


def bump_version(version: tuple[int, int, int], bump_type: str) -> tuple[int, int, int]:
    """Bump version tuple based on bump type.
    
    Args:
        version: Tuple of (major, minor, patch)
        bump_type: One of "major", "minor", "patch"
    
    Returns:
        New version tuple.
    
    Raises:
        ValueError: If bump_type is invalid.
    """
    major, minor, patch = version
    if bump_type == "major":
        return (major + 1, 0, 0)
    elif bump_type == "minor":
        return (major, minor + 1, 0)
    elif bump_type == "patch":
        return (major, minor, patch + 1)
    else:
        raise ValueError(f"Invalid bump type: {bump_type!r}. Must be 'major', 'minor', or 'patch'")


def read_skill_version(root: Path) -> tuple[str, list[str]]:
    """Read the version from SKILL.md frontmatter.
    
    Args:
        root: Path to the project root directory.
    
    Returns:
        Tuple of (version_string, frontmatter_lines) where frontmatter_lines
        is the list of all frontmatter lines for later replacement.
    
    Raises:
        FileNotFoundError: If SKILL.md doesn't exist.
        ValueError: If version field is not found in frontmatter.
    """
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        raise FileNotFoundError(f"SKILL.md not found at {skill_file}")
    
    content = skill_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    
    # Find frontmatter
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md: No YAML frontmatter found (expected '---' at start)")
    
    frontmatter_lines = []
    version_line_idx = None
    version_str = None
    
    for i, line in enumerate(lines[1:], start=1):
        frontmatter_lines.append(line)
        if line.startswith("version:"):
            version_line_idx = i
            # Extract version value
            match = re.match(r'^version:\s*["\']?([^"\']+)["\']?\s*$', line)
            if match:
                version_str = match.group(1)
        elif line.strip() == "---":
            # End of frontmatter
            break
    
    if version_str is None:
        raise ValueError("SKILL.md: 'version' field not found in frontmatter")
    
    return version_str, frontmatter_lines, version_line_idx


def update_skill_version(root: Path, new_version: str, frontmatter_lines: list[str], version_line_idx: int) -> None:
    """Update the version in SKILL.md frontmatter.
    
    Args:
        root: Path to the project root directory.
        new_version: New version string to write.
        frontmatter_lines: List of frontmatter lines.
        version_line_idx: Index of the version line in the full file (1-indexed after '---').
    """
    skill_file = root / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    
    # Replace the version line
    # version_line_idx is 1-indexed from the start of frontmatter (after first '---')
    # So in the full lines list, it's at index version_line_idx (0-indexed from line 1)
    actual_idx = version_line_idx  # Already 1-indexed from frontmatter start
    if actual_idx < len(lines):
        lines[actual_idx] = f'version: "{new_version}"'
    
    skill_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_changelog(root: Path, old_version: str, new_version: str) -> None:
    """Append a new CHANGELOG.md entry for the version bump.
    
    Args:
        root: Path to the project root directory.
        old_version: Previous version string.
        new_version: New version string.
    """
    changelog_file = root / "CHANGELOG.md"
    today = date.today().isoformat()
    
    if changelog_file.is_file():
        content = changelog_file.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n\nAll notable changes to `skill-basketball-streams` will be documented in this file.\n\nThe format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\nand this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n"
    
    # Find the [Unreleased] section or create one
    unreleased_pattern = r'## \[Unreleased\]'
    if re.search(unreleased_pattern, content):
        # Replace [Unreleased] with dated section
        new_section = f"## [{new_version}] - {today}\n\n### Added\n\n- Placeholder for new features\n\n### Changed\n\n- Placeholder for changes\n\n### Fixed\n\n- Placeholder for fixes\n\n\n## [Unreleased]\n"
        content = re.sub(unreleased_pattern, new_section, content, count=1)
    else:
        # Add new section at the top (after the intro)
        intro_end = content.find("\n\n## [")
        if intro_end == -1:
            # No existing sections, add after the intro
            content = content.rstrip() + f"\n\n## [{new_version}] - {today}\n\n### Added\n\n- Placeholder for new features\n\n### Changed\n\n- Placeholder for changes\n\n### Fixed\n\n- Placeholder for fixes\n\n\n"
        else:
            content = content[:intro_end] + f"\n## [{new_version}] - {today}\n\n### Added\n\n- Placeholder for new features\n\n### Changed\n\n- Placeholder for changes\n\n### Fixed\n\n- Placeholder for fixes\n\n\n" + content[intro_end:]
    
    changelog_file.write_text(content, encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Bump version in SKILL.md and CHANGELOG.md",
    )
    p.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        required=True,
        help="Version component to bump (major, minor, or patch)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would change without modifying files",
    )
    p.add_argument(
        "--root",
        default=".",
        help="Path to the skill directory (default: current working directory)",
    )
    args = p.parse_args()
    
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: --root {root}: not a directory", file=sys.stderr)
        sys.exit(2)
    
    try:
        # Read current version
        old_version_str, frontmatter_lines, version_line_idx = read_skill_version(root)
        old_version = parse_version(old_version_str)
        
        # Calculate new version
        new_version = bump_version(old_version, args.bump)
        new_version_str = format_version(*new_version)
        
        print(f"Current version: {old_version_str}")
        print(f"New version:     {new_version_str} ({args.bump} bump)")
        
        if args.dry_run:
            print("\n[DRY RUN] Would perform the following changes:")
            print(f"  - Update SKILL.md version: {old_version_str} -> {new_version_str}")
            print(f"  - Add CHANGELOG.md entry for [{new_version_str}] - {date.today().isoformat()}")
            print("\nRun without --dry-run to apply changes.")
            sys.exit(0)
        
        # Update SKILL.md
        update_skill_version(root, new_version_str, frontmatter_lines, version_line_idx)
        print(f"OK: SKILL.md version updated to {new_version_str}")
        
        # Update CHANGELOG.md
        update_changelog(root, old_version_str, new_version_str)
        print(f"OK: CHANGELOG.md updated with entry for {new_version_str}")
        
        print("\nVersion bump complete!")
        print(f"  SKILL.md: version = \"{new_version_str}\"")
        print(f"  CHANGELOG.md: Added section for [{new_version_str}]")
        
    except (FileNotFoundError, ValueError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
