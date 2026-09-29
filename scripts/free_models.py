#!/usr/bin/env python3
"""free_models.py — which free models exist, right now.

Why this exists. `references/search-backends.md` carried a hand-written list of
six "genuinely free" OpenCode model ids, taken from a live `GET /v1/models` on
2026-09-14 and annotated *"the free catalogue rotates — re-read before relying on
a specific ID."* Three weeks later **three of the six no longer resolved** against
the CLI, and `big-pickle` — the one the runtime pins — had degenerated into
gibberish mid-generation on a real run. A stale list is not a list that is out of
date; it is a list that is confidently wrong.

So the list is fetched. `https://models.dev/api.json` publishes every provider's
catalogue with a `cost` block per model, and `cost` all-zero is the definition of
free that does not depend on a name suffix — `big-pickle` has no `-free` in it,
while `muse-spark-1.2-contributor-free` does, and both are genuinely free.

**This is a report, not a gate.** It deliberately does not decide which model the
run uses, and nothing in `runtime-daily.yml` calls it on the critical path. That
separation is the point:

* a *selection* that follows a live catalogue changes the model under a pinned
  expectation with no diff to review, and a provider hiccup becomes a red cron;
* a *report* is looked at by a human, who then changes a pinned constant on a
  branch, where the change is reviewable and the reason is written down.

That is the same reason `rehearse.py --offline` and `capture_transcripts
--check-rungs` are presence-based rather than validity-based, and the same
reason `write_mode.py` exists: **a decision that can be tested is a decision; a
decision that can only be observed at 08:30 is a dependency.**

Usage:
    python3 scripts/free_models.py                      # the table
    python3 scripts/free_models.py --json               # payload on stdout alone
    python3 scripts/free_models.py --provider opencode  # a different provider
    python3 scripts/free_models.py --pinned             # is the pinned id still free?
    python3 scripts/free_models.py --all                # include paid models

Exit codes:
    0  a report was produced
    1  the catalogue could not be fetched or parsed (offline is a legitimate
       state for this command: it is a report, and a report that cannot be made
       says so rather than guessing)
    2  USAGE — bad arguments

The `pinned` mode is the one that earns its keep in CI: it answers "is the id in
`llm_model.py` still a free, tool-calling model?" without changing it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

CATALOGUE_URL = "https://models.dev/api.json"
DEFAULT_PROVIDER = "opencode"
DEFAULT_TIMEOUT = 30
# The model the runtime pins today. Duplicated as a constant rather than imported
# so this file stays a stdlib-only report and cannot be broken by an import cycle
# with `llm_model.py`; `test_free_models.py` asserts the two agree.
PINNED_MODEL = "opencode/big-pickle"

# Characters that would break a `>> "$GITHUB_OUTPUT"` line or a markdown table
# cell if a provider ever put them in a display name. Same set as
# `write_mode.UNSAFE_IN_REASON`, restated so this module has no imports beyond the
# standard library.
_UNSAFE = str.maketrans({c: " " for c in "\n\r\t`\"'$\\|<>"})


def _safe(text: str) -> str:
    return " ".join(str(text or "").translate(_UNSAFE).split())


def fetch_catalogue(timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """The whole models.dev catalogue, or the reason it could not be read.

    Raises `RuntimeError` with a message that names the URL, so a network
    failure reads as "models.dev was unreachable" rather than as "no models
    exist" — the distinction that matters when the report is a gate.
    """
    request = urllib.request.Request(
        CATALOGUE_URL, headers={"User-Agent": "skill-basketball-streams/free_models"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"{CATALOGUE_URL} is unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{CATALOGUE_URL} did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{CATALOGUE_URL} returned {type(payload).__name__}")
    return payload


def is_free(model: dict[str, Any]) -> bool:
    """Is this model free?

    By **cost**, not by name. `big-pickle` is free and has no `-free` suffix;
    several `-free` ids have stopped resolving entirely. A name convention is a
    guess about a catalogue that changes; `cost` is the fact.
    """
    cost = model.get("cost")
    if not isinstance(cost, dict):
        return False
    values = list(cost.values())
    if not values:
        return False
    return all(value in (0, 0.0, None) for value in values)


def free_models(
    catalogue: dict[str, Any], provider: str = DEFAULT_PROVIDER
) -> list[dict[str, Any]]:
    """Every free model for `provider`, newest release first.

    Newest first because a *pinned* id that has just been replaced is the case
    worth seeing: the report is most useful exactly when the pin is ageing.
    """
    entry = catalogue.get(provider)
    if not isinstance(entry, dict):
        raise RuntimeError(f"provider {provider!r} is not in the catalogue")
    models = entry.get("models")
    if not isinstance(models, dict):
        raise RuntimeError(f"provider {provider!r} has no model list")
    free = [
        {**model, "id": str(model.get("id") or model_id)}
        for model_id, model in models.items()
        if isinstance(model, dict) and is_free(model)
    ]
    return sorted(free, key=lambda m: str(m.get("release_date") or ""), reverse=True)


def pinned_status(
    models: list[dict[str, Any]], pinned: str = PINNED_MODEL
) -> dict[str, Any]:
    """Is the pinned model still free, tool-calling, and how old is it?

    Deliberately answers rather than acts. A pin that has gone stale is a
    **reviewable change on a branch**, not something a nightly job should decide
    at 08:30 while nobody is reading.
    """
    bare = pinned.split("/", 1)[1] if "/" in pinned else pinned
    match = next((m for m in models if m["id"] == bare), None)
    if match is None:
        return {
            "pinned": pinned,
            "status": "not-free",
            "detail": "the pinned id is not among the free models in the catalogue",
        }
    return {
        "pinned": pinned,
        "status": "free",
        "released": str(match.get("release_date") or ""),
        "reasoning": bool(match.get("reasoning")),
        "tool_call": bool(match.get("tool_call")),
        "context": (match.get("limit") or {}).get("context"),
    }


def render_table(
    models: list[dict[str, Any]],
    *,
    provider: str = DEFAULT_PROVIDER,
    pinned: dict[str, Any] | None = None,
) -> str:
    """The PR-ready table. One row per model, newest first."""
    lines = [
        f"## Free `{provider}` models ({len(models)})",
        "",
        "| Model | Released | Reasoning | Tools | Context |",
        "|---|---|---|---|---|",
    ]
    for model in models:
        limit = model.get("limit") or {}
        context = limit.get("context")
        lines.append(
            "| `{id}` | {released} | {reasoning} | {tools} | {context} |".format(
                id=_safe(model["id"]),
                released=_safe(model.get("release_date") or "?"),
                reasoning="yes" if model.get("reasoning") else "-",
                tools="yes" if model.get("tool_call") else "-",
                context=f"{context:,}" if isinstance(context, int) else "?",
            )
        )
    if pinned:
        lines += ["", "## Pinned by the runtime", ""]
        lines.append(f"- `{_safe(pinned['pinned'])}` — **{pinned['status']}**")
        if pinned["status"] == "free":
            lines.append(
                f"  released {pinned['released']}, reasoning "
                f"{'yes' if pinned['reasoning'] else 'no'}, tools "
                f"{'yes' if pinned['tool_call'] else 'no'}"
            )
    lines += [
        "",
        "Free is decided by `cost`, not by a name suffix. This table is a "
        "**report**:",
        "nothing here changes the model a run uses — that is a pinned constant in",
        "`scripts/llm_model.py`, changed on a branch, so the diff explains itself.",
        f"Source: {CATALOGUE_URL}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report the models that are free right now (a report, not a gate)."
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--json", action="store_true", help="payload alone on stdout")
    parser.add_argument(
        "--pinned",
        action="store_true",
        help=f"report whether {PINNED_MODEL} is still free",
    )
    parser.add_argument("--all", action="store_true", help="include paid models")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--dry-run", action="store_true", help="accepted for symmetry; writes nothing"
    )
    args = parser.parse_args()

    try:
        catalogue = fetch_catalogue(args.timeout)
        if args.all:
            entry = catalogue.get(args.provider) or {}
            models = sorted(
                (
                    {**m, "id": str(m.get("id") or k)}
                    for k, m in (entry.get("models") or {}).items()
                    if isinstance(m, dict)
                ),
                key=lambda m: str(m.get("release_date") or ""),
                reverse=True,
            )
        else:
            models = free_models(catalogue, args.provider)
    except RuntimeError as exc:
        # Offline is a legitimate state for a report. Say so, and do not guess —
        # inventing a list here is how the hand-written one went stale.
        print(f"FAIL: free_models: {exc}", file=sys.stderr)
        sys.exit(1)

    if not models:
        print(
            f"FAIL: free_models: the catalogue lists no free {args.provider} models",
            file=sys.stderr,
        )
        sys.exit(1)

    pinned = pinned_status(models) if args.pinned else None
    if args.json:
        # stdout is the payload alone; every human line goes to stderr.
        print(
            json.dumps(
                {"provider": args.provider, "free": models, "pinned": pinned},
                indent=2,
            )
        )
        print(
            f"OK: free_models: {len(models)} free {args.provider} model(s)",
            file=sys.stderr,
        )
        return

    print(render_table(models, provider=args.provider, pinned=pinned))
    if pinned:
        status = pinned["status"]
        print(
            f"OK: free_models: {len(models)} free model(s); pinned "
            f"{pinned['pinned']} is {status}"
        )
    else:
        print(f"OK: free_models: {len(models)} free {args.provider} model(s)")


if __name__ == "__main__":
    main()
