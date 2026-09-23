"""
Instagram Group Chat Anti-Spam & Welcome Bot
=============================================
Built on top of the `instagrapi` library.

WHAT THIS BOT DOES
-------------------
1. Automatically discovers every GROUP chat thread your bot account is part
   of — no manual THREAD_ID needed. New groups the account gets added to are
   picked up automatically on the next poll cycle.
2. Welcomes new members who join any monitored group thread.
3. Scans incoming text for bad words / profanity (Hindi + English list, editable).
4. Scans incoming text for spam keywords and links (URLs, t.me, wa.me, .com, etc).
5. Detects reel/video/media shares in any monitored thread (instagrapi cannot
   inspect the content of a shared reel before it renders — it can only
   detect that a media item of a certain type was shared and act on it after
   the fact).
6. On any rule violation, posts an alert message explaining the reason and
   then attempts to remove the offending user from that thread.

IMPORTANT — READ BEFORE RUNNING
--------------------------------
* Automating actions on an Instagram account (auto-messaging, polling threads
  in a loop, programmatically removing users) is against Instagram's Terms of
  Service. Accounts that do this are routinely rate-limited, challenged, or
  permanently banned — a longer delay reduces but does not remove that risk.
  Do not run this against an account you cannot afford to lose.
* instagrapi is an unofficial, reverse-engineered client. Instagram can change
  its private API at any time and break this script without warning.
* This script cannot truly "block" a reel from ever appearing in the chat —
  by the time the bot's poll loop sees the message, group members have
  already seen it appear. What the bot *can* do is detect the share quickly
  and remove the sender / delete the message so it doesn't stay visible long.
* Store credentials as environment variables in production, not as plaintext
  in this file (see CONFIGURATION below).
* A single account polling MANY group threads every few seconds multiplies
  the number of API calls per cycle. If the bot is in a lot of groups,
  consider raising LOOP_DELAY_SECONDS so total request volume stays low.

INSTALL
-------
    pip install instagrapi --break-system-packages

RUN
---
    python instagram_group_bot.py
"""

import os
import re
import time
import logging
import traceback
from typing import Set, Dict, Any, Optional

from instagrapi import Client
from instagrapi.exceptions import ClientError, LoginRequired


# =====================================================================
# CONFIGURATION — edit these, or better, set them as environment vars
# =====================================================================

BOT_USERNAME = os.environ.get("IG_BOT_USERNAME", "your_bot_username_here")
BOT_PASSWORD = os.environ.get("IG_BOT_PASSWORD", "your_bot_password_here")

# No THREAD_ID needed anymore — the bot auto-discovers every GROUP thread
# (3+ participants) the account is part of, and monitors all of them.
# How often (in poll cycles) to re-scan for newly-added group threads.
THREAD_DISCOVERY_EVERY_N_POLLS = 12  # e.g. 12 * 5s = every ~60s

# Session file so we don't have to re-login (and trigger checkpoints) every run
SESSION_FILE = "ig_session.json"

# How long to sleep between poll cycles (seconds). Keep this reasonably high —
# short intervals are what get bot accounts flagged.
LOOP_DELAY_SECONDS = 5

# Extra pause after every write action (send message / remove user), on top
# of LOOP_DELAY_SECONDS, to further space out API calls that Instagram
# watches closely.
ACTION_COOLDOWN_SECONDS = 3

# Group rules text included in the welcome message (Hindi)
GROUP_RULES_TEXT = (
    "1) गाली-गलौज या गंदी भाषा का इस्तेमाल न करें।\n"
    "2) कोई भी लिंक, विज्ञापन या स्पैम (क्रिप्टो, \"पैसे कमाओ\" आदि) शेयर न करें।\n"
    "3) रील्स, वीडियो या 18+ कंटेंट शेयर न करें।\n"
    "इन नियमों को तोड़ने पर आपको ऑटोमैटिकली ग्रुप से हटा दिया जाएगा।"
)

