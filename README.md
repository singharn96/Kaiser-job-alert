# Kaiser NorCal Nursing Job Alert

A free, hands-off alert that reads Kaiser Permanente's public jobs feed for
**Northern California** postings matching your keywords — **Staff Nurse**,
**Emergency**, and **Short Hour / SH** — and sends you a **Telegram** message
the moment a new one appears.

- **Cost:** $0. GitHub Actions runs it on a schedule; Telegram delivers the alert.
- **Schedule:** every **8 hours** — 6 AM, 2 PM, 10 PM Pacific.
- **No server, no computer left on.** It runs in the cloud.

---

## How it works

1. On a schedule, GitHub Actions runs `check_kaiser_jobs.py`.
2. The script reads Kaiser's public jobs RSS feed (every open posting, with
   title, location and link).
3. It keeps postings in ~70 Northern California cities whose title matches your
   keywords (default: `staff nurse`, `emergency`, `short hour`, `SH`).
4. It compares the results against `seen_jobs.json` (its memory of past postings).
5. Anything new gets sent to your Telegram, each with a direct apply link.

> **Why the RSS feed?** Kaiser's careers site renders search results in the
> browser through a session-based call a plain script can't replay. The `/rss`
> feed is the stable, public source that lists every posting — the right
> foundation for an alert.

The first run just captures a **baseline** and sends a confirmation — so you
don't get blasted with every existing posting. After that, you only hear about
genuinely new jobs.

---

## One-time setup (~10 minutes)

### Part 1 — Telegram bot (do this first, ~3 min)
1. Open Telegram, search **@BotFather**, tap **Start**.
2. Send `/newbot`. Give it a name (e.g. "Kaiser Job Alerts") and a username
   ending in `bot` (e.g. `amandeep_kaiser_alerts_bot`).
3. It replies with a **token** like `7123456789:AAH8fK...` — copy it.
4. **Important:** open a chat with your new bot and press **Start**.
   Bots can't message you until you've messaged them first.
5. Search **@userinfobot**, tap **Start** — it replies with your numeric
   **chat ID** (like `123456789`). Copy that too.

### Part 2 — Create the repo (~2 min)
1. On github.com, click **+** (top-right) → **New repository**.
2. Name it e.g. `kaiser-job-alert`, leave it **Public**, check
   **Add a README file**, click **Create repository**.

### Part 3 — Upload the files (~3 min)
Unzip this project on your computer, then:
1. In your repo: **Add file → Create new file**.
2. In the filename box type exactly: `.github/workflows/job-check.yml`
   (GitHub creates the folders as you type the slashes).
3. Open `job-check.yml` from the unzipped folder in a text editor, copy
   everything, paste it in, click **Commit changes**.
4. Now **Add file → Upload files**, and drag in the two loose files:
   `check_kaiser_jobs.py` and `requirements.txt`. Commit.

### Part 4 — Add your secrets (~1 min)
In the repo: **Settings → Secrets and variables → Actions →
New repository secret**. Add two (names must match **exactly**, all caps):

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your number from userinfobot |

### Part 5 — Test it
1. Click the **Actions** tab. If prompted, **enable workflows**.
2. Click **Kaiser NorCal RN Job Check** in the sidebar → **Run workflow**.
3. Wait a minute, refresh — a green checkmark should appear and a
   ✅ baseline message should land in your Telegram.

From then on it runs itself every 8 hours.

---

## Customizing

Edit the `KEYWORDS` line in `.github/workflows/job-check.yml` under the
**Run job check** step. It's a comma-separated list of title terms; a posting
alerts if its title contains **any** of them (short all-letter terms like `SH`
match as whole words).

- **Default:** `KEYWORDS: "staff nurse,emergency,short hour,SH"`.
- **ED-only:** `KEYWORDS: "emergency"`.
- **Add specialties:** e.g. `KEYWORDS: "staff nurse,emergency,icu,telemetry"`.

Change the schedule by editing the `cron` line at the top of the same file.
The times are **UTC**. Current setting `0 5,13,21 * * *` = 6 AM / 2 PM / 10 PM
Pacific. (Note: GitHub may delay scheduled runs a few minutes under load.)

---

## If alerts stop coming

This reads Kaiser's public RSS feed, which is far more stable than scraping the
web pages — but if Kaiser ever moves or reformats the feed, it may need a tweak.

**How to tell:** open the **Actions** tab → click the latest run → open the
**Run job check** step. The log prints:

```
[info] feed items: 2511
[info] NorCal matches for ['staff nurse', 'emergency', 'short hour', 'sh']: 80
```

If `feed items` is `0`, the feed URL changed. If `feed items` looks normal but
`NorCal matches` is `0` for a long time, the title/location format changed —
update the filters in `check_kaiser_jobs.py`.

---

## Files

| File | Purpose |
|------|---------|
| `check_kaiser_jobs.py` | the scraper + Telegram sender |
| `requirements.txt` | Python dependencies |
| `.github/workflows/job-check.yml` | the every-8-hours schedule |
| `seen_jobs.json` | auto-created memory of past postings (don't edit) |
