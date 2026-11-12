"""Collect current article evidence before sending stories to the cloud model."""
import logging
import re
from urllib.parse import urlencode

import feedparser
import requests
import trafilatura

log = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; IndiaNewsAIStudio/1.0; news research)"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _article_text(url: str, max_chars: int) -> tuple[str, str]:
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=18,
                                allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower() and "text" not in content_type.lower():
            return "", final_url
        text = trafilatura.extract(
            response.text,
            include_comments=False,
            include_tables=False,
            include_links=False,
            favor_precision=True,
        ) or ""
        return _clean(text)[:max_chars], final_url
    except Exception as exc:
        log.debug("Could not extract article %s: %s", url, exc)
        return "", url


def _search_feed(title: str) -> list[dict]:
    query = urlencode({"q": f'"{title}"', "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    url = f"https://news.google.com/rss/search?{query}"
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        results = []
        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            headline = _clean(entry.get("title", ""))
            if not link or not headline:
                continue
            results.append({
                "title": headline,
                "url": link,
                "source": _clean((entry.get("source") or {}).get("title", "")),
                "published": _clean(entry.get("published", "") or entry.get("updated", "")),
                "summary": _clean(entry.get("summary", ""))[:700],
            })
        return results
    except Exception as exc:
        log.warning("Related Google News search failed for '%s': %s", title[:70], exc)
        return []


def build_news_context(candidates: list[dict], max_sources: int = 3,
                       article_chars: int = 3500) -> list[dict]:
    """Attach up to max_sources current reports and extracted text to each RSS candidate."""
    enriched = []
    for index, candidate in enumerate(candidates, start=1):
        log.info("Collecting source material %d/%d: %s", index, len(candidates), candidate["title"][:90])
        discovered = [{
            "title": candidate.get("title", ""),
            "url": candidate.get("url", ""),
            "source": candidate.get("source", ""),
            "published": candidate.get("published", ""),
            "summary": candidate.get("summary", ""),
        }]
        discovered.extend(_search_feed(candidate.get("title", "")))

        unique, seen = [], set()
        for item in discovered:
            key = (item.get("url") or "").split("?")[0]
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= max(1, min(max_sources, 4)):
                break

        source_material = []
        for item in unique:
            article_text, final_url = _article_text(item["url"], article_chars)
            source_material.append({
                **item,
                "resolved_url": final_url,
                "article_text": article_text,
                "extraction_status": "extracted" if article_text else "rss_metadata_only",
            })
        enriched.append({**candidate, "research_sources": source_material})
    return enriched
