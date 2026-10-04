#!/usr/bin/env python3
"""check_product_contract.py — the product doc and the task board are checked.

Two documents are worth committing only because something reads them.

`PRODUCT.md` is the product contract: what this repository is, who it is for,
and which sensor enforces each promise. It has no producer otherwise. The one
claim it makes that a file cannot make for it is that the sensors it names
*exist*: a document naming a deleted sensor is the same defect as a rule that
reads a field nothing writes — it points at an enforcement that is not there.
The repository already contains one instance of exactly that, in
`plans/methods.json`, where a subtask still names `source-registry` after
commit 01f8e77 deleted it (replaced by `retired-sources`). That is the defect
this gate was written for.

`tasks.md` is the human/agent-readable mirror of the harness task database.
The database (`.do-harness/agent_state.db`, harness-owned and gitignored) is
authoritative; `plans/tasks.json` is its committed export, produced by
`do-harness task export`. A board that disagrees with the export is worse than
no board: it is a stale claim someone will trust.

What is checked, and why each can fail:

    1. `PRODUCT.md` exists and is non-empty.
    2. Every `## ` heading listed in its own `## Contents` table of contents
       exists, and every heading is listed there (a section no reader can find
       is a section that is not part of the contract).
    3. Every sensor named in `PRODUCT.md`'s `Sensor` table is declared in
       `do-harness.toml`. Sensor mentions are read from that table on purpose —
       the same rule as `check_retired_sources._declared_domains_cell`: a table
       cell is a declaration, prose is evidence, and evidence is allowed to name
       a retired thing in order to explain it.
    4. Every sensor an invariant in `plans/invariants.json` relies on is named
       in that table, so the doc cannot silently fall behind the invariant set.
    5. Any eval-case count `PRODUCT.md` states matches the number of cases in
       `evals/evals.json`. A hand-typed count rots the moment a case is added,
       so the number is only allowed in the document if the file can veto it.
    6. `tasks.md` exists and every id, title and status matches
       `plans/tasks.json`, whose own `summary` block must match its tasks.
    7. Every `sensor` value in `plans/methods.json` is declared.
    8. Every `sensor` value in `plans/invariants.json` is declared.

Checks 7 and 8 are the "rule that reads a field nothing writes" trap applied to
configuration: a sensor name that no `[[sensors]]` entry declares is a gate that
has never run.

Usage:
    python3 scripts/check_product_contract.py [--root .]

Exit codes:
    0  PASS — the documents and the harness agree
    1  FAIL — a document names something that does not exist, or the board drifted
    2  USAGE — the root is not a directory
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PRODUCT_RELATIVE_PATH = "PRODUCT.md"
TASKS_BOARD_RELATIVE_PATH = "tasks.md"
HARNESS_CONFIG_RELATIVE_PATH = "do-harness.toml"
INVARIANTS_RELATIVE_PATH = "plans/invariants.json"
METHODS_RELATIVE_PATH = "plans/methods.json"
TASKS_JSON_RELATIVE_PATH = "plans/tasks.json"
EVALS_RELATIVE_PATH = "evals/evals.json"

INVARIANT_KEYS = ("invariant", "rationale", "sensor", "category")

# The same extraction `tests/test_harness_boundary.py` uses: a `[[sensors]]`
# table's `name`, wherever it appears in the file.
SENSOR_DECLARATION = re.compile(r'\[\[sensors\]\]\s*\nname\s*=\s*"([^"]+)"')
HEADING = re.compile(r"^##\s+(.+?)\s*$")
CONTENTS_HEADING = re.compile(r"^##\s+Contents\s*$")
TOC_ENTRY = re.compile(r"^[-*]\s+(.+?)\s*$")
TOC_LINK = re.compile(r"^\[(.+?)\]\(#.*?\)$")
SENSOR_TABLE_HEADER = re.compile(r"^\|\s*Sensor\s*\|", re.IGNORECASE)
TASK_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|\s*([A-Za-z_]+)\s*\|\s*$")
TOTAL = re.compile(r"([a-z_]+)\s*=\s*(\d+)")
EVAL_COUNT_CLAIM = re.compile(r"(\d+)\s+eval cases?\b", re.IGNORECASE)


def _declared_sensors(text: str) -> list[str]:
    return SENSOR_DECLARATION.findall(text)


def _headings(text: str) -> list[str]:
    return [m.group(1) for m in (HEADING.match(line) for line in text.splitlines()) if m]


def _toc_entries(text: str) -> list[str]:
    """The bullets of the `## Contents` section, in order.

    An entry is `- Name` or `- [Name](#anchor)`; the anchor is not compared,
    only the visible text, so renames that carry the link along still count.
    """
    entries: list[str] = []
    inside = False
    for line in text.splitlines():
        if CONTENTS_HEADING.match(line):
            inside = True
            continue
        if inside and HEADING.match(line):
            break
        if not inside:
            continue
        match = TOC_ENTRY.match(line.strip())
        if not match:
            continue
        item = match.group(1).strip()
        link = TOC_LINK.match(item)
        if link:
            item = link.group(1)
        entries.append(item.strip().strip("`").strip())
    return entries


def _sensor_table(text: str) -> list[str]:
    """Sensor names declared by the `Sensor` table, header row included.

    Table-scoped deliberately: prose may explain a retired sensor's history, and
    a check over the whole file would forbid the explanation. The same
    asymmetry as `plans/methods.json` naming what it uses and
    `references/approved-sources.md`'s Notes column naming what was retired.
    """
    names: list[str] = []
    inside = False
    for line in text.splitlines():
        if SENSOR_TABLE_HEADER.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not line.lstrip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        if set(cells[0]) <= set("-: "):
            continue  # the |---|---| separator, not a row
        name = cells[0].strip("`").strip()
        if name:
            names.append(name)
    return names


def _task_rows(text: str) -> dict[int, tuple[str, str]]:
    rows: dict[int, tuple[str, str]] = {}
    for line in text.splitlines():
        match = TASK_ROW.match(line)
        if match:
            rows[int(match.group(1))] = (match.group(2).strip(), match.group(3))
    return rows


def _totals(text: str) -> dict[str, int]:
    for line in text.splitlines():
        if "totals" not in line.lower():
            continue
        found = {key: int(value) for key, value in TOTAL.findall(line)}
        if found:
            return found
    return {}


def _load_json(root: Path, relative: str, failures: list[str]):
    path = root / relative
    if not path.is_file():
        failures.append(f"{relative} is missing — the check that reads it asserts nothing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        failures.append(f"{relative} is not readable JSON: {error}")
        return None


def check(root: Path) -> int:
    failures: list[str] = []

    config_path = root / HARNESS_CONFIG_RELATIVE_PATH
    if not config_path.is_file():
        failures.append(
            f"{HARNESS_CONFIG_RELATIVE_PATH} is missing — with no sensor "
            f"declarations every reference below would look valid"
        )
        declared: list[str] = []
    else:
        declared = _declared_sensors(config_path.read_text(encoding="utf-8"))
        if not declared:
            failures.append(
                f"{HARNESS_CONFIG_RELATIVE_PATH} declares no [[sensors]] — a "
                f"reference check with an empty declaration set passes vacuously"
            )

    invariants = _load_json(root, INVARIANTS_RELATIVE_PATH, failures)
    methods = _load_json(root, METHODS_RELATIVE_PATH, failures)
    tasks_export = _load_json(root, TASKS_JSON_RELATIVE_PATH, failures)

    # ---- 1-4: PRODUCT.md -------------------------------------------------
    product_path = root / PRODUCT_RELATIVE_PATH
    product_table_sensors: list[str] = []
    if not product_path.is_file():
        failures.append(
            f"{PRODUCT_RELATIVE_PATH} is missing — the product contract is not "
            f"optional, and a gate that skips an absent document is decoration"
        )
    else:
        product = product_path.read_text(encoding="utf-8")
        headings = _headings(product)
        if not product.strip() or not headings:
            failures.append(
                f"{PRODUCT_RELATIVE_PATH} is empty or has no `## ` sections — "
                f"there is nothing for a reader to rely on"
            )

        toc = _toc_entries(product)
        body_headings = [h for h in headings if h.lower() != "contents"]
        for name in toc:
            if name not in body_headings:
                failures.append(
                    f"{PRODUCT_RELATIVE_PATH}: the contents list promises "
                    f"section {name!r}, which does not exist"
                )
        for name in body_headings:
            if name not in toc:
                failures.append(
                    f"{PRODUCT_RELATIVE_PATH}: section {name!r} is missing from "
                    f"the contents list, so the contract cannot be found"
                )

        product_table_sensors = _sensor_table(product)
        if not product_table_sensors:
            failures.append(
                f"{PRODUCT_RELATIVE_PATH}: the `Sensor` table names no sensors — "
                f"the enforcement claim would be unreadable"
            )
        for name in product_table_sensors:
            if name not in declared:
                failures.append(
                    f"{PRODUCT_RELATIVE_PATH}: names sensor {name!r}, which "
                    f"{HARNESS_CONFIG_RELATIVE_PATH} does not declare — a "
                    f"promise guarded by a sensor that does not exist"
                )

        # A hardcoded count in a hand-edited document rots silently, which is
        # the drift this file refuses to create for invariants. Any count the
        # document states is checked against the file it describes.
        claims = {int(value) for value in EVAL_COUNT_CLAIM.findall(product)}
        if claims:
            evals = _load_json(root, EVALS_RELATIVE_PATH, failures)
            cases = evals.get("evals") if isinstance(evals, dict) else None
            if evals is not None and not isinstance(cases, list):
                failures.append(
                    f"{EVALS_RELATIVE_PATH} has no 'evals' array, so the "
                    f"eval-case count stated in {PRODUCT_RELATIVE_PATH} cannot "
                    f"be checked"
                )
            for claim in sorted(claims):
                if isinstance(cases, list) and claim != len(cases):
                    failures.append(
                        f"{PRODUCT_RELATIVE_PATH}: states {claim} eval case(s), "
                        f"but {EVALS_RELATIVE_PATH} holds {len(cases)}"
                    )

    # ---- 5: tasks.md <-> plans/tasks.json --------------------------------
    board_path = root / TASKS_BOARD_RELATIVE_PATH
    if not board_path.is_file():
        failures.append(
            f"{TASKS_BOARD_RELATIVE_PATH} is missing — the board is the readable "
            f"half of the task state, and its absence is a doc with no artifact"
        )
    elif tasks_export is not None:
        board = board_path.read_text(encoding="utf-8")
        rows = _task_rows(board)
        exported = {
            int(task["id"]): (str(task.get("title", "")), str(task.get("status", "")))
            for task in tasks_export.get("tasks", [])
        }
        for task_id in sorted(set(rows) | set(exported)):
            if task_id not in rows:
                failures.append(
                    f"{TASKS_BOARD_RELATIVE_PATH}: task {task_id} is in "
                    f"{TASKS_JSON_RELATIVE_PATH} but not on the board"
                )
            elif task_id not in exported:
                failures.append(
                    f"{TASKS_BOARD_RELATIVE_PATH}: task {task_id} is on the board "
                    f"but not in {TASKS_JSON_RELATIVE_PATH}"
                )
            elif rows[task_id] != exported[task_id]:
                got_title, got_status = rows[task_id]
                want_title, want_status = exported[task_id]
                if got_status != want_status:
                    failures.append(
                        f"{TASKS_BOARD_RELATIVE_PATH}: task {task_id} status "
                        f"{got_status!r} does not match "
                        f"{TASKS_JSON_RELATIVE_PATH} {want_status!r}"
                    )
                if got_title != want_title:
                    failures.append(
                        f"{TASKS_BOARD_RELATIVE_PATH}: task {task_id} title does "
                        f"not match {TASKS_JSON_RELATIVE_PATH}: {got_title!r} != "
                        f"{want_title!r}"
                    )

        totals = _totals(board)
        if not totals:
            failures.append(
                f"{TASKS_BOARD_RELATIVE_PATH}: no `Totals:` line, so the summary "
                f"the export carries has nothing to agree with"
            )
        summary = {
            str(key): int(value)
            for key, value in (tasks_export.get("summary") or {}).items()
        }
        for key, value in sorted(summary.items()):
            if totals.get(key) != value:
                failures.append(
                    f"{TASKS_BOARD_RELATIVE_PATH}: Totals {key}={totals.get(key)} "
                    f"does not match {TASKS_JSON_RELATIVE_PATH} summary {key}={value}"
                )

        counts = {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}
        for _, status in exported.values():
            if status in counts:
                counts[status] += 1
        for key, value in sorted(counts.items()):
            if summary.get(key) != value:
                failures.append(
                    f"{TASKS_JSON_RELATIVE_PATH}: summary {key}={summary.get(key)} "
                    f"does not match its {value} {key} task(s)"
                )

    # ---- 4/6/7: every configured reference names a declared sensor -------
    if invariants is not None:
        if not isinstance(invariants, list) or not invariants:
            failures.append(
                f"{INVARIANTS_RELATIVE_PATH} is not a non-empty array — the "
                f"invariant set is what `do-harness seed` writes"
            )
        else:
            invariant_sensors: set[str] = set()
            for index, entry in enumerate(invariants):
                if not isinstance(entry, dict):
                    failures.append(
                        f"{INVARIANTS_RELATIVE_PATH}[{index}] is not an object"
                    )
                    continue
                missing = [key for key in INVARIANT_KEYS if not str(entry.get(key) or "").strip()]
                if missing:
                    failures.append(
                        f"{INVARIANTS_RELATIVE_PATH}[{index}] is missing {missing}"
                    )
                    continue
                sensor = str(entry["sensor"])
                invariant_sensors.add(sensor)
                if sensor not in declared:
                    failures.append(
                        f"{INVARIANTS_RELATIVE_PATH}[{index}]: {entry['invariant']!r} "
                        f"names sensor {sensor!r}, which "
                        f"{HARNESS_CONFIG_RELATIVE_PATH} does not declare"
                    )
            if product_table_sensors:
                for sensor in sorted(invariant_sensors - set(product_table_sensors)):
                    failures.append(
                        f"{PRODUCT_RELATIVE_PATH}: the `Sensor` table does not "
                        f"name {sensor!r}, which an invariant in "
                        f"{INVARIANTS_RELATIVE_PATH} relies on"
                    )

    if methods is not None:
        entries = methods.get("methods") if isinstance(methods, dict) else None
        if not isinstance(entries, list) or not entries:
            failures.append(
                f"{METHODS_RELATIVE_PATH} has no non-empty 'methods' array"
            )
        else:
            for index, method in enumerate(entries):
                name = str((method or {}).get("name", f"[{index}]"))
                for jndex, subtask in enumerate(method.get("subtasks") or []):
                    sensor = str((subtask or {}).get("sensor") or "").strip()
                    if not sensor:
                        failures.append(
                            f"{METHODS_RELATIVE_PATH}: method {name!r} subtask "
                            f"{jndex} has no 'sensor'"
                        )
                    elif sensor not in declared:
                        failures.append(
                            f"{METHODS_RELATIVE_PATH}: method {name!r} subtask "
                            f"{jndex} names sensor {sensor!r}, which "
                            f"{HARNESS_CONFIG_RELATIVE_PATH} does not declare"
                        )

    for line in failures:
        print(f"FAIL: check_product_contract: {line}", file=sys.stderr)
    if failures:
        print(
            "FAIL: check_product_contract: a document names an enforcement that "
            "does not exist, or the task board has drifted from its export. Fix "
            "the document (or restore the sensor), and re-run `do-harness task "
            "export` before editing tasks.md — do not delete this gate.",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: check_product_contract: {PRODUCT_RELATIVE_PATH} names {len(product_table_sensors)} "
        f"declared sensor(s), {TASKS_BOARD_RELATIVE_PATH} matches "
        f"{len(tasks_export.get('tasks', []))} exported task(s), and every "
        f"method/invariant reference resolves in {HARNESS_CONFIG_RELATIVE_PATH}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f"FAIL: check_product_contract: {root}: not a directory", file=sys.stderr)
        return 2
    return check(root)


if __name__ == "__main__":
    sys.exit(main())
