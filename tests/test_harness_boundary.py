#!/usr/bin/env python3
"""test_harness_boundary.py — pins the do-harness adoption boundary.

`docs/do-harness.md` is the adoption plan of record. Its Step 6 originally
intended to collapse grading onto `do-harness eval` and demote
`scripts/validate.py --check evals`. That step is **withdrawn**, because
`do-harness eval` resolves skills only under `.agents/skills` (a hardcoded
path — `error: skill '...' not found under .agents/skills`, with no config key
to widen it). It grades `harness` and `skill-creator`, never the root product
skill, so demoting the product graders would leave `evals/evals.json` ungraded.

These tests are the tripwire for that. They assert the product graders are
still wired up rather than that a document still says so, and that every
invariant names a sensor that actually exists.

The TOML is parsed with a regex rather than `tomllib`: CI runs Python 3.9 and
`tomllib` arrived in 3.11. The config format is regular enough that the regex
is faithful, and it keeps the suite dependency-free.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "do-harness.toml"
INVARIANTS = ROOT / "plans" / "invariants.json"
VERIFY_WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
COMMITLINT = ROOT / "scripts" / "check-commitlint.sh"

INVARIANT_KEYS = {"invariant", "rationale", "sensor", "category"}


# --------------------------------------------------------------------------
# TOML extraction (regex, 3.9-safe)
# --------------------------------------------------------------------------


def sensor_names(toml: str) -> list[str]:
    """Names in declaration order from every `[[sensors]]` table."""
    return re.findall(r'\[\[sensors\]\]\s*\nname\s*=\s*"([^"]+)"', toml)


def array_values(toml: str, key: str) -> list[str]:
    """Every string inside a `key = [ ... ]` array, wherever it appears.

    Multi-line arrays are handled because the config uses them heavily.
    """
    names: list[str] = []
    for match in re.finditer(rf"^{key}\s*=\s*\[(.*?)\]", toml, re.S | re.M):
        names.extend(re.findall(r'"([^"]+)"', match.group(1)))
    return names


def signal_sets(toml: str) -> dict[str, list[str]]:
    """`[signal-sets]` entries as {set name: [sensor names]}."""
    block = re.search(r"^\[signal-sets\]\s*\n(.*)", toml, re.S | re.M)
    if block is None:
        return {}
    sets: dict[str, list[str]] = {}
    for match in re.finditer(r"^(\w+)\s*=\s*\[(.*?)\]", block.group(1), re.S | re.M):
        sets[match.group(1)] = re.findall(r'"([^"]+)"', match.group(2))
    return sets


@pytest.fixture(scope="module")
def config_text() -> str:
    assert CONFIG.exists(), "do-harness.toml is missing"
    return CONFIG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def declared(config_text: str) -> list[str]:
    return sensor_names(config_text)


# --------------------------------------------------------------------------
# The config actually declares sensors (the vacuous-pass trap)
# --------------------------------------------------------------------------


def test_config_declares_at_least_one_sensor(declared: list[str]) -> None:
    """The generic pack ships zero sensors: a `verify` pass then executes
    nothing and is evidence of nothing."""
    assert declared, (
        "do-harness.toml declares no [[sensors]]; `do-harness verify` would "
        "exit 0 having run nothing, which is a vacuous pass, not evidence"
    )


def test_sensor_names_are_unique(declared: list[str]) -> None:
    duplicates = sorted({n for n in declared if declared.count(n) > 1})
    assert not duplicates, f"duplicate sensor names: {duplicates}"


def test_every_sensor_has_a_command(config_text: str, declared: list[str]) -> None:
    """A sensor without `argv` cannot run, and do-harness treats a missing
    command as nothing to do rather than a failure."""
    blocks = config_text.split("[[sensors]]")[1:]
    assert len(blocks) == len(declared)
    for name, block in zip(declared, blocks):
        assert re.search(r"^argv\s*=\s*\[", block, re.M), f"sensor {name!r} has no argv"


# --------------------------------------------------------------------------
# Signal sets and hooks only reference sensors that exist
# --------------------------------------------------------------------------


def test_signal_sets_reference_only_declared_sensors(
    config_text: str, declared: list[str]
) -> None:
    sets = signal_sets(config_text)
    assert sets, "no [signal-sets] declared; verify --set would select nothing"
    for name, members in sets.items():
        unknown = [m for m in members if m not in declared]
        assert not unknown, f"signal-set {name!r} names undeclared sensor(s): {unknown}"


def test_verification_set_is_non_empty(config_text: str) -> None:
    assert signal_sets(config_text).get("verification"), (
        "the `verification` set is empty or missing; `verify --set verification` "
        "would pass vacuously"
    )


def test_hook_references_only_declared_sensors(
    config_text: str, declared: list[str]
) -> None:
    """A hook naming a sensor that does not exist cannot be satisfied."""
    for key in ("pre-commit", "pre-push"):
        for member in array_values(config_text, key):
            assert member in declared, f"[hooks].{key} names undeclared sensor {member!r}"


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def invariants() -> list[dict]:
    data = json.loads(INVARIANTS.read_text(encoding="utf-8"))
    assert isinstance(data, list), "plans/invariants.json must be a bare array"
    return data


def test_invariants_is_a_bare_array(invariants: list[dict]) -> None:
    """do-harness `seed` rejects an object wrapper. An `{"invariants": [...]}`
    shape makes `init`/`seed` fail outright."""
    assert invariants, "plans/invariants.json is empty"


def test_every_invariant_carries_the_expected_keys(invariants: list[dict]) -> None:
    for i, entry in enumerate(invariants):
        assert isinstance(entry, dict), f"invariants[{i}] is not an object"
        missing = INVARIANT_KEYS - entry.keys()
        assert not missing, f"invariants[{i}] missing {sorted(missing)}"
        for key in INVARIANT_KEYS:
            assert isinstance(entry[key], str) and entry[key].strip(), (
                f"invariants[{i}].{key} must be a non-empty string"
            )


def test_invariants_are_unique(invariants: list[dict]) -> None:
    statements = [entry["invariant"] for entry in invariants]
    duplicates = sorted({s for s in statements if statements.count(s) > 1})
    assert not duplicates, f"duplicate invariant statements: {duplicates}"


def test_every_invariant_names_a_declared_sensor(
    invariants: list[dict], declared: list[str]
) -> None:
    """An invariant pointing at a sensor that does not exist is guarded by
    nothing while looking guarded — worse than naming no sensor at all."""
    dangling = sorted({e["sensor"] for e in invariants if e["sensor"] not in declared})
    assert not dangling, (
        f"invariants name sensor(s) absent from do-harness.toml: {dangling}"
    )


# --------------------------------------------------------------------------
# The Step 6 boundary: do-harness eval must not replace the product graders
# --------------------------------------------------------------------------


def test_adoption_plan_headings_are_intact() -> None:
    """The plan of record is the input to this boundary; if it vanishes the
    assertions below would pass against nothing."""
    plan = ROOT / "docs" / "do-harness.md"
    assert plan.exists(), "docs/do-harness.md (the adoption plan) is missing"
    text = plan.read_text(encoding="utf-8")
    assert "Step 6" in text
    assert "withdrawn" in text.lower(), (
        "docs/do-harness.md must record that Step 6 is withdrawn; leaving the "
        "original instruction in place invites someone to demote the product "
        "graders on the false belief that `do-harness eval` covers the root skill"
    )


def test_agents_contract_states_the_product_graders_stay() -> None:
    """AGENTS.md is the feedforward half: it must tell an agent that
    `do-harness eval` does not grade the root skill."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "never" in text and "`.agents/skills" in text, (
        "AGENTS.md must state that `do-harness eval` resolves skills only under "
        ".agents/skills and never grades the root skill"
    )
    for command in ("scripts/validate.py", "scripts/runtime_eval.py", "pytest"):
        assert command in text, f"AGENTS.md must name the product grader {command!r}"


def test_verify_workflow_keeps_the_product_graders() -> None:
    """The concrete form of the boundary: CI must run the four product graders
    *and* `do-harness eval`, not swap one for the other."""
    assert VERIFY_WORKFLOW.exists(), ".github/workflows/verify.yml is missing"
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")

    assert "do-harness eval" in workflow, "verify.yml must run `do-harness eval`"
    for grader in (
        "scripts/validate.py",
        "scripts/runtime_eval.py",
        "scripts/synthesise_eval_case.py",
    ):
        assert grader in workflow, (
            f"verify.yml must still run {grader!r}; `do-harness eval` does not "
            "grade the root skill, so dropping a product grader leaves it ungraded"
        )


def test_verify_workflow_reports_harness_metrics() -> None:
    """`metrics` is where a *halted* sensor surfaces. Without it, a sensor stuck
    on 3 consecutive failures is reported failed without being run, and the red
    looks permanent and unexplained."""
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    assert "do-harness metrics" in workflow

    # It must run even when verify itself failed — that is when the strike
    # report is most useful. Slice up to the next step rather than on the first
    # hyphen, which appears inside `do-harness`.
    block = workflow.split("name: Harness trends", 1)[1]
    block = block.split("\n      - ", 1)[0]
    assert "if: always()" in block, (
        "the harness-trends step must run on failure too (if: always())"
    )


def test_agents_documents_the_strike_escape_hatch() -> None:
    """The halt behaviour is surprising; a reader who does not know about it
    will conclude the sensor is permanently broken and silence it."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "halted" in text
    assert "do-harness errors clear" in text


def test_verify_workflow_pins_the_harness_version() -> None:
    """A floating `latest` would let a harness release change the gate silently.

    The pin may be inline (`--version v0.1.0`) or hoisted into `env:` and
    referenced (`--version "$DO_HARNESS_VERSION"`); the latter is preferred, so
    both forms are accepted and only a *missing* pin fails.
    """
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")

    assert "--version" in workflow, "verify.yml must pass --version to the installer"
    assert re.search(r"v\d+\.\d+\.\d+", workflow), (
        "verify.yml must pin an explicit do-harness version (vX.Y.Z)"
    )
    # Comments are stripped first: this file's own header explains why `latest`
    # is wrong, and that prose must not trip the check. (`ubuntu-latest` is a
    # runner image, not a harness version, so the check targets the version
    # source specifically rather than the bare word.)
    code = "\n".join(
        line for line in workflow.splitlines() if not line.lstrip().startswith("#")
    )

    env_pin = re.search(r"DO_HARNESS_VERSION:\s*(\S+)", code)
    inline_pin = re.search(r"--version\s+\"?(v\d+\.\d+\.\d+)\"?", code)
    assert env_pin or inline_pin, (
        "verify.yml must pin the harness version via DO_HARNESS_VERSION: vX.Y.Z "
        "or an inline `--version vX.Y.Z`"
    )
    if env_pin:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", env_pin.group(1)), (
            f"DO_HARNESS_VERSION must be a concrete pin, got {env_pin.group(1)!r}"
        )
    assert "| sh" in workflow, "verify.yml must install via the pinned installer"


def test_verify_workflow_pin_is_a_single_value() -> None:
    """Two different pins in one file is how a workflow ends up testing a
    version its docs do not mention."""
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    versions = set(re.findall(r"v\d+\.\d+\.\d+", workflow))
    assert len(versions) == 1, f"verify.yml pins more than one version: {sorted(versions)}"


def test_documented_pin_matches_the_workflow() -> None:
    """AGENTS.md and the plan both tell a human which version to install; if
    either drifts from CI, `verify` is green against a different binary than
    the one a maintainer runs."""
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    pinned = set(re.findall(r"v\d+\.\d+\.\d+", workflow))
    assert len(pinned) == 1
    version = pinned.pop()
    for doc in (ROOT / "AGENTS.md", ROOT / "docs" / "do-harness.md"):
        text = doc.read_text(encoding="utf-8")
        assert version in text, (
            f"{doc.relative_to(ROOT)} does not mention the pinned version {version}"
        )


# --------------------------------------------------------------------------
# Step 1 prerequisite — the fail-closed commit-msg hook
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The verify.yml step contract (CI-visible bugs only appear on a fresh checkout)
# --------------------------------------------------------------------------


def _steps(workflow: str) -> list[str]:
    """Split a workflow's job steps into blocks.

    Regex rather than `yaml.safe_load`: the production scripts stay stdlib-only
    (`requirements-dev.txt` promises that), and keeping these tests YAML-free
    means the suite still parses workflows if PyYAML ever goes missing again.
    """
    body = workflow.split("steps:", 1)[1] if "steps:" in workflow else workflow
    parts = re.split(r"\n      - ", body)
    return [p for p in parts[1:] if p.strip()]


def _step_named(steps: list[str], needle: str) -> str:
    matches = [s for s in steps if needle in s.split("\n", 1)[0]]
    assert len(matches) == 1, f"expected exactly one step matching {needle!r}, got {len(matches)}"
    return matches[0]


def test_verify_step_is_the_gate_and_is_not_tolerant() -> None:
    """`--strict --evidence` is the only green worth trusting, so its exit code
    must be authoritative. Marking it tolerant would make the gate decorative."""
    steps = _steps(VERIFY_WORKFLOW.read_text(encoding="utf-8"))
    step = _step_named(steps, "THE GATE")
    assert "do-harness verify" in step
    assert "--strict" in step
    assert "--evidence" in step

    # Comments first: the step's own comment says "intentionally NOT
    # continue-on-error", which a naive substring check would read as a hit.
    code = "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )
    assert "continue-on-error" not in code, "the gate must not be tolerant"


def test_eval_step_is_supplemental_and_tolerant() -> None:
    """`do-harness eval` grades only `.agents/skills` (see Step 6), and it
    segfaults on a fresh checkout once a state DB exists. It is supplemental, so
    a harness defect must not red the build — but it must stay visible."""
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    step = _step_named(_steps(workflow), "do-harness eval")
    assert "continue-on-error" in step, (
        "`do-harness eval` must be continue-on-error: it is supplemental, and it "
        "SIGSEGVs (exit 139) when a state database already exists"
    )
    assert "139" in workflow or "SIGSEGV" in workflow, (
        "verify.yml must document the eval segfault; an undocumented "
        "continue-on-error reads as leniency"
    )


def test_eval_runs_before_the_state_database_exists() -> None:
    """The segfault reproduces on the SECOND invocation once `.do-harness/
    agent_state.db` exists, so `eval` must run before anything creates it."""
    steps = _steps(VERIFY_WORKFLOW.read_text(encoding="utf-8"))
    # Match against the whole block, not the name line: the step name is prose
    # ("Verify (…) — THE GATE") and does not contain the command.
    eval_index = next(i for i, s in enumerate(steps) if "do-harness eval" in s)
    verify_index = next(i for i, s in enumerate(steps) if "do-harness verify" in s)
    assert eval_index < verify_index, (
        "eval must run before verify: verify --record creates the state database, "
        "after which eval segfaults"
    )


def test_verify_workflow_does_not_seed() -> None:
    """`seed` writes the state database. On an ephemeral CI runner it proves
    nothing the invariants-shape sensor does not already prove, and it is one of
    the ways to trigger the eval segfault."""
    workflow = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in workflow.splitlines() if not line.lstrip().startswith("#")
    )
    assert "do-harness seed" not in code


def test_status_step_is_reporting_not_gating() -> None:
    """A fresh ephemeral checkout has no recorded beats, so `status` exits 1 with
    `missing`. Failing the build over that would make the workflow red on every
    first run."""
    steps = _steps(VERIFY_WORKFLOW.read_text(encoding="utf-8"))
    step = _step_named(steps, "Status")
    assert "if: always()" in step or "||" in step, (
        "the status step must tolerate a `missing` verdict"
    )


def test_commitlint_script_exists_and_is_executable() -> None:
    """`do-harness hook install` writes a commit-msg hook that runs this script
    and blocks the commit when it is missing. Installing hooks before the script
    exists would make every later commit fail."""
    assert COMMITLINT.exists(), (
        "scripts/check-commitlint.sh is missing; installing the commit-msg hook "
        "would block every commit"
    )
    if sys.platform != "win32":
        mode = COMMITLINT.stat().st_mode
        assert mode & 0o111, "scripts/check-commitlint.sh must be executable"


def _run_commitlint(message: str, *args: str):
    """Invoke the script the way its usage documents it.

    The stdin form is **no `--message` flag at all**; `--message <path>` takes a
    file. Passing `--message -` is a usage error (exit 2), not a stdin alias.
    """
    import subprocess

    return subprocess.run(
        [str(COMMITLINT), *args],
        input=message,
        capture_output=True,
        text=True,
    )


def test_commitlint_accepts_the_documented_prefixes() -> None:
    """CONTRIBUTING.md documents eleven prefixes; the script must accept each
    one, or the hook is stricter than the documented contract."""
    prefixes = [
        "feat", "fix", "chore", "docs", "test", "refactor", "perf", "build",
        "ci", "style", "revert",
    ]
    for prefix in prefixes:
        proc = _run_commitlint(f"{prefix}: a valid subject\n")
        assert proc.returncode == 0, (
            f"check-commitlint.sh rejected the documented prefix {prefix!r}: "
            f"{proc.stderr.strip()}"
        )


def test_commitlint_accepts_a_scoped_and_a_breaking_subject() -> None:
    for subject in ("feat(cli): add a flag", "fix(api)!: drop the old field"):
        proc = _run_commitlint(subject + "\n")
        assert proc.returncode == 0, (
            f"check-commitlint.sh rejected {subject!r}: {proc.stderr.strip()}"
        )


def test_commitlint_rejects_an_unprefixed_subject() -> None:
    proc = _run_commitlint("just a subject with no prefix\n")
    assert proc.returncode == 1, "check-commitlint.sh accepted a subject with no prefix"


def test_commitlint_rejects_an_unknown_type() -> None:
    proc = _run_commitlint("wibble: not a real type\n")
    assert proc.returncode == 1


def test_commitlint_rejects_an_over_long_subject() -> None:
    proc = _run_commitlint("chore: " + "x" * 80 + "\n")
    assert proc.returncode == 1, "check-commitlint.sh accepted a >72 char subject"


def test_commitlint_ignores_comment_lines() -> None:
    proc = _run_commitlint("# a comment\nfeat: after a comment\n")
    assert proc.returncode == 0


def test_commitlint_does_not_block_gits_own_subjects() -> None:
    """git generates these itself. A hook that rejects them makes merges and
    reverts impossible, which is how a fail-closed hook gets uninstalled."""
    for subject in (
        "Merge branch 'main' into feat/x",
        "Revert \"feat: x\"",
        "fixup! feat: x",
        "squash! feat: x",
    ):
        proc = _run_commitlint(subject + "\n")
        assert proc.returncode == 0, (
            f"check-commitlint.sh blocked git's own subject {subject!r}"
        )


def test_commitlint_reads_a_message_file(tmp_path: Path) -> None:
    """The managed commit-msg hook passes `--message .git/COMMIT_EDITMSG`, so
    that path must actually be read."""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("feat: from a file\n", encoding="utf-8")
    proc = _run_commitlint("", "--message", str(msg))
    assert proc.returncode == 0, proc.stderr.strip()


def test_commitlint_fails_open_on_a_missing_message_file(tmp_path: Path) -> None:
    """Deliberate deviation from the fail-closed rule, pinned so it is a
    decision rather than an accident: a hook must not block a commit because it
    could not read its own input. The repository/script being missing is the
    fail-closed case, and do-harness itself enforces that one."""
    proc = _run_commitlint("", "--message", str(tmp_path / "absent"))
    assert proc.returncode == 0
    assert "allow" in proc.stderr.lower()


def test_commitlint_rejects_an_unknown_flag() -> None:
    proc = _run_commitlint("feat: x\n", "--nope")
    assert proc.returncode == 2, "an unknown flag must be a usage error, not a pass"
