# Setup Guide

This guide walks you through setting up `skill-basketball-streams` for your own use, including creating a Google Calendar, connecting it to Composio, configuring the skill, and running your first validation.

## Quick Start

1. **Create a Google Calendar** for your basketball streams
2. **Get your Calendar ID** from Google Calendar settings
3. **Connect that calendar in Composio** and note your API key + user id (see *Calendar Credentials* below) — no Google Cloud project is needed
4. **Configure the skill** by updating `config/calendar.json`
5. **Validate your setup** with `python3 scripts/validate.py --root .`
6. **Run the skill** with your preferred AI agent framework

## Step 1: Create a Google Calendar

### Using Google Calendar Web Interface

1. Go to [Google Calendar](https://calendar.google.com)
2. Sign in with your Google account
3. Click the **+** button next to "Other calendars" in the left sidebar
4. Select **"Create new calendar"**
5. Enter a name for your calendar (e.g., "Basketball Streams")
6. Optionally add a description
7. Choose a timezone (recommended: **Europe/Berlin** for Germany-based streams)
8. Click **"Create calendar"**

### Using Google Calendar Mobile App

1. Open the Google Calendar app
2. Tap the menu (☰) in the top left
3. Tap **"Create new calendar"**
4. Enter calendar details and tap **"Create"**

## Step 2: Get Your Calendar ID

### Method A: From Calendar Settings (Web)

1. In Google Calendar web, find your new calendar in the left sidebar
2. Hover over the calendar name and click the **three dots (⋮)**
3. Select **"Settings and sharing"**
4. Scroll down to the **"Calendar address"** section
5. Look for **"Calendar ID"** - it will look like:
   ```
   f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com
   ```
6. **Copy this entire string** - this is your Calendar ID

### Method B: From the Calendar's Settings Page

There is no supported way to *enumerate* your calendars from a script here, and that is
deliberate: the calendar credential is a Composio API key whose Google grant is scoped to
`calendar.events`, and enumerating calendars can come back empty under that scope. The
method that always works is the settings page in **Method A** — the Calendar ID is right
there under *Settings and sharing → Calendar address*.

(This step used to suggest `gcloud calendar calendars list`, which is not a real command,
and a `curl` to `googleapis.com/calendar/v3/calendars` with a bearer token that nothing in
this repository can mint.)

### Method C: From Calendar URL

1. Open your calendar in Google Calendar web
2. Look at the URL in your browser's address bar
3. The Calendar ID appears after `/calendar/u/` or in the `cid=` parameter

## Step 3: Configure the Skill

### Option A: Update config/calendar.json (Recommended)

Edit the configuration file with your Calendar ID:

```bash
# Navigate to the project root
cd skill-basketball-streams

# Edit the config file
nano config/calendar.json
# or use your preferred editor
```

Update the file with your Calendar ID:

```json
{
  "calendarId": "YOUR_CALENDAR_ID@group.calendar.google.com",
  "timezone": "Europe/Berlin",
  "visibility": "public"
}
```

**Configuration Fields:**

| Field | Description | Default | Required |
|-------|-------------|---------|----------|
| `calendarId` | Your Google Calendar ID | `""` | **Yes** |
| `timezone` | Timezone for events | `"Europe/Berlin"` | No |
| `visibility` | Event visibility — `default`, `public`, `private` or `confidential`. An event-level property, not a substitute for the calendar's sharing setting | `"public"` | No |

**Event colours are assigned, not configured.** Every colour comes from the league and
from the verification state (`scripts/color_mapping.py` + `scripts/verification.py`),
so there is no colour setting in this file. The palette is still worth knowing, because
it is how a glance at the calendar reads:

- `"6"` Tangerine — the league default, and every `VERIFIED` event
- `"2"` Sage — FIBA international games
- `"11"` Tomato — EuroLeague / Basketball Champions League finals
- `"5"` Banana — `UNVERIFIED`: free access not confirmed
- `"7"` Peacock — `WRONG`: the audit proved it was never live, or was paid

`"2"`, `"5"`, `"7"` and `"11"` are **reserved**. `tests/test_color_mapping.py`
asserts the league default is never one of them, because a confirmed game rendered in
the `UNVERIFIED` or `WRONG` colour would make the calendar say something false — which
is also why this used to be a `defaultColorId` field and no longer is.

### Option B: Use Environment Variable (For Testing/CI)

Instead of modifying the config file, you can set the `BASKETBALL_CALENDAR_ID` environment variable:

```bash
# Linux/macOS
export BASKETBALL_CALENDAR_ID="YOUR_CALENDAR_ID@group.calendar.google.com"

# Windows (Command Prompt)
set BASKETBALL_CALENDAR_ID=YOUR_CALENDAR_ID@group.calendar.google.com

# Windows (PowerShell)
$env:BASKETBALL_CALENDAR_ID="YOUR_CALENDAR_ID@group.calendar.google.com"
```

**Note:** The environment variable **only overrides the `calendarId`**. Other settings (timezone, visibility) still come from `config/calendar.json`.

`visibility` reaches the API through `scripts/calendar_io.py`'s `--visibility` flag, whose
default is this config file (via `scripts/calendar_config.get_visibility()`). It is applied
on updates as well as creates, so a value set here is not silently cleared by a later run.
An unaccepted value is refused with exit 2 before any request is made.

### Option C: Both Config File + Environment Variable

You can use both methods together. The environment variable takes precedence for `calendarId`, while other settings come from the config file.

## Step 4: Validate Your Setup

Before running the skill, validate that everything is configured correctly:

```bash
# Run the full validator
python3 scripts/validate.py --root .

# Expected output — one OK line per check:
# OK: evals: <n> cases conform to standard schema (skill_name=skill-basketball-streams)
# OK: SKILL.md: frontmatter valid + body <n> <= 250 lines + mandatory sections present
# OK: references: all <n> backtick-wrapped .md paths resolve (...)
# OK: calendar config: config/calendar.json: calendarId=... (valid format)
```

Run just one check with `--check`, e.g. `python3 scripts/validate.py --root . --check calendar-config`.

If you see a **FAIL** about calendar configuration, double-check:
1. `config/calendar.json` exists and has a valid `calendarId`
2. The `calendarId` matches the pattern: `...@group.calendar.google.com`
3. The `calendarId` is not a placeholder like `"f8a14c40..."`

## Step 5: Test with a Sandbox Calendar (Optional)

For development and testing, create a separate sandbox calendar:

1. Create a calendar named "Basketball Streams - Sandbox"
2. Get its Calendar ID
3. Set the environment variable for testing:

```bash
export BASKETBALL_CALENDAR_ID="SANDBOX_CALENDAR_ID@group.calendar.google.com"
python3 scripts/validate.py --root .
```

This way, your test runs won't affect your production calendar.

## Step 6: Run the Skill

Once configured, you can run the skill using your preferred AI agent framework. The skill will:

1. Search for free basketball streams from approved sources
2. Validate each stream with the 7-check pipeline
3. Check for duplicates in your configured calendar
4. Create events in your calendar for valid, non-duplicate streams

## Troubleshooting

### "Calendar config file not found"

**Solution:** Ensure `config/calendar.json` exists in your project root.

```bash
# Check if the file exists
ls -la config/calendar.json

# If not, create it
mkdir -p config
touch config/calendar.json
```

### "calendarId is missing, placeholder, or malformed"

**Solution:** Verify your `calendarId` in `config/calendar.json`:

```bash
# Check the current value
cat config/calendar.json | grep calendarId

# Update with your actual Calendar ID
# Make sure it ends with @group.calendar.google.com
```

**Valid format:** `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`

**Invalid formats:**
- `""` (empty string)
- `"YOUR_CALENDAR_ID_HERE"` (placeholder)
- `"f8a14c40..."` (truncated)
- `"primary"` (special keyword, not a real Calendar ID)

### `FAIL: calendar_io: no Composio API key` / `no account to act as`

Two separate credentials, and the message names the missing one. `calendar_io.py` exits 2
before making any request — a *dry run* (`apply` without `--live`) needs neither, so if you
see this on a dry run, something else is wrong.

```bash
export COMPOSIO_API_KEY=...        # from the Composio dashboard
# and either:
export COMPOSIO_USER_ID=...        # whose connected account to act as (survives reconnect)
# or:
export COMPOSIO_CONNECTED_ACCOUNT_ID=ca_...   # never both
```

On CI these are the repository secrets `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID`. See
*Calendar Credentials (CI)* below.

### `FAIL: calendar_io: ... reported failure: no connected account`

A 200 response carrying `successful: false` — the credential is present but the account is
not usable. Almost always one of: the connected account was revoked, a connected-account id
was sent where a user id belongs, or the grant does not cover the calendar in
`config/calendar.json`. To separate the two, list the window and read what comes back —
`python3 scripts/calendar_io.py list --days 7 --out existing.json` succeeds with any number
of events, including zero, so an empty result is a *fact about the calendar* while this
error is a fact about the credential. (For the LLM side, the equivalent probe is
`python3 scripts/capture_transcripts.py --list-models`.)

### "No events created"

**Possible causes:**
1. No free basketball streams available in the next 7 days
2. All found streams failed validation (check the skill's output)
3. Calendar ID is incorrect (events are being created in the wrong calendar)
4. Duplicate detection is too aggressive
5. The run was a dry run — check the job summary, which states which mode ran

**Solution:** Check the skill's output for validation failures and ensure your Calendar ID is correct.

## Configuration for Different Use Cases

### Multiple Forks/Users

Each user/fork should have their own `config/calendar.json` with their own Calendar ID. The `.gitignore` should include `config/calendar.json` to prevent accidental commits:

```bash
# Add to .gitignore
echo "config/calendar.json" >> .gitignore
git add .gitignore
git commit -m "Ignore calendar config"
```

However, this repository **does include** `config/calendar.json` as a template. If you want to keep it tracked with a placeholder, ensure the validation check allows it.

### CI/CD Testing

For CI/CD pipelines, use the environment variable approach:

```yaml
# In your GitHub Actions workflow
- name: Run tests with sandbox calendar
  env:
    BASKETBALL_CALENDAR_ID: ${{ secrets.SANDBOX_CALENDAR_ID }}
  run: |
    python3 scripts/validate.py --root .
    # Your test commands here
```

### Calendar Credentials (CI)

Nothing in this repository talks to Google directly, so there is **no service account,
no OAuth token and no Google Cloud project to configure**. `scripts/calendar_io.py`
executes Composio tools, and Composio holds the Google grant for a connected account.
A fork therefore needs two secrets:

| Secret | What it is |
|---|---|
| `COMPOSIO_API_KEY` | your Composio API key (Composio dashboard → API keys) |
| `COMPOSIO_USER_ID` | the user id of the connected Google Calendar account |

```bash
# Locally: the same two variables, and no credential at all is needed for a dry run
export COMPOSIO_API_KEY=...
export COMPOSIO_USER_ID=...
python3 scripts/calendar_io.py list --days 7 --out existing.json
python3 scripts/calendar_io.py apply --plan plan.json          # dry run, no HTTP
```

`COMPOSIO_CONNECTED_ACCOUNT_ID` may be set **instead of** `COMPOSIO_USER_ID` (not as well
as — passing both is refused, because the request would be ambiguous about whose calendar a
write belongs to). A user id is the better default: it survives the account being
reconnected, where a pinned connected-account id does not.

### Different Timezones

If you're tracking basketball streams in a different timezone:

```json
{
  "calendarId": "YOUR_CALENDAR_ID@group.calendar.google.com",
  "timezone": "America/New_York",
  "visibility": "public"
}
```

Note: You'll also need to update the timezone references in SKILL.md and other documentation.

### Private Calendar

If you want events to be private instead of public:

```json
{
  "calendarId": "YOUR_CALENDAR_ID@group.calendar.google.com",
  "timezone": "Europe/Berlin",
  "visibility": "private"
}
```

## Calendar ID Format Reference

Google Calendar IDs follow this format:

```
[64-character-hex-string]@group.calendar.google.com
```

Examples:
- `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com` ✅ Valid (64 hex chars)
- `abc123@group.calendar.google.com` ❌ Too short (not 64 chars)
- `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f` ❌ Missing `@group.calendar.google.com`
- `primary` ❌ Special keyword, not a real ID

## Validation Check

The `scripts/validate.py` script includes a check for calendar configuration. It verifies:

1. ✅ `config/calendar.json` exists
2. ✅ `calendarId` field is present and non-empty
3. ✅ `calendarId` is not a placeholder (doesn't contain `YOUR_`, `HERE`, `TODO`, etc.)
4. ✅ `calendarId` matches the pattern: `...@group.calendar.google.com`

If any of these checks fail, the validation will exit with code 1 and print a helpful error message.

## Production credentials (the dress rehearsal)

A calendar id and a green `validate.py` do not mean the runtime will *work*.
A credential that is **set** is not a credential that **works** — this repository
carried a `OPENROUTER_API_KEY` answering `401 User not found` for a month while
every presence-based preflight printed `OK`, because a preflight that reds a
correctly configured runner gets deleted. One command asks the other question for
every surface at once, and it is the step before a release tag:

```bash
python3 scripts/rehearse.py --require-all
```

It **never writes** — no calendar event, no issue, no file: the calendar probe
reads one day and the search probes ask for a single result. It exits `0` only
when nothing is configured-but-rejected and nothing required is missing.

| Surface | Variable(s) | Where it comes from |
|---|---|---|
| `calendar` | `BASKETBALL_CALENDAR_ID`, `COMPOSIO_API_KEY`, and `COMPOSIO_USER_ID` *or* `COMPOSIO_CONNECTED_ACCOUNT_ID` | Steps 1–4 above; the key from [composio.dev](https://composio.dev/) with the Google Calendar toolkit connected |
| `github-issues` | `GITHUB_TOKEN` or `GH_TOKEN` | Actions sets `GITHUB_TOKEN` automatically; locally `gh auth token` |
| `github-issues:grant` | *(none — read from the workflows)* | Whether the workflows that file an issue declare `issues: write`. Checked separately because a **read-only** token authenticates perfectly, so the token probe can only ever prove identity |
| `llm:gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) — the free tier needs no billing |
| `llm:opencode` | `OPENCODE_ZEN_API_KEY` (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `OPENAI_API_KEY`) | `opencode auth login`, or the key page for whichever provider the pin names. The probe runs `opencode --version` as well: a key is not enough if the binary cannot start |
| `llm:openrouter` | `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `search:exa-mcp` | `EXA_API_KEY` | [dashboard.exa.ai](https://dashboard.exa.ai) |
| `search:tinyfish` | `TINYFISH_API_KEY` | [agent.tinyfish.ai/api-keys](https://agent.tinyfish.ai/api-keys) |
| `render:firecrawl` *(optional)* | `FIRECRAWL_API_KEY` | [firecrawl.dev](https://www.firecrawl.dev/) — the hosted escape hatch for SPA pages like `magenta.tv`; keyless mode exists without it |
| `youtube-data-api` *(optional)* | `YOUTUBE_API_KEY` | [Google Cloud console](https://console.cloud.google.com/) with YouTube Data API v3 enabled — only the Data API path needs it, the HTML live filter does not |

Reading the output:

- `OK <name> valid` — set, and a live probe was accepted.
- `OK <name> unverified` — set, and this surface has no probe. Reported, never failed.
- `NO <name> missing` — not set. A legitimate local state, and the reason
  `--require-all` exists: without it the command reports and exits `0`.
- `FAIL <name> invalid` — **set and rejected.** The runtime will try it and fail
  in production, so this is the one row that always blocks a release.

The two rows worth recognising are the ones a presence check cannot see: a rung
whose key is dead, and a rung whose CLI cannot start.

On CI the same names are repository secrets. The workflows reference
`GEMINI_API_KEY`, `EXA_API_KEY`, `TINYFISH_API_KEY`, `COMPOSIO_API_KEY` +
`COMPOSIO_USER_ID` and, optionally, `FIRECRAWL_API_KEY`; `GITHUB_TOKEN` is
supplied by Actions. `llm_model.py` resolves the rung from whichever model
credential is set, so one of the three LLM variables is enough for the run — the
rehearsal reports all three, so a half-configured ladder is visible rather than
inferred.

### Filling them in: one gitignored file

[`.env.example`](.env.example) is the template — every variable in the table above,
with where to get it:

```bash
cp .env.example .env                                    # then fill in what you have
python3 scripts/rehearse.py --env-file .env --require-all
python3 scripts/rehearse.py --env-file .env --markdown   # the same table, for the PR
```

`.env` is gitignored, and it has to be: this repository is public and the file holds
a calendar API key. Two properties of `--env-file` are deliberate. It is **explicit,
never discovered**, so a stray `.env` cannot change what a report says about the
environment it was given; and an **already-set environment variable wins over the
file**, so a single run stays overridable without editing anything:

```bash
GEMINI_API_KEY=another python3 scripts/rehearse.py --env-file .env
```

A malformed line is refused by name and line number rather than skipped, because a
skipped `GEMINI_API_KEY gemini-…` reads as a `missing` row and sends you looking at
Google instead of at your own typo. `--offline` probes only the checks that need no
credential — the half that also runs in CI — which is what to use on a machine with
no keys at all, and `--list` prints the surfaces without touching anything.

## Next Steps

- [ ] Create your Google Calendar
- [ ] Get your Calendar ID
- [ ] Update `config/calendar.json`
- [ ] Run `python3 scripts/validate.py --root .`
- [ ] Test with a sandbox calendar (optional)
- [ ] Run `python3 scripts/rehearse.py --require-all` before releasing
- [ ] Run the skill!

For more information:
- [SKILL.md](SKILL.md) - Main skill instructions
- [README.md](README.md) - Project overview
- [scripts/README.md](scripts/README.md) - Every helper, including the rehearsal
- [references/calendar-setup.md](references/calendar-setup.md) - Calendar event schema
- [references/validation-workflow.md](references/validation-workflow.md) - 7-check validation pipeline