WELCOME_MESSAGE_TEMPLATE = (
    "👋 ग्रुप में आपका स्वागत है, @{username}!\n\n"
    "कृपया ग्रुप के नियम पढ़ें और उनका पालन करें:\n"
    "{rules}"
)

# ---- Bad word / profanity list (Hindi + English, lowercase, editable) ----
BAD_WORDS = {
    # English
    "fuck", "fucking", "fucker", "bitch", "asshole", "bastard", "slut",
    "whore", "cunt", "dick", "pussy", "motherfucker",
    # Hinglish / Hindi (romanized)
    "chutiya", "chutiye", "madarchod", "behenchod", "bhenchod", "randi",
    "gandu", "gaand", "lund", "lauda", "harami", "saala kutta", "kamina",
    "kutte", "chodu", "bhosdike", "bhosdi", "randi ka baccha",
}

# ---- Spam keyword list (editable) ----
SPAM_KEYWORDS = {
    "earn money", "earn from home", "work from home", "crypto", "bitcoin",
    "forex", "investment opportunity", "double your money", "click here",
    "free followers", "buy followers", "cash prize", "you have won",
    "claim your reward", "loan approved", "lottery winner",
}

# ---- Link / URL detection patterns ----
LINK_PATTERNS = [
    r"https?://\S+",
    r"www\.\S+\.\S+",
    r"\bt\.me/\S+",
    r"\bwa\.me/\S+",
    r"\b\S+\.com\b",
    r"\b\S+\.in\b",
    r"\b\S+\.xyz\b",
]
LINK_REGEX = re.compile("|".join(LINK_PATTERNS), re.IGNORECASE)

# instagrapi media/item types considered "video / reel / external media"
BLOCKED_MEDIA_ITEM_TYPES = {"media_share", "clip", "reel_share", "video_call_event", "raven_media"}


# =====================================================================
# LOGGING
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ig_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ig_group_bot")


# =====================================================================
# BOT CLASS
# =====================================================================

