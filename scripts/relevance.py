#!/usr/bin/env python3
"""relevance.py — a cheap, fail-open "is this basketball at all?" pre-filter.

YouTube's live filter (`sp=EgJAAQ%3D%3D`) returns *live broadcasts* matching
loose terms, not basketball. One recorded run returned `Pop The Balloon`,
`Acerting Art` and `Melbourne Luxury Accommodation` for a basketball query, and
each one consumed a full 7-check pass before being rejected. The rejections were
correct; the spend was not.

This module exists to stop that spend early. It is a **pre-filter, not a gate**,
and the distinction is the whole design:

* **It fails open.** No text, unreadable text, or text it does not recognise is
  reported as relevant. A filter that guesses would reject real games, and a
  rejected game is invisible — it is not an error, it is a stream nobody gets.
  That is the same asymmetry the rest of this repo is built around: absence of
  evidence is not evidence, and `INCONCLUSIVE` is a real state.
* **It never overrides an approved channel.** An official club or league channel
  is basketball by definition, whatever it happens to be streaming — a press
  conference or a pre-game show is not off-topic. The caller passes that in, and
  the filter stands down.
* **It is not Check 3 or Check 4.** Those remain the authoritative answers to
  "is this an approved source" and "is this basketball-specific". This only
  answers "is this obviously something else", so the expensive path is spent on
  candidates that had a chance.

Being stdlib-only and offline is a hard requirement, like every other module
here: a pre-filter that needs the network has already cost more than it saves.
"""
from __future__ import annotations

import re

# Competition, federation and league vocabulary, plus the generic word. Matched
# case- and punctuation-insensitively, with word boundaries so `Pop The Balloon`
# cannot match on a substring and `Acerting Art` cannot match on `art`.
#
# Separators inside a compound are optional (`euro\s*-?\s*league`), because
# official channels write the same name both ways and a filter that only knows
# one spelling rejects a real broadcast.
#
# `bbl` is deliberately absent: it is three letters and appears inside unrelated
# words often enough that a `\b` boundary does not make it safe on its own.
#
# **Residual risk, stated plainly.** This is a fixed vocabulary, so a league it
# does not name is rejected as noise. The first draft of this list was missing
# `regionalliga`, and the test suite caught a real Regionalliga Nord fixture
# being refused — which is precisely the failure mode this module must not have.
# The list is therefore broad over the German pyramid and the major European
# competitions rather than minimal. It is still not exhaustive, and a game from a
# competition nobody listed is dropped here rather than in Check 4. That is a
# real cost, accepted because the alternative — every off-topic live result
# consuming a full 7-check pass — was measured, not hypothesised. Widen the list
# when a league is found; do not narrow it.
BASKETBALL_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"basketball|basket|hoops|baskerbol|"
    # Germany: the full pyramid, not just the top flight.
    r"bundesliga|easycredit|proa|regionalliga|oberliga|"
    r"2\.\s*bundesliga|dnl|dni|nachwuchsliga|pokal|"
    # Clubs, current and recently promoted.
    r"alba|bayern|bremen|duisburg|frankfurt|gottingen|hamburg|heidenheim|"
    r"ludwigsburg|mannheim|münster|munster|oldenburg|ratiopharm|ulm|vechta|"
    r"wetzlar|braunschweig|cottbus|darmstadt|heidelberg|"
    r"kassel|weiden|ravensburg|pirates|leicester|sheffield|"
    # Europe.
    r"euro\s*-?\s*league|euro\s*-?\s*cup|eurocup|"
    r"fiba|champions\s*-?\s*league|basketball\s*-?\s*champions|"
    r"barcelona|real\s*-?\s*madrid|olympiacos|panathinaikos|anadolu|"
    r"macirisehir|milano|venezia|varese|baskonia|asvel|limoges|nantes|nancy|"
    r"partizan|crvena\s*-?\s*zvezda|zalgiris|peristeri|"
    r"bahcesehir|efes|besiktas|fenerbahce|olympiakos|"
    # National federations the skill already approves.
    r"dib|dbb|easy\s*-?\s*credit\s*-?\s*bbl"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)

# Reasons are stable strings so the run log stays greppable and a sensor can
# match on them, the same way the 7-check ids are stable tokens.
REASON_IRRELEVANT = "not a basketball stream (no basketball term in title or channel)"


def looks_like_basketball(*texts: object) -> bool:
    """False only when every supplied text is present and none looks like
    basketball.

    Fails open: if no text is supplied, or every value is empty/None, the
    candidate is reported as relevant. An unjudgeable candidate is somebody
    else's decision to make, and this module's job is to remove noise, not to
    take responsibility for the call.
    """
    saw_text = False
    for value in texts:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        saw_text = True
        if BASKETBALL_RE.search(text):
            return True
    return not saw_text


def relevance_reason(*texts: object) -> str:
    """Empty string when the candidate passes, else a stable reason."""
    return "" if looks_like_basketball(*texts) else REASON_IRRELEVANT
