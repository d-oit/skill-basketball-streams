#!/usr/bin/env python3
"""validate.py — self-contained validator for skill-basketball-streams.

Performs three independent checks against the project at --root. Stdlib only
(no external dependencies); runnable on any Python 3.8+ environment.

Usage:
    python3 scripts/validate.py [--root PATH] [--check {all,evals|skill|references}]

Checks:
    evals       evals/evals.json conforms to the v1.0.0 standard schema
                ({skill_name, evals:[{id:int, prompt, expected_output, assertions[]}]}).
    skill       SKILL.md frontmatter carries name+description+version+category,
                description <= 1024 chars, body <= 250 lines, ## Rationalizations
                and ## Red Flags sections present.
    references  Every backtick-wrapped `.md` path cited from SKILL.md and
                README.md resolves to a real file under --root.

Exit codes:
    0  PASS  — every requested check succeeded.
    1  FAIL  — one or more checks reported an error (printed to stderr).
    2  USAGE — bad arguments or a required input file is missing.

Output conventions (per agentskills.io using-scripts.md):
    * "OK: ..." goes to stdout when each check passes.
    * "FAIL: ..." goes to stderr on failure.
    * The script is idempotent — re-running has no side effects.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_NAME = "skill-basketball-streams"
NAME_REGEX = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
NAME_MAX_LENGTH = 64
MAX_DESCRIPTION_CHARS = 1024
MAX_COMPATIBILITY_CHARS = 500
MAX_BODY_LINES = 250
EVALS_REQUIRED_KEYS = {"id", "prompt", "expected_output", "assertions"}
FRONTMATTER_REQUIRED_KEYS = {"name", "description", "version", "category"}
MANDATORY_SECTIONS = {"## Rationalizations", "## Red Flags"}


def _fail(msg: str):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def check_evals(root: Path) -> None:
    """Validate evals/evals.json against the standard schema."""
    f = root / "evals" / "evals.json"
    if not f.is_file():
        _fail(f"{f.relative_to(root)}: file missing")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"evals/evals.json: invalid JSON ({e})")
    errs: list[str] = []
    if d.get("skill_name") != SKILL_NAME:
        errs.append(
            f"skill_name expected {SKILL_NAME!r}, got {d.get('skill_name')!r}"
        )
    cases = d.get("evals")
    if not isinstance(cases, list):
        errs.append("evals: expected list, got " + type(cases).__name__)
        cases = []
    for i, case in enumerate(cases):
        missing = EVALS_REQUIRED_KEYS - case.keys()
        if missing:
            errs.append(f"evals[{i}]: missing keys {sorted(missing)}")
            continue
        if not isinstance(case["id"], int):
            errs.append(
                f"evals[{i}].id: expected int, got {type(case['id']).__name__}"
            )
        if not isinstance(case["assertions"], list):
            errs.append(
                f"evals[{i}].assertions: expected list, got "
                f"{type(case['assertions']).__name__}"
            )
    if errs:
        for e in errs:
            print(f"FAIL: evals: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"OK: evals: {len(cases)} cases conform to standard schema "
        f"(skill_name={SKILL_NAME})"
    )


def check_skill(root: Path) -> None:
    """Validate SKILL.md frontmatter, body length, and mandatory sections."""
    f = root / "SKILL.md"
    if not f.is_file():
        _fail(f"SKILL.md: file missing")
    t = f.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", t, re.DOTALL)
    errs: list[str] = []
    if not m:
        errs.append("no YAML frontmatter delimited by '---' ... '---'")
    else:
        fm = m.group(1)
        body = t.splitlines()[len(m.group(0).splitlines()):]
        fm_keys = {
            ln.split(":", 1)[0].strip()
            for ln in fm.splitlines()
            if ":" in ln and not ln.startswith(" ")
        }
        missing = FRONTMATTER_REQUIRED_KEYS - fm_keys
        if missing:
            errs.append(f"frontmatter missing keys: {sorted(missing)}")
        name_match = re.search(r"^name:\s*(.*)", fm, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip()
            if name != SKILL_NAME:
                errs.append(f"frontmatter.name expected {SKILL_NAME!r}, got {name!r}")
            if not NAME_REGEX.fullmatch(name):
                errs.append(
                    f"frontmatter.name {name!r} fails spec format "
                    "(lowercase letters/numbers/hyphens only; "
                    "no leading/trailing/consecutive hyphens)"
                )
            if len(name) > NAME_MAX_LENGTH:
                errs.append(
                    f"frontmatter.name length {len(name)} > {NAME_MAX_LENGTH}"
                )
        desc_match = re.search(r"^description:\s*(.+)", fm, re.MULTILINE)
        if desc_match:
            desc = desc_match.group(1).strip()
            if len(desc) > MAX_DESCRIPTION_CHARS:
                errs.append(
                    f"description length {len(desc)} > {MAX_DESCRIPTION_CHARS}"
                )
        compat_match = re.search(r"^compatibility:\s*(.+)", fm, re.MULTILINE)
        if compat_match:
            compat = compat_match.group(1).strip()
            if len(compat) > MAX_COMPATIBILITY_CHARS:
                errs.append(
                    f"compatibility length {len(compat)} > {MAX_COMPATIBILITY_CHARS}"
                )
        if len(body) > MAX_BODY_LINES:
            errs.append(
                f"body has {len(body)} lines (limit {MAX_BODY_LINES})"
            )
        present = set(re.findall(r"^## .+", t, re.MULTILINE))
        missing_sections = MANDATORY_SECTIONS - present
        if missing_sections:
            errs.append(
                f"missing mandatory sections: {sorted(missing_sections)}"
            )
    if errs:
        for e in errs:
            print(f"FAIL: SKILL.md: {e}", file=sys.stderr)
        sys.exit(1)
    body_line_count = len(t.splitlines()[len(m.group(0).splitlines()):])
    print(
        f"OK: SKILL.md: frontmatter valid + body {body_line_count} <= "
        f"{MAX_BODY_LINES} lines + mandatory sections present"
    )


def check_references(root: Path) -> None:
    """Resolve every backtick-wrapped `.md` path cited from SKILL.md + README.md."""
    refs: set[str] = set()
    for name in ("SKILL.md", "README.md"):
        f = root / name
        if not f.is_file():
            print(
                f"WARN: references: {name}: file missing for reference scan",
                file=sys.stderr,
            )
            continue
        for r in re.findall(r"`([\w/.\-]+\.md)`", f.read_text(encoding="utf-8")):
            if "/" in r:
                refs.add(r)
    missing = [r for r in sorted(refs) if not (root / r).is_file()]
    if missing:
        for r in missing:
            print(f"FAIL: references: {r}: file missing", file=sys.stderr)
        sys.exit(1)
    print(
        f"OK: references: all {len(refs)} backtick-wrapped .md paths resolve "
        f"({', '.join(sorted(refs))})"
    )


CHECKS = {
    "evals": check_evals,
    "skill": check_skill,
    "references": check_references,
}


def main() -> None:
    p = argparse.ArgumentParser(
        description="Validate skill-basketball-streams project structure.",
    )
    p.add_argument(
        "--root",
        default=".",
        help="path to the skill directory (default: current working directory)",
    )
    p.add_argument(
        "--check",
        default="all",
        choices=("all", "evals", "skill", "references"),
        help="which check to run (default: all)",
    )
    args = p.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(
            f"FAIL: --root {root}: not a directory",
            file=sys.stderr,
        )
        sys.exit(2)
    selected = CHECKS.keys() if args.check == "all" else (args.check,)
    for name in selected:
        CHECKS[name](root)


if __name__ == "__main__":
    main()
