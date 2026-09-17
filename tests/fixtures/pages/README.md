# Recorded page corpus — the stream-evidence gate

Real HTTP responses, recorded once, that pin the decision made by
`FetchResult.has_stream_evidence()` in [`scripts/render_ladder.py`](../../../scripts/render_ladder.py).
That function is the last check before a rendered page may become a link in the
calendar, so it is the one predicate in this repo that must be judged against
the actual web rather than against strings we invented.

Recorded: **2026-09-15**, `python3 scripts/record_pages.py --refresh`.
Re-check without network: `python3 scripts/record_pages.py --check` (also a
`validate.yml` step and the `page-corpus` sensor).

## What the gate does

A page carries stream evidence only if **both** hold:

1. a **media** marker — `<video`, `.m3u8`, `.mpd`, `application/x-mpegurl`,
   `application/dash+xml`, or `og:video`; and
2. a **live-state** marker — `Jetzt live`, `"isLiveContent":true`,
   `"isLiveNow":true`, or `"liveBroadcastContent":"live"`.

Media markers are matched after `<script>` blocks are stripped; the structured
YouTube fields are matched against the raw document, since a script block is the
only place they occur.

## What went wrong before

The rule until 2026-09-15 accepted bare vocabulary (`player`, `dash`, `live`,
`hls`, `läuft`, a play-button label) anywhere in the document. Every modern page
embeds its JavaScript runtime inline, so those words are always present. On this
corpus the old rule accepted:

| Page | Why it is not a stream |
|---|---|
| `youtube-watch-recorded` | a **recorded video** (`isLiveContent:false`); `player`/`dash`/`live`/`livestream` come from the bundle |
| `magentasport-home` | the *announce* site — it announces games, `magenta.tv` streams them. Bundle contains `.mpd` and `läuft` |
| `clb-home` | a league homepage; prose says "live now", bundle says `player` |
| `youtube-home` | the YouTube home page |
| `youtube-live-search` | the Live-filtered *results* page, which lists streams but is not one |

`tests/test_recorded_pages.py::test_the_old_vocabulary_fooled_by_real_pages`
asserts that the old rule still accepts these fixtures, so the corpus cannot be
quietly "simplified" into passing for the wrong reason.

## The files

| File | Page | Verdict | Stored as |
|---|---|---|---|
| `magenta-tv-shell.html` | `https://www.magenta.tv/` | no-evidence | whole (942 B) |
| `magentasport-home.html.gz` | `https://www.magentasport.de/` | no-evidence | whole |
| `clb-home.html.gz` | `https://www.championsleague.basketball/` | no-evidence | whole |
| `youtube-watch-recorded.html.gz` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | no-evidence | extract |
| `youtube-watch-live.html.gz` | a real live broadcast found via the Live filter | **evidence** | extract |
| `youtube-channel-live.html.gz` | `https://www.youtube.com/@FIBA/live` | **evidence** | extract |
| *(not stored)* | `https://www.youtube.com/` | no-evidence | verify-only |
| *(not stored)* | the Live-filtered results page | no-evidence | verify-only |

`manifest.json` records, for every page: the URL, HTTP status, **full response
byte length and SHA-256**, fetch timestamp, stored file, size, expected verdict
and why the page is interesting. The hash is what makes a stored extract
checkable — the extract is a verbatim slice of that response, not a paraphrase.

There are **two** hashes, because a page is stored in one of two ways:

- `response_sha256` — the whole HTTP response. For a `full` store the file on
disk *is* that response, so this is re-checkable.
- `stored_sha256` — the bytes of the stored payload. For an `extract` this is the
  only applicable one, and for a `full` store it must equal `response_sha256`.

`--check` verifies the payload hash on every run and refuses an edited file
outright (`not the recorded bytes`). Without it, a fixture could be quietly
tweaked into agreeing with a weakened gate — the file would still classify
correctly, so nothing else would notice.

### Why some pages are extracts and two are not stored at all

The YouTube documents are 1.2–1.4 MB each and their verdict turns on two fields
plus one meta tag. `extract_youtube()` keeps those **verbatim** — the real
`og:video` meta and the real `ytInitialPlayerResponse` object, at their real
nesting — and drops the rest. That is the axis the skill already uses: per
`references/youtube-live-search.md`, raw YouTube payloads are mapped into the
candidate schema rather than parsed wholesale.

The two `verify-only` pages are worse value: 250 KB **each** after gzip, for
pages whose verdict is "no media token anywhere". The same false-positive class
is stored whole as `magentasport-home` and `clb-home`. They are still fetched
and classified by `--refresh`, and `--check` verifies the manifest's own record
of that verdict is self-consistent, so nothing is hidden — it is just not
committed twice.

## Refreshing

```bash
python3 scripts/record_pages.py --refresh   # re-fetch, rewrite, print verdict FLIPs
python3 scripts/record_pages.py --check     # gate: stored pages still classify
python3 scripts/record_pages.py --list      # what the corpus covers
```

A refresh that flips a verdict is a genuine finding, not an inconvenience:
either the page changed or the gate did. Update the `note`, not the expectation,
and only after checking which of the two moved.

### A response that is not the page

