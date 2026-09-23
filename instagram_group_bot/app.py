"""
app.py — Web Service Wrapper for the Instagram Group Moderation Bot
=====================================================================
This file turns the bot into a deployable "Web Service" (e.g. on Render,
Railway, Heroku-style platforms, or a plain VPS behind a reverse proxy).

WHY THIS EXISTS
---------------
Most hosting platforms (Render, Railway, etc.) expect a "Web Service" to bind
to a port and respond to HTTP requests — otherwise they consider it unhealthy
and may restart/kill it. The actual moderation bot has no need for HTTP at
all (it just polls Instagram in a loop), so this file:

  1. Starts the bot's `run_forever()` loop in a background thread.
  2. Starts a minimal Flask server on the port the platform gives you
     (via the PORT environment variable) so the platform's health checks
     pass and the service stays up.

Endpoints:
  GET /            -> simple status page
  GET /health       -> JSON health check (used by hosting platform)
  GET /status       -> JSON with bot stats (members tracked, uptime, etc.)

RUN LOCALLY
-----------
    pip install -r requirements.txt --break-system-packages
    python app.py

DEPLOY (Render / Railway / similar)
------------------------------------
    Start command:  python app.py
    Set env vars:   IG_BOT_USERNAME, IG_BOT_PASSWORD
    Instance type:  This is a paid/background-capable web service —
                    free tiers that spin instances down on idle are NOT
                    suitable, since the bot must run continuously.
"""

import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify

from instagram_group_bot import GroupModerationBot, BOT_USERNAME, BOT_PASSWORD, log

app = Flask(__name__)

# Shared state for the /status endpoint
bot_state = {
    "started_at": None,
    "last_poll_at": None,
    "status": "starting",   # starting | running | error | stopped
    "last_error": None,
    "monitored_groups": 0,
}

bot_instance = GroupModerationBot(BOT_USERNAME, BOT_PASSWORD)


def run_bot_with_status_updates():
    """Wraps the bot's run_forever loop so we can report live status via HTTP."""
    bot_state["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        bot_instance.login()
        bot_instance.refresh_monitored_threads()
        bot_state["status"] = "running"
        bot_state["monitored_groups"] = len(bot_instance.monitored_thread_ids)
        log.info("Bot started inside web service thread.")

        while True:
            try:
                bot_instance.poll_once()
                bot_state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
                bot_state["monitored_groups"] = len(bot_instance.monitored_thread_ids)
                bot_state["status"] = "running"
                bot_state["last_error"] = None
            except Exception as e:
                bot_state["status"] = "error"
                bot_state["last_error"] = str(e)
                log.error("Poll error: %s", e)
            finally:
                from instagram_group_bot import LOOP_DELAY_SECONDS
                time.sleep(LOOP_DELAY_SECONDS)
    except Exception as e:
        bot_state["status"] = "stopped"
        bot_state["last_error"] = str(e)
        log.error("Bot thread crashed: %s", e)


@app.route("/")
def index():
    return (
        "<h2>Instagram Group Moderation Bot</h2>"
        f"<p>Status: <b>{bot_state['status']}</b></p>"
        f"<p>Started: {bot_state['started_at']}</p>"
        f"<p>Last poll: {bot_state['last_poll_at']}</p>"
        "<p>See <a href='/status'>/status</a> for JSON details, "
        "<a href='/health'>/health</a> for health check.</p>"
    )


@app.route("/health")
def health():
    """Used by hosting platform health checks. Always 200 as long as the
    web process itself is alive, even if the bot is mid-retry."""
    return jsonify({"ok": True}), 200


@app.route("/status")
def status():
    return jsonify(bot_state), 200


if __name__ == "__main__":
    # Start the bot loop in a background thread so it doesn't block Flask
    bot_thread = threading.Thread(target=run_bot_with_status_updates, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
