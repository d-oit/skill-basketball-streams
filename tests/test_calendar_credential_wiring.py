"""Pins for the calendar credential, now that its transport is Composio.

The change this guards is a *substitution*, and every failure mode of a
substitution is silent:

* a step still exporting `GOOGLE_OAUTH_ACCESS_TOKEN` from `steps.auth.outputs`
  reads a credential nothing mints — the variable is simply empty, and
  `calendar_io.py` reports "no Composio API key", which reads as a missing secret
  rather than as a stale step;
* a `google-github-actions/auth@v2` step that nothing consumes is worse than
  useless: it is a step that can only fail or be ignored, and it was one of the
  two places a fork was told to configure a GCP project it no longer needs;
* a workflow that sets `COMPOSIO_API_KEY` but not `COMPOSIO_USER_ID` looks
  configured and is not — a call scoped to no account fails as "no connected
  account", which reads as a broken key;
* and SKILL.md is the *product*. Naming `googleCalendarListEvents` in it after
  this change would document a tool the repo's own credential cannot serve —
  the "documented artifact with no producer" trap, one layer up. So the doc
  checks below are not prose hygiene: every `GOOGLECALENDAR_*` slug the product
  names must be a `TOOL_*` constant `calendar_io.py` actually calls.

Parsed with regex rather than PyYAML: the production scripts stay stdlib-only.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts import calendar_io

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUNTIME_DAILY = WORKFLOWS / "runtime-daily.yml"
SELF_IMPROVE = WORKFLOWS / "self-improve.yml"

JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)
STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
# A slug as Composio spells it: the toolkit prefix is uppercase.
SLUG = re.compile(r"GOOGLECALENDAR_[A-Z_]+")
# The MCP tool names this repository used to document. `googleCalendar` is
# camelCase and cannot be confused with a slug.
LEGACY = re.compile(r"googleCalendar\w*")

DOCS = (
    "SKILL.md",
    "README.md",
    "SETUP.md",
    "docs/runtime.md",
    "live-stream-runtime-spec.md",
    "scripts/README.md",
    "scripts/runtime_eval.py",
    "references/calendar-setup.md",
    "references/implementation-notes.md",
    "references/search-backends.md",
)

# The variables the CLI reads, taken from the argument parser rather than
# restated — a workflow may set a name this module does not read, and that is
# exactly the defect (an exported variable that reaches nothing).
CALENDAR_ENV_VARS = {
    "COMPOSIO_API_KEY",
    "COMPOSIO_USER_ID",
    "COMPOSIO_CONNECTED_ACCOUNT_ID",
    "BASKETBALL_CALENDAR_ID",
}


def _workflow(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _jobs(text: str) -> dict[str, str]:
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    return {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }


def _steps(job: str) -> list[str]:
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _runtime_steps() -> list[str]:
    return _steps(_jobs(_workflow(RUNTIME_DAILY))["runtime"])


def _without_comments(text: str) -> str:
    """Prose describing a defect is not the defect.

    Every explanatory comment here names the thing it replaced, so a naive
    substring search would fail on the comment that explains the fix.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _calendar_steps() -> list[str]:
    steps = [
        step
        for step in _runtime_steps()
        if "scripts/calendar_io.py" in _without_comments(step)
    ]
    assert len(steps) >= 2, "expected the list and apply steps to call calendar_io.py"
    return steps