A page that does not yield the response this corpus is a statement about is
**named and collected**, not fatal. Three things qualify, and each prints a
`FAIL:` line the weekly workflow's warning tells the reader to look for — a bare
traceback would point at a message that does not exist:

| What happened | Why a verdict from it would be a finding about *us*, not the web |
|---|---|
| the connection failed | nothing was fetched at all |
| **an HTTP error status** | a `429` is a rate limit, not a page — recorded, its empty evidence is a `lost-evidence` FLIP on an `expect=evidence` page, and an in-place refresh writes the error body over the fixture |
| the document fetched but carries no extract | the YouTube consent wall: a 200 whose body says nothing about the gate |

The middle row is the one that was live on 2026-09-16 and had been reachable
since `--refresh` was written: `_fetch` returns an error's status and body like
any other response, so only a *connection* failure counted as a failure and a
`429` from YouTube was classified as content. An HTTP error status is now a
fetch failure. See `test_an_error_status_is_not_recorded_as_the_page`.

Collecting rather than aborting is what lets the run still answer its question:
the flips among the pages it *did* reach are reported (`--json` adds `ok: false`
and an `unusable` list, which `corpus_flip_issue.py --markdown` renders as a
partial comparison). Aborting reported nothing at all, so a week when a provider
rate-limits the runner was a week with no verdict comparison — indistinguishable
from a week in which nothing flipped.

**The write is all-or-nothing.** Everything is buffered and written only once
every page has been reduced, so an aborted refresh leaves the corpus
byte-identical. The earlier ordering — each stored file as it was fetched, the
manifest last — meant an abort in the middle of an in-place refresh rewrote the
pages that **succeeded** while the manifest kept the old hashes: the corpus then
failed its own `--check` with `hash mismatch`, and the documented remedy
("re-record it rather than editing it") was unavailable for as long as the
blocking page stayed blocked. See
`test_nothing_is_written_when_a_page_is_unreducible`.

### A verdict flip is reported; a byte change is not

`--refresh` prints a `FLIP:` line when a page's *verdict* differs from the
previous manifest (`--baseline`, default `--out`, read before anything is
written, so an in-place refresh can see what it is replacing). It deliberately
says nothing about a page whose bytes changed and whose verdict did not — these
fixtures are extracts of a 1.3 MB YouTube document, so a hash diff is the norm
and would drown the signal.

`.github/workflows/corpus-refresh.yml` runs that comparison weekly and files at
most one issue, using `scripts/corpus_flip_issue.py`. Nothing is filed when no
verdict changed — which is what makes a filed issue worth reading. The two
directions are not symmetrical:

| Direction | Means |
|---|---|
| `lost-evidence` | a page playing a real stream stopped being recognised → **missed games** |
| `gained-evidence` | a page playing nothing is accepted → **the false positives this corpus exists to prevent** |

Both are answered by one question — did the source move, or did the gate? — and
never by editing a fixture into agreement. `--check` refuses a stored file whose
hash is not the recorded bytes for exactly that reason: the corpus must stay a
statement about the web, not about a file we wrote.

## The second question: does the page name *this* game?

The recorded pages also pin `render_ladder.anchor_game()`, which matches the expected clubs
against the document's **own name** (`<title>`, `og:title`, `twitter:title`, YouTube's
`videoDetails.title`) and never against the body — a body links to every club it mentions, which
is the same trap as a body-wide "live" marker.

Two of the recorded titles are real-world hard cases, and both are assertions rather than
notes:

| Recorded page | Real title | Verdict |
|---|---|---|
| `youtube-channel-live` | `LIVE - Tauranga Whai v Northern Kāhu \| Tauihi Basketball Aotearoa 2026` | attributed for `Tauranga Whai vs Northern Kāhu`; **not** attributed for `Tauihi Basketball Aotearoa` or for another game |
| `youtube-watch-live` | `🔴 2026 FIBA Women's Basketball World Cup Final USA vs France live \| Germany vs Spain` | **ambiguous** — it satisfies "both teams appear" for *either* pairing, so it is refused rather than attributed to one |

Two defects this found, both now fixed and tested:

- `Kāhu` tokenised to `{k, hu}`, so the real single-game page could not be attributed to the game
  it was named after. The shared matcher now folds diacritics and German digraphs
  (`München` = `Munchen` = `Muenchen`), which also stops the dedupe planner creating the same
  game twice from two spellings.
- A competition name matched as though it were a club, because its tokens are a subset of the
  same clause. The anchor now requires the clause to **be** a pairing (`A v B`, `A vs B`,
  `A vs. B`, `A gegen B`).

## Residual risk, stated plainly

The gate is a **screen**, not a proof. A non-YouTube page that publishes a real
video asset (`og:video`) *and* shows a "Jetzt live" badge while not actually
streaming that game would still pass **the evidence half** — the anchor closes that
by requiring the page's own title to name the expected clubs, but it cannot see inside
a player: a page whose title names the game and whose player is showing something else
is still a pass. That is why Check 1 (the `magentasport.de` free-access announcement,
matched on *teams*) and Check 4 (basketball-specific) still have to hold. What the
corpus removes are the failures that were invisible: *every* page passing, including
pages with no stream at all, and a live page of the wrong match being attributed to
this one.
