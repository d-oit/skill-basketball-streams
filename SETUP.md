# Setup Guide

This guide walks you through setting up `skill-basketball-streams` for your own use, including creating a Google Calendar, configuring the skill, and running your first validation.

## Quick Start

1. **Create a Google Calendar** for your basketball streams
2. **Get your Calendar ID** from Google Calendar settings
3. **Configure the skill** by updating `config/calendar.json`
4. **Validate your setup** with `python3 scripts/validate.py --root .`
5. **Run the skill** with your preferred AI agent framework

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

### Method B: From Google Calendar API

If you're using the Google Calendar API, you can list your calendars:

```bash
# Using gcloud (if authenticated)
gcloud calendar calendars list

# Or using the Google Calendar API directly
curl -X GET \
  'https://www.googleapis.com/calendar/v3/calendars' \
  -H 'Authorization: Bearer YOUR_ACCESS_TOKEN'
```

Look for the `id` field of your calendar.

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
  "defaultColorId": "6",
  "visibility": "public"
}
```

**Configuration Fields:**

| Field | Description | Default | Required |
|-------|-------------|---------|----------|
| `calendarId` | Your Google Calendar ID | `""` | **Yes** |
| `timezone` | Timezone for events | `"Europe/Berlin"` | No |
| `defaultColorId` | Default event color | `"6"` (Tangerine/Orange) | No |
| `visibility` | Event visibility | `"public"` | No |

**Color ID Options:**
- `"1"` - Lavender
- `"2"` - Sage (FIBA international games)
- `"3"` - Grape
- `"4"` - Flamingo
- `"5"` - Banana
- `"6"` - Tangerine/Orange (default)
- `"7"` - Peacock
- `"8"` - Graphite
- `"9"` - Blueberry
- `"10"` - Basil
- `"11"` - Tomato/Red (EuroLeague finals)

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

**Note:** The environment variable **only overrides the `calendarId`**. Other settings (timezone, defaultColorId, visibility) still come from `config/calendar.json`.

### Option C: Both Config File + Environment Variable

You can use both methods together. The environment variable takes precedence for `calendarId`, while other settings come from the config file.

## Step 4: Validate Your Setup

Before running the skill, validate that everything is configured correctly:

```bash
# Run the full validator
python3 scripts/validate.py --root .

# Expected output:
# OK: evals: 20 cases conform to standard schema (skill_name=skill-basketball-streams)
# OK: SKILL.md: frontmatter valid + body 138 <= 250 lines + mandatory sections present
# OK: references: all 10 backtick-wrapped .md paths resolve (references/approved-sources.md, ...)
```

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

### "No events created"

**Possible causes:**
1. No free basketball streams available in the next 7 days
2. All found streams failed validation (check the skill's output)
3. Calendar ID is incorrect (events are being created in the wrong calendar)
4. Duplicate detection is too aggressive

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

### Different Timezones

If you're tracking basketball streams in a different timezone:

```json
{
  "calendarId": "YOUR_CALENDAR_ID@group.calendar.google.com",
  "timezone": "America/New_York",
  "defaultColorId": "6",
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
  "defaultColorId": "6",
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

## Next Steps

- [ ] Create your Google Calendar
- [ ] Get your Calendar ID
- [ ] Update `config/calendar.json`
- [ ] Run `python3 scripts/validate.py --root .`
- [ ] Test with a sandbox calendar (optional)
- [ ] Run the skill!

For more information:
- [SKILL.md](SKILL.md) - Main skill instructions
- [README.md](README.md) - Project overview
- [references/calendar-setup.md](references/calendar-setup.md) - Calendar event schema
- [references/validation-workflow.md](references/validation-workflow.md) - 7-check validation pipeline
