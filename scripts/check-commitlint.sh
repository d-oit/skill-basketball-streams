#!/usr/bin/env bash
# check-commitlint.sh — enforce the commit-message contract from CONTRIBUTING.md.
#
# This script is REQUIRED before `do-harness hook install`. The managed
# `commit-msg` hook runs it and is **fail-closed**: if the script or the repo is
# missing it blocks the commit. Installing hooks first would therefore make every
# subsequent commit in this repository fail, which is why the adoption plan
# (docs/do-harness.md, Step 1) puts this ahead of everything else.
#
# Contract:
#   * read the message from `--message <path>` or stdin;
#   * ignore comment lines and the housekeeping subjects git generates
#     (`Merge`, `Revert`, `fixup!`, `squash!`, `amend!`);
#   * the first remaining line must match
#       <type>(<scope>)!: <subject>   or   <type>: <subject>
#     with <type> one of the prefixes CONTRIBUTING.md documents;
#   * the subject must be 72 characters or fewer;
#   * exit 0 for an acceptable message, 1 otherwise, 2 for a usage error.
#
# Dependency-free on purpose (`bash` + `grep`/`sed`): it has to run on the CI
# runner and on a maintainer's machine with nothing installed.
set -euo pipefail

readonly MIN_SUBJECT_LENGTH=3
readonly MAX_SUBJECT_LENGTH=72
readonly TYPES="feat fix chore docs test refactor perf build ci style revert"
readonly PATTERN='^([a-z]+)(\([a-z0-9._-]+\))?(!)?: .+'
# `Merge`/`Revert` are capitalised, `fixup!`/`squash!`/`amend!` are autosquash
# markers. All are authored by git, not by a human.
readonly HOUSEKEEPING_PATTERN='^(Merge |Revert |fixup!|squash!|amend!)'

usage() {
  cat <<'USAGE'
Usage: scripts/check-commitlint.sh --message <file>
       <message> | scripts/check-commitlint.sh

Exit codes:
  0  the message satisfies the contract (or is git-generated housekeeping,
     or the message file could not be read — a hook must not fail closed)
  1  the message violates the contract
  2  usage error
USAGE
}

message_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --message) message_file="${2:-}"; shift 2 ;;
    --message=*) message_file="${1#--message=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: check-commitlint: unexpected argument '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$message_file" ]; then
  if [ ! -f "$message_file" ]; then
    # WARN, not FAIL, and exit 0: the hook must never block a commit because it
    # could not read its own input. A missing message file is our bug, not the
    # author's, and a fail-closed hook here would look like a broken repo.
    echo "WARN: check-commitlint: $message_file: file not found, allowing the commit" >&2
    exit 0
  fi
  raw="$(cat "$message_file")"
else
  # No --message and no pipe: nothing to check, so this is a usage error rather
  # than a silent pass. `read` on a terminal would otherwise hang forever.
  if [ -t 0 ]; then
    echo "FAIL: check-commitlint: no --message and no stdin" >&2
    usage >&2
    exit 2
  fi
  raw="$(cat)"
fi

# Strip comments and blank lines, then take the first real line.
subject="$(
  printf '%s\n' "$raw" \
    | sed -e 's/\r$//' \
    | grep -v '^#' \
    | grep -v '^[[:space:]]*$' \
    | head -n 1
)"

if [ -z "$subject" ]; then
  echo "FAIL: check-commitlint: no commit subject found" >&2
  exit 1
fi

# Git's own housekeeping subjects are generated, not authored: an author cannot
# change their shape, and blocking them would make `git merge` and `git revert`
# impossible. Accept them here, before the type check, rather than filtering
# them out and then failing on the empty remainder — that was the first bug this
# script had, and it would have made the managed hook unusable in practice.
if printf '%s' "$subject" | grep -qE "$HOUSEKEEPING_PATTERN"; then
  echo "OK: check-commitlint: git-generated message allowed ($subject)"
  exit 0
fi

# `sed` rather than bash regex so this works on bash 3.2 (macOS) too.
type_prefix="$(printf '%s' "$subject" | sed -nE 's/^([a-z]+)(\([a-z0-9._-]+\))?!?: .*/\1/p')"
if [ -z "$type_prefix" ]; then
  echo "FAIL: check-commitlint: subject must be '<type>: <subject>' or '<type>(<scope>): <subject>'" >&2
  echo "      got: $subject" >&2
  echo "      types: $TYPES" >&2
  exit 1
fi

if ! printf '%s' "$TYPES" | tr ' ' '\n' | grep -qx "$type_prefix"; then
  echo "FAIL: check-commitlint: unknown type '$type_prefix'" >&2
  echo "      allowed: $TYPES" >&2
  exit 1
fi

if ! printf '%s' "$subject" | grep -qE "$PATTERN"; then
  echo "FAIL: check-commitlint: malformed subject header" >&2
  echo "      got: $subject" >&2
  exit 1
fi

body="${subject#*: }"
if [ "${#body}" -lt "$MIN_SUBJECT_LENGTH" ]; then
  echo "FAIL: check-commitlint: subject is too short (min $MIN_SUBJECT_LENGTH chars)" >&2
  exit 1
fi

if [ "${#subject}" -gt "$MAX_SUBJECT_LENGTH" ]; then
  echo "FAIL: check-commitlint: subject is ${#subject} chars, max is $MAX_SUBJECT_LENGTH" >&2
  echo "      got: $subject" >&2
  exit 1
fi

echo "OK: check-commitlint: $subject"
exit 0
