#!/usr/bin/env python3
"""capture_transcripts.py — capture REAL runtime transcripts for the eval cases.

Replaces the hand-written canned stubs of `runtime_eval.py` with outputs produced
by an actual run (spec §9.1). Nothing here fakes a result: the runner is invoked
per case and its stdout is recorded verbatim, so `runtime_eval.py --transcripts`
grades reality rather than a fixture authored to pass.

Runners:

    gemini     direct HTTPS to `generativelanguage.googleapis.com`. This is rung
               1 of the documented ladder (`live-stream-runtime-spec.md` §4):
               the **free AI Studio** product, which needs no GCP billing. It is
               the only rung with no CLI dependency, so it also works on a bare
               runner. `--list-models` reports the ladder and the live model
               list without spending a generate call.
    opencode   `opencode run --attach URL --model M --format json <prompt>`
               (the production path; needs credentials)
    openrouter direct HTTPS to the provider ladder's last rung. Works without
               the opencode CLI, so a capture is possible from a plain runner.
               Defaults to `openrouter/free`, which selects $0 models.
    ladder     failover across the documented ladder in order — gemini, then
               opencode, then openrouter — recording which rung served each
               case. Free tiers are withdrawn without notice (GitHub Models,
               2026-07-30), so a single hardcoded rung rots; the per-rung error
               is kept so a total failure names every rung that was tried.
    command    any shell command; the prompt is fed on stdin, stdout is captured
               (use this to point at a local wrapper or a different agent CLI)
    replay     copies an existing transcripts file — no execution. Use to verify
               the grading path offline, or in CI without secrets.

Usage:
    python3 scripts/capture_transcripts.py --runner replay \\
        --from tests/fixtures/runtime_transcripts.json --check

    python3 scripts/capture_transcripts.py --runner opencode \\
        --model opencode/big-pickle --attach http://localhost:4096 \\
        --out tests/fixtures/runtime_transcripts.json --only 21 --only 22

Exit codes:
    0  PASS — every requested case produced a transcript
    1  FAIL — one or more cases produced no output
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT = "tests/fixtures/runtime_transcripts.json"
RUNNERS = ("gemini", "opencode", "openrouter", "ladder", "command", "replay")
TIMEOUT_SECONDS = 900
# $0 by construction: the /free router picks from the no-cost model list.
OPENROUTER_DEFAULT_MODEL = "openrouter/free"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# Rung 1 of the ladder: the FREE AI Studio product, a different thing from the
# paid Gemini API on a GCP billing account. The model id below is a LAST-RESORT
# fallback, not a pin — `resolve_gemini_model` asks the API first, because a
# hardcoded id is exactly how a rung rots unnoticed.
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash-lite"
# Ladder order. Was spec §4's "Gemini free -> OpenCode Zen free -> OpenRouter
# free"; reordered 2026-09-17 by operator decision: the FREE OpenRouter router
# serves first, the AI Studio key second, the Zen CLI last. OpenRouter's free
# tier is rate-limited (20 req/min, 50 req/day; 1,000/day after a one-time $10
# deposit), which a once-daily runtime fits comfortably — and its key works
# from any runner, unlike the Zen free tier (in-CLI only).
LADDER = ("openrouter", "gemini", "opencode")
GEMINI_VERSION = re.compile(r"^gemini-(\d+(?:\.\d+)?)")
OPENROUTER_KEY_ENDPOINT = "https://openrouter.ai/api/v1/key"
# What makes each rung usable. The list is the union of the documented ladder
# **and** the providers the opencode CLI accepts on its own: a preflight that
# reds a correctly configured runner gets deleted, so a false "nothing is
# configured" is the failure mode to design against here.
RUNG_CREDENTIALS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "opencode": (
        "OPENCODE_ZEN_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def load_cases(root: Path, only: list[int]) -> list[dict]:
    path = root / "evals" / "evals.json"
    if not path.is_file():
        print(f"FAIL: capture_transcripts: {path}: file missing", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"FAIL: capture_transcripts: {path}: invalid JSON ({exc})",
            file=sys.stderr,
        )
        sys.exit(2)
    cases = [c for c in payload.get("evals", []) if isinstance(c, dict)]
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.get("id") in wanted]
    return cases


def load_transcripts(path: Path) -> dict[int, str]:
    """Accept either {id: str} or {"transcripts": [{"id":..,"output":..}]}."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(
            f"FAIL: capture_transcripts: {path}: file not found", file=sys.stderr
        )
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(
            f"FAIL: capture_transcripts: {path}: invalid JSON ({exc})",
            file=sys.stderr,
        )
        sys.exit(2)
    if isinstance(payload, dict) and "transcripts" in payload:
        payload = payload["transcripts"]
    out: dict[int, str] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            try:
                out[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                out[int(item["id"])] = str(item.get("output", ""))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def run_opencode(prompt: str, args) -> str:
    command = ["opencode", "run"]
    if args.attach:
        command += ["--attach", args.attach]
    if args.model:
        command += ["--model", args.model]
    if args.format:
        command += ["--format", args.format]
    for extra in args.file or []:
        command += ["--file", extra]
    command.append(prompt)
    return _exec(command)


# Diagnostics from the most recent attempt per runner, so a failed capture says
# WHY instead of silently reporting "no output". A dead credential must never
# look like a model that simply returned nothing.
LAST_ERRORS: dict[str, str] = {}

# The model that actually answered, per rung — recorded where a rung can support
# the claim and left empty where it cannot. `openrouter` is the case that forces
# this to exist: `openrouter/free` is a *router*, so the id sent in the request is
# deliberately not a model. Without the response's `model`, a transcript the free
# router served is attributed to "the free router", which is not an answer to
# "which model wrote this?" — and that is the question a grading failure raises.
LAST_MODELS: dict[str, str] = {}


def _note_error(runner: str, message: str) -> None:
    LAST_ERRORS[runner] = message


def _request(
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
) -> tuple[int, object, str]:
    """The only place this module touches the network. `(status, body, error)`.

    `error` is `""` only when a status *and* a JSON body were both obtained, so
    no caller can mistake a transport failure — or an HTML error page — for a
    model that answered with nothing. Every provider above shares this one
    function, and the tests monkeypatch it rather than opening a socket.
    """
    import urllib.error
    import urllib.request

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return int(exc.code), None, f"HTTP {exc.code}: {detail or exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, None, f"{type(exc).__name__}: {exc}"
    if not raw.strip():
        return status, None, f"HTTP {status}: empty response body"
    try:
        return status, json.loads(raw), ""
    except json.JSONDecodeError as exc:
        return status, None, f"HTTP {status}: response was not JSON ({exc})"


def rung_status(*, attach: str | None = None) -> list[tuple[str, bool, str]]:
    """`[(rung, configured, detail)]`, decided by the environment alone.

    Deliberately **presence**, not validity, and therefore offline and
    deterministic: a CI preflight that made a network call would red a run for a
    reason that is not the run's fault. A key that is present but rejected is a
    different failure, reported by `--list-models` and by the capture itself.
    """
    status: list[tuple[str, bool, str]] = []
    for rung in LADDER:
        envs = RUNG_CREDENTIALS[rung]
        present = [name for name in envs if os.environ.get(name)]
        if present:
            status.append((rung, True, f"{present[0]} is set"))
        elif rung == "opencode" and (attach or shutil.which("opencode")):
            status.append(
                (rung, True, "CLI present; it may hold its own auth")
            )
        else:
            status.append((rung, False, f"none of {'/'.join(envs)} is set"))
    return status


def probe_opencode_cli(*, timeout: int = 30) -> tuple[bool, str]:
    """Does the `opencode` rung's CLI actually start?

    Written after finding the exact gap this closes: `--check-rungs` reported
    `OK rung opencode: ANTHROPIC_AUTH_TOKEN is set` while `opencode --version`
    failed with "failed to install the right version of the opencode CLI for
    your platform" (a shim on PATH that cannot run). `rung_status` is
    presence-based by design, and `--list-models` probed the OpenRouter key and
    the Gemini list but **not this rung** — so for `opencode` there was no
    validity check anywhere. A run would pass the CI preflight and then produce
    no transcript, which is the silent failure the preflight exists to prevent.

    Zero-token: `--version` starts the binary and exits.
    """
    executable = shutil.which("opencode")
    if not executable:
        return False, "no `opencode` on PATH"
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    first = output[0][:160] if output else ""
    if completed.returncode != 0:
        return False, f"exit {completed.returncode}: {first}" or "no output"
    return True, first or "the binary starts"


def probe_openrouter_key(*, timeout: int = 60) -> tuple[bool, str]:
    """Free, zero-token key check: `GET /api/v1/key`.

    Written after finding a key that was *present* and returned
    `401 User not found`. A dead credential is the one failure this whole module
    exists to tell apart from a model that answered with nothing, so it is worth
    one free request to catch it before spending 36 generate calls.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return False, "OPENROUTER_API_KEY is not set"
    _, body, error = _request(
        "GET",
        OPENROUTER_KEY_ENDPOINT,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    if error:
        return False, error
    data = (body or {}).get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return False, "key probe had no data object"
    if data.get("label") is None and data.get("limit") is None and data.get("usage") is None:
        return False, f"key accepted but unrecognised: {data}"[:120]
    return True, "key accepted"


def gemini_api_key() -> str:
    """The AI Studio key. `GEMINI_API_KEY` is the documented name; the SDK also
    accepts `GOOGLE_API_KEY`, so a runner configured either way still works."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def list_gemini_models(*, timeout: int = 60) -> tuple[list[str], str]:
    """Model ids that support `generateContent`. Returns `(ids, error)`.

    Read live rather than pinned: an id that has been retired, or one this free
    project is not entitled to, must fail here with a named reason instead of
    surfacing later as "the model returned nothing".
    """
    key = gemini_api_key()
    if not key:
        return [], "GEMINI_API_KEY is not set"
    status, body, error = _request(
        "GET",
        f"{GEMINI_ENDPOINT}/models",
        headers={"x-goog-api-key": key},
        timeout=timeout,
    )
    if error:
        return [], error
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return [], f"HTTP {status}: models.list had no models array"
    ids: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = str(item.get("name") or "")
        ids.append(name[len("models/") :] if name.startswith("models/") else name)
    return [name for name in ids if name], ""


def rank_gemini_models(ids: list[str]) -> list[str]:
    """Best-first: the Lite tier, newest version, non-preview.

    Why Lite first is a cost decision, not a quality one — the Lite tier
    carries the most generous free RPD (spec §15.3 item 3), and a capture run
    spends one call per eval case.
    """

    def score(model: str) -> tuple[int, float, int, str]:
        match = GEMINI_VERSION.match(model)
        return (
            1 if "lite" in model else 0,
            float(match.group(1)) if match else 0.0,
            0 if ("preview" in model or "exp" in model) else 1,
            model,
        )

    flash_like = [m for m in ids if "flash" in m or "lite" in m]
    return sorted(flash_like or list(ids), key=score, reverse=True)


def resolve_gemini_model(args) -> tuple[str, str]:
    """`(model_id, where_it_came_from)`.

    Order: explicit `--model`, then `GEMINI_MODEL`, then a LIVE `models.list`
    probe, then the fallback constant. The probe is the point of this function
    — free tiers are withdrawn without notice, so the id is discovered at run
    time and the source is reported next to it.
    """
    # In ladder mode `--model` names the *CLI* rung's model (`opencode/...`), so
    # it must not be handed to Gemini. Pin Gemini with GEMINI_MODEL instead.
    if args.model and getattr(args, "runner", None) != "ladder":
        return args.model, "--model"
    from_env = os.environ.get("GEMINI_MODEL")
    if from_env:
        return from_env, "GEMINI_MODEL"
    ids, error = list_gemini_models(timeout=args.timeout)
    if ids:
        return rank_gemini_models(ids)[0], "models.list"
    return GEMINI_DEFAULT_MODEL, f"fallback (models.list said: {error})"


def gemini_completion(
    prompt: str, *, model: str = GEMINI_DEFAULT_MODEL, timeout: int = 180
) -> tuple[str, str]:
    """Call Gemini `generateContent`. Returns `(text, error)`.

    Same contract as `openrouter_completion`: `error` is `""` only on a usable
    completion, and a missing key, a rejected key, a 429 quota exhaustion, a
    safety block and an empty candidate set are all reported distinctly.
    """
    key = gemini_api_key()
    if not key:
        return "", "GEMINI_API_KEY is not set"
    status, body, error = _request(
        "POST",
        f"{GEMINI_ENDPOINT}/models/{model}:generateContent",
        payload={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        },
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        timeout=timeout,
    )
    if error:
        return "", error
    if not isinstance(body, dict):
        return "", f"HTTP {status}: unexpected response shape"
    feedback = body.get("promptFeedback") or {}
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return "", f"prompt blocked: {feedback['blockReason']}"
    candidates = body.get("candidates") or []
    if not candidates:
        detail = body.get("error") or body
        return "", f"no candidates in response: {detail}"[:200]
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = ((first.get("content") or {}).get("parts")) or []
    text = "".join(
        str(part.get("text") or "") for part in parts if isinstance(part, dict)
    )
    if not text.strip():
        return "", (
            "model returned an empty completion "
            f"(finishReason={first.get('finishReason') or 'none'})"
        )
    return text, ""


def openrouter_completion(
    prompt: str, *, model: str = OPENROUTER_DEFAULT_MODEL, timeout: int = 180
) -> tuple[str, str]:
    """Call the OpenRouter chat endpoint. Returns `(text, error)`.

    `error` is `""` only on success — a missing key, a rejected key (401), a
    rate limit (429) and an unreachable host are all reported distinctly, so a
    capture run can say what went wrong rather than reporting an empty result.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return "", "OPENROUTER_API_KEY is not set"
    status, body, error = _request(
        "POST",
        OPENROUTER_ENDPOINT,
        payload={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/d-o-hub/skill-basketball-streams",
            "X-Title": "skill-basketball-streams capture",
        },
        timeout=timeout,
    )
    if error:
        return "", error
    if not isinstance(body, dict):
        return "", f"HTTP {status}: unexpected response shape"
    choices = body.get("choices") or []
    if not choices:
        error_detail = body.get("error")
        return "", f"no choices in response: {error_detail if error_detail else body}"[:200]
    message = choices[0].get("message") or {}
    text = str(message.get("content") or "")
    if not text.strip():
        return "", "model returned an empty completion"
    served = body.get("model")
    if isinstance(served, str) and served.strip():
        LAST_MODELS["openrouter"] = served.strip()
    return text, ""


def openrouter_model_for(args) -> str:
    """The model this rung may use. `--model` is not ours in ladder mode.

    In `--runner ladder` a `--model` names the *CLI* rung's model (`opencode/...`,
    `docs/runtime.md`), so it is not an OpenRouter slug. Handing it over turned a
    working rung into `no choices in response` — a rung that reads as dead for a
    reason that has nothing to do with the rung. Same guard, same reason, as
    `resolve_gemini_model` applies on the other side of the ladder.
    """
    if args.model and getattr(args, "runner", None) != "ladder":
        return args.model
    return OPENROUTER_DEFAULT_MODEL


def run_openrouter(prompt: str, args) -> str:
    """Direct API call — no CLI needed. Records the reason on failure."""
    text, error = openrouter_completion(
        prompt,
        model=openrouter_model_for(args),
        timeout=args.timeout,
    )
    if error:
        _note_error("openrouter", error)
        return ""
    return text


# Resolved once per process: the probe costs a request, and free-tier capacity
# cannot change mid-run in a way that helps.
_GEMINI_MODEL: list[str] = []


def gemini_model_for(args) -> str:
    if not _GEMINI_MODEL:
        model, source = resolve_gemini_model(args)
        _GEMINI_MODEL.append(model)
        print(
            f"INFO: capture_transcripts: gemini model {model} (from {source})",
            file=sys.stderr,
        )
    return _GEMINI_MODEL[0]


def served_model(rung: str) -> str:
    """The model id that answered for `rung`, or `""` when it cannot be known.

    Only two of the three rungs can support the claim. `gemini` is a direct call
    to one id, so the id that was resolved IS the model. `openrouter` reports the
    model it routed to in the response body. `opencode` runs a CLI that picks its
    own model and may fail over inside itself, so the id passed on the command
    line is what was *asked for* — returning it would be a claim this function
    cannot support, and a wrong attribution is worse than none.
    """
    return LAST_MODELS.get(rung, "")


def run_gemini(prompt: str, args) -> str:
    """Direct AI Studio call — no CLI, no billing. Records the reason on failure."""
    model = gemini_model_for(args)
    text, error = gemini_completion(prompt, model=model, timeout=args.timeout)
    if error:
        _note_error("gemini", error)
        return ""
    LAST_MODELS["gemini"] = model
    return text


def run_ladder(prompt: str, args, served: list[str]) -> str:
    """Try each rung in order; `served` receives the rung that answered.

    `opencode` is skipped without `--attach`: the CLI path needs a live backend,
    and a rung that was never reachable is not a rung that failed.
    """
    for rung in LADDER:
        if rung == "opencode" and not args.attach:
            continue
        if rung == "gemini":
            output = run_gemini(prompt, args)
        elif rung == "opencode":
            output = run_opencode(prompt, args)
        else:
            output = run_openrouter(prompt, args)
        if output.strip():
            served.append(rung)
            return output
    return ""


def run_command(prompt: str, args) -> str:
    command = shlex.split(args.command)
    try:
        proc = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return ""
    return proc.stdout or ""


def _exec(command: list[str]) -> str:
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def build_prompt(case: dict) -> str:
    """The prompt a capture run sends. Deliberately carries **no** answer.

    This used to append `Expected shape for reference: <expected_output[:200]>`.
    For a case whose canonical output is about 200 characters long that is not a
    shape hint, it is the answer — and since grading collects `name=PASS|FAIL`
    needles by substring, a model that echoed the prompt back would score 100%.
    The check *vocabulary* is fair to state (the verdict is not), so the prompt
    names the form and points at the reference that names the checks.
    """
    return (
        "You are executing the skill-basketball-streams pipeline for one case. "
        "Apply the 7 checks and reply with exactly one line, no prose:\n"
        "Decision=<CREATE|SKIP|REJECT>; reason=<short reason>; "
        "checks: <check>=PASS|FAIL, <check>=PASS|FAIL, ...\n\n"
        "Use the check names exactly as `references/validation-workflow.md` "
        "names them.\n\n"
        f"Case {case.get('id')}: {case.get('prompt', '')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture real runtime transcripts for evals/evals.json cases.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--runner", choices=RUNNERS, default="replay")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--from", dest="source", help="source transcripts for --runner replay"
    )
    parser.add_argument(
        "--model",
        help="model id; for --runner openrouter defaults to "
             f"{OPENROUTER_DEFAULT_MODEL} ($0)",
    )
    parser.add_argument("--attach")
    parser.add_argument(
        "--format", default="json", help="opencode --format value (default: json)"
    )
    parser.add_argument(
        "--file", action="append",
        help="extra file/dir to attach (repeatable)",
    )
    parser.add_argument("--command", help="shell command for --runner command")
    parser.add_argument("--only", type=int, action="append", default=[])
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; only report which cases have usable output",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="report the ladder, which credentials are set, and the live Gemini "
             "model list (Lite tier first) — spends no generate call",
    )
    parser.add_argument(
        "--check-rungs",
        action="store_true",
        help="fail when NO ladder rung is configured; offline and deterministic, "
             "so a CI preflight can gate on it, unlike --list-models",
    )
    args = parser.parse_args()

    # A scheduled run with no LLM configured at all is a configuration fault,
    # not a quiet day: it would look exactly like "no free streams found". This
    # answers that offline, so a preflight can fail loudly on it.
    if args.check_rungs:
        status = rung_status(attach=args.attach)
        for rung, configured, detail in status:
            print(f"{'OK  ' if configured else 'NO  '} rung {rung}: {detail}")
        if not any(configured for _, configured, _ in status):
            print(
                "FAIL: capture_transcripts: no LLM rung is configured — set "
                "GEMINI_API_KEY (a free AI Studio key needs no billing) or one "
                "of the CLI providers",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "note: this reports configuration, not validity — a set key can "
            "still be rejected. `--list-models` proves it."
        )
        sys.exit(0)

    # A **report**, not a gate, and it exits 0 whenever it managed to report —
    # including when the report is "every rung is dead", which is a successful
    # answer to the question asked. `--check-rungs` is the gate.
    if args.list_models:
        print(f"ladder: {' -> '.join(LADDER)}")
        for rung, configured, detail in rung_status(attach=args.attach):
            print(f"{'OK  ' if configured else 'NO  '} rung {rung}: {detail}")
        ok, detail = probe_opencode_cli(timeout=args.timeout)
        print(f"{'OK  ' if ok else 'NO  '} opencode cli: {detail}")
        ok, detail = probe_openrouter_key(timeout=args.timeout)
        print(f"{'OK  ' if ok else 'NO  '} openrouter key: {detail}")
        ids, error = list_gemini_models(timeout=args.timeout)
        if error:
            print(f"NO   gemini models: {error}")
        for model in rank_gemini_models(ids):
            print(f"gemini model: {model}")
        sys.exit(0)

    root = Path(args.root).resolve()
    cases = load_cases(root, args.only)
    if not cases:
        print("FAIL: capture_transcripts: no matching eval cases", file=sys.stderr)
        sys.exit(2)

    if args.runner == "command" and not args.command:
        parser.error("--runner command requires --command")
    if args.runner == "replay" and not args.source:
        parser.error("--runner replay requires --from")

    replayed = load_transcripts(Path(args.source)) if args.runner == "replay" else {}

    # Resolve the Gemini model once, and say which one — a silent fallback is
    # how a capture gets attributed to a model that never answered.
    if args.runner in ("gemini", "ladder"):
        gemini_model_for(args)

    transcripts: dict[str, str] = {}
    rungs: dict[str, str] = {}
    models: dict[str, str] = {}
    missing: list[int] = []
    for case in cases:
        case_id = case.get("id")
        # Which rung answered this case. Assigned before the call rather than
        # after, so the attribution is read from the same variable the call was
        # dispatched on instead of from a second, independent guess.
        rung = args.runner
        if args.runner == "replay":
            output = replayed.get(case_id, "")
        elif args.runner == "gemini":
            output = run_gemini(build_prompt(case), args)
        elif args.runner == "opencode":
            output = run_opencode(build_prompt(case), args)
        elif args.runner == "openrouter":
            output = run_openrouter(build_prompt(case), args)
        elif args.runner == "ladder":
            served: list[str] = []
            output = run_ladder(build_prompt(case), args, served)
            if output.strip():
                rung = served[-1]
                rungs[str(case_id)] = rung
        else:
            output = run_command(build_prompt(case), args)
        if not output.strip():
            # A case that produced nothing is never attributed to a model: the
            # per-rung record still holds the last rung that DID answer, and
            # reading it here would credit this case to that model.
            missing.append(case_id)
            continue
        transcripts[str(case_id)] = output.strip()
        answered = served_model(rung)
        if answered:
            models[str(case_id)] = answered

    payload = {
        "generated_by": f"capture_transcripts.py --runner {args.runner}",
        "cases": len(cases),
        "captured": len(transcripts),
        "transcripts": [
            {"id": int(key), "output": value} for key, value in sorted(
                transcripts.items(), key=lambda kv: int(kv[0])
            )
        ],
    }
    # Provenance, so a graded transcript can be traced to the model that wrote
    # it and the rung that served it rather than to "the ladder" in general.
    #
    # `model` is the single-rung shape (`--runner gemini`) and is derived from
    # `LAST_MODELS` rather than from `_GEMINI_MODEL` directly, so the two keys
    # cannot disagree about which model answered.
    if args.runner == "gemini":
        top_model = served_model("gemini")
        if top_model:
            payload["model"] = top_model
    if rungs:
        payload["rungs"] = rungs
    if models:
        payload["models"] = models

    if missing:
        print(
            "FAIL: capture_transcripts: no output for case(s) "
            f"{sorted(missing)} — a transcript must never be invented",
            file=sys.stderr,
        )
        diagnosed = LAST_ERRORS.get(args.runner)
        if diagnosed:
            print(
                f"FAIL: capture_transcripts: runner '{args.runner}' reported: "
                f"{diagnosed}",
                file=sys.stderr,
            )
        elif args.runner == "opencode":
            print(
                "FAIL: capture_transcripts: is the opencode CLI installed and "
                "is the backend reachable via --attach?",
                file=sys.stderr,
            )
        elif args.runner == "ladder":
            # Name every rung that was tried. "The ladder failed" is not a
            # diagnosis; three named reasons are.
            print(
                "FAIL: capture_transcripts: ladder rungs reported: "
                + "; ".join(
                    f"{rung}: {LAST_ERRORS.get(rung, 'no output, no error')}"
                    for rung in LADDER
                    if rung != "opencode" or args.attach
                ),
                file=sys.stderr,
            )

    if args.check:
        print(
            f"OK: capture_transcripts: {len(transcripts)}/{len(cases)} cases have "
            f"a usable transcript"
        )
        sys.exit(1 if missing else 0)

    if missing:
        # Deliberately no file at all. The downstream gate keys on the *file
        # existing* (`if [ -f tests/fixtures/runtime_transcripts.json ]`), so
        # writing the 30 cases that worked would present a partial capture as a
        # complete one and quietly narrow what the gate covers.
        print(
            "FAIL: capture_transcripts: nothing written — a partial transcript "
            "file would satisfy the existence check that gates grading.",
            file=sys.stderr,
        )
        sys.exit(1)

    out = Path(args.out)
    if out.parent and str(out.parent) not in ("", "."):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"OK: capture_transcripts: wrote {len(transcripts)} transcripts -> {out}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
