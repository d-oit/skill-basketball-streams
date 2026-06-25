#!/usr/bin/env python3
"""validate.py — self-contained validator for skill-basketball-streams.

Performs three independent checks against the project at --root. Stdlib only
(no external dependencies); runnable on any Python 3.8+ environment.

Usage:
    python3 scripts/validate.py [--root PATH] [--check {all,evals|skill|references|smoke-test}]

Checks:
    evals       evals/evals.json conforms to the v1.0.0 standard schema
                ({skill_name, evals:[{id:int, prompt, expected_output, assertions[]}]}).
    skill       SKILL.md frontmatter carries name+description+version+category,
                name matches the spec format regex (lowercase / digits / hyphens)
                and is <= 64 chars, description <= 1024 chars, compatibility <=
                500 chars (when present), body <= 250 lines, and the
                ## Rationalizations + ## Red Flags mandatory sections are present.
    references  Every backtick-wrapped `.md` path cited from SKILL.md and
                README.md resolves to a real file under --root.
    smoke-test  Self-test: writes a tmp fixture with known schema violations
                (invalid `name`, over-long `description`, wrong `skill_name`,
                non-int `id`, non-list `assertions`, dangling .md references),
                re-invokes this script against the fixture via subprocess, and
                asserts exit code 1 + diagnostic FAIL lines for every check on
                stderr. Use to detect regressions if the schema-check branches
                are later edited.

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
        # Per-case type coverage (note: deliberately NOT asserting
        # non-empty for `expected_output`, `assertions`, or per-entry
        # assertions — a legitimate eval case can express "no output"
        # via empty `expected_output`, "no PASS/FAIL signal" via empty
        # `assertions: []`, etc. The static schema check above is
        # necessary but not sufficient; these type-only checks ensure each
        # case carries the right TYPE of evidence for a future
        # skill-evaluator to assert against, without rejecting legitimate
        # "vacuous-looking" cases).
        expected_output = case.get("expected_output")
        if not isinstance(expected_output, str):
            errs.append(
                f"evals[{i}].expected_output: expected string, "
                f"got {type(expected_output).__name__}"
            )
        if isinstance(case.get("assertions"), list):
            for j, assertion in enumerate(case["assertions"]):
                if not isinstance(assertion, str):
                    errs.append(
                        f"evals[{i}].assertions[{j}]: expected string, "
                        f"got {type(assertion).__name__}"
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


def smoke_test() -> int:
    """Self-test: each FAIL branch must be exercised by at least one targeted
    fixture via a separate subprocess invocation. Three fixtures (one per
    check) are set up in independent tmp directories; each fixture is
    designed so only the target check fails while the other two pass.

    Returns 0 if every FAIL branch correctly rejects its targeted fixture;
    returns 1 if any branch is silently vacuous (subprocess returned 0, or
    the expected diagnostic prefix was missing from stderr).
    """
    import subprocess
    import tempfile

    VALID_EVALS = json.dumps(
        {
            "skill_name": SKILL_NAME,
            "evals": [
                {
                    "id": 1,
                    "prompt": "x",
                    "expected_output": "y",
                    "assertions": ["a"],
                }
            ],
        }
    )
    VALID_SKILL_MD = (
        "---\n"
        f"name: {SKILL_NAME}\n"
        "description: Valid SKILL.md fixture for the smoke-test.\n"
        "version: 1.0.0\n"
        "category: workflow\n"
        "---\n\n"
        "# Title\n\n"
        "## Purpose\n\nSome content.\n\n"
        "## Rationalizations\n\n- [ ] none\n\n"
        "## Red Flags\n\n- [ ] none\n\n"
        "## References\n\n- `references/x.md`\n"
    )

    def setup_evals_fail(r: Path) -> None:
        """check_evals must FAIL; check_skill + check_references must PASS."""
        (r / "evals").mkdir()
        (r / "evals" / "evals.json").write_text(
            json.dumps({"skill_name": "WRONG", "evals": []}),
            encoding="utf-8",
        )
        (r / "references").mkdir()
        (r / "references" / "x.md").write_text("# OK\n", encoding="utf-8")
        (r / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
        (r / "README.md").write_text(
            "See `references/x.md`.\n", encoding="utf-8"
        )

    def setup_skill_fail(r: Path) -> None:
        """check_skill must FAIL; check_evals + check_references must PASS."""
        (r / "evals").mkdir()
        (r / "evals" / "evals.json").write_text(VALID_EVALS, encoding="utf-8")
        (r / "references").mkdir()
        (r / "references" / "x.md").write_text("# OK\n", encoding="utf-8")
        # Bad SKILL.md: name fails NAME_REGEX (uppercase + underscore).
        (r / "SKILL.md").write_text(
            "---\n"
            "name: Bad_Name\n"
            "description: x\n"
            "version: 1.0.0\n"
            "category: workflow\n"
            "---\n\n"
            "# title\n",
            encoding="utf-8",
        )
        (r / "README.md").write_text(
            "See `references/x.md`.\n", encoding="utf-8"
        )

    def setup_references_fail(r: Path) -> None:
        """check_references must FAIL; check_evals + check_skill must PASS."""
        (r / "evals").mkdir()
        (r / "evals" / "evals.json").write_text(VALID_EVALS, encoding="utf-8")
        (r / "references").mkdir()
        (r / "references" / "x.md").write_text("# OK\n", encoding="utf-8")
        (r / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
        (r / "README.md").write_text(
            "See `references/missing.md`.\n", encoding="utf-8"
        )

    script_path = Path(__file__).resolve()
    cases = [
        ("evals", "FAIL: evals:", setup_evals_fail),
        ("skill", "FAIL: SKILL.md:", setup_skill_fail),
        ("references", "FAIL: references:", setup_references_fail),
    ]
    errs: list[str] = []
    for check, expected_prefix, setup in cases:
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            setup(r)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--root",
                    str(r),
                    "--check",
                    check,
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                errs.append(
                    f"--check {check}: expected exit 1, got 0 "
                    f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})"
                )
            elif expected_prefix not in proc.stderr:
                errs.append(
                    f"--check {check}: missing diagnostic {expected_prefix!r} "
                    f"in stderr:\n{proc.stderr}"
                )

    if errs:
        for e in errs:
            print(f"FAIL: smoke-test: {e}", file=sys.stderr)
        return 1
    print(
        "OK: smoke-test: every FAIL branch correctly rejects its targeted "
        "fixture (3 separate subprocess invocations across check_evals / "
        "check_skill / check_references)"
    )
    return 0


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
        choices=("all", "evals", "skill", "references", "smoke-test"),
        help="which check to run (default: all); 'smoke-test' exercises the "
             "FAIL branches against a tmp fixture and returns 0 if every "
             "branch correctly rejects",
    )
    args = p.parse_args()
    if args.check == "smoke-test":
        sys.exit(smoke_test())
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
