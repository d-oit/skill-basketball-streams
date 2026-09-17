#!/usr/bin/env python3
"""check_workflow_refs.py — every repo path a workflow names must exist.

Why this exists. `runtime-daily.yml` carried

    python3 scripts/capture_transcripts.py --runner replay \\
      --from tests/fixtures/runtime_transcripts.json --check || true

for a long time. The fixture was never committed (correctly — it must be
*captured*, see `tests/fixtures/README.md`), so `--from` exited 2 and `|| true`
turned the step into a no-op that read as a pass in the job summary. The same
shape — `... || true`, `... || echo "::warning::"` — hides *any* missing input.

So this checks the paths, not the pattern. A step may legitimately tolerate a
failure; it may not name a file that does not exist and still claim to have run.

Rules, deliberately narrow so the signal stays trustworthy:

* Only tokens under a known repo root (`scripts/`, `tests/`, `config/`,
  `evals/`, `references/`, `plans/`, `docs/`) count. A bare filename is not a
  claim about the repo.
* URLs, shell expansions, globs, absolute paths and `.tmp/` paths are ignored —
  none of them is a committed repo path.
* Write targets (`--out X`, `--dest X`, `> X`) are ignored: creating the file is
  the point of the step, so its absence is correct.
* If the same file contains an existence test for a path (`[ -f X ]`), that path
  is a question rather than a claim and may legitimately be absent. That rule is
  **file-scoped and therefore coarse**. It is coarse on purpose: a check that
  cries wolf gets deleted or reverted rather than investigated.

Usage:
    python3 scripts/check_workflow_refs.py [--root .] [--workflows DIR]

Exit codes:
    0  every referenced path exists (or is guarded by an existence test)
    1  at least one referenced path does not exist
    2  USAGE — the workflow directory is missing
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOTS = (
    "scripts/",
    "tests/",
    "config/",
    "evals/",
    "references/",
    "plans/",
    "docs/",
)
# A path token: no whitespace, no shell metacharacters, no URL punctuation.
TOKEN = re.compile(r"[A-Za-z0-9._/@+-]+")
WRITE_TARGET = re.compile(r"(--out|--dest|--to|--target|>>?)\s*$")
EXISTENCE_TEST = re.compile(r"\[\s*-[fe]\s+([^\s\]]+)")
TRAILING_PUNCTUATION = ".,;:)]}\"'`"


def _is_path(token: str, following: str = "") -> bool:
    if not token.startswith(ROOTS):
        return False
    if "//" in token or token.startswith("/") or token.startswith("."):
        return False
    if "$" in token or "*" in token or "{" in token or "}" in token:
        return False
    # `tests/fixtures/*.json` splits at the glob, leaving a directory prefix.
    # The prefix is not a claim about a file, and neither is prose like
    # "scripts/foo.py:" or "docs/do-harness.md.".
    if token.endswith("/") or following in ("*", "?"):
        return False
    return token.rstrip(TRAILING_PUNCTUATION) == token


def _strip_comment(line: str) -> str:
    """Drop a shell comment, so prose about a path is not read as a claim.

    Learned the hard way elsewhere in this repo: a path named inside a comment
    turned a check red for a reason that had nothing to do with the code.
    """
    if line.lstrip().startswith("#"):
        return ""
    return re.sub(r"(^|\s)#[^\"']*$", " ", line)


def find_refs(text: str) -> set[str]:
    """Every repo path named in a workflow body, comments excluded."""
    found: set[str] = set()
    for line in text.splitlines():
        line = _strip_comment(line)
        if not line.strip():
            continue
        for match in TOKEN.finditer(line):
            token = match.group(0)
            following = line[match.end() : match.end() + 1]
            if not _is_path(token, following):
                continue
            if WRITE_TARGET.search(line[: match.start()]):
                continue
            found.add(token)
    return found


def find_guarded(text: str) -> set[str]:
    """Paths this file asks about with `[ -f X ]` — allowed to not exist."""
    guarded: set[str] = set()
    for line in text.splitlines():
        for match in EXISTENCE_TEST.finditer(_strip_comment(line)):
            guarded.add(match.group(1).strip(TRAILING_PUNCTUATION + "\"'"))
    return guarded


def check_file(path: Path, root: Path) -> list[str]:
    """Missing paths named by one workflow. Empty list means it is clean."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    guarded = find_guarded(text)
    missing = [
        ref
        for ref in sorted(find_refs(text))
        if ref not in guarded and not (root / ref).exists()
    ]
    return [f"{path.name}: references missing path {ref}" for ref in missing]


def workflow_files(directory: Path) -> list[Path]:
    return sorted(
        [*directory.glob("*.yml"), *directory.glob("*.yaml")],
        key=lambda p: p.name,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail when a workflow names a repo path that does not exist.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--workflows", default=None)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    directory = Path(args.workflows) if args.workflows else root / ".github" / "workflows"
    if not directory.is_dir():
        print(f"FAIL: check_workflow_refs: {directory}: not a directory", file=sys.stderr)
        sys.exit(2)

    problems: list[str] = []
    checked = 0
    for path in workflow_files(directory):
        checked += 1
        problems.extend(check_file(path, root))

    if problems:
        for problem in problems:
            print(f"FAIL: check_workflow_refs: {problem}", file=sys.stderr)
        print(
            "FAIL: check_workflow_refs: a workflow may not name a file that does "
            "not exist — either commit it, or guard the step with `[ -f X ]`",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"OK: check_workflow_refs: {checked} workflow(s) reference only existing paths"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
