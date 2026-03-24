"""News aggregator — Hacker News API + RSS feeds + DuckDuckGo search.

HN API (free, no key): hacker-news.firebaseio.com/v0/
RSS: via feedparser (pip install feedparser)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger("lumen.news.aggregator")

_HN_BASE = "https://hacker-news.firebaseio.com/v0"
_TIMEOUT = 15.0
_HEADERS = {"User-Agent": "LumenNewsBot/1.0", "Accept": "application/json"}

# Default RSS feeds (tech/AI focused)
DEFAULT_RSS_FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
]


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    source: str  # "hn", "rss", "search"
    score: int = 0
    comments: int = 0
    published: str = ""
    summary: str = ""


async def fetch_hn_top(count: int = 20) -> list[NewsItem]:
    """Fetch top stories from Hacker News."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(f"{_HN_BASE}/topstories.json", headers=_HEADERS)
            resp.raise_for_status()
            story_ids = resp.json()[:count]
        except Exception as e:
            log.warning("HN topstories failed: %s", e)
            return []

        # Fetch story details concurrently (batch of 10 at a time)
        items = []
        for batch_start in range(0, len(story_ids), 10):
            batch = story_ids[batch_start:batch_start + 10]
            tasks = [
                client.get(f"{_HN_BASE}/item/{sid}.json", headers=_HEADERS)
                for sid in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    continue
                try:
                    data = result.json()
                    if not data or data.get("type") != "story":
                        continue
                    items.append(NewsItem(
                        title=data.get("title", ""),
                        url=data.get("url", f"https://news.ycombinator.com/item?id={data.get('id', '')}"),
                        source="hn",
                        score=data.get("score", 0),
                        comments=data.get("descendants", 0),
                        published=datetime.fromtimestamp(
                            data.get("time", 0), tz=timezone.utc
                        ).strftime("%Y-%m-%d %H:%M UTC") if data.get("time") else "",
                    ))
                except Exception:
                    continue

    return items


async def fetch_rss(feed_urls: list[str] | None = None) -> list[NewsItem]:
    """Fetch items from RSS feeds."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser not installed — skipping RSS")
        return []

    urls = feed_urls or DEFAULT_RSS_FEEDS
    items = []

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers={"User-Agent": "LumenNewsBot/1.0"})
                feed = feedparser.parse(resp.text)
                source_name = feed.feed.get("title", url)[:30]

                for entry in feed.entries[:10]:
                    items.append(NewsItem(
                        title=entry.get("title", ""),
                        url=entry.get("link", ""),
                        source=f"rss:{source_name}",
                        published=entry.get("published", ""),
                        summary=entry.get("summary", "")[:200] if entry.get("summary") else "",
                    ))
            except Exception as e:
                log.warning("RSS feed %s failed: %s", url, e)

    return items


async def fetch_news_search(topic: str, count: int = 5) -> list[NewsItem]:
    """Search DuckDuckGo for recent news on a topic."""
    from server.search import search
    results = await search(f"{topic} news today", max_results=count)
    return [
        NewsItem(
            title=r.title, url=r.url, source="search",
            summary=r.snippet,
        )
        for r in results
    ]


async def get_all_news(hn_count: int = 15, rss_feeds: list[str] | None = None,
                        search_topic: str | None = None) -> list[NewsItem]:
    """Aggregate news from all sources."""
    tasks = [fetch_hn_top(hn_count), fetch_rss(rss_feeds)]
    if search_topic:
        tasks.append(fetch_news_search(search_topic))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for result in results:
        if isinstance(result, Exception):
            log.warning("News source failed: %s", result)
            continue
        all_items.extend(result)

    # Deduplicate by URL
    seen = set()
    unique = []
    for item in all_items:
        if item.url not in seen:
            seen.add(item.url)
            unique.append(item)

    # Sort: HN by score, others by position
    unique.sort(key=lambda x: x.score, reverse=True)
    return unique


def news_to_text(items: list[NewsItem], max_items: int = 15) -> str:
    """Format news items as plain text for LLM context."""
    if not items:
        return "No news items available."

    lines = ["=== NEWS FEED ===\n"]
    for i, item in enumerate(items[:max_items], 1):
        source_tag = item.source.upper()
        score_str = f" ({item.score} pts, {item.comments} comments)" if item.score else ""
        lines.append(f"[{i}] [{source_tag}] {item.title}{score_str}")
        if item.url:
            lines.append(f"    {item.url}")
        if item.summary:
            lines.append(f"    {item.summary[:120]}")
        lines.append("")

    return "\n".join(lines)
