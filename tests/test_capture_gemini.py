"""Pytest suite for the Gemini (free AI Studio) rung of capture_transcripts.py.

Why this file exists: the spec's LLM ladder (``live-stream-runtime-spec.md`` §4)
was "Gemini free → OpenCode Zen free → OpenRouter free, automatic failover"
(reordered 2026-09-17 by operator decision to put the free OpenRouter router
first), and the capture path needs its rungs implemented and tested offline.

Every test here is offline: ``_request`` is monkeypatched, which is the single
function the module uses to touch the network. A test that opened a socket would
fail for the wrong reason (a missing key) and hide a real regression.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import capture_transcripts as ct

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "capture_transcripts.py"

GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": "Decision=CREATE; checks: a=PASS"}]}}]
}


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    """Both globals are per-process caches in production; in a suite they leak.

    Every rung credential is removed too. The ambient environment here really
    does carry `ANTHROPIC_AUTH_TOKEN` (and a dead `OPENROUTER_API_KEY`), so a
    test that only cleared the Gemini variables would silently be asserting
    against a *configured* runner.
    """
    monkeypatch.setattr(ct, "_GEMINI_MODEL", [])
    monkeypatch.setattr(ct, "LAST_ERRORS", {})
    monkeypatch.setattr(ct, "LAST_MODELS", {})
    monkeypatch.setattr(ct.shutil, "which", lambda name: None)
    for var in ("GEMINI_MODEL", *_all_rung_credentials()):
        monkeypatch.delenv(var, raising=False)


def _all_rung_credentials() -> list[str]:
    seen: list[str] = []
    for names in ct.RUNG_CREDENTIALS.values():
        seen.extend(names)
    return seen


def _args(**overrides) -> SimpleNamespace:
    base = dict(
        model=None, attach=None, timeout=5, runner="gemini", file=None, format="json"
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _recorder(calls: list, body=GEMINI_OK, status: int = 200, error: str = ""):
    def fake(method, url, *, payload=None, headers=None, timeout=60):
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        return status, body, error

    return fake


# ---------------------------------------------------------------------------
# _request — the one shared transport
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, raw: bytes, status: int = 200) -> None:
        self._raw = raw
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRequestTransport:
    def test_success_returns_status_body_and_no_error(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout=None: _FakeResponse(b'{"ok": true}'),
        )
        status, body, error = ct._request("GET", "https://example.invalid/x")
        assert (status, body, error) == (200, {"ok": True}, "")

    def test_non_json_body_is_an_error_not_a_body(self, monkeypatch):
        """An HTML error page must never read as a model that answered nothing."""
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout=None: _FakeResponse(b"<html>gateway</html>"),
        )
        status, body, error = ct._request("GET", "https://example.invalid/x")
        assert body is None
        assert "was not JSON" in error

    def test_empty_body_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout=None: _FakeResponse(b"  \n"),
        )
        _, body, error = ct._request("GET", "https://example.invalid/x")
        assert body is None
        assert "empty response body" in error

    def test_transport_failure_is_reported_with_its_type(self, monkeypatch):
        def boom(request, timeout=None):
            raise OSError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        status, body, error = ct._request("GET", "https://example.invalid/x")
        assert (status, body) == (0, None)
        assert "OSError" in error and "connection refused" in error


# ---------------------------------------------------------------------------
# gemini_completion — distinct reasons for distinct failures
# ---------------------------------------------------------------------------


class TestGeminiCompletion:
    def test_missing_key_is_named(self, monkeypatch):
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert error == "GEMINI_API_KEY is not set"

    def test_google_api_key_is_accepted_as_an_alias(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        calls: list = []
        monkeypatch.setattr(ct, "_request", _recorder(calls))
        text, error = ct.gemini_completion("hi")
        assert error == ""
        assert "Decision=CREATE" in text

    def test_posts_to_generate_content_with_a_zero_temperature(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        calls: list = []
        monkeypatch.setattr(ct, "_request", _recorder(calls))
        ct.gemini_completion("hello world", model="gemini-3.5-flash-lite")
        call = calls[0]
        assert call["method"] == "POST"
        assert call["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")
        assert call["payload"]["contents"][0]["parts"][0]["text"] == "hello world"
        assert call["payload"]["generationConfig"]["temperature"] == 0
        # The key travels in the header, never in the query string — a URL ends
        # up in logs and in the artifact upload.
        assert call["headers"]["x-goog-api-key"] == "k"
        assert "key=" not in call["url"]

    def test_multiple_parts_are_joined(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        text, error = ct.gemini_completion("hi")
        assert (text, error) == ("ab", "")

    def test_quota_exhaustion_is_reported_verbatim(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(
            ct, "_request", _recorder([], body=None, error="HTTP 429: RESOURCE_EXHAUSTED")
        )
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert error == "HTTP 429: RESOURCE_EXHAUSTED"

    def test_safety_block_is_reported_as_a_block(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = {"promptFeedback": {"blockReason": "SAFETY"}}
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert error == "prompt blocked: SAFETY"

    def test_no_candidates_is_reported(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ct, "_request", _recorder([], body={"candidates": []}))
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert "no candidates in response" in error

    def test_empty_text_carries_the_finish_reason(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert "empty completion" in error and "MAX_TOKENS" in error

    def test_whitespace_only_text_is_empty_not_success(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = {"candidates": [{"content": {"parts": [{"text": "   \n"}]}}]}
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        text, error = ct.gemini_completion("hi")
        assert text == ""
        assert "empty completion" in error


# ---------------------------------------------------------------------------
# models.list + the resolution policy
# ---------------------------------------------------------------------------


def _models_payload(*models: tuple[str, list[str]]) -> dict:
    return {
        "models": [
            {"name": f"models/{name}", "supportedGenerationMethods": methods}
            for name, methods in models
        ]
    }


class TestListGeminiModels:
    def test_missing_key_is_named(self):
        ids, error = ct.list_gemini_models()
        assert ids == []
        assert "GEMINI_API_KEY is not set" in error

    def test_filters_by_generate_content_and_strips_the_prefix(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = _models_payload(
            ("gemini-3.5-flash-lite", ["generateContent"]),
            ("gemini-embedding-001", ["embedContent"]),
        )
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        ids, error = ct.list_gemini_models()
        assert (ids, error) == (["gemini-3.5-flash-lite"], "")

    def test_unexpected_shape_is_an_error(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ct, "_request", _recorder([], body={"models": "nope"}))
        ids, error = ct.list_gemini_models()
        assert ids == []
        assert "no models array" in error


class TestRungStatus:
    def test_missing_everything_reports_no_rung_configured(self):
        status = dict((rung, ok) for rung, ok, _ in ct.rung_status())
        assert status["gemini"] is False
        assert status["openrouter"] is False

    def test_google_api_key_counts_as_the_gemini_rung(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        status = dict((rung, ok) for rung, ok, _ in ct.rung_status())
        assert status["gemini"] is True

    def test_a_cli_on_path_counts_as_the_opencode_rung(self, monkeypatch):
        monkeypatch.setattr(ct.shutil, "which", lambda name: "/usr/bin/opencode")
        detail = {rung: d for rung, _, d in ct.rung_status()}["opencode"]
        assert "CLI present" in detail

    def test_it_reports_the_first_credential_it_found(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setattr(ct.shutil, "which", lambda name: None)
        detail = {rung: d for rung, _, d in ct.rung_status()}["opencode"]
        assert detail == "ANTHROPIC_API_KEY is set"


class TestProbeOpenrouterKey:
    def test_missing_key_is_named(self):
        ok, detail = ct.probe_openrouter_key()
        assert ok is False
        assert detail == "OPENROUTER_API_KEY is not set"

    def test_a_dead_key_is_reported_verbatim(self, monkeypatch):
        """The exact failure this repo hit: present, and 401."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "dead")
        monkeypatch.setattr(
            ct,
            "_request",
            _recorder([], body=None, error='HTTP 401: {"error":{"code":401}}'),
        )
        ok, detail = ct.probe_openrouter_key()
        assert ok is False
        assert detail.startswith("HTTP 401")

    def test_a_live_key_is_accepted(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        calls: list = []
        monkeypatch.setattr(
            ct, "_request", _recorder(calls, body={"data": {"label": "ci", "usage": 0}})
        )
        ok, detail = ct.probe_openrouter_key()
        assert (ok, detail) == (True, "key accepted")
        assert calls[0]["url"].endswith("/api/v1/key")

    def test_an_unrecognised_payload_is_not_an_acceptance(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        monkeypatch.setattr(ct, "_request", _recorder([], body={"data": {}}))
        ok, detail = ct.probe_openrouter_key()
        assert ok is False
        assert "unrecognised" in detail


class TestRankGeminiModels:
    def test_lite_tier_is_preferred_over_a_newer_non_lite(self):
        ranked = ct.rank_gemini_models(["gemini-3.8-flash", "gemini-3.1-flash-lite"])
        assert ranked[0] == "gemini-3.1-flash-lite"

    def test_newest_version_first_within_a_tier(self):
        ranked = ct.rank_gemini_models(
            ["gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        )
        assert ranked[0] == "gemini-3.5-flash-lite"

    def test_preview_is_demoted_below_a_stable_id(self):
        ranked = ct.rank_gemini_models(
            ["gemini-3.5-flash-lite-preview", "gemini-3.5-flash-lite"]
        )
        assert ranked[0] == "gemini-3.5-flash-lite"

    def test_non_flash_models_are_used_only_if_nothing_else_exists(self):
        assert ct.rank_gemini_models(["gemini-embedding-001"]) == ["gemini-embedding-001"]
        ranked = ct.rank_gemini_models(["other-model", "gemini-3.5-flash-lite"])
        assert ranked[0] == "gemini-3.5-flash-lite"


class TestResolveGeminiModel:
    def test_explicit_model_wins(self):
        model, source = ct.resolve_gemini_model(_args(model="gemini-custom"))
        assert (model, source) == ("gemini-custom", "--model")

    def test_ladder_ignores_model_so_the_cli_id_is_not_sent_to_gemini(self):
        calls: list = []
        ct._request = _recorder(calls)  # type: ignore[assignment]
        model, source = ct.resolve_gemini_model(
            _args(model="opencode/big-pickle", runner="ladder")
        )
        assert model == ct.GEMINI_DEFAULT_MODEL
        assert "--model" not in source

    def test_env_var_beats_the_probe(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "gemini-from-env")
        model, source = ct.resolve_gemini_model(_args())
        assert (model, source) == ("gemini-from-env", "GEMINI_MODEL")

    def test_live_probe_supplies_the_id(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        body = _models_payload(
            ("gemini-3.5-flash-lite", ["generateContent"]),
            ("gemini-3.1-flash-lite", ["generateContent"]),
        )
        monkeypatch.setattr(ct, "_request", _recorder([], body=body))
        model, source = ct.resolve_gemini_model(_args())
        assert model == "gemini-3.5-flash-lite"
        assert source == "models.list"

    def test_probe_failure_falls_back_and_says_why(self, monkeypatch):
        model, source = ct.resolve_gemini_model(_args())
        assert model == ct.GEMINI_DEFAULT_MODEL
        assert "fallback" in source and "GEMINI_API_KEY is not set" in source


# ---------------------------------------------------------------------------
# the ladder
# ---------------------------------------------------------------------------


class TestLadder:
    def test_first_answering_rung_wins_and_is_recorded(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ct, "_request", _recorder([], body=GEMINI_OK))
        served: list[str] = []
        output = ct.run_ladder("prompt", _args(runner="ladder"), served)
        assert "Decision=CREATE" in output
        assert served == ["gemini"]

    def test_opencode_rung_is_skipped_without_attach(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(ct, "run_gemini", lambda p, a: called.append("gemini") or "")
        monkeypatch.setattr(
            ct, "run_opencode", lambda p, a: called.append("opencode") or "from-cli"
        )
        served: list[str] = []
        output = ct.run_ladder("prompt", _args(runner="ladder"), served)
        assert output == ""
        assert "opencode" not in called
        assert served == []

    def test_failover_moves_to_the_next_rung(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")

        def fake_request(method, url, *, payload=None, headers=None, timeout=60):
            if "generativelanguage" in url:
                return 429, None, "HTTP 429: RESOURCE_EXHAUSTED"
            return 200, {"choices": [{"message": {"content": "from-openrouter"}}]}, ""

        monkeypatch.setattr(ct, "_request", fake_request)
        served: list[str] = []
        output = ct.run_ladder("prompt", _args(runner="ladder"), served)
        assert output == "from-openrouter"
        assert served == ["openrouter"]

    def test_total_failure_records_every_rung_with_its_own_reason(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setattr(
            ct, "_request", _recorder([], body=None, error="HTTP 500: upstream")
        )
        served: list[str] = []
        assert ct.run_ladder("prompt", _args(runner="ladder"), served) == ""
        assert ct.LAST_ERRORS["gemini"] == "HTTP 500: upstream"
        assert ct.LAST_ERRORS["openrouter"] == "HTTP 500: upstream"

    def test_a_dead_key_is_not_mistaken_for_an_empty_completion(self, monkeypatch):
        text, error = ct.run_gemini("prompt", _args()), None
        assert text == ""
        assert ct.LAST_ERRORS["gemini"] == "GEMINI_API_KEY is not set"


# ---------------------------------------------------------------------------
# which model answered — the payload's provenance
# ---------------------------------------------------------------------------


class TestServedModel:
    """`openrouter/free` is a *router*, so the id in the request is not a model.

    Without the response's `model`, a captured transcript is attributed to "the
    free router" — which is not an answer to "which model wrote this?", the
    question a grading failure immediately raises.
    """

    @staticmethod
    def _openrouter_body(content: str, **extra) -> dict:
        return {"choices": [{"message": {"content": content}}], **extra}

    def test_openrouter_records_the_model_the_router_picked(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setattr(
            ct,
            "_request",
            _recorder(
                [],
                body=self._openrouter_body(
                    "Decision=CREATE", model="upstage/solar-pro-3:free"
                ),
            ),
        )
        text, error = ct.openrouter_completion("prompt")
        assert error == ""
        assert "Decision=CREATE" in text
        assert ct.served_model("openrouter") == "upstage/solar-pro-3:free"

    def test_a_response_without_a_model_records_nothing(self, monkeypatch):
        # Recorded, not assumed: a provider that does not name the model must not
        # leave the previous answer standing as this one's attribution.
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setattr(
            ct, "_request", _recorder([], body=self._openrouter_body("Decision=SKIP"))
        )
        ct.openrouter_completion("prompt")
        assert ct.served_model("openrouter") == ""

    def test_a_failed_completion_records_nothing(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setattr(
            ct, "_request", _recorder([], body=None, error="HTTP 429: slow down")
        )
        text, error = ct.openrouter_completion("prompt")
        assert text == "" and "429" in error
        assert ct.served_model("openrouter") == ""

    def test_gemini_records_the_id_it_resolved(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ct, "_request", _recorder([], body=GEMINI_OK))
        assert ct.run_gemini("prompt", _args()) != ""
        assert ct.served_model("gemini") == ct.GEMINI_DEFAULT_MODEL

    def test_opencode_does_not_claim_a_model(self):
        """The CLI picks its own model and may fail over inside itself, so the id
        on the command line is what was *asked for*. A wrong attribution is worse
        than none, so this returns nothing rather than the argument."""
        assert ct.served_model("opencode") == ""
        assert ct.served_model("replay") == ""


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class TestCli:
    @staticmethod
    def _no_keys_env() -> dict:
        """Strip every rung credential, not just the Gemini one.

        The ambient environment here really does carry `ANTHROPIC_AUTH_TOKEN`,
        so a test that only removed `GEMINI_API_KEY` would assert "nothing is
        configured" while a rung was in fact configured.
        """
        env = dict(os.environ)
        for var in ("GEMINI_MODEL", *ct.RUNG_CREDENTIALS["gemini"], *ct.RUNG_CREDENTIALS["opencode"], *ct.RUNG_CREDENTIALS["openrouter"]):
            env.pop(var, None)
        # The `opencode` CLI counts as a configured rung on its own (it may hold
        # its own auth), so a "nothing is configured" environment must also hide
        # the binary. `sys.executable` is absolute, so Python still starts.
        env["PATH"] = "/usr/bin:/bin"
        return env

    def test_runner_choices_include_gemini_and_ladder(self):
        result = _run(["--help"])
        assert result.returncode == 0
        assert "gemini" in result.stdout
        assert "ladder" in result.stdout

    def test_list_models_reports_the_ladder_without_a_key(self):
        """A report, so it exits 0 even when the report is "all rungs down"."""
        result = _run(["--list-models"], env=self._no_keys_env())
        assert result.returncode == 0
        assert "ladder: openrouter -> gemini -> opencode" in result.stdout
        assert "NO   rung gemini:" in result.stdout
        assert "NO   gemini models: GEMINI_API_KEY is not set" in result.stdout

    def test_check_rungs_fails_when_nothing_is_configured(self):
        result = _run(["--check-rungs"], env=self._no_keys_env())
        assert result.returncode == 1
        assert "NO   rung gemini:" in result.stdout
        assert "no LLM rung is configured" in result.stderr

    def test_check_rungs_passes_with_one_rung_configured(self, monkeypatch):
        env = self._no_keys_env()
        env["GEMINI_API_KEY"] = "dummy"
        result = _run(["--check-rungs"], env=env)
        assert result.returncode == 0
        assert "OK   rung gemini: GEMINI_API_KEY is set" in result.stdout
        # Reported, not proven: a set key can still be rejected (the dead
        # OPENROUTER_API_KEY this repo hit returns 401).
        assert "configuration, not validity" in result.stdout

    def test_gemini_runner_fails_loudly_without_a_key(self, tmp_path):
        out = tmp_path / "t.json"
        result = _run(
            ["--runner", "gemini", "--only", "1", "--out", str(out)],
            env=self._no_keys_env(),
        )
        assert result.returncode == 1
        assert "GEMINI_API_KEY is not set" in result.stderr
        # A run that captured nothing must not leave a file that looks captured.
        assert not out.exists()


class TestProbeOpencodeCli:
    """The gap this closes, found in the wild on this machine.

    `--check-rungs` reported `OK rung opencode: ANTHROPIC_AUTH_TOKEN is set` while
    `opencode --version` failed with `failed to install the right version of the
    opencode CLI for your platform` — a shim on PATH that cannot run. The rung
    status is presence-based by design, and `--list-models` probed the OpenRouter
    key and the Gemini list but **not** this rung, so nothing anywhere checked
    whether it was usable. The CI preflight passed; the capture then produced no
    transcript.
    """

    def test_no_cli_on_path(self, monkeypatch):
        monkeypatch.setattr(ct.shutil, "which", lambda name: None)
        ok, detail = ct.probe_opencode_cli()
        assert ok is False
        assert "no `opencode` on PATH" in detail

    def test_a_broken_shim_is_reported_with_its_own_message(self, monkeypatch, tmp_path):
        broken = tmp_path / "opencode"
        broken.write_text(
            "#!/bin/sh\necho 'failed to install the right version' >&2\nexit 1\n",
            encoding="utf-8",
        )
        broken.chmod(0o755)
        monkeypatch.setattr(ct.shutil, "which", lambda name: str(broken))
        ok, detail = ct.probe_opencode_cli()
        assert ok is False
        assert "failed to install the right version" in detail
        assert "exit 1" in detail

    def test_a_working_cli_reports_its_version(self, monkeypatch, tmp_path):
        working = tmp_path / "opencode"
        working.write_text("#!/bin/sh\necho '0.4.2'\n", encoding="utf-8")
        working.chmod(0o755)
        monkeypatch.setattr(ct.shutil, "which", lambda name: str(working))
        ok, detail = ct.probe_opencode_cli()
        assert ok is True
        assert detail == "0.4.2"

    def test_a_hanging_cli_is_a_failure_not_a_hang(self, monkeypatch, tmp_path):
        slow = tmp_path / "opencode"
        slow.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        slow.chmod(0o755)
        monkeypatch.setattr(ct.shutil, "which", lambda name: str(slow))
        ok, detail = ct.probe_opencode_cli(timeout=1)
        assert ok is False
        assert "TimeoutExpired" in detail

    def test_output_is_truncated_so_one_line_stays_one_line(self, monkeypatch, tmp_path):
        noisy = tmp_path / "opencode"
        noisy.write_text("#!/bin/sh\necho 'x' | tr -d '\\n'; printf 'y%.0s' $(seq 400)\n", encoding="utf-8")
        noisy.chmod(0o755)
        monkeypatch.setattr(ct.shutil, "which", lambda name: str(noisy))
        ok, detail = ct.probe_opencode_cli()
        assert ok is True
        assert len(detail) <= 160
        assert "\n" not in detail

    def test_the_report_includes_the_cli_probe(self):
        # `--list-models` is the report a human reads when a run captured
        # nothing; a rung that is "configured" but unusable has to appear in it.
        # The PATH is emptied for the subprocess so this is deterministic
        # wherever the suite runs — including on a machine that *does* have a
        # working `opencode`, where the line would otherwise read OK.
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in _all_rung_credentials()
        }
        env["PATH"] = "/nonexistent"
        result = _run(["--list-models"], env=env)
        assert result.returncode == 0
        assert "NO   opencode cli: no `opencode` on PATH" in result.stdout
