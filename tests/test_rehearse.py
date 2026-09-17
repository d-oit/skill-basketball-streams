"""Tests for scripts/rehearse.py — the pre-release production rehearsal.

These are offline. The live probes are never called: `assess()` takes the
environment and the prober as parameters, so the whole classification matrix can
be exercised without a socket or a mutated process environment — the same shape
`llm_model.resolve_model(pin, environ)` uses, and the reason that decision is
worth making twice.

The registry tests are the ones that keep this from rotting: a new ladder rung or
search backend must arrive with a rehearsal surface, or the command that exists
to tell you a credential is missing would not know the surface was there.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REHEARSE = REPO_ROOT / "scripts" / "rehearse.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rehearse import (  # noqa: E402
    INVALID,
    MISSING,
    UNVERIFIED,
    VALID,
    Check,
    Row,
    apply_env_file,
    apply_env_text,
    assess,
    build_checks,
    env_file_values,
    exit_code,
    render_markdown,
    structural,
)


def _check(name: str = "thing", env: tuple[str, ...] = ("THING_KEY",), optional: bool = False):
    return Check(name=name, env=env, what="", probe=lambda: (True, ""), optional=optional)


def _recording_prober(result=(True, "ok")):
    calls: list[str] = []

    def prober(check):
        calls.append(check.name)
        return result

    return prober, calls


class TestPresenceIsDecidedFirst:
    def test_an_unset_credential_is_missing(self):
        rows = assess([_check()], {}, prober=None)
        assert [(row.state, row.name) for row in rows] == [(MISSING, "thing")]

    def test_a_missing_surface_never_spends_a_call(self):
        """Presence is decided alone, so an unconfigured surface costs nothing."""
        prober, calls = _recording_prober()
        assess([_check(env=("A", "B"))], {}, prober=prober)
        assert calls == []

    def test_any_one_of_the_names_is_enough(self):
        prober, calls = _recording_prober()
        rows = assess([_check(env=("A", "B"))], {"B": "set"}, prober=prober)
        assert rows[0].state == VALID and calls == ["thing"]

    def test_a_whitespace_only_value_is_not_configured(self):
        """`export KEY= ` is a mistake, not a credential."""
        prober, calls = _recording_prober()
        rows = assess([_check()], {"THING_KEY": "   "}, prober=prober)
        assert rows[0].state == MISSING and calls == []

    def test_the_detail_names_every_accepted_variable(self):
        rows = assess([_check(env=("EXA_API_KEY", "OTHER"))], {}, prober=None)
        assert "EXA_API_KEY" in rows[0].detail and "OTHER" in rows[0].detail


class TestTheProbeDecidesValidity:
    def test_an_accepted_probe_is_valid(self):
        prober, _ = _recording_prober((True, "key accepted"))
        rows = assess([_check()], {"THING_KEY": "x"}, prober=prober)
        assert (rows[0].state, rows[0].detail) == (VALID, "key accepted")

    def test_a_rejected_probe_is_invalid_and_carries_the_reason(self):
        prober, _ = _recording_prober((False, "HTTP 401: User not found"))
        rows = assess([_check()], {"THING_KEY": "x"}, prober=prober)
        assert rows[0].state == INVALID
        assert "401" in rows[0].detail

    def test_could_not_probe_is_not_a_rejected_credential(self):
        """`grep`-able distinction: a machine without `gh` is not a bad token."""
        prober, _ = _recording_prober((None, "no `gh` on PATH"))
        rows = assess([_check()], {"THING_KEY": "x"}, prober=prober)
        assert rows[0].state == UNVERIFIED

    def test_a_surface_with_no_probe_is_unverified_not_valid(self):
        rows = assess(
            [Check(name="thing", env=("THING_KEY",), what="", probe=None)],
            {"THING_KEY": "x"},
            prober=None,
        )
        assert rows[0].state == UNVERIFIED


class TestExitCode:
    def test_a_rejected_credential_is_always_fatal(self):
        from rehearse import Row

        rows = [Row(name="x", what="", state=INVALID, detail="")]
        assert exit_code(rows, require_all=False) == 1

    def test_a_missing_credential_is_fatal_only_under_require_all(self):
        from rehearse import Row

        rows = [Row(name="x", what="", state=MISSING, detail="")]
        assert exit_code(rows, require_all=False) == 0
        assert exit_code(rows, require_all=True) == 1

    def test_an_optional_surface_never_fails_even_under_require_all(self):
        from rehearse import Row

        rows = [Row(name="x", what="", state=MISSING, detail="", optional=True)]
        assert exit_code(rows, require_all=True) == 0

    def test_an_unverified_surface_never_fails(self):
        from rehearse import Row

        rows = [Row(name="x", what="", state=UNVERIFIED, detail="")]
        assert exit_code(rows, require_all=True) == 0

    def test_a_fully_valid_rehearsal_passes(self):
        from rehearse import Row

        rows = [Row(name="x", what="", state=VALID, detail="")]
        assert exit_code(rows, require_all=True) == 0


class TestAnEnvLessCheckIsProbedAnyway:
    """A check about the REPOSITORY has no credential to be missing."""

    def _check(self):
        return Check(name="repo", env=(), what="", probe=lambda: (True, "fine"))

    def test_it_is_probed_with_nothing_set(self):
        prober, calls = _recording_prober((True, "fine"))
        rows = assess([self._check()], {}, prober=prober)
        assert (rows[0].state, rows[0].detail) == (VALID, "fine")
        assert calls == ["repo"]

    def test_its_invalid_state_is_fatal_like_any_other(self):
        from rehearse import Row

        rows = [Row(name="repo", what="", state=INVALID, detail="")]
        assert exit_code(rows, require_all=False) == 1


class TestIssueGrantDetection:
    """Which workflows count as filing an issue, kept precise on purpose.

    Both directions cost something real: a missed filer lets a release claim the
    surface works when the issue would never appear, and an over-eager match
    demands `issues: write` from a workflow that never touches the tracker — the
    "preflight that reds a correctly configured runner" this repo has already
    decided not to build.
    """

    def test_the_cli_form_is_detected(self):
        """`self-improve.yml` files with `gh issue create` and no helper script.

        A detector built only from script names would miss it — the blind spot
        this test exists for.
        """
        from rehearse import _files_issues

        assert _files_issues("          gh issue create \\\n") is True
        assert _files_issues("gh issue comment 12 --body x") is True

    def test_the_unambiguous_scripts_are_detected(self):
        from rehearse import _files_issues

        assert _files_issues("python3 scripts/corpus_flip_issue.py --report x") is True
        assert _files_issues("python3 scripts/gh_issue.py --title y") is True

    def test_an_ambiguous_script_needs_the_flag(self):
        """`rung_health.py` only files with `parked --file`."""
        from rehearse import _files_issues

        assert _files_issues("python3 scripts/rung_health.py snapshot") is False
        assert _files_issues("python3 scripts/rung_health.py parked --file") is True

    def test_an_attach_flag_is_not_filing(self):
        """A bare `--file` is a false positive: `opencode` takes attachments."""
        from rehearse import _files_issues

        assert _files_issues('--file SKILL.md --file references/ --attach x') is False

    def test_a_comment_mentioning_the_grant_is_not_the_grant(self):
        """The bug that falsifying the first version of this guard found.

        `corpus-refresh.yml`'s own header comment says "`contents: read` plus
        `issues: write`", so a substring search reported the grant as present
        **after it was deleted from the YAML** — and the live rehearsal stayed
        green. A rule reading a string that no block writes.
        """
        from rehearse import _grants_issues_write

        assert _grants_issues_write(
            "# `contents: read` plus `issues: write`, and it never commits\n"
        ) is False
        assert _grants_issues_write("permissions:\n  contents: read\n") is False
        assert _grants_issues_write(
            "permissions:\n  contents: read\n  issues: write\n"
        ) is True
        # A job-level block counts too — three of the four real ones are nested.
        assert _grants_issues_write(
            "jobs:\n  a:\n    permissions:\n      issues: write\n"
        ) is True
        # A key after the block belongs to something else and must not count.
        assert _grants_issues_write(
            "permissions:\n  contents: read\nenv:\n  issues: write\n"
        ) is False

    def test_the_real_repository_passes_and_names_every_filer(self):
        from rehearse import probe_issue_grant

        ok, detail = probe_issue_grant()
        assert ok is True
        # All three, including the one that uses the CLI directly.
        assert "self-improve.yml" in detail
        assert "corpus-refresh.yml" in detail and "runtime-daily.yml" in detail

    def test_a_filer_without_the_grant_is_invalid(self, tmp_path):
        from rehearse import probe_issue_grant

        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "files.yml").write_text(
            "name: files\npermissions:\n  contents: read\n"
            "      run: gh issue create --title x\n"
        )

        ok, detail = probe_issue_grant(tmp_path)
        assert ok is False and "files.yml" in detail

    def test_a_non_filer_without_the_grant_is_not_flagged(self, tmp_path):
        """The read-only gate workflow must not be reported as broken."""
        from rehearse import probe_issue_grant

        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "verify.yml").write_text(
            "name: verify\npermissions:\n  contents: read\n  run: pytest tests/\n"
        )

        ok, detail = probe_issue_grant(tmp_path)
        assert ok is None and "nothing to grant" in detail


class TestTheCredentialFile:
    """`--env-file`'s parsing half, which is pure and therefore cheap to pin."""

    def test_pairs_comments_and_blanks(self):
        values = env_file_values(
            "# comment\n\nA=1\n  B = two \nC='quoted'\nD=\"also quoted\"\n", "f"
        )
        assert values == {"A": "1", "B": "two", "C": "quoted", "D": "also quoted"}

    def test_a_lone_quote_is_not_stripped_off_a_value(self):
        assert env_file_values("A='\n", "f") == {"A": "'"}

    def test_a_line_without_an_equals_is_refused_by_line_number(self):
        with pytest.raises(SystemExit) as exc:
            env_file_values("A=1\nGEMINI_API_KEY\n", "creds.env")
        assert "creds.env:2" in str(exc.value) and "not KEY=VALUE" in str(exc.value)

    def test_a_line_with_no_usable_name_is_refused(self):
        with pytest.raises(SystemExit) as exc:
            env_file_values("=value\n", "f")
        assert "no usable variable name" in str(exc.value)

    def test_the_environment_wins_over_the_file(self):
        """`setdefault` semantics, so one run is overridable without an edit."""
        environ = {"EXA_API_KEY": "from-the-environment"}
        supplied = apply_env_text(
            "EXA_API_KEY=from-file\nNEW=value\n", "f", environ
        )
        assert environ["EXA_API_KEY"] == "from-the-environment"
        # Only the name actually taken from the file: the note beside it reads as
        # "supplied N variable(s)", and that has to be true of the run.
        assert supplied == ["NEW"]
        assert environ["NEW"] == "value"

    def test_an_empty_value_is_not_a_credential(self):
        environ: dict[str, str] = {}
        supplied = apply_env_text("GEMINI_API_KEY=\n", "f", environ)
        assert supplied == [] and "GEMINI_API_KEY" not in environ

    def test_a_missing_file_is_refused_by_name(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            apply_env_file(tmp_path / "nope.env", {})
        assert "nope.env" in str(exc.value)


class TestTheRegistryStaysComplete:
    """A new rung or backend must arrive with a rehearsal surface."""

    def _by_name(self):
        return {check.name: check for check in build_checks()}

    def test_every_ladder_rung_has_a_surface(self):
        from capture_transcripts import RUNG_CREDENTIALS

        checks = self._by_name()
        for rung in RUNG_CREDENTIALS:
            assert f"llm:{rung}" in checks, rung
            # Same variables, imported rather than restated, or the rehearsal
            # would green-light a rung the ladder does not read.
            assert checks[f"llm:{rung}"].env == RUNG_CREDENTIALS[rung]

    def test_every_search_backend_has_a_surface(self):
        from run_daily import ALL_BACKENDS

        checks = self._by_name()
        for backend in ALL_BACKENDS.values():
            assert f"search:{backend.name}" in checks, backend.name
            # A keyless rung carries no credential, so its surface must have an
            # empty env tuple — `assess` reads that as "probe directly".
            expected_env = (backend.env_var,) if backend.env_var else ()
            assert checks[f"search:{backend.name}"].env == expected_env, backend.name

    def test_every_credential_the_runtime_reads_has_a_surface(self):
        """The invariant that matters, over the union rather than per-component.

        Written this way after finding `FIRECRAWL_API_KEY` missing from the
        registry: it is a real secret the workflows pass (`render rung 1`) and the
        per-component tests above would not have noticed, because it belongs to
        neither the model ladder nor the search backends. A credential that some
        rung reads and no surface reports is the gap this command exists to close.
        """
        from capture_transcripts import RUNG_CREDENTIALS
        from render_ladder import ALL_RUNGS
        from run_daily import ALL_BACKENDS

        required: set[str] = set()
        for names in RUNG_CREDENTIALS.values():
            required.update(names)
        for backend in ALL_BACKENDS.values():
            # A keyless rung reads no credential (env_var is ""); adding the
            # empty string here would demand an env var nobody can set.
            if backend.env_var:
                required.add(backend.env_var)
        for rung in ALL_RUNGS.values():
            # Only the hosted rungs carry a key; the local ones have no `env_var`
            # attribute at all, which is what makes the ladder never empty.
            if getattr(rung, "env_var", ""):
                required.add(rung.env_var)

        covered = {
            name for check in build_checks() for name in check.env
        }
        assert required - covered == set(), (
            "unrehearsed credential(s): "
            + ", ".join(sorted(required - covered))
        )

    def test_every_surface_states_what_it_unlocks(self):
        for check in build_checks():
            assert check.what, check.name
            # Every surface says where its answer comes from: a credential, or a
            # probe that reads something else (the issue grant reads the
            # workflows). An env-less check with no probe could never be
            # anything but `unverified`, which is noise.
            assert check.env or check.probe, check.name

    def test_the_setup_guide_documents_every_surface(self):
        """The walkthrough and the command must not drift apart.

        A surface added to `build_checks()` without a row in `SETUP.md` is one an
        operator has no instructions for, and the failure they would see is a
        `NO` row naming an environment variable nobody told them about.
        """
        guide = (REPO_ROOT / "SETUP.md").read_text(encoding="utf-8")
        for check in build_checks():
            assert f"`{check.name}`" in guide, check.name

    def test_the_optional_surfaces_are_the_ones_that_degrade(self):
        """Optional means a missing key degrades a rung, not a broken run.

        The render ladder climbs past a dead hosted rung to the keyless `urllib`
        one, and the YouTube Data API is an alternative to the keyless HTML live
        filter. Everything else is required for the scheduled workflows.
        """
        optional = {check.name for check in build_checks() if check.optional}
        assert optional == {"render:firecrawl", "youtube-data-api"}


class TestOfflineIsStructural:
    """`--offline` exists so the credential-free half can be a CI gate."""

    def test_only_the_credential_free_checks_survive(self):
        checks = build_checks()
        offline = structural(checks)
        assert [check.name for check in offline] == ["github-issues:grant"]
        assert all(not check.env for check in offline)
        assert all(check.structural for check in offline)
        assert len(offline) < len(checks), "a mode that probes everything is not offline"

    def test_env_free_does_not_imply_structural(self):
        """No credential does not mean no network: the keyless rung proves it."""
        by_name = {check.name: check for check in build_checks()}
        keyless = by_name["search:exa-mcp-keyless"]
        assert keyless.env == () and keyless.structural is False
        # The repository check stays structural, and its probe reads workflows,
        # not sockets — that is what earns it a place in `--offline`.
        assert by_name["github-issues:grant"].structural is True

    def test_every_offline_surface_has_a_probe(self):
        """An env-less check with no probe could never be anything but noise."""
        for check in structural(build_checks()):
            assert check.probe, check.name


class TestMarkdownRenderer:
    def _rows(self):
        return [
            Row(name="calendar", what="", state=MISSING, detail="none of A, B is set"),
            Row(name="llm:gemini", what="", state=INVALID, detail="HTTP 401 | nope"),
        ]

    def test_a_pipe_in_a_detail_cannot_split_the_row(self):
        text = render_markdown(
            self._rows(), counts={VALID: 0, INVALID: 1, MISSING: 1, UNVERIFIED: 0}
        )
        row = [line for line in text.splitlines() if line.startswith("| `llm:gemini`")]
        assert row == [r"| `llm:gemini` | invalid | HTTP 401 \| nope |"]

    def test_it_names_every_surface_and_carries_the_counts(self):
        text = render_markdown(
            self._rows(), counts={VALID: 0, INVALID: 1, MISSING: 1, UNVERIFIED: 0}
        )
        assert "| `calendar` | missing |" in text
        assert "0 valid, 1 invalid, 1 missing, 0 unverified (of 2 surface(s))." in text
        # The one rule a release turns on, stated in the artifact that gates it.
        assert "**rejected**" in text


class TestTheCredentialTemplate:
    """`.env.example` and the registry must not drift apart."""

    TEMPLATE = REPO_ROOT / ".env.example"

    def test_every_credential_the_registry_names_is_in_the_template(self):
        """A surface with no line in the template is one the walkthrough skips."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        keys = env_file_values(text, ".env.example")
        for check in build_checks():
            for name in check.env:
                assert name in keys, f"{name} ({check.name}) is missing from .env.example"

    def test_the_template_ships_no_values(self):
        """It is a template. A filled-in value here would be a published key."""
        keys = env_file_values(self.TEMPLATE.read_text(encoding="utf-8"), ".env.example")
        assert keys, "the template parsed empty — its format drifted"
        assert [name for name, value in keys.items() if value] == []

    def test_the_filled_in_file_is_ignored_and_the_template_is_not(self):
        """The file holds a calendar API key and this repository is public."""
        git = shutil.which("git")
        if not git or subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_ROOT),
            capture_output=True,
        ).returncode != 0:
            pytest.skip("not a git work tree, so there is nothing to ignore")

        ignored = subprocess.run(
            [git, "check-ignore", "-q", ".env"], cwd=str(REPO_ROOT)
        )
        template = subprocess.run(
            [git, "check-ignore", "-q", ".env.example"], cwd=str(REPO_ROOT)
        )
        assert ignored.returncode == 0, ".env must never be committable"
        assert template.returncode != 0, ".env.example must stay tracked"


class TestCli:
    def _run(
        self,
        *args: str,
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        """Runs with every credential stripped, so a real key cannot be spent."""
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.endswith(("_API_KEY", "_TOKEN", "_ID", "_SECRET"))
            and key not in {"ANTHROPIC_AUTH_TOKEN", "GOOGLE_API_KEY", "GH_TOKEN"}
        }
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, str(REHEARSE), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd or REPO_ROOT),
        )

    def test_list_is_offline_and_exits_zero(self):
        result = self._run("--list")
        assert result.returncode == 0
        for name in ("calendar", "llm:openrouter", "search:exa-mcp"):
            assert name in result.stdout

    def test_unconfigured_surfaces_exit_zero_without_require_all(self):
        result = self._run()
        assert result.returncode == 0
        assert "0 invalid" in result.stderr
        assert "NO" in result.stderr

    def test_require_all_fails_on_a_missing_credential(self):
        result = self._run("--require-all")
        assert result.returncode == 1
        assert "FAIL: rehearse" in result.stderr

    def test_json_stdout_is_payload_only(self):
        result = self._run("--json")
        payload = json.loads(result.stdout)  # raises if a human line leaked
        assert payload["ok"] is True and payload["require_all"] is False
        assert set(payload["counts"]) == {"valid", "invalid", "missing", "unverified"}
        assert len(payload["surfaces"]) == len(build_checks())
        # The human report still exists, on stderr where it belongs.
        assert "rehearse:" in result.stderr

    def test_no_credential_surface_is_probed_when_nothing_is_configured(self):
        """The rehearsal is inert on a bare checkout: no call, no write.

        The single `valid` row is `github-issues:grant`, which is read from the
        workflows and needs no key — so it is `valid` on a bare checkout, and
        asserting `0 valid` here would be asserting the wrong thing.
        """
        result = self._run()
        assert "probe raised" not in result.stderr
        assert "0 invalid" in result.stderr
        assert "0 unverified" in result.stderr
        assert "9 missing" in result.stderr
        assert "github-issues:grant" in result.stderr

    def test_offline_probes_the_grant_and_says_what_it_did_not_answer(self):
        result = self._run("--offline")
        assert result.returncode == 0
        assert "github-issues:grant" in result.stderr
        assert "not consulted" in result.stderr
        assert "(of 1 surface(s))" in result.stderr
        # The point of the mode: it must not report a credential surface at all —
        # neither a `missing` row (which would read like a verdict on a surface
        # this run never asked about) nor a name in any row.
        assert "NO   " not in result.stderr
        assert "llm:" not in result.stderr
        # Not structural either: the keyless rung has no credential but still
        # talks to a service, and offline promises not to touch the network.
        assert "search:" not in result.stderr

    def test_offline_spends_nothing_even_when_credentials_are_set(self):
        """The property a CI gate depends on: configured-but-offline is inert.

        Deliberately given a live-looking key: if `--offline` ever stopped
        filtering, this run would ask Google with it and report a `FAIL` row.
        """
        result = self._run(
            "--offline", extra_env={"GEMINI_API_KEY": "not-a-real-key"}
        )
        assert result.returncode == 0
        assert "llm:gemini" not in result.stderr
        assert "1 valid" in result.stderr

    def test_offline_never_probes_a_service_backed_by_no_credential(self):
        """structural is declared, not derived from an empty env.

        The keyless Exa rung needs no key yet still speaks to mcp.exa.ai; an
        env-derived offline set would probe it, and --offline would spend the
        network it promises a CI gate it never touches. The env-less checks that
        DO read the repository (exactly one) are the only ones probed.
        """
        from run_daily import ALL_BACKENDS

        assert ALL_BACKENDS["exa-mcp-keyless"].env_var == ""  # the trap exists
        result = self._run("--offline")
        assert result.returncode == 0
        assert "search:" not in result.stderr
        assert "(of 1 surface(s))" in result.stderr
        assert "1 valid" in result.stderr

    def test_markdown_is_payload_only_and_reads_as_a_table(self):
        result = self._run("--markdown")
        assert result.returncode == 0
        lines = result.stdout.splitlines()
        assert lines[0] == "## Production rehearsal"
        assert lines[2] == "| Surface | State | Detail |"
        assert lines[3] == "|---|---|---|"
        table = [line for line in lines if line.startswith("| `")]
        assert len(table) == len(build_checks())
        assert "rejected" in result.stdout

    def test_json_and_markdown_together_is_a_usage_error(self):
        """Two payloads, one stdout: refused rather than interleaved."""
        result = self._run("--json", "--markdown")
        assert result.returncode == 2
        assert "pick one" in result.stderr

    def test_a_missing_env_file_is_named_and_nothing_is_probed(self):
        result = self._run("--env-file", ".env.does-not-exist")
        assert result.returncode == 1
        assert "is not a readable file" in result.stderr
        assert ".env.does-not-exist" in result.stderr
        # No rows at all: it refused before consulting any surface.
        assert "(of 10 surface(s))" not in result.stderr

    def test_a_malformed_env_file_names_the_line(self, tmp_path):
        env_file = tmp_path / "creds.env"
        env_file.write_text("# a comment\nGEMINI_API_KEY\n")
        result = self._run("--env-file", str(env_file))
        assert result.returncode == 1
        assert "creds.env:2 is not KEY=VALUE" in result.stderr

    def test_a_stray_env_file_is_not_read(self, tmp_path):
        """No implicit discovery: the environment given is the one reported on.

        A `.env` sitting in the working directory holds `GEMINI_API_KEY`, and
        the run must still report it missing — otherwise the file that decides a
        release would be one nobody named, which is the `${{ }}` failure mode in
        a different costume.
        """
        (tmp_path / ".env").write_text("GEMINI_API_KEY=not-a-real-key\n")
        result = self._run(cwd=tmp_path)
        assert result.returncode == 0
        assert "9 missing" in result.stderr
        assert "NO   llm:gemini" in result.stderr
        assert "0 invalid" in result.stderr

    def test_the_env_file_is_loaded_and_reported_by_name_only(self, tmp_path):
        """`--offline` keeps this safe: the file's key is never spent."""
        env_file = tmp_path / "creds.env"
        env_file.write_text(
            "# a comment\nUNRELATED=whatever\nGEMINI_API_KEY=\n"
            "EXA_API_KEY=not-a-real-key\n"
        )
        result = self._run("--env-file", str(env_file), "--offline", "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["offline"] is True
        assert payload["env_file"] == str(env_file)
        # Only the two non-empty lines count as supplied: the template copied
        # unfilled is reported `missing`, not sent to a provider as "".
        assert "supplied 2 variable(s)" in result.stderr
        assert "EXA_API_KEY" in result.stderr and "UNRELATED" in result.stderr
        # Names, never values — this output exists to be pasted into a PR.
        assert "whatever" not in result.stdout + result.stderr
        assert "not-a-real-key" not in result.stdout + result.stderr
