import calendar
import difflib
import logging
import re
from datetime import datetime, timezone

import feedparser
import requests

log = logging.getLogger(__name__)


def _normalise_title(value: str) -> str:
    value = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    return re.sub(r"\s+", " ", value).strip()


def fetch_indian_news(rss_url: str, limit: int = 5) -> list[dict]:
    """Fetch recent all-topic English India news and remove near-duplicate headlines."""
    headers = {"User-Agent": "IndiaNewsAIStudio/1.0 (RSS news discovery)"}
    response = requests.get(rss_url, headers=headers, timeout=25)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Could not parse Google News RSS: {feed.bozo_exception}")

    entries = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        published = entry.get("published") or entry.get("updated") or ""
        published_iso = ""
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            published_iso = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc).isoformat()
        entries.append({
            "title": title,
            "url": link,
            "published": published,
            "published_at": published_iso,
            "summary": re.sub(r"<[^>]+>", " ", entry.get("summary", "") or "").strip(),
            "source": (entry.get("source") or {}).get("title", ""),
        })

    entries.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    unique, seen_titles = [], []
    for item in entries:
        norm = _normalise_title(item["title"])
        if any(difflib.SequenceMatcher(None, norm, old).ratio() >= 0.88 for old in seen_titles):
            continue
        unique.append(item)
        seen_titles.append(norm)
        if len(unique) >= max(1, min(limit, 5)):
            break
    log.info("Google News RSS returned %d unique candidate(s).", len(unique))
    return unique
