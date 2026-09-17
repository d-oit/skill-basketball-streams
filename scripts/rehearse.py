#!/usr/bin/env python3
"""rehearse.py — the pre-release production rehearsal.

The release flow ends with a tag. This is the step that has to be green first,
and it is deliberately **not** a sensor: sensors in `do-harness.toml` are offline
and deterministic by design, so a check that depends on a free provider having a
good day would red the build for a reason that is not the change's fault. The
live question therefore lives in a command a human runs, and only the part that
can be *wrong in a way tests can catch* — the classification below — is a pure
function.

It answers one thing, once, for every surface that leaves this machine: **is it
configured, and does it actually work?**

    missing     the credential is not set, so the surface cannot be used
    valid       set, and a live probe was accepted
    invalid     set, and the probe was REJECTED — the case this command exists
                for. `capture_transcripts.py --check-rungs` and `run_daily.py
                --check-backends` are presence-based on purpose ("a preflight
                that reds a correctly configured runner gets deleted"), so a
                *set* key that is a dead one reads as configured everywhere
                except here. This repository lost a month to exactly that: a
                present `OPENROUTER_API_KEY` answering `401 User not found`.
    unverified  set, and this surface has no usable probe (reported, never failed)

**It never writes.** No calendar event, no issue, no repository file: the
calendar probe is a read of one day, and the search probes ask for a single
result. What it cannot do is add a credential for you — a `missing` row means the
rehearsal is incomplete, which is why `--require-all` exists.

Usage:
    python3 scripts/rehearse.py                  # report; exit 1 only on a rejected key
    python3 scripts/rehearse.py --require-all     # also exit 1 on a missing credential
    python3 scripts/rehearse.py --json            # payload on stdout, humans on stderr
    python3 scripts/rehearse.py --markdown        # the same report, for the release PR
    python3 scripts/rehearse.py --list            # the surfaces, offline
    python3 scripts/rehearse.py --offline         # structural checks only, no credential

For a checkout whose credentials live in one file (see `.env.example`):

    cp .env.example .env            # then fill in what you have
    python3 scripts/rehearse.py --env-file .env --require-all

`--env-file` is explicit, never discovered: a stray `.env` cannot change what a
rehearsal reports about the environment it was actually given.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MISSING = "missing"
VALID = "valid"
INVALID = "invalid"
UNVERIFIED = "unverified"

# `ok is None` from a probe means "could not be probed here", which is a
# different statement from "the credential was rejected" and must not be
# collapsed into it: a surface with no `gh` installed is not a bad token.
#
# The return type is a STRING on purpose. This is an assignment, not an
# annotation, so it is evaluated at import — and `bool | None` is PEP 604,
# which is Python 3.10+. The CI matrix runs the suite on **3.9**
# (`.github/workflows/validate.yml`), where the evaluated form raises
# `TypeError: unsupported operand type(s) for |` before a single test collects.
# Quoting it keeps the annotation identical and evaluable nowhere.
Probe = Callable[[], "tuple[bool | None, str]"]


@dataclass(frozen=True)
class Check:
    name: str
    env: tuple[str, ...]
    what: str
    probe: Probe | None = None
    optional: bool = False
    # True only for checks that read the REPOSITORY and never the network —
    # the set `--offline` runs. Derived from `env` in the past, but env-less no
    # longer implies repo-bound: the keyless search rung has no credential and
    # still speaks to a service, and probing it offline would spend nothing
    # less than the network the mode promises not to touch.
    structural: bool = False


@dataclass(frozen=True)
class Row:
    name: str
    what: str
    state: str
    detail: str
    optional: bool = False


def assess(
    checks: list[Check],
    environ: dict[str, str],
    *,
    prober: Callable[[Check], tuple[bool | None, str]] | None,
) -> list[Row]:
    """Classify every surface. Pure: no network, no environment mutation.

    `environ` and `prober` are parameters rather than globals so the matrix can
    be exercised without opening a socket — the same shape as
    `llm_model.resolve_model(pin, environ)`. Presence is decided first and
    alone, so a `missing` row never spends a call.
    """
    rows: list[Row] = []
    for check in checks:
        present = [
            name for name in check.env if (environ.get(name) or "").strip()
        ]
        # A check with no env vars asserts something about the REPOSITORY rather
        # than the environment — a workflow's declared permissions, say — so
        # there is no credential to be missing and it is probed directly.
        if not present and check.env:
            rows.append(
                Row(
                    name=check.name,
                    what=check.what,
                    state=MISSING,
                    detail=f"none of {', '.join(check.env)} is set",
                    optional=check.optional,
                )
            )
            continue
        if check.probe is None or prober is None:
            rows.append(
                Row(
                    name=check.name,
                    what=check.what,
                    state=UNVERIFIED,
                    detail=(
                        f"{present[0]} is set; this surface has no live probe"
                        if present
                        else "this surface has no probe"
                    ),
                    optional=check.optional,
                )
            )
            continue
        ok, detail = prober(check)
        state = UNVERIFIED if ok is None else (VALID if ok else INVALID)
        rows.append(
            Row(
                name=check.name,
                what=check.what,
                state=state,
                detail=detail,
                optional=check.optional,
            )
        )
    return rows


def structural(checks: list[Check]) -> list[Check]:
    """The checks that need no credential AND no network — they read the repo.

    `--offline` runs only these, which is what makes the half of the rehearsal
    that is deterministic and network-free usable as a CI gate: `github-issues`
    can only ever prove *identity* (a read-only `GITHUB_TOKEN` authenticates
    perfectly), while whether a workflow may file is a fact about its
    `permissions:` block and needs no key at all.

    Selected by the explicit `structural` flag, not by an empty `env`: a
    credential-free check may still be a live service (the keyless Exa MCP
    rung), and "does this check read the repository?" is a fact about intent
    that only the author can declare.
    """
    return [check for check in checks if check.structural]


def env_file_values(text: str, source: str) -> dict[str, str]:
    """Parse dotenv-style `KEY=VALUE` lines. Pure, so the strictness is testable.

    Deliberately small: no interpolation, no `export` handling, no multi-line
    values. A credential file that needs a shell to interpret it is a credential
    file whose contents nobody can predict, and the failure it would produce
    (a key that is subtly the wrong string) is one the rehearsal exists to catch.

    A malformed line is REFUSED by name and line number rather than skipped: a
    silently-ignored `GEMINI_API_KEY gemini-...` reads as a `missing` row, which
    sends the reader looking at Google instead of at their own typo.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(
                f"FAIL: rehearse: {source}:{number} is not KEY=VALUE: {line[:60]!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            raise SystemExit(
                f"FAIL: rehearse: {source}:{number} has no usable variable name: "
                f"{key[:60]!r}"
            )
        # Quotes are stripped in pairs only, so a value that legitimately begins
        # with one is not silently truncated.
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_text(text: str, source: str, environ: dict[str, str]) -> list[str]:
    """Load `text`'s pairs into `environ`, returning the names it actually set.

    `setdefault`, so the real environment WINS. That ordering is what makes a
    single run overridable (`GEMINI_API_KEY=other python3 scripts/rehearse.py
    --env-file .env`) without editing the file — the same precedence every
    dotenv loader uses, and the one an operator will assume. The return value
    counts only what was taken *from the file*, so the note printed next to it
    ("supplied 2 variable(s)") is a statement about this run and not about the
    file's contents.

    `environ` is mutated rather than returned as a dict because the probes are
    the *runtime's own* code (`capture_transcripts`, `run_daily`, `calendar_io`)
    and read `os.environ` directly. A rehearsal that classified a merged dict
    while the probe read the real environment would be reporting on a different
    run than the one the workflow performs.

    Empty values are not credentials. A template copied and not yet filled in
    must leave those surfaces `missing` rather than `invalid`, which is what
    sending `""` to a provider would produce.
    """
    taken: list[str] = []
    for key, value in env_file_values(text, source).items():
        if not value or key in environ:
            continue
        environ[key] = value
        taken.append(key)
    return taken