class GroupModerationBot:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.cl = Client()

        # Per-thread state, keyed by thread_id (str)
        self.known_user_ids: Dict[str, Set[int]] = {}
        self.seen_message_ids: Dict[str, Set[str]] = {}
        self.monitored_thread_ids: Set[str] = set()

        self._poll_count = 0

    # -----------------------------------------------------------------
    # LOGIN
    # -----------------------------------------------------------------
    def login(self) -> None:
        try:
            if os.path.exists(SESSION_FILE):
                self.cl.load_settings(SESSION_FILE)
                self.cl.login(self.username, self.password)
                # NOTE: we deliberately do NOT call an extra verification
                # endpoint here (e.g. account_info/get_timeline_feed). On
                # some hosting providers, Instagram returns 403 on those
                # verification calls even for a perfectly valid session
                # (datacenter IPs get extra scrutiny), which was wrongly
                # triggering a fresh username/password login every time —
                # and that fresh login fails with "Instagram is out of
                # date" for unrelated reasons. Trust the loaded session;
                # if it's genuinely invalid, real calls later will raise
                # LoginRequired and run_forever() already re-logs-in then.
                log.info("Logged in using saved session.")
            else:
                raise FileNotFoundError
        except Exception:
            log.info("No valid saved session, doing fresh login.")
            self.cl.set_settings({})
            self.cl.login(self.username, self.password)
            self.cl.dump_settings(SESSION_FILE)
            log.info("Fresh login successful, session saved.")

    # -----------------------------------------------------------------
    # GROUP THREAD DISCOVERY
    # -----------------------------------------------------------------
    def discover_group_threads(self) -> Set[str]:
        """Finds every thread the bot account is part of that looks like a
        GROUP chat (more than 2 participants including the bot itself, or
        instagrapi's own is_group flag when available)."""
        group_ids: Set[str] = set()
        try:
            threads = self.cl.direct_threads(amount=100)
            for thread in threads:
                is_group = getattr(thread, "is_group", None)
                if is_group is None:
                    # Fallback: 2 participants = 1-on-1 DM, 3+ = group
                    is_group = len(thread.users) > 1
                if is_group:
                    group_ids.add(str(thread.id))
        except Exception as e:
            log.error("Error while discovering group threads: %s", e)
        return group_ids

    def prime_thread_state(self, thread_id: str) -> None:
        """Snapshot current members and message ids for one thread so we
        only react to things that happen AFTER the bot starts watching it."""
        try:
            thread = self.cl.direct_thread(thread_id)
            self.known_user_ids[thread_id] = {u.pk for u in thread.users}
            self.known_user_ids[thread_id].add(self.cl.user_id)
            self.seen_message_ids[thread_id] = {item.id for item in thread.messages}
            log.info(
                "Primed thread %s: %d known members, %d known messages.",
                thread_id,
                len(self.known_user_ids[thread_id]),
                len(self.seen_message_ids[thread_id]),
            )
        except Exception as e:
            log.error("Failed to prime state for thread %s: %s", thread_id, e)

    def refresh_monitored_threads(self) -> None:
        """Discovers group threads and starts tracking any new ones found
        (without touching state for threads already being monitored)."""
        discovered = self.discover_group_threads()
        new_threads = discovered - self.monitored_thread_ids
        for tid in new_threads:
            log.info("New group thread discovered: %s", tid)
            self.prime_thread_state(tid)
        self.monitored_thread_ids |= discovered
        if new_threads:
            log.info("Now monitoring %d group thread(s) total.", len(self.monitored_thread_ids))

    # -----------------------------------------------------------------
    # RULE CHECKS
    # -----------------------------------------------------------------
    @staticmethod
    def contains_bad_word(text: str) -> Optional[str]:
        lowered = text.lower()
        for word in BAD_WORDS:
            if word in lowered:
                return word
        return None

    @staticmethod
    def contains_spam_keyword(text: str) -> Optional[str]:
        lowered = text.lower()
        for kw in SPAM_KEYWORDS:
            if kw in lowered:
                return kw
        return None

    @staticmethod
    def contains_link(text: str) -> bool:
        return bool(LINK_REGEX.search(text))

    def evaluate_text_message(self, text: str) -> Optional[str]:
        """Returns a violation reason string (Hindi), or None if the message is clean."""
        bad_word = self.contains_bad_word(text)
        if bad_word:
            return f"गंदी भाषा का इस्तेमाल किया गया ('{bad_word}')"

        spam_kw = self.contains_spam_keyword(text)
        if spam_kw:
            return f"स्पैम कीवर्ड मिला ('{spam_kw}')"

        if self.contains_link(text):
            return "लिंक/URL शेयर करना मना है"

        return None

    @staticmethod
    def is_blocked_media_item(item: Any) -> bool:
        item_type = getattr(item, "item_type", None)
        return item_type in BLOCKED_MEDIA_ITEM_TYPES

    # -----------------------------------------------------------------
    # ACTIONS
    # -----------------------------------------------------------------
    def send_message(self, thread_id: str, text: str) -> None:
        try:
            self.cl.direct_send(text, thread_ids=[int(thread_id)])
            log.info("[%s] Sent message: %s", thread_id, text[:80])
        except Exception as e:
            log.error("[%s] Failed to send message: %s", thread_id, e)
        finally:
            time.sleep(ACTION_COOLDOWN_SECONDS)

    def welcome_new_user(self, thread_id: str, user_id: int) -> None:
        try:
            user_info = self.cl.user_info(user_id)
            username = user_info.username
        except Exception as e:
            log.error("Could not fetch user info for %s: %s", user_id, e)
            username = str(user_id)

        text = WELCOME_MESSAGE_TEMPLATE.format(username=username, rules=GROUP_RULES_TEXT)
        self.send_message(thread_id, text)

    def remove_user(self, thread_id: str, user_id: int, reason: str) -> None:
        try:
            username = self._safe_username(user_id)
            alert = f"⚠️ @{username} को ग्रुप से हटा दिया गया है। कारण: {reason}"
            self.send_message(thread_id, alert)

            removed = self.cl.direct_thread_remove_user(thread_id, user_id)
            if removed:
                log.info("[%s] Removed user %s (%s). Reason: %s", thread_id, username, user_id, reason)
            else:
                log.warning("[%s] Remove call returned falsy for user %s", thread_id, user_id)

            # stop tracking them so we don't try to act on stale state
            self.known_user_ids.get(thread_id, set()).discard(user_id)
        except ClientError as e:
            log.error("[%s] Instagram API error removing user %s: %s", thread_id, user_id, e)
        except Exception as e:
            log.error("[%s] Unexpected error removing user %s: %s", thread_id, user_id, e)
        finally:
            time.sleep(ACTION_COOLDOWN_SECONDS)

    def _safe_username(self, user_id: int) -> str:
        try:
            return self.cl.user_info(user_id).username
        except Exception:
            return str(user_id)

    # -----------------------------------------------------------------
    # POLL CYCLE (per thread)
    # -----------------------------------------------------------------
    def poll_thread_once(self, thread_id: str) -> None:
        thread = self.cl.direct_thread(thread_id)

        # ---- 1. New member detection -> welcome message ----
        current_user_ids = {u.pk for u in thread.users}
        known = self.known_user_ids.setdefault(thread_id, set())
        new_user_ids = current_user_ids - known
        for uid in new_user_ids:
            if uid == self.cl.user_id:
                continue
            log.info("[%s] New member detected: %s", thread_id, uid)
            self.welcome_new_user(thread_id, uid)
        self.known_user_ids[thread_id] |= current_user_ids

        # ---- 2. New message detection -> content moderation ----
        seen = self.seen_message_ids.setdefault(thread_id, set())
        # instagrapi returns messages newest-first; process oldest-first
        new_items = [m for m in thread.messages if m.id not in seen]
        new_items.reverse()

        for item in new_items:
            self.seen_message_ids[thread_id].add(item.id)

            sender_id = getattr(item, "user_id", None)
            if sender_id is None or sender_id == self.cl.user_id:
                continue  # ignore the bot's own messages

            # --- Reels / video / external media check ---
            if self.is_blocked_media_item(item):
                self.remove_user(thread_id, sender_id, "रील्स/वीडियो/मीडिया शेयर करना इस ग्रुप में मना है")
                continue

            # --- Text-based checks (bad words, spam, links) ---
            text = getattr(item, "text", None)
            if text:
                reason = self.evaluate_text_message(text)
                if reason:
                    self.remove_user(thread_id, sender_id, reason)

    def poll_once(self) -> None:
        """One full cycle: periodically re-discover group threads, then poll
        every currently-monitored group thread."""
        if self._poll_count % THREAD_DISCOVERY_EVERY_N_POLLS == 0:
            self.refresh_monitored_threads()
        self._poll_count += 1

        for thread_id in list(self.monitored_thread_ids):
            try:
                self.poll_thread_once(thread_id)
            except Exception as e:
                log.error("[%s] Error polling thread: %s", thread_id, e)

    # -----------------------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------------------
    def run_forever(self) -> None:
        self.login()
        self.refresh_monitored_threads()
        log.info(
            "Bot is now auto-monitoring %d group thread(s), polling every %ss.",
            len(self.monitored_thread_ids), LOOP_DELAY_SECONDS,
        )

        while True:
            try:
                self.poll_once()
            except LoginRequired:
                log.warning("Session expired, re-logging in...")
                self.login()
            except ClientError as e:
                log.error("Instagram client error during poll: %s", e)
            except Exception:
                log.error("Unexpected error during poll:\n%s", traceback.format_exc())
            finally:
                time.sleep(LOOP_DELAY_SECONDS)


# =====================================================================
# ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    if BOT_USERNAME.startswith("your_bot_username") or BOT_PASSWORD.startswith("your_bot_password"):
        log.warning(
            "You are using placeholder credentials. Set IG_BOT_USERNAME / "
            "IG_BOT_PASSWORD environment variables, or edit the "
            "CONFIGURATION section at the top of this file."
        )

    bot = GroupModerationBot(BOT_USERNAME, BOT_PASSWORD)
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")
