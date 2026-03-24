"""DuckDuckGo web search for Lumen. Ported from OpenClaw search.sh.

No API keys required. Uses DuckDuckGo Lite (POST form) for reliable results.
"""

import re
import html
import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("lumen.search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

_DDG_URL = "https://lite.duckduckgo.com/lite/"


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search DuckDuckGo Lite. Returns up to max_results."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.post(
                _DDG_URL,
                data={"q": query},
                headers=_HEADERS,
            )
            resp.raise_for_status()
        except Exception as e:
            log.warning("DuckDuckGo search failed: %s", e)
            return []

    raw = resp.text

    # Extract title+URL pairs
    title_url_pairs = re.findall(
        r'href=["\']([^"\']+)["\']\s+class=["\']?result-link["\']?>(.*?)</a>',
        raw, re.DOTALL,
    )
    # Extract snippets
    snippets = re.findall(
        r'class=["\']?result-snippet[^>]*>(.*?)</td>',
        raw, re.DOTALL,
    )

    results = []
    for i in range(min(max_results, len(title_url_pairs), len(snippets))):
        url, title = title_url_pairs[i]
        title = _clean_html(title)
        snippet = _clean_html(snippets[i])
        if title and snippet and url.startswith("http"):
            results.append(SearchResult(title=title, url=url, snippet=snippet))

    return results


async def search_text(query: str, max_results: int = 5) -> str:
    """Search and return formatted plain text (for LLM context injection)."""
    results = await search(query, max_results)
    if not results:
        return f"No search results found for: {query}"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}")
        lines.append(f"    {r.url}")
        lines.append(f"    {r.snippet}")
        lines.append("")
    return "\n".join(lines)


async def fetch_page_text(url: str, max_lines: int = 40) -> str:
    """Fetch a URL and extract readable text content."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        except Exception as e:
            log.warning("Failed to fetch %s: %s", url, e)
            return ""

    raw = resp.text

    # Remove noise blocks
    for tag in ("script", "style", "nav", "header", "footer", "aside", "form"):
        raw = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", raw, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)

    # Keep meaningful lines
    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 40]
    return "\n".join(lines[:max_lines])


async def search_and_summarize(query: str, ollama_client, model: str,
                                max_results: int = 5) -> str:
    """Search DuckDuckGo and summarize results via Ollama."""
    search_results = await search_text(query, max_results)
    if search_results.startswith("No search results"):
        return search_results

    prompt = (
        f"Based on these search results, give a concise 3-5 bullet point summary.\n\n"
        f"Search: {query}\n\n"
        f"Results:\n{search_results}"
    )
    system = "Summarize the search results concisely. Use only information from the results. Do not speculate."

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system=system,
        temperature=0.3,
        max_tokens=300,
    )
