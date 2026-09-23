# Instagram Group Chat Anti-Spam & Welcome Bot

A moderation bot for Instagram group DMs, built on `instagrapi`. It
**automatically discovers every group thread** your bot account is part of —
no manual thread ID needed.

## ⚠️ Before you run this

- Automating an Instagram account this way (auto-messaging, polling in a loop,
  programmatically removing members) **violates Instagram's Terms of Service**.
  Accounts doing this are commonly rate-limited, challenged for verification, or
  permanently banned — even with delays between requests. Only run this on an
  account you're fully prepared to lose.
- `instagrapi` is an unofficial, reverse-engineered client and can break any
  time Instagram changes its private API.
- The bot can only react to a reel/video/link/bad word **after** it's already
  been posted and briefly visible — it can't truly stop content from ever
  appearing in the chat before members see it.
- If the bot account is in many groups, polling all of them every few seconds
  multiplies the number of API calls per cycle — consider raising
  `LOOP_DELAY_SECONDS` if you're in a lot of groups.

## Setup

```bash
pip install instagrapi --break-system-packages
```

Set your credentials as environment variables (recommended over editing the
file directly):

```bash
export IG_BOT_USERNAME="your_username"
export IG_BOT_PASSWORD="your_password"
```

No thread ID is needed — on startup, and periodically afterward (every
`THREAD_DISCOVERY_EVERY_N_POLLS` poll cycles), the bot lists all of the
account's DM threads and automatically starts monitoring every one that
looks like a group chat (more than one other participant). If the account
gets added to a new group later, the bot picks it up on its next discovery
scan without a restart.

## Run locally (as a plain script — no web server)

```bash
python instagram_group_bot.py
```

## Run as a Web Service

Use `app.py` instead — it wraps the bot in a Flask server so hosting
platforms that require a "Web Service" (binds to a port, responds to health
checks) will keep it running.

```bash
pip install -r requirements.txt --break-system-packages
python app.py
```

Endpoints:
- `GET /` — simple status page
- `GET /health` — JSON health check (used by the hosting platform)
- `GET /status` — JSON with live bot state (groups monitored, last poll time, last error)

### Deploying (Render / Railway / any VPS)

1. Push this folder to a Git repo (or upload directly if the platform supports it).
2. Create a **Web Service** (not a free/auto-sleep tier — the bot must run
   continuously, so pick a plan that doesn't spin the instance down when idle).
3. Set the start command to:
   ```
   python app.py
   ```
4. Set environment variables in the platform's dashboard:
   - `IG_BOT_USERNAME`
   - `IG_BOT_PASSWORD`
5. Deploy. The platform will hit `/health` to confirm the service is alive;
   the actual moderation loop runs in a background thread independent of
   any HTTP traffic.

### Deploying with Docker

```bash
docker build -t ig-group-bot .
docker run -d \
  -e IG_BOT_USERNAME=your_username \
  -e IG_BOT_PASSWORD=your_password \
  -p 8080:8080 \
  ig-group-bot
```

The bot will:
1. Log in (and reuse a saved session on future runs, in `ig_session.json`, to
   avoid repeated logins that Instagram flags).
2. Discover every group thread the account is in and snapshot each one's
   current members and messages, so it only reacts to things that happen
   after it starts watching that group.
3. Poll every monitored group every `LOOP_DELAY_SECONDS` (default 5s) for:
   - New members → sends a welcome message tagging their username and the
     group rules.
   - New text messages → checks against the bad-word list, the spam keyword
     list, and a link/URL regex.
   - New media items → flags reel/video/external-media shares.
4. On any violation, sends an alert message stating the reason, then removes
   the user via `cl.direct_thread_remove_user(thread_id, user_id)`.
5. Periodically re-scans for newly-added group threads (every
   `THREAD_DISCOVERY_EVERY_N_POLLS` cycles) so new groups get picked up
   automatically.

## Customizing

All of the following are plain Python sets/strings near the top of
`instagram_group_bot.py` — edit them directly:

- `BAD_WORDS` — profanity list (Hindi/English, lowercase).
- `SPAM_KEYWORDS` — spam phrases to block.
- `LINK_PATTERNS` — regex patterns used to detect URLs/links.
- `BLOCKED_MEDIA_ITEM_TYPES` — instagrapi message `item_type` values treated
  as reels/video/media shares.
- `GROUP_RULES_TEXT` / `WELCOME_MESSAGE_TEMPLATE` — welcome message wording.
- `LOOP_DELAY_SECONDS` / `ACTION_COOLDOWN_SECONDS` — timing between API calls.
- `THREAD_DISCOVERY_EVERY_N_POLLS` — how often to re-scan for new group threads.

## Logs

Everything is logged to both the console and `ig_bot.log` (timestamped),
including every removal and the reason for it.
