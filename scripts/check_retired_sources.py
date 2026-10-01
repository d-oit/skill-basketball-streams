#!/usr/bin/env python3
"""check_retired_sources.py — a domain proven dead must stay dead.

The positive registry (`config/sources.json`) can say which hosts are approved.
It cannot say which ones **failed**, and that is the half that mattered here:
`basketball-bundesliga.de` stopped serving the BBL long before this repository
noticed, and because the failure and the absence of an entry look the same, the
correction existed only in a note in a document. The fetcher kept the dead URL
and reported a TLS error whose real cause was a stale string.

So the negative list is committed, with its evidence, and this gate reads the
three places a source can be *declared*:

    1. `config/sources.json`          — domains, social hosts, handles
    2. `scripts/fixtures.py`          — `DEFAULT_SOURCES`, the URLs actually fetched
    3. `references/approved-sources.md` — the **Domains column** only

The third is the interesting one. That document *must* be able to name a retired
domain in its Notes column -- that is where a human is told why it was retired --
so a check over the whole file would forbid the explanation and force the
retirement to be undocumented. Only column 3 of the table is a declaration; the
prose is evidence, and evidence has to be allowed to name the thing.

    python3 scripts/check_retired_sources.py --root .

Exit codes:
    0  PASS — no retired domain is declared as a source
    1  FAIL — a retired domain is back, or the list itself is unusable
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RETIRED_RELATIVE_PATH = "config/retired-sources.json"
SOURCES_RELATIVE_PATH = "config/sources.json"
FIXTURES_RELATIVE_PATH = "scripts/fixtures.py"
APPROVED_DOC_RELATIVE_PATH = "references/approved-sources.md"

# A host, optionally with a path, as it appears in a table cell: `x.com/FIBA`.
_HOSTLIKE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def _host_of(value: str) -> str:
    """A bare, lowercase, `www.`-stripped host from a URL or a bare host.

    `config/sources.json` lists social accounts as host+path (`x.com/FIBA`), and
    `scripts/fixtures.py` stores full URLs, so one normaliser has to accept both.
    """
    text = value.split("://", 1)[-1].strip().lower()
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" in text:
        # A port is not part of the host, and a registry entry written with one
        # would otherwise never match the bare host it retires.
        text = text.rsplit(":", 1)[0]
    return text[4:] if text.startswith("www.") else text


def load_retired(root: Path) -> list[dict]:
    """The negative registry, or a clear error about why it is unusable.

    An empty list would be the worst possible failure: the gate would pass
    having checked nothing, which is the vacuous sensor this script replaces.
    """
    path = root / RETIRED_RELATIVE_PATH
    if not path.is_file():
        raise ValueError(
            f"{RETIRED_RELATIVE_PATH} is missing — without it this gate asserts "
            f"nothing, which is worse than having no gate"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    retired = data.get("retired")
    if not isinstance(retired, list) or not retired:
        raise ValueError(
            f"{RETIRED_RELATIVE_PATH} has no non-empty 'retired' array; a gate "
            f"with nothing to check passes on anything"
        )
    domains = []
    for entry in retired:
        if not isinstance(entry, dict):
            raise ValueError(f"'retired' entry is not an object: {entry!r}")
        domain = str(entry.get("domain") or "").strip().lower()
        if not domain:
            raise ValueError("a 'retired' entry has no 'domain'")
        if not str(entry.get("reason") or "").strip():
            # A retirement with no stated cause cannot be evaluated later, so it
            # is not a fact yet, it is a shrug.
            raise ValueError(f"retired domain {domain!r} has no 'reason'")
        if not str(entry.get("retired_on") or "").strip():
            raise ValueError(f"retired domain {domain!r} has no 'retired_on' date")
        # A retirement that names a replacement must not name a dead one.
        for replacement in entry.get("replaced_by") or []:
            host = _host_of(str(replacement))
            if any(_matches(other, host) for other in domains):
                raise ValueError(
                    f"retired domain {domain!r} lists {replacement!r} as its "
                    f"replacement, but that host is itself retired"
                )
        domains.append({**entry, "domain": domain})
    return domains


def _matches(entry: dict, candidate: str) -> bool:
    """Is `candidate` covered by this retirement?

    **Exact host by default, and that default is load-bearing.** The first
    version took subdomains too, on the reasonable-sounding argument that
    retiring `example.com` and leaving `api.example.com` approved is a
    retirement that retires nothing. It fired immediately on this repository's
    own committed state, because the BBL fix for this very defect is a feed on
    `api.basketball-bundesliga.de` -- a subdomain of the retired host. A
    blanket subdomain rule would have forbidden the correction to the defect it
    was written to prevent.

    The asymmetry is real, so the field is explicit: a retirement records a
    **measured host failure**, and a host failure is about that host. An entry
    that genuinely means "this whole domain is gone" says
    `"takes_subdomains": true` and gets the stricter rule.
    """
    domain = _host_of(entry["domain"])
    # Normalised here rather than trusted from the caller: a caller that forgets
    # turns a retirement into a no-op, and it does so silently, which is the
    # failure mode this gate exists to detect.
    candidate = _host_of(candidate)
    if candidate == domain:
        return True
    return bool(entry.get("takes_subdomains")) and candidate.endswith("." + domain)


def _scan_registry(root: Path, retired: list[dict]) -> list[str]:
    from source_learning import load_sources  # local import: shares the loader

    data = load_sources(root)
    offences: list[str] = []
    for entry in data.get("sources", []):
        name = entry.get("name", "?")
        values: list[str] = []
        for key in ("domain", "domains", "social", "handles"):
            value = entry.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(item) for item in value)
        for value in values:
            host = _host_of(value)
            for item in retired:
                if _matches(item, host):
                    offences.append(
                        f"config/sources.json: {name!r} declares {value!r}, which "
                        f"is retired ({item['domain']}: {item['reason'][:60]}…)"
                    )
    return offences


def _scan_fixture_sources(root: Path, retired: list[dict]) -> list[str]:
    from fixtures import DEFAULT_SOURCES

    offences: list[str] = []
    for name, config in DEFAULT_SOURCES.items():
        host = _host_of(str(config.get("url") or ""))
        for item in retired:
            if _matches(item, host):
                offences.append(
                    f"scripts/fixtures.py: DEFAULT_SOURCES[{name!r}] fetches "
                    f"{config.get('url')!r}, which is retired "
                    f"({item['domain']}: {item['reason'][:60]}…)"
                )
    return offences


def _declared_domains_cell(row: str) -> str:
    """The Domains column of one markdown table row.

    Column 3, so the Notes column — where a retirement is *explained* — is not
    read. A prose mention is evidence; a column entry is a declaration.
    """
    if not row.lstrip().startswith("|"):
        return ""
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    if len(cells) < 3:
        return ""
    return cells[2]


def _scan_approved_doc(root: Path, retired: list[dict]) -> list[str]:
    path = root / APPROVED_DOC_RELATIVE_PATH
    if not path.is_file():
        return [f"{APPROVED_DOC_RELATIVE_PATH} is missing, so its table cannot be checked"]
    offences: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        cell = _declared_domains_cell(line)
        if not cell:
            continue
        for token in re.findall(r"`([^`]+)`", cell):
            host = _host_of(token)
            if not host or not _HOSTLIKE.match(host):
                continue
            for item in retired:
                if _matches(item, host):
                    offences.append(
                        f"references/approved-sources.md:{number}: the Domains "
                        f"column lists {token!r}, which is retired "
                        f"({item['domain']}: {item['reason'][:60]}…)"
                    )
    return offences


def check(root: Path) -> int:
    try:
        retired = load_retired(root)
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(f"FAIL: check_retired_sources: {error}", file=sys.stderr)
        return 1

    offences = (
        _scan_registry(root, retired)
        + _scan_fixture_sources(root, retired)
        + _scan_approved_doc(root, retired)
    )
    for line in offences:
        print(f"FAIL: check_retired_sources: {line}", file=sys.stderr)
    if offences:
        print(
            "FAIL: check_retired_sources: a retired domain is declared as a "
            "source again. If it works now, delete its entry from "
            f"{RETIRED_RELATIVE_PATH} with the evidence that it came back -- do "
            "not delete this gate.",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: check_retired_sources: {len(retired)} retired domain(s) absent from "
        f"the registry, DEFAULT_SOURCES and the approved-sources Domains column"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)
    return check(args.root.resolve())


if __name__ == "__main__":
    sys.exit(main())
