"""Brave Search is excluded by project policy — this is what enforces it.

The ban was written into `references/search-backends.md` and `SKILL.md` and
nothing checked it, which is the pattern this repo keeps re-learning: *a rule
nothing enforces is not a rule*. A future contributor could add a `brave`
backend to `run_daily.py`, wire a `BRAVE_API_KEY` into `runtime-daily.yml`, and
all 1331 tests would still pass.

Two halves, because the failure can arrive from either direction:

* **the code half** — no script may carry a Brave credential, a Brave endpoint,
  or a rung registered under that name;
* **the documentation half** — the exclusion must stay stated where a
  contributor choosing a backend actually looks. Deleting the paragraph is a
  failure too, so the rule and its enforcement cannot drift apart.

The scanner is deliberately *not* a bare `/brave/i`: prose that states the ban
is not a violation, and a scan that flagged the sentence forbidding something
would make the ban impossible to write down. `TestTheScannerItself` is the
control — it fails if the predicate goes dead, so this file can never pass by
scanning for nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.run_daily import ALL_BACKENDS, LADDER

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
BACKENDS_DOC = REPO_ROOT / "references" / "search-backends.md"
SKILL_DOC = REPO_ROOT / "SKILL.md"

# The operational signatures of a Brave rung: its credential, its endpoint, or a
# backend whose registered name is "brave". A comment or a docstring saying
# "Brave is excluded" matches none of these.
BRAVE_RUNG = re.compile(
    r"BRAVE_[A-Z_]*KEY"  # the credential, in any script or workflow
    r"|brave\.com"  # any Brave host, including api.search.brave.com
    r"|['\"](?:brave|brave-search|brave-search-api)['\"]",  # a registered rung name
    re.IGNORECASE,
)

EXCLUSION_CLAIM = re.compile(r"(?i)excluded by project policy.{0,40}brave|brave.{0,40}excluded by policy")


def brave_signatures(text: str) -> list[str]:
    """Every Brave rung signature in `text`.

    Exposed as a function so the control below can prove it still detects a
    reintroduced rung, rather than trusting the pattern to be alive.
    """
    return [match.group(0) for match in BRAVE_RUNG.finditer(text)]


def _offenders(directory: Path, pattern: str) -> list[str]:
    """`path: signature` for every file under `directory` matching `pattern`."""
    found: list[str] = []
    for path in sorted(directory.rglob(pattern)):
        for signature in brave_signatures(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(REPO_ROOT)}: {signature}")
    return found


class TestTheScannerItself:
    """The control. A scan that matches nothing would pass every other test."""

    def test_it_detects_a_reintroduced_brave_rung(self):
        sample = (
            "BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY')\n"
            'response = get("https://api.search.brave.com/res/v1/web/search")\n'
            'name = "brave"\n'
        )
        assert len(brave_signatures(sample)) >= 4

    def test_it_does_not_flag_the_sentence_that_states_the_ban(self):
        # If this failed, the exclusion could not be written down anywhere —
        # documenting the rule would be indistinguishable from breaking it.
        assert brave_signatures("**Excluded by project policy: Brave Search API.**") == []
        assert brave_signatures("Brave is excluded by policy, do not add it.") == []


class TestNoBraveRung:
    def test_the_search_ladder_names_no_brave_backend(self):
        registered = set(ALL_BACKENDS) | set(LADDER)
        assert registered, "the ladder is empty — this assertion would be vacuous"
        assert [name for name in registered if "brave" in name.lower()] == []

    def test_no_script_carries_a_brave_credential_or_endpoint(self):
        assert _offenders(SCRIPTS, "*.py") == []

    def test_no_workflow_passes_a_brave_secret(self):
        # A secret in a workflow implies a rung that reads it.
        assert _offenders(WORKFLOWS, "*.yml") == []

    def test_the_registry_in_sources_json_is_not_a_backend_registry(self):
        # `config/sources.json` lists stream *sources*, not search backends, so
        # it must never grow a search rung either. Pinned so the two registries
        # cannot be conflated when someone reaches for "the config file".
        text = (REPO_ROOT / "config" / "sources.json").read_text(encoding="utf-8")
        assert brave_signatures(text) == []


class TestTheBanIsDocumented:
    """The documentation half: the rule must remain readable where it matters."""

    def test_the_backend_reference_states_the_exclusion(self):
        assert EXCLUSION_CLAIM.search(BACKENDS_DOC.read_text(encoding="utf-8")), (
            "references/search-backends.md must state that Brave is excluded by "
            "policy; without it the exclusion reads as an oversight to be filled in"
        )

    def test_the_skill_contract_carries_the_exclusion(self):
        assert EXCLUSION_CLAIM.search(SKILL_DOC.read_text(encoding="utf-8")), (
            "SKILL.md must carry the exclusion too — an executing agent that only "
            "reads the contract would otherwise see an unexplained gap"
        )
