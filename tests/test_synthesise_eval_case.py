"""Tests for scripts/synthesise_eval_case.py.

The synthesiser is the only component that writes to `evals/` automatically, so
the tests pin the two properties that keep that safe:

1. **A case is never written ungradeable.** `assert_gradeable` refuses a case
   whose assertion has no needle in `expected_output`, because such a case grades
   *nothing* and passes vacuously.
2. **Synthesis is idempotent.** The same misjudgement filed twice would inflate
   the eval count and make an all-green run meaningless.

Plus the mapping itself: a paid broadcast must blame Check 1, a never-broadcast
game must blame Check 7, and an event that claimed `VERIFIED` without evidence is
its own class — that last one is a 100 % requirement, not a nice-to-have.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.synthesise_eval_case import (
    ASSERTION_PATTERN,
    CHECK_DIRECT_STREAM,
    CHECK_EVENT_CREATED,
    CHECK_FREE_ACCESS,
    CHECK_REGRESSION,
    CHECK_VERIFIED_CLAIMED,
    CLASS_NEVER_LIVE,
    CLASS_OVERCLAIM,
    CLASS_PAID,
    _context_for,
    _input_clause,
    assert_gradeable,
    assertion_needle,
    build_case_assertions,
    build_expected_output,
    classify,
    dedupe_key,
    load_jsonl,
    next_case_id,
    read_verdicts,
    synthesise,
    verify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "synthesise_eval_case.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
VERDICTS = FIXTURES / "synthesise_verdicts.jsonl"


def _verdict(**overrides) -> dict:
    row = {
        "event_id": "evt-1",
        "game_key": "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-14T18:00Z",
        "prior_state": "UNVERIFIED",
        "verdict": "WRONG",
        "reason": "audit found paid access, not a free stream",
        "actionable": True,
        "ts": "2026-09-15T08:33:40+00:00",
    }
    row.update(overrides)
    return row


class TestAssertionGrammar:
    def test_grammar_matches_runtime_eval(self):
        # If these diverge, the synthesiser emits cases the grader cannot check.
        assert ASSERTION_PATTERN.match("freeAccess expected FAIL")
        assert not ASSERTION_PATTERN.match("freeAccess should fail")
        assert not ASSERTION_PATTERN.match("freeAccess expected MAYBE")

    def test_needle_translation(self):
        assert assertion_needle("freeAccess expected FAIL") == "freeAccess=FAIL"
        assert assertion_needle("regression expected PASS") == "regression=PASS"

    @pytest.mark.parametrize("bad", ["", None, "freeAccess", "freeAccess=FAIL", "a b c"])
    def test_malformed_assertions_have_no_needle(self, bad):
        assert assertion_needle(bad) is None


class TestBuilders:
    def test_every_assertion_has_a_needle(self):
        for overclaim in (True, False):
            expected = build_expected_output(
                CHECK_FREE_ACCESS, "because", overclaim=overclaim
            )
            for assertion in build_case_assertions(
                CHECK_FREE_ACCESS, overclaim=overclaim
            ):
                assert assertion_needle(assertion) in expected

    def test_overclaim_uses_its_own_consequence_check(self):
        assertions = build_case_assertions(CHECK_FREE_ACCESS, overclaim=True)
        assert f"{CHECK_VERIFIED_CLAIMED} expected FAIL" in assertions
        assert f"{CHECK_EVENT_CREATED} expected FAIL" not in assertions

    def test_the_failing_check_is_named_first(self):
        assertions = build_case_assertions(CHECK_DIRECT_STREAM)
        assert assertions[0] == f"{CHECK_DIRECT_STREAM} expected FAIL"
        assert assertions[-1] == f"{CHECK_REGRESSION} expected PASS"


class TestClassify:
    def test_paid_is_its_own_class(self):
        assert classify(_verdict(reason="audit found paid access, not a free stream")) == (
            CLASS_PAID
        )

    def test_any_other_wrong_is_never_live(self):
        assert classify(_verdict(reason="audit found no live broadcast")) == (
            CLASS_NEVER_LIVE
        )
        assert classify(_verdict(reason="something unexpected")) == CLASS_NEVER_LIVE

    def test_inconclusive_on_a_verified_event_is_an_overclaim(self):
        assert classify(
            _verdict(verdict="INCONCLUSIVE", prior_state="VERIFIED")
        ) == CLASS_OVERCLAIM

    def test_inconclusive_on_an_unverified_event_is_clean(self):
        assert classify(_verdict(verdict="INCONCLUSIVE", prior_state="UNVERIFIED")) is None

    def test_verified_verdict_is_clean(self):
        assert classify(_verdict(verdict="VERIFIED")) is None

    def test_non_dict_and_missing_verdict_are_clean(self):
        assert classify("nonsense") is None
        assert classify({}) is None

    def test_case_insensitive(self):
        assert classify(_verdict(verdict="wrong", reason="PAID")) == CLASS_PAID


class TestSynthesise:
    def test_clean_verdict_yields_no_case(self):
        assert synthesise(_verdict(verdict="VERIFIED"), None, case_id=1) is None

    def test_paid_case_blames_the_free_access_check(self):
        case = synthesise(_verdict(), None, case_id=7)
        assert case["id"] == 7
        assert case["assertions"][0] == f"{CHECK_FREE_ACCESS} expected FAIL"
        assert case["synthesised_from"] == "evt-1"
        assert "Check 1" in case["expected_output"]

    def test_never_live_case_blames_the_stream_check(self):
        case = synthesise(_verdict(reason="no live broadcast found"), None, case_id=8)
        assert case["assertions"][0] == f"{CHECK_DIRECT_STREAM} expected FAIL"
        assert "Check 7" in case["expected_output"]

    def test_overclaim_case_points_at_the_calendar_docs(self):
        case = synthesise(
            _verdict(verdict="INCONCLUSIVE", prior_state="VERIFIED"),
            None,
            case_id=9,
        )
        assert "references/calendar-setup.md" in case["files"]
        assert f"{CHECK_VERIFIED_CLAIMED} expected FAIL" in case["assertions"]

    def test_every_synthesised_case_is_gradeable(self):
        for verdict in read_verdicts(VERDICTS):
            case = synthesise(verdict, None, case_id=1)
            if case is not None:
                assert_gradeable(case)

    def test_prompt_records_provenance(self):
        case = synthesise(_verdict(), None, case_id=1, audited_at="2026-09-15T08:40:00Z")
        assert "evt-1" in case["prompt"]
        assert "2026-09-15T08:40:00Z" in case["prompt"]
        assert case["prompt"].startswith("Audit regression (synthesised from")

    def test_case_does_not_overwrite_a_hand_written_key(self):
        case = synthesise(_verdict(), None, case_id=1)
        assert set(case) >= {
            "id",
            "prompt",
            "expected_output",
            "assertions",
            "files",
            "synthesised_from",
        }


class TestContext:
    def test_lookup_by_game_key(self):
        ledger = [
            {"game_key": "other", "url": "https://a.test"},
            {"game_key": "BBL|X|Y|Z", "url": "https://b.test", "backend": "exa-mcp"},
        ]
        found = _context_for({"game_key": "BBL|X|Y|Z"}, ledger)
        assert found["url"] == "https://b.test"

    def test_lookup_by_event_id(self):
        ledger = [{"event_id": "evt-9", "url": "https://c.test"}]
        assert _context_for({"event_id": "evt-9"}, ledger)["url"] == "https://c.test"

    def test_no_match_is_an_empty_dict_not_an_error(self):
        assert _context_for({"game_key": "nope"}, [{"game_key": "other"}]) == {}

    def test_non_dict_rows_are_ignored(self):
        assert _context_for({"game_key": "g"}, ["junk", None]) == {}

    def test_input_clause_includes_url_and_backend(self):
        clause = _input_clause(
            _verdict(), {"url": "https://x.test/live", "backend": "firecrawl"}
        )
        assert "url=https://x.test/live" in clause
        assert "backend=firecrawl" in clause

    def test_input_clause_without_context_still_names_the_game(self):
        clause = _input_clause({"game_key": "G", "prior_state": "UNVERIFIED"}, {})
        assert "game_key=G" in clause
        assert clause.endswith(".")


class TestNextCaseId:
    def test_empty_file_starts_at_one(self):
        assert next_case_id([]) == 1

    def test_highest_plus_one(self):
        assert next_case_id([{"id": 1}, {"id": 7}, {"id": 3}]) == 8

    def test_non_int_and_malformed_entries_are_skipped(self):
        assert next_case_id([{"id": "9"}, {"no_id": 1}, "junk", {"id": 2}]) == 3


class TestDedupeKey:
    def test_the_audit_timestamp_does_not_change_the_key(self):
        # The prompt carries the audit timestamp, so keying on it would re-file
        # the same misjudgement every day.
        first = synthesise(_verdict(), None, case_id=1, audited_at="2026-09-15T08:40Z")
        second = synthesise(_verdict(), None, case_id=2, audited_at="2026-09-16T08:40Z")
        assert first["prompt"] != second["prompt"]
        assert dedupe_key(first) == dedupe_key(second)

    def test_different_event_ids_are_different_keys(self):
        first = synthesise(_verdict(event_id="a"), None, case_id=1)
        second = synthesise(_verdict(event_id="b"), None, case_id=2)
        assert dedupe_key(first) != dedupe_key(second)

    def test_misjudgement_classes_on_one_event_are_distinct(self):
        paid = synthesise(_verdict(), None, case_id=1)
        never_live = synthesise(_verdict(reason="no live broadcast"), None, case_id=2)
        assert dedupe_key(paid) != dedupe_key(never_live)


class TestAssertGradeable:
    def test_a_good_case_passes(self):
        assert_gradeable(
            {
                "expected_output": "checks: freeAccess=FAIL",
                "assertions": ["freeAccess expected FAIL"],
            }
        )

    def test_missing_needle_is_refused(self):
        with pytest.raises(ValueError, match="no needle"):
            assert_gradeable(
                {
                    "expected_output": "Decision=SKIP",
                    "assertions": ["freeAccess expected FAIL"],
                }
            )

    def test_malformed_assertion_is_refused(self):
        with pytest.raises(ValueError, match="malformed"):
            assert_gradeable(
                {"expected_output": "x", "assertions": ["freeAccess should fail"]}
            )


class TestLoader:
    def test_jsonl(self):
        rows = read_verdicts(VERDICTS)
        assert len(rows) == 4
        assert {row["event_id"] for row in rows} >= {"evt-paid-bayern"}

    def test_json_envelope(self, tmp_path):
        path = tmp_path / "audit.json"
        path.write_text(json.dumps({"verdicts": [_verdict()]}), encoding="utf-8")
        assert read_verdicts(path)[0]["event_id"] == "evt-1"

    def test_json_bare_list(self, tmp_path):
        path = tmp_path / "audit.json"
        path.write_text(json.dumps([_verdict()]), encoding="utf-8")
        assert len(read_verdicts(path)) == 1

    def test_bare_object_is_one_row(self, tmp_path):
        path = tmp_path / "audit.json"
        path.write_text(json.dumps(_verdict()), encoding="utf-8")
        assert len(read_verdicts(path)) == 1

    def test_missing_jsonl_is_empty_not_an_error(self, tmp_path):
        assert load_jsonl(tmp_path / "nope.jsonl") == []

    def test_jsonl_loader_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "x.jsonl"
        path.write_text('{"a": 1}\nnot json\n\n[1,2]\n{"b": 2}\n', encoding="utf-8")
        assert len(load_jsonl(path)) == 2


class TestVerify:
    def test_current_evals_are_all_gradeable(self):
        payload = json.loads((REPO_ROOT / "evals" / "evals.json").read_text("utf-8"))
        assert verify(payload) == 0

    def test_a_broken_needle_is_reported(self, capsys):
        payload = {
            "evals": [
                {"id": 1, "expected_output": "no needles", "assertions": ["x expected FAIL"]}
            ]
        }
        assert verify(payload) == 1
        assert "case #1" in capsys.readouterr().err

    def test_non_list_evals_fails(self):
        assert verify({"evals": "nope"}) == 1


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def _copy_evals(self, tmp_path) -> Path:
        target = tmp_path / "evals.json"
        target.write_text(
            (REPO_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return target

    def test_verify_mode_passes_on_the_real_file(self):
        result = _run(["--verify", "--evals", str(REPO_ROOT / "evals" / "evals.json")])
        assert result.returncode == 0
        assert "gradeable" in result.stdout

    def test_dry_run_writes_nothing(self, tmp_path):
        target = self._copy_evals(tmp_path)
        before = target.read_text(encoding="utf-8")
        result = _run(
            ["--verdicts", str(VERDICTS), "--evals", str(target), "--dry-run"]
        )
        assert result.returncode == 0
        assert "would append 3 cases" in result.stdout
        assert target.read_text(encoding="utf-8") == before

    def test_append_is_idempotent(self, tmp_path):
        target = self._copy_evals(tmp_path)
        first = _run(["--verdicts", str(VERDICTS), "--evals", str(target)])
        assert first.returncode == 0

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert len(payload["evals"]) == 39
        assert [c["id"] for c in payload["evals"]][-3:] == [37, 38, 39]

        second = _run(["--verdicts", str(VERDICTS), "--evals", str(target)])
        assert second.returncode == 1
        assert "already filed" in second.stderr
        # Still 39 — the second run must not duplicate anything.
        assert len(json.loads(target.read_text(encoding="utf-8"))["evals"]) == 39

    def test_appended_file_still_validates(self, tmp_path):
        target = self._copy_evals(tmp_path)
        _run(["--verdicts", str(VERDICTS), "--evals", str(target)])
        assert _run(["--verify", "--evals", str(target)]).returncode == 0

    def test_limit_caps_the_damage_of_a_bad_day(self, tmp_path):
        target = self._copy_evals(tmp_path)
        result = _run(
            ["--verdicts", str(VERDICTS), "--evals", str(target), "--limit", "1"]
        )
        assert result.returncode == 0
        assert len(json.loads(target.read_text(encoding="utf-8"))["evals"]) == 37

    def test_limit_below_one_is_a_usage_error(self, tmp_path):
        target = self._copy_evals(tmp_path)
        result = _run(
            ["--verdicts", str(VERDICTS), "--evals", str(target), "--limit", "0"]
        )
        assert result.returncode == 2

    def test_no_actionable_verdicts_exits_one(self, tmp_path):
        target = self._copy_evals(tmp_path)
        clean = tmp_path / "clean.jsonl"
        clean.write_text(
            json.dumps(
                {
                    "event_id": "evt-ok",
                    "verdict": "VERIFIED",
                    "prior_state": "UNVERIFIED",
                    "reason": "free access and live stream both confirmed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = _run(["--verdicts", str(clean), "--evals", str(target)])
        assert result.returncode == 1
        assert "no actionable misjudgements" in result.stderr

    def test_missing_verdicts_flag_is_a_usage_error(self, tmp_path):
        target = self._copy_evals(tmp_path)
        result = _run(["--evals", str(target)])
        assert result.returncode == 2

    def test_missing_verdicts_file_is_a_usage_error(self, tmp_path):
        target = self._copy_evals(tmp_path)
        result = _run(
            ["--verdicts", str(tmp_path / "nope.jsonl"), "--evals", str(target)]
        )
        assert result.returncode == 2

    def test_json_mode_is_parseable(self, tmp_path):
        """`json.load(stdout)` must work directly.

        This test used to read `result.stdout.split("\n", 1)[1]`, which was not
        parseability — it was a workaround for the summary line being printed
        first, written into a test whose name claimed the opposite. The payload
        is now alone on stdout and the summary went to stderr.
        """
        target = self._copy_evals(tmp_path)
        result = _run(
            ["--verdicts", str(VERDICTS), "--evals", str(target), "--dry-run", "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert len(payload["cases"]) == 3
        assert payload["dry_run"] is True
        assert "would append" in result.stderr
