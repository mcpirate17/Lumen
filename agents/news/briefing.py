"""Morning/on-demand news briefing generator."""

import logging
from datetime import datetime, timezone

from agents.news.aggregator import get_all_news, news_to_text

log = logging.getLogger("lumen.news.briefing")


async def generate_briefing(ollama_client, model: str,
                             focus: str = "tech and AI") -> str:
    """Generate a daily news briefing.

    Fetches from HN + RSS + optional search, then asks Qwen to
    distill into a 5-item briefing.
    """
    items = await get_all_news(hn_count=20, search_topic=f"{focus} news")
    text = news_to_text(items, max_items=20)

    prompt = (
        f"Create a concise news briefing from these sources. "
        f"Focus on {focus}.\n\n"
        "Format:\n"
        "1. [HEADLINE] — 1 sentence summary\n"
        "2. [HEADLINE] — 1 sentence summary\n"
        "... (up to 5 items)\n\n"
        "Pick the 5 most important/interesting stories. "
        "If there's a big breaking story, lead with it.\n\n"
        f"Sources:\n{text}"
    )

    briefing = await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="You are a news editor creating a daily briefing. Be concise and factual.",
        temperature=0.2,
        max_tokens=400,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"# News Briefing — {timestamp}\n\n{briefing}"


async def generate_voice_briefing(ollama_client, model: str,
                                   focus: str = "tech and AI") -> str:
    """Generate a short voice-friendly briefing (2-3 sentences)."""
    items = await get_all_news(hn_count=10, search_topic=f"{focus} news")
    text = news_to_text(items, max_items=10)

    prompt = (
        "Give me the top 2-3 news stories in 2-3 sentences total. "
        "Lead with the biggest story. Be conversational.\n\n"
        f"Sources:\n{text}"
    )

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="Brief, conversational news summary for voice delivery. 2-3 sentences max.",
        temperature=0.3,
        max_tokens=100,
    )