def apply_env_file(path: Path, environ: dict[str, str]) -> list[str]:
    """`apply_env_text` against a path, refusing one that is not a file."""
    if not path.is_file():
        raise SystemExit(f"FAIL: rehearse: --env-file {path} is not a readable file")
    return apply_env_text(path.read_text(encoding="utf-8"), str(path), environ)


def exit_code(rows: list[Row], *, require_all: bool) -> int:
    """`1` when a release would be reaching a surface it cannot reach.

    A rejected credential is always fatal: the surface is configured, so the
    runtime will try it and fail at 06:00 rather than here. A missing one is
    fatal only under `--require-all`, because "this checkout is not configured
    for the calendar" is a legitimate local state and not a defect.

    `invalid` from an env-less check counts like any other: it is a fact about
    the repository that a release would be shipping.
    """
    if any(row.state == INVALID for row in rows):
        return 1
    if require_all and any(
        row.state == MISSING and not row.optional for row in rows
    ):
        return 1
    return 0


def _probe_gemini(timeout: int) -> Probe:
    def run() -> tuple[bool | None, str]:
        import capture_transcripts as ct

        ids, error = ct.list_gemini_models(timeout=timeout)
        if error:
            return False, error
        return True, f"models.list: {len(ids)} generateContent model(s)"

    return run


