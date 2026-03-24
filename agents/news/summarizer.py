"""News summarizer — Qwen-powered summaries of articles and topics."""

import logging

from server.search import search_text, fetch_page_text

log = logging.getLogger("lumen.news.summarizer")


async def summarize_topic(topic: str, ollama_client, model: str) -> str:
    """Search for a topic and return a concise summary."""
    results = await search_text(f"{topic} latest news", max_results=5)
    if results.startswith("No search results"):
        return f"No recent news found about {topic}."

    prompt = (
        f"Based on these search results about '{topic}', give a concise summary. "
        "3-5 bullet points. Lead with the most important development.\n\n"
        f"Results:\n{results}"
    )

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="Concise news summary. Only use information from the provided results. No speculation.",
        temperature=0.3,
        max_tokens=300,
    )


async def summarize_url(url: str, ollama_client, model: str) -> str:
    """Fetch a page and summarize it."""
    text = await fetch_page_text(url, max_lines=50)
    if not text:
        return f"Could not fetch content from {url}"

    prompt = (
        f"Summarize this article in 3-5 bullet points:\n\n{text[:3000]}"
    )

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="Summarize the article concisely. Only use information from the text.",
        temperature=0.3,
        max_tokens=300,
    )
