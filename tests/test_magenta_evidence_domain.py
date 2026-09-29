"""Where magenta.tv free access is evidenced — pinned, because it moved.

**Operator fact, 2026-09-29:** the free ("kostenlos") basketball game is no
longer announced on `magentasport.de`. The free arena now lives **only on
`magenta.tv`**.

That is not a detail — it is the difference between accepting and rejecting a
game. The rule in force until now was *"a `magenta.tv` URL is only valid if a
free announcement exists on `magentasport.de`; no announcement = REJECT"*, and on
the first live dispatch it rejected two real EuroLeague games:

> the Magenta two-domain rule requires a matching per-game free announcement —
> none found

Both were rejected because the evidence had moved. A rule that rejects valid
games is worse than no rule: it is invisible, it looks like correct caution, and
it silently shrinks coverage.

Web search cannot settle this — every search for it returned mutually
contradictory summaries (some claiming a free game per matchday, some claiming
none, most about other sports or other years). So the rule is pinned from the
**operator's statement**, and the tests below assert the codebase agrees rather
than trying to re-derive the fact.

What these tests protect is the *shape* of the rule, which is the part that is
easy to get wrong in either direction:

* the evidence must be read from the **rendered `magenta.tv` page**;
* `magentasport.de` and social must be **corroboration only**;
* **silence must never reject** — that is the specific regression;
* a game with **no** free indication is still **rejected** — the rule that
  actually matters must survive the change.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "SKILL.md"
WORKFLOW_REF = REPO_ROOT / "references" / "validation-workflow.md"
TV_REF = REPO_ROOT / "references" / "magenta-tv.md"
SOURCES = REPO_ROOT / "config" / "sources.json"
EVALS = REPO_ROOT / "evals" / "evals.json"
APPROVED = REPO_ROOT / "references" / "approved-sources.md"

CORROBORATION_NOT_EVIDENCE = (
    "silence",
    "corroboration",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Headings that introduce a deliberate quotation of a superseded rule.
_HISTORY_HEADINGS = (
    "what changed and why",
    "changed 2026-09-29",
)


def _history_stripped(text: str) -> str:
    """Drop the quoted-history section, so quoting the old rule is not a match.

    `references/validation-workflow.md` keeps a "What changed and why" block
    naming the rule it replaced. That is provenance, not a second statement of
    policy, and a check that flags it would push the next author to delete the
    history rather than fix the policy.

    Cut by **span, not by line**: the block is one wrapped paragraph, and its
    continuation lines start with dates and sentences, so a line-oriented skip
    stops halfway through and still matches.
    """
    # The block is introduced by a bold run, not a heading, in the shipped file.
    opener = re.search(
        r"^\*\*\s*(?:what changed|why this changed|changed 2026-09-29)",
        text,
        re.I | re.M,
    )
    if not opener:
        return text
    end = re.search(r"^##\s", text[opener.end() :], re.M)
    return text[: opener.start()] + (
        text[opener.end() + end.start() :] if end else ""
    )



class TestTheEvidenceDomainIsMagentaTv:
    def test_the_skill_contract_says_so(self):
        text = _text(SKILL)
        # The page AND its section: a reader told only "magenta.tv" would still
        # have to guess which of its pages carries the free listing.
        assert "magenta.tv/sport" in text, (
            "SKILL.md must name the free arena page, not the bare domain"
        )
        assert "KOSTENLOS & OHNE LOGIN" in text, (
            "SKILL.md must name the section heading that carries the free games"
        )

    def test_the_validation_workflow_says_so(self):
        text = _text(WORKFLOW_REF)
        assert "magenta.tv/sport" in text
        assert "KOSTENLOS & OHNE LOGIN" in text
        assert re.search(
            r"silence is \*\*not\*\* evidence|not\*\* evidence against free access",
            text,
            re.I | re.S,
        ), "the workflow must state that magentasport.de silence is not evidence"

    def test_the_arena_page_is_recorded_in_the_corpus(self):
        """The page is in no search index, so the recorded page is the evidence.

        Without it the corpus holds a `magenta.tv` shell for some *other* path,
        and the render gate has never seen the arena that actually carries the
        free games.
        """
        manifest = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "pages" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        page = next(
            p for p in manifest["pages"] if p["url"].rstrip("/") == "https://www.magenta.tv/sport"
        )
        stored = REPO_ROOT / "tests" / "fixtures" / "pages" / page["stored"]
        assert stored.is_file()
        # An app shell, deliberately: the point of the record is that a plain
        # fetch yields no game and no media token.
        assert page["expect"] == "no-evidence"

    def test_the_tv_reference_says_so(self):
        text = _text(TV_REF)
        assert re.search(r"no longer\s+announced on `magentasport\.de`|lives \*\*here\*\*", text)
        assert "corroboration only" in text

    def test_the_registry_separates_evidence_from_corroboration(self):
        """Machine-readable, so the two roles cannot be conflated downstream."""
        registry = json.loads(_text(SOURCES))
        entry = next(
            s for s in registry["sources"] if "Magenta" in s.get("name", "")
        )
        assert entry["free_access_evidence"] == ["magenta.tv/sport"], (
            "magenta.tv carries the free-access evidence"
        )
        assert "magentasport.de" in entry["free_access_corroboration"]
        for domain in entry["free_access_corroboration"]:
            assert domain not in entry["free_access_evidence"], (
                f"{domain} cannot be both evidence and corroboration"
            )


class TestTheRuleStillRejects:
    """The change moved where evidence is read, not whether it is required."""

    def test_no_indication_still_means_reject(self):
        for path in (SKILL, WORKFLOW_REF, TV_REF):
            text = _text(path)
            assert re.search(
                r"no (free )?(access )?indication.{0,140}(REJECT|subscription-gated)|"
                r"REJECT.{0,40}no.{0,20}indication|"
                r"without the indication.{0,60}never as `VERIFIED`|"
                r"shows \*\*no\*\* free indication.{0,160}REJECTED|"
                r"absent from that section.{0,20}REJECTED|"
                r"No indication for\s+that game.{0,20}REJECT",
                text,
                re.I | re.S,
            ), f"{path.name} must still reject a game with no free indication"

    def test_free_access_is_still_per_game(self):
        """A free arena does not mean every game in it is free."""
        for path in (SKILL, WORKFLOW_REF):
            assert re.search(r"per game", _text(path), re.I | re.S), path.name

    def test_the_render_ladder_is_still_required(self):
        """Reading the indication off the page needs a rendered body."""
        text = _text(SKILL)
        assert re.search(r"render ladder|SPA", text, re.I | re.S)
        assert re.search(r"app shell", text, re.I | re.S)


class TestTheSilenceRegressionIsPinned:
    def test_an_eval_case_covers_corroboration_silence(self):
        """The exact failure the 2026-09-29 run hit, as a case.

        A free indication on `magenta.tv` plus silence on `magentasport.de` must
        be **accepted**. Before this case existed nothing asserted it, which is
        why the live run could regress it silently.
        """
        evals = json.loads(_text(EVALS))
        cases = evals["evals"] if isinstance(evals, dict) else evals
        hits = [
            c
            for c in cases
            if "magentasport" in c["prompt"].lower()
            and "false" in c["prompt"]
            and "freeAccess expected PASS" in c.get("assertions", [])
        ]
        assert hits, (
            "no eval asserts that magentasport.de silence does not reject; the "
            "2026-09-29 regression would return unnoticed"
        )

    def test_the_rule_cases_no_longer_name_magentasport_as_the_evidence(self):
        """Only the *cross-reference* cases, not every case mentioning Magenta.

        A paid-content case legitimately quotes `"mit MagentaSport Abo"` as the
        reason it fails, so matching on the domain alone produced a false
        positive — and a test that cries wolf gets deleted.
        """
        evals = json.loads(_text(EVALS))
        cases = evals["evals"] if isinstance(evals, dict) else evals
        for case in cases:
            # Scope to the cross-reference cases. A paid-keyword case
            # legitimately says "announcement" in its prompt while quoting
            # `mit MagentaSport Abo` as the reason it FAILS, so requiring
            # magenta.tv in its expectation would be wrong.
            if "free-access announcement" not in case["prompt"].lower():
                continue
            if "magentasport" in case["expected_output"].lower():
                assert "magenta.tv" in case["expected_output"].lower(), (
                    f"case {case['id']} still points at magentasport.de as the "
                    "evidence source"
                )

    def test_the_no_longer_reachable_rule_is_not_stated_as_current(self):
        """Guard against the old rule surviving as a second, contradicting copy.

        Deliberately quoted history is **not** a contradiction: the workflow
        keeps a "What changed and why" block that names the old rule so the
        change is auditable. What must not survive is the rule stated as
        current — so the history block is excluded rather than the phrase.
        """
        for path in (SKILL, WORKFLOW_REF, TV_REF, APPROVED):
            text = _history_stripped(_text(path))
            stale = re.findall(
                r"only valid (?:if|when) a matching official free-access\s+"
                r"announcement exists on `magentasport\.de`|"
                r"requires? a matching official free-access announcement on\s+"
                r"`magentasport\.de`|"
                r"No announcement = REJECT|"
                r"requires two-domain verification|"
                r"two-domain rule",
                text,
                re.I | re.S,
            )
            assert not stale, f"{path.name} still states the superseded rule: {stale}"