class TestTheCredentialIsComposios:
    def test_no_step_reads_a_google_oauth_token(self):
        """`steps.auth` no longer exists, so the variable can only ever be empty."""
        for path in (RUNTIME_DAILY, SELF_IMPROVE):
            text = _without_comments(_workflow(path))
            assert "GOOGLE_OAUTH_ACCESS_TOKEN" not in text, path.name
            assert "steps.auth" not in text, path.name

    def test_no_step_authenticates_to_google(self):
        """A step nothing consumes can only fail or be ignored."""
        for path in (RUNTIME_DAILY, SELF_IMPROVE):
            assert "google-github-actions/auth" not in _without_comments(
                _workflow(path)
            ), path.name

    def test_the_gcp_variables_are_gone(self):
        for path in (RUNTIME_DAILY, SELF_IMPROVE):
            text = _without_comments(_workflow(path))
            assert "GCP_WORKLOAD_IDENTITY_PROVIDER" not in text, path.name
            assert "GCP_SERVICE_ACCOUNT" not in text, path.name

    def test_no_workflow_requests_an_oidc_token(self):
        """`id-token: write` was for the removed auth step, in two jobs.

        Nothing here performs OIDC any more. Re-adding the permission is easy to
        do "just in case" and grants every step in the job a capability nobody
        decided it should have. If a future step genuinely needs it, delete this
        test and say why in the same change rather than restoring it silently.
        """
        for path in sorted(WORKFLOWS.glob("*.yml")):
            assert "id-token" not in _without_comments(_workflow(path)), path.name

    def test_the_self_improve_workflow_holds_no_calendar_credential(self):
        """Its security model is an *absence*, so the absence is what gets pinned.

        This workflow may edit SKILL.md. If it could also reach the calendar,
        "rewrite its own instructions" would be a route to a subscriber's
        calendar — the reason the two authorities live in different workflows.
        The claim is now "no Composio credential is passed", not the older "no
        `id-token: write` and no Google auth step", which stopped being the
        guarantee the moment the credential became a key in `env`.
        """
        text = _without_comments(_workflow(SELF_IMPROVE))
        assert "COMPOSIO_API_KEY" not in text
        assert "COMPOSIO_USER_ID" not in text

    def test_no_workflow_sets_a_composio_variable_the_cli_does_not_read(self):
        """A variable exported to a step that never reads it is not a credential."""
        text = _without_comments(_workflow(RUNTIME_DAILY))
        named = set(re.findall(r"\b(COMPOSIO_[A-Z_]+)\s*:", text))
        assert named, "expected the runtime job to pass a Composio credential"
        assert named <= CALENDAR_ENV_VARS, named - CALENDAR_ENV_VARS

    def test_every_calendar_step_receives_both_the_key_and_the_account(self):
        """The API key alone scopes to nobody, and fails as a broken key."""
        for step in _calendar_steps():
            assert "COMPOSIO_API_KEY" in step
            assert "COMPOSIO_USER_ID" in step

    def test_the_calendar_steps_still_receive_the_calendar_id(self):
        for step in _calendar_steps():
            assert "BASKETBALL_CALENDAR_ID" in step

    def test_the_calendar_steps_remain_gated_on_the_write_switch(self):
        """The credential must not be the thing that decides whether a run may write."""
        for step in _calendar_steps():
            assert "ENABLE_CALENDAR_WRITES == 'true'" in step

    def _env_vars_the_cli_reads(self) -> set[str]:
        source = (REPO_ROOT / "scripts" / "calendar_io.py").read_text(encoding="utf-8")
        return set(re.findall(r'os\.environ\.get\(\s*"([A-Z_]+)"', source))

    def test_the_cli_reads_exactly_the_advertised_variables(self):
        """A name a workflow exports and the CLI never reads is not a credential.

        Both directions matter: an exported variable that reaches nothing is the
        defect, and a variable the CLI reads that nothing exports is the same bug
        seen from the other side.
        """
        assert self._env_vars_the_cli_reads() == CALENDAR_ENV_VARS


class TestTheProductNamesOnlyToolsThatExist:
    def _slugs_in(self, relative: str) -> set[str]:
        return set(SLUG.findall((REPO_ROOT / relative).read_text(encoding="utf-8")))

    def test_every_slug_the_docs_name_is_one_this_module_calls(self):
        available = {
            value
            for name, value in vars(calendar_io).items()
            if name.startswith("TOOL_") and isinstance(value, str)
        }
        assert available, "calendar_io.py must expose its tool slugs as TOOL_* names"
        named: dict[str, set[str]] = {
            doc: self._slugs_in(doc) for doc in DOCS
        }
        unknown = {
            doc: slugs - available for doc, slugs in named.items() if slugs - available
        }
        assert not unknown, f"docs name tools this module never calls: {unknown}"

    def test_the_docs_do_name_the_tools_this_module_uses(self):
        """The other direction: an unmentioned tool is one nobody can discover."""
        available = {
            value
            for name, value in vars(calendar_io).items()
            if name.startswith("TOOL_") and isinstance(value, str)
        }
        named = set().union(*(self._slugs_in(doc) for doc in DOCS))
        assert available <= named, available - named

    def test_no_product_doc_still_names_an_mcp_calendar_tool(self):
        offenders = {
            doc: sorted(set(LEGACY.findall((REPO_ROOT / doc).read_text(encoding="utf-8"))))
            for doc in DOCS
        }
        non_empty = {doc: found for doc, found in offenders.items() if found}
        assert not non_empty, f"stale MCP tool names remain: {non_empty}"

    def test_skill_frontmatter_allows_exactly_the_calendar_tools_it_names(self):
        """`allowed-tools` is a contract; a tool in it that does not exist is a hole."""
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        line = next(
            ln for ln in frontmatter.splitlines() if ln.startswith("allowed-tools:")
        )
        allowed = set(line.split(":", 1)[1].split())
        available = {
            value
            for name, value in vars(calendar_io).items()
            if name.startswith("TOOL_") and isinstance(value, str)
        }
        calendar_allowed = {name for name in allowed if name.startswith("GOOGLECALENDAR_")}
        assert calendar_allowed == available, (calendar_allowed, available)


class TestTheCredentialDocsAgreeWithTheCode:
    def test_the_secrets_table_names_composio_and_not_gcp(self):
        table = (REPO_ROOT / "docs/runtime.md").read_text(encoding="utf-8")
        section = table.split("## Secrets and variables", 1)[1].split("\n## ", 1)[0]
        assert "COMPOSIO_API_KEY" in section
        assert "COMPOSIO_USER_ID" in section
        assert "GCP_WORKLOAD_IDENTITY_PROVIDER" not in section
        assert "GCP_SERVICE_ACCOUNT" not in section

    def test_the_workflow_comments_state_why_the_auth_step_is_absent(self):
        """Otherwise the next reader re-adds it, reasonably, as a missing step."""
        text = _workflow(RUNTIME_DAILY)
        assert "NO Google auth step" in text
