"""Every step that *reports* a rung's configuration must see its credential.

Written after the `render-arena` rung shipped with `FIRECRAWL_API_KEY` wired
into the ladder step but not into the preflight step that sits two steps
above it. The preflight ran `--check-backends`, printed

    NO   backend render-arena: no rendering credential; the arena cannot be read

on a runner where the secret *was* registered, and the job stayed green. That
rung is the only one that can read `magenta.tv/sport`, so the report was a
false negative about the job's own most important source — and it is exactly
the shape of failure this repository has been bitten by four times already: a
step that references a credential it cannot see, so a quiet configuration
reads as a quiet day.

The invariant is **parity over the union**: for every variable any step
*interrogates* about a rung, some step that runs that interrogation must
export it. A variable used only by a rung's own implementation is not in
scope — this is about the steps that ask "is this configured?".

Parsed with regex rather than PyYAML: the production scripts stay stdlib-only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
ENV_VAR = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")
#: The ONE flag that reports search-backend availability. Scoped to a flag rather
#: than to a whole job because `--check-rungs` is about *model* credentials, and
#: requiring search keys there would be a different (and wrong) invariant.
SEARCH_BACKEND_PROBE = "--check-backends"


def _steps(text: str) -> list[tuple[str, str]]:
    """`[(step_name, step_body)]` for every `run:` step in the workflow."""
    starts = [m.start() for m in STEP_START.finditer(text)]
    out: list[tuple[str, str]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        body = text[start:end]
        name = body.splitlines()[0].strip("- name: ").strip()
        out.append((name, body))
    return out


def _probe_steps() -> list[tuple[str, set[str]]]:
    """`[(step_name, env_vars_exported)]` for the configuration-probe steps."""
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    found = []
    for name, body in _steps(text):
        run = body.split("run:", 1)
        if len(run) < 2:
            continue
        script = run[1]
        if SEARCH_BACKEND_PROBE not in script:
            continue
        env = body.split("env:", 1)
        exported = set(ENV_VAR.findall(env[1].split("run:", 1)[0])) if len(env) > 1 else set()
        found.append((name, exported))
    assert found, "no configuration-probe step found — the invariant would be vacuous"
    return found


def _credentials_the_runtime_reads() -> set[str]:
    from scripts.run_daily import ALL_BACKENDS

    return {b.env_var for b in ALL_BACKENDS.values() if b.env_var}


@pytest.mark.parametrize("step_name,exported", _probe_steps())
def test_a_probe_step_cannot_report_a_rung_it_cannot_see(step_name, exported):
    """A probe that reads a rung's credential list must be given that credential.

    The failure is not a red build; it is a green build reporting that the one
    rung which can read the free arena is unconfigured, on a runner where it is
    configured. So this asserts the *wiring*, not the outcome.
    """
    for var in _credentials_the_runtime_reads():
        assert var in exported, (
            f"step {step_name!r} reports every search backend, so it must receive "
            f"{var}: without it the probe prints 'no rendering credential' for a "
            f"rung that is configured, and the job looks like a quiet day"
        )


def test_the_render_arena_credential_reaches_both_phase_0_steps():
    """Named explicitly: the two steps must not drift apart again.

    `--check-backends` and the ladder run are the same subsystem reading the
    same rung. This shipped once with only the second wired, and the report
    contradicted the run it was guarding.
    """
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    for name, body in _steps(text):
        if "run_daily.py" not in body:
            continue
        assert "FIRECRAWL_API_KEY" in body, (
            f"step {name!r} calls run_daily.py, which serves the render-arena "
            f"rung, but never receives its credential"
        )