def _probe_opencode(timeout: int) -> Probe:
    def run() -> tuple[bool | None, str]:
        import capture_transcripts as ct

        return ct.probe_opencode_cli(timeout=timeout)

    return run


def _probe_openrouter(timeout: int) -> Probe:
    def run() -> tuple[bool | None, str]:
        import capture_transcripts as ct

        return ct.probe_openrouter_key(timeout=timeout)

    return run


def _probe_search(backend_name: str) -> Probe:
    def run() -> tuple[bool | None, str]:
        import run_daily

        backend = run_daily.ALL_BACKENDS[backend_name]
        results = backend.search("basketball live stream", 1)
        # `last_error` is what makes the question answerable at all: an empty
        # result list means "the key was rejected" and "the query found nothing"
        # alike, and only one of those is a finding about the credential.
        if backend.last_error:
            return False, backend.last_error
        return True, f"answered with {len(results)} result(s) for a 1-result probe"

    return run


def _probe_calendar(timeout: int) -> Probe:
    def run() -> tuple[bool | None, str]:
        import calendar_io
        from calendar_config import get_calendar_id

        user_id = os.environ.get("COMPOSIO_USER_ID", "")
        connected = os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID", "")
        if user_id and connected:
            return False, (
                "COMPOSIO_USER_ID and COMPOSIO_CONNECTED_ACCOUNT_ID are both "
                "set — the request would be ambiguous about whose calendar it is"
            )
        scope = calendar_io.scope_arguments(user_id, connected)
        now = datetime.now(timezone.utc)
        try:
            events = calendar_io.list_events(
                os.environ.get("COMPOSIO_API_KEY", ""),
                scope,
                get_calendar_id(),
                time_min=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_max=(now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except calendar_io.CalendarError as exc:
            return False, str(exc)[:200]
        return True, f"read {len(events)} event(s) for the next day (no write)"

    return run


# Three ways a workflow can file an issue, kept apart by how ambiguous each is:
#
#   * `gh issue create|comment` — the CLI directly, which `self-improve.yml`
#     does, and which a script-name list alone would MISS.
#   * `gh_issue.py` / `corpus_flip_issue.py` — both exist only to file or comment,
#     so naming one is unambiguous. `gh_issue.py` is documented as "the one place
#     that opens or comments on a dedupe-keyed GitHub issue".
#   * `rung_health.py` — ambiguous: `probe` and `snapshot` never touch the
#     tracker and only `parked --file` files, so it needs the flag to count.
#     (A bare `--file` is not evidence: `runtime-daily.yml` also passes
#     `--file SKILL.md` to `opencode` as an attachment.)
GH_ISSUE_CLI = re.compile(r"gh issue (?:create|comment)\b")
UNAMBIGUOUS_FILERS = ("gh_issue.py", "corpus_flip_issue.py")
AMBIGUOUS_FILER = "rung_health.py"


PERMISSIONS_BLOCK = re.compile(r"^(\s*)permissions:\s*$", re.M)


def _files_issues(text: str) -> bool:
    if GH_ISSUE_CLI.search(text):
        return True
    if any(script in text for script in UNAMBIGUOUS_FILERS):
        return True
    return AMBIGUOUS_FILER in text and "--file" in text


def _grants_issues_write(text: str) -> bool:
    """True when a `permissions:` block actually grants `issues: write`.

    Structured rather than a substring search, and the difference is not
    theoretical: `corpus-refresh.yml`'s own header comment contains the words
    "`contents: read` plus `issues: write`", so a substring check reported the
grant as present **after it had been deleted from the YAML** — a rule reading a
string that no block writes, which is the trap this repository keeps finding.
It was found the same way the other ones were: by falsifying the guard and
watching it not move.

    Block form only. An inline `permissions: {issues: write}` would not be seen,
    and this repository does not use one; the precise per-job pins are in
    `tests/test_rung_health.py` and `tests/test_corpus_flip_issue.py`.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = PERMISSIONS_BLOCK.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break  # dedented: the block ended
            key, _, value = stripped.partition(":")
            if key.strip() == "issues" and value.strip() == "write":
                return True
    return False


def probe_issue_grant(root: Path | None = None) -> tuple[bool | None, str]:
    """Offline: every workflow that files an issue declares `issues: write`.

    Static on purpose, and it is the half of the GitHub surface the token cannot
    answer. On a runner `GITHUB_TOKEN` is **always** set, and `gh api user`
    succeeds for a token scoped read-only — so `github-issues` above can only
    ever prove *identity*, while whether the surface actually works is decided by
    the workflow's `permissions:` block. \"It authenticated\" and \"it may file\"
    are different sentences, and only the second one gates a release.

    File-level rather than job-level: the precise per-job pins live in
    `tests/test_rung_health.py` and `tests/test_corpus_flip_issue.py`, which parse
    the job dictionaries. This reports the shape of the repository for a human
    release check; the tests are the enforcement.
    """
    base = (root or REPO_ROOT) / ".github" / "workflows"
    workflows = sorted(base.glob("*.yml"))
    if not workflows:
        return None, f"no workflows under {base}"
    filers: list[str] = []
    missing: list[str] = []
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if not _files_issues(text):
            continue
        filers.append(path.name)
        if not _grants_issues_write(text):
            missing.append(path.name)
    if not filers:
        return None, "no workflow files an issue, so nothing to grant"
    if missing:
        return False, (
            f"{', '.join(missing)} files an issue without `issues: write` — "
            "the run would read fine and the issue would never appear"
        )
    return True, f"{', '.join(filers)} declare `issues: write`"


def _probe_github(timeout: int) -> Probe:
    def run() -> tuple[bool | None, str]:
        executable = shutil.which("gh")
        if not executable:
            # Not a bad token: this machine cannot ask the question.
            return None, "no `gh` on PATH, so the token cannot be probed"
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        try:
            completed = subprocess.run(
                [executable, "api", "user", "--jq", ".login"],
                capture_output=True,
                text=True,
                timeout=timeout,
                # The token is passed explicitly rather than inherited, so what
                # is probed is the credential the workflow will use and not
                # whatever `gh` happens to be logged in as locally.
                env={**os.environ, "GH_TOKEN": token},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return False, (detail.splitlines() or ["gh api user failed"])[0][:200]
        login = (completed.stdout or "").strip()
        # The limit is stated in the result rather than left implicit, because a
        # bare "valid" here would read as "this repository can file issues" — and
        # it cannot say that. See `probe_issue_grant` for the half that can.
        return True, (
            f"authenticated as {login or 'a user'} (identity only: the write "
            "grant is the workflow's `permissions:` block)"
        )

    return run


def build_checks(*, timeout: int = 60) -> list[Check]:
    """Every surface a release depends on, in the order a failure hurts."""
    return [
        Check(
            name="calendar",
            env=(
                "BASKETBALL_CALENDAR_ID",
                "COMPOSIO_API_KEY",
                "COMPOSIO_USER_ID",
                "COMPOSIO_CONNECTED_ACCOUNT_ID",
            ),
            what="read and write the Google Calendar the runtime drives",
            probe=_probe_calendar(timeout),
        ),
        Check(
            name="github-issues",
            env=("GITHUB_TOKEN", "GH_TOKEN"),
            what="file the self-improvement and corpus-flip issues",
            probe=_probe_github(timeout),
        ),
        Check(
            name="github-issues:grant",
            # No credential: this is a fact about the repository, and it is the
            # one that decides the surface on a runner.
            env=(),
            what="the workflows that file issues are allowed to",
            probe=probe_issue_grant,
            structural=True,
        ),
        Check(
            name="llm:gemini",
            env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            what="rung 1 of the model ladder",
            probe=_probe_gemini(timeout),
        ),
        Check(
            name="llm:opencode",
            env=(
                "OPENCODE_ZEN_API_KEY",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "OPENAI_API_KEY",
            ),
            what="rung 2 of the model ladder (the CLI, not just the key)",
            probe=_probe_opencode(timeout),
        ),
        Check(
            name="llm:openrouter",
            env=("OPENROUTER_API_KEY",),
            what="rung 3 of the model ladder",
            probe=_probe_openrouter(timeout),
        ),
        Check(
            name="search:exa-mcp",
            env=("EXA_API_KEY",),
            what="Phase 0's first search backend",
            probe=_probe_search("exa-mcp"),
        ),
        Check(
            name="search:exa-mcp-keyless",
            # Free keyless tier of the hosted Exa MCP server: no credential to
            # be missing, so `env` stays empty and `assess` probes it directly.
            # The probe is real even when EXA_API_KEY is set — availability (a
            # ladder-routing fact) and usability (this question) are apart.
            # NOT structural: it talks to a service, so `--offline` must skip it.
            env=(),
            what="Phase 0's zero-secret search rung (hosted mcp.exa.ai, free keyless tier)",
            probe=_probe_search("exa-mcp-keyless"),
        ),
        Check(
            name="search:tinyfish",
            env=("TINYFISH_API_KEY",),
            what="Phase 0's second search backend",
            probe=_probe_search("tinyfish"),
        ),
        Check(
            name="render:firecrawl",
            env=("FIRECRAWL_API_KEY",),
            what="render rung 1, the hosted escape hatch for SPAs like magenta.tv",
            # No live probe, deliberately. The ladder treats a `blocked` status as
            # "climb", so a 401 from the *provider* (a dead key) and a 403 from
            # the *target* (the ladder working as designed) arrive here looking
            # the same — and a probe costs a paid credit either way. The keyless
            # `urllib` rung means the ladder is never unavailable, so a dead key
            # degrades to rung 2 rather than breaking a run. Reporting presence
            # is the honest half of the answer, and `unverified` says so rather
            # than implying a verdict.
            probe=None,
            optional=True,
        ),
        Check(
            name="youtube-data-api",
            env=("YOUTUBE_API_KEY",),
            what="optional: only the Data API path; the HTML live filter needs no key",
            probe=None,
            optional=True,
        ),
    ]


def render_markdown(rows: list[Row], *, counts: dict[str, int]) -> str:
    """The report as a PR-ready table.

    A renderer rather than prose in the release notes, for the reason this repo
    keeps re-learning: two formatters for one artifact drift, and the misleading
    one wins. `--markdown` is what goes in the release PR, so the table pasted
    there and the table the command printed cannot disagree.
    """
    lines = [
        "## Production rehearsal",
        "",
        "| Surface | State | Detail |",
        "|---|---|---|",
    ]
    for row in rows:
        # A pipe in a probe's own message (an HTTP error body, say) would split
        # the row into extra columns and misalign every row after it.
        detail = row.detail.replace("|", "\\|")
        lines.append(f"| `{row.name}` | {row.state} | {detail} |")
    lines += [
        "",
        f"{counts[VALID]} valid, {counts[INVALID]} invalid, "
        f"{counts[MISSING]} missing, {counts[UNVERIFIED]} unverified "
        f"(of {len(rows)} surface(s)).",
        "",
        "`invalid` is a credential that is set and **rejected** — the one state a",
        "release must not proceed past. `missing` is a surface this checkout is not",
        "configured for; under `--require-all` it is fatal too. `unverified` means no",
        "probe exists for that surface, which is reported rather than inferred.",
    ]
    return "\n".join(lines)


def _live_prober(check: Check) -> tuple[bool | None, str]:
    assert check.probe is not None
    try:
        return check.probe()
    except Exception as exc:  # noqa: BLE001 - a probe that raises is a finding
        # A probe crashing is information about the surface, not a reason to
        # stop reporting the other seven.
        return False, f"probe raised {type(exc).__name__}: {exc}"[:200]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-release production rehearsal: which surfaces are configured, "
            "and which actually accept their credential."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="also fail when a required credential is not set at all",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--list", action="store_true", help="list the surfaces, offline")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="probe only the checks that need no credential (the CI half)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="print the report as a PR-ready table on stdout",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="read credentials from a dotenv file (never discovered implicitly)",
    )
    args = parser.parse_args()

    if args.json and args.markdown:
        # Both write the payload to stdout, so one would corrupt the other — and
        # a `json.load` failure three steps downstream is not where the mistake
        # was made. (This repository has paid for that confusion four times; see
        # the `--json leaves stdout as the payload alone` notes.)
        parser.error("--json and --markdown both write stdout; pick one")

    checks = build_checks(timeout=args.timeout)

    if args.list:
        for check in checks:
            tier = "optional" if check.optional else "required"
            source = f"env: {', '.join(check.env)}" if check.env else "repository"
            print(f"OK: rehearse: {check.name:20s} {tier:8s} {check.what} ({source})")
        return

    # After `--list`, so listing the surfaces stays possible on a machine with no
    # credential file at all — that is what makes it a diagnostic.
    if args.env_file is not None:
        supplied = apply_env_file(args.env_file, os.environ)
        print(
            f"OK: rehearse: {args.env_file}: supplied {len(supplied)} variable(s) "
            f"({', '.join(supplied) if supplied else 'none'}) — an already-set "
            "environment variable wins over the file",
            file=sys.stderr,
        )

    probed = structural(checks) if args.offline else checks
    if args.offline:
        print(
            f"OK: rehearse: offline — probed {len(probed)} structural check(s); "
            f"{len(checks) - len(probed)} credential surface(s) not consulted, so "
            "this run says nothing about them",
            file=sys.stderr,
        )

    rows = assess(probed, os.environ, prober=_live_prober)
    code = exit_code(rows, require_all=args.require_all)

    # `OK` / `NO` / `FAIL` per row is the vocabulary `run_daily --check-backends`
    # already uses, and the distinction earns its keep here: `NO` says this
    # checkout is not configured for that surface, which is a legitimate state
    # and not a failure, while `FAIL` says a credential was *rejected* — the one
    # outcome a release must not proceed past.
    label = {VALID: "OK", UNVERIFIED: "OK", INVALID: "FAIL", MISSING: "NO"}

    # Human lines on stderr in both modes, like every other --json in this repo:
    # a summary printed to stdout before the payload makes json.load raise.
    for row in rows:
        print(
            f"{label[row.state]:4s} {row.name:20s} {row.state:10s} {row.detail}",
            file=sys.stderr,
        )
    counted = {
        state: sum(1 for row in rows if row.state == state)
        for state in (VALID, INVALID, MISSING, UNVERIFIED)
    }
    verdict = "OK" if code == 0 else "FAIL"
    print(
        f"{verdict}: rehearse: {counted[VALID]} valid, {counted[INVALID]} invalid, "
        f"{counted[MISSING]} missing, {counted[UNVERIFIED]} unverified "
        f"(of {len(rows)} surface(s))"
        + (
            ""
            if code == 0
            else " — a rejected credential is configured, so the runtime would "
            "try it and fail in production"
        ),
        file=sys.stderr,
    )

    if args.markdown:
        print(render_markdown(rows, counts=counted))
    elif args.json:
        print(
            json.dumps(
                {
                    "surfaces": [row.__dict__ for row in rows],
                    "counts": counted,
                    "require_all": args.require_all,
                    "offline": args.offline,
                    # The path, never the values: this payload is meant to be
                    # pasted into a PR, and a release note is not a secret store.
                    "env_file": str(args.env_file) if args.env_file else None,
                    "ok": code == 0,
                },
                indent=2,
            )
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
