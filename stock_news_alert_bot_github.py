"""
Equity News & Corporate Announcement Alert Bot - GitHub Actions Edition
------------------------------------------------------------------------
Same detection logic as the original script, but redesigned to RUN ONCE
and exit - because GitHub Actions starts a fresh container each time,
runs your code, then throws it away. There's no persistent process here;
GitHub's scheduler calls this script every 5 minutes instead.

Since each run starts from scratch, "seen_items.json" (which remembers
what's already been alerted on) is committed back into the repo after
each run by the workflow file, so the next run knows what it already sent.

You do NOT run this file directly for normal use - GitHub Actions runs it
for you on schedule. See the setup guide for the one-time repo setup.
BOT_TOKEN and CHAT_ID are read from GitHub Secrets (environment variables),
not hardcoded here, since this file lives in a public repo.
"""

import requests
import feedparser
import json
import os
import sys
import logging

# ---------------- CONFIG ----------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Optional: leave empty to track ALL stocks, or add specific names to
# ALSO always alert on them regardless of event type (e.g. stocks you hold).
ALWAYS_ALERT_ON = [
    # "Reliance", "TCS",
]

# These are the event TYPES that make a stock's news "major" - any stock
# matching one of these gets flagged, whichever company it is.
MAJOR_EVENT_KEYWORDS = {
    "Results/Earnings": [
        "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results",
        "net profit", "net loss", "profit jumps", "profit falls", "misses estimates",
        "beats estimates",
    ],
    "Corporate Action": [
        "stock split", "bonus issue", "buyback", "dividend", "rights issue",
        "qip", "fpo", "ipo",
    ],
    "M&A / Stake Changes": [
        "acquisition", "acquires", "stake sale", "stake buy", "block deal",
        "bulk deal", "merger", "demerger", "open offer", "divest", "takeover",
    ],
    "Contracts / Orders": [
        "wins order", "bags contract", "order win", "contract win", "loi",
        "letter of intent", "mou signed",
    ],
    "Ratings / Analyst Action": [
        "upgraded to", "downgraded to", "rating upgrade", "rating downgrade",
        "target price raised", "target price cut", "brokerage upgrades",
        "brokerage downgrades",
    ],
    "Price Action / Extremes": [
        "upper circuit", "lower circuit", "52-week high", "52 week high",
        "52-week low", "52 week low", "hits record high", "hits record low",
    ],
    "Governance / Legal / Risk": [
        "sebi action", "sebi probe", "fraud", "raid", "resigns", "resignation",
        "steps down", "debt default", "default on", "insolvency", "cbi",
        "ed raids", "credit rating downgrade",
    ],
    "Regulatory / Compliance": [
        "show cause notice", "penalty imposed", "fined by", "license cancelled",
        "ban imposed",
    ],
}

RSS_FEEDS = {
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Economic Times Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Business Standard Markets": "https://www.business-standard.com/rss/markets-106.rss",
}

SEEN_FILE = "seen_items.json"

# NSE tags each announcement with a category - these are ones worth
# alerting on regardless of the free-text wording.
NSE_IMPORTANT_CATEGORIES = {
    "financial results", "board meeting", "acquisition", "amalgamation",
    "merger / demerger", "buyback", "bonus", "stock split", "dividend",
    "credit rating", "resignation", "change in directors", "insolvency",
    "fund raising", "preferential issue", "open offer",
}

# ---------------- LOGGING (prints to the GitHub Actions run log) ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    trimmed = list(seen)[-3000:]  # cap so the committed file doesn't grow forever
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f)


def matches_major_event(text):
    text_lower = text.lower()

    for name in ALWAYS_ALERT_ON:
        if name.lower() in text_lower:
            return f"Tracked stock: {name}"

    for category, keywords in MAJOR_EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return f"{category} ({kw})"

    return None


def send_telegram_alert(title, link, source, match_reason):
    if not BOT_TOKEN or not CHAT_ID:
        logging.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set - check repo secrets.")
        return
    message = f"📢 {match_reason}\n{title}\nSource: {source}\n{link}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            logging.error(f"Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"Telegram send exception: {e}")


def check_rss_feeds(seen):
    new_items = 0
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logging.error(f"Failed to parse feed {source}: {e}")
            continue

        for entry in feed.entries:
            item_id = entry.get("id") or entry.get("link")
            if not item_id or item_id in seen:
                continue

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined_text = f"{title} {summary}"

            match_reason = matches_major_event(combined_text)
            if match_reason:
                send_telegram_alert(title, entry.get("link", ""), source, match_reason)
                new_items += 1

            seen.add(item_id)
    return new_items


def check_nse_announcements(seen):
    """
    Best-effort fetch - NSE actively rate-limits non-browser traffic. If
    this consistently fails in the GitHub Actions log (403/401), the RSS
    feeds above still provide coverage on their own.
    """
    new_items = 0
    url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    }
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        resp = session.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logging.warning(f"NSE announcements request returned {resp.status_code}")
            return 0
        data = resp.json()
    except Exception as e:
        logging.error(f"NSE announcements fetch failed: {e}")
        return 0

    for item in data:
        item_id = item.get("attchmntFile") or f"{item.get('symbol')}_{item.get('an_dt')}"
        if not item_id or item_id in seen:
            continue

        symbol = item.get("symbol", "")
        subject
