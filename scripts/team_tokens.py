#!/usr/bin/env python3
"""team_tokens.py — one canonical way to decide whether two spellings are the
same club.

Extracted from `upsert_events.py` when the stream-evidence gate needed the same
judgement: `upsert_events` asks it to avoid creating the same game twice, and
`render_ladder` asks it to decide whether a rendered page actually names the game
it was fetched for. Two copies of this rule would drift, and the drift would show
up as either duplicate calendar events or a legitimate game rejected.

Rules, deliberately loose in one direction only:

* Tokens are matched case-insensitively and punctuation-insensitively
  (`FC Bayern Basketball` → `{fc, bayern}`), so `ALBA` and `ALBA Berlin` are the
  same club — which is what stops the same game being created twice.
* Diacritics fold (`Kāhu` = `Kahu`, `München` = `Munchen`), and the German
  digraph spellings fold too (`München` = `Muenchen`, `Straße` = `Strasse`).
  This was a real miss: `Kāhu` tokenised to `{k, hu}`, so the recorded live page
  naming `Tauranga Whai v Northern Kāhu` could not be attributed to that game.
* `GENERIC_TEAM_TOKENS` is tiny and contains only words that carry no club
  identity. Dropping a *city* token would merge genuinely different teams, so
  nothing like it is in the list.
* Matching is set equality or nesting, never overlap: `ALBA` ⊂ `ALBA Berlin`,
  but `Berlin` ⊄ `ALBA Berlin` is **not** claimed — a bare city name does match
  the full club name, which is the same looseness that lets one source write the
  short name and another the full one.
"""
from __future__ import annotations

import re
import unicodedata

# Words that carry no club identity, so "FC Bayern Basketball" and "FC Bayern"
# are the same club. Kept deliberately tiny — dropping a *city* token would
# merge genuinely different teams.
GENERIC_TEAM_TOKENS = frozenset(
    {"basketball", "baskets", "basket", "bc", "bb", "club", "sport", "sports",
     "ev", "e", "v"}
)

# `ü` is written `ue` by some sources and `München`/`Muenchen` must match. Applied
# before decomposition, because NFKD deliberately leaves `ß` alone.
GERMAN_DIGRAPHS = (("ß", "ss"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"))

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# `\bv\.?\b` (not `\bv\.\b`) because `A v B` is the standard Commonwealth
# fixture format: requiring the dot made the recorded real page's title
# (`Tauranga Whai v Northern Kāhu`) look like it advertised no pairing at all.
# The `vs` alternative is listed first so `vs` is never split into `v` + `s`.
# The boundary sits **before** the optional dot (`\bvs\b\.?`), not after it:
# `\bvs\.?\b` cannot match `vs.` at all, because there is no word boundary
# between `.` and the following space — so `USA vs. France` fell through to the
# single-`v` alternative and split into `["USA", ". France"]`.
PAIRING_SEPARATORS = re.compile(
    r"\bvs\b\.?|\bv\b\.?|\bgegen\b\.?|\bcontra\b\.?", re.IGNORECASE
)


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _tokenise(text: str) -> frozenset:
    # `[^\W_]` rather than `[a-z0-9]`: a letter with no ASCII equivalent (`ø` in
    # Køge) must stay part of its token instead of splitting the name in two.
    return frozenset(TOKEN_RE.findall(text))


def team_tokens(name: object) -> frozenset:
    """The identity-bearing tokens of a club name, diacritics folded."""
    text = _strip_marks(str(name or ""))
    return frozenset(token for token in _tokenise(text) if token not in GENERIC_TEAM_TOKENS)


def _digraph_tokens(name: object) -> frozenset:
    """The same tokens for the `ue`-style spelling (`München` → `muenchen`)."""
    text = str(name or "")
    for umlaut, digraph in GERMAN_DIGRAPHS:
        text = text.replace(umlaut, digraph).replace(umlaut.upper(), digraph.upper())
    text = _strip_marks(text)
    return frozenset(token for token in _tokenise(text) if token not in GENERIC_TEAM_TOKENS)


def _sets_match(left: frozenset, right: frozenset) -> bool:
    return left == right or left <= right or right <= left


def team_matches(left: object, right: object) -> bool:
    """Do two spellings refer to the same club?

    Either spelling may arrive in either form, so both foldings are tried:
    `Würzburg` matches `Würzburg`, `Wurzburg` and `Wuerzburg`.
    """
    for left_set in (team_tokens(left), _digraph_tokens(left)):
        for right_set in (team_tokens(right), _digraph_tokens(right)):
            if left_set and right_set and _sets_match(left_set, right_set):
                return True
    return str(left or "").strip().lower() == str(right or "").strip().lower()


def split_teams(teams: object) -> list[str]:
    """Coerce `"A vs B"` / `["A", "B"]` / `{"team1": …}` into a team list."""
    if isinstance(teams, (list, tuple)):
        return [str(part).strip() for part in teams if str(part).strip()]
    text = str(teams or "").strip()
    if not text:
        return []
    parts = PAIRING_SEPARATORS.split(text)
    parts = [part.strip() for part in parts if part.strip()]
    return parts if len(parts) > 1 else [text]


def text_names_all(text: object, teams: object) -> bool:
    """Does `text` name every one of `teams`?

    Deliberately a weaker question than `render_ladder.anchor_game`, and the
    difference is the point. The anchor asks whether a *clause* is a pairing and
    refuses anything else, because a rendered page that merely mentions two clubs
    is not a page showing that game. Here the text is a stored calendar entry's
    own label for itself, so there is no page to be fooled: the only question is
    whether this entry names these clubs at all. Demanding a separator here would
    reject real entries — on the live calendar the pairings are written
    `A - B` about as often as `A vs B`.

    Rejects a name with no identity-bearing tokens (`BC`, `Basketball`), because
    a name that cannot identify anything must not match every text; `team_matches`
    already refuses those, since its token sets are required to be non-empty.

    `teams` may be a `"A vs B"` string or a list; an empty one is never a match,
    so a caller cannot treat "no expectation" as "everything matches".
    """
    expected = [team for team in split_teams(teams) if str(team).strip()]
    if not expected:
        return False
    return all(team_matches(team, text) for team in expected)


def pairing_count(title: object) -> int:
    """How many pairings a title advertises (`"A vs B | C vs D"` → 2).

    A count above 1 is the signature of a keyword-stuffed live-stream title, and
    it matters: such a title satisfies "both teams appear" for *every* pairing it
    lists, so a match on it must be reported as ambiguous rather than trusted.
    """
    return len(PAIRING_SEPARATORS.findall(str(title or "")))
