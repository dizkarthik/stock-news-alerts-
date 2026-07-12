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

# TEST MODE: matches almost anything, to verify the pipeline end-to-end.
# Revert this to an empty list (or specific tickers) once testing is done.
ALWAYS_ALERT_ON = [
    "a", "the", "market",
]

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

NSE_IMPORTANT_CATEGORIES = {
    "financial results", "board meeting", "acquisition", "amalgamation",
    "merger / demerger", "buyback", "bonus", "stock split", "dividend",
    "credit rating", "resignation", "change in directors", "insolvency",
    "fund raising", "preferential issue", "open offer",
}

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
    trimmed = list(seen)[-3000:]
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
    print(f"DEBUG: send_telegram_alert called - title={title[:50]}", flush=True)
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
        print(f"DEBUG: Telegram response status={resp.status_code} body={resp.text[:200]}", flush=True)
        if resp.status_code != 200:
            logging.error(f"Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"DEBUG: Telegram send exception: {e}", flush=True)
        logging.error(f"Telegram send exception: {e}")


def check_rss_feeds(seen):
    new_items = 0
    for source, url in RSS_FEEDS.items():
        print(f"DEBUG: fetching {source}", flush=True)
        try:
            feed = feedparser.parse(url)
            print(f"DEBUG: {source} returned {len(feed.entries)} entries", flush=True)
        except Exception as e:
            print(f"DEBUG: {source} fetch exception: {e}", flush=True)
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
        subject = item.get("desc", "") or item.get("subject", "")
        category = (item.get("category") or "").lower().strip()
        combined_text = f"{symbol} {subject}"

        match_reason = matches_major_event(combined_text)
        if not match_reason and category in NSE_IMPORTANT_CATEGORIES:
            match_reason = f"NSE category: {category}"

        if match_reason:
            title = f"{symbol}: {subject}"
            link = item.get(
                "attchmntFile",
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            )
            send_telegram_alert(title, link, "NSE Announcements", match_reason)
            new_items += 1

        seen.add(item_id)
    return new_items


def main():
    print("DEBUG: main() started", flush=True)
    logging.info("Running scheduled check (single pass)")
    seen = load_seen()
    print(f"DEBUG: loaded {len(seen)} seen items", flush=True)

    rss_new = check_rss_feeds(seen)
    nse_new = check_nse_announcements(seen)
    save_seen(seen)

    total = rss_new + nse_new
    logging.info(f"Done. Sent {total} new alert(s) this run.")
    print(f"DEBUG: main() finished, total={total}", flush=True)


print("DEBUG: script loaded, about to check __name__", flush=True)
if __name__ == "__main__":
    main()
