"""Context filtering for domain data injection.

Instead of dumping full market snapshots (2000+ tokens) into small model prompts,
filter to only what's relevant to the specific query. Small models perform dramatically
better with 2 high-quality chunks than 8 marginally relevant ones.

Research: Optimal context for small models is 500-1000 tokens of highly relevant data.
"""

import re
import logging

log = logging.getLogger("lumen.context")


def _split_sections(text: str) -> dict[str, str]:
    """Split text into named sections delimited by '--- TITLE ---' or '--- TITLE (source) ---' headers.
    Only lines matching '--- WORD(S) ---' pattern are section headers.
    Lines that are just '----...' separators within tables are NOT headers."""
    sections = {}
    current_name = ""
    current_lines = []

    for line in text.split('\n'):
        stripped = line.strip()
        # A section header has words between the dashes: "--- TOP CRYPTO (CoinGecko) ---"
        # A table separator is just dashes: "------..." or "--- " with no closing "---"
        # Section header: starts with "--- " and contains letters (not just dashes)
        is_header = (
            stripped.startswith('--- ')
            and any(c.isalpha() for c in stripped)
            and stripped.endswith('---')
        )
        if is_header:
            if current_name:
                sections[current_name] = '\n'.join(current_lines).strip()
            current_name = stripped.strip('-').strip().upper()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_name:
        sections[current_name] = '\n'.join(current_lines).strip()

    return sections


def filter_finance_context(query: str, full_text: str) -> str:
    """Filter finance data to what's relevant to the query."""
    query_lower = query.lower()
    sections = _split_sections(full_text)
    result_parts = []

    # Always include Fear & Greed (short, always relevant)
    for key, content in sections.items():
        if 'FEAR' in key and 'GREED' in key:
            # Keep just current + trend (first 3 content lines)
            lines = content.split('\n')
            result_parts.append('\n'.join(lines[:5]))
            break

    # Check if asking about crypto
    crypto_keywords = re.findall(
        r'\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge|ada|bnb|crypto|coin)\b',
        query_lower
    )
    if crypto_keywords or 'crypto' in query_lower:
        for key, content in sections.items():
            if 'CRYPTO' in key:
                lines = content.split('\n')
                result_parts.append('\n'.join(lines[:13]))  # header + top 10
                break

    # Check if asking about stocks
    stock_keywords = re.findall(
        r'\b(stock|stocks|market|nasdaq|dow|s&p|gainer|loser|active|trending|share)\b',
        query_lower
    )
    if stock_keywords:
        for key, content in sections.items():
            if any(k in key for k in ['GAINERS', 'LOSERS', 'ACTIVE', 'TRENDING']):
                lines = content.split('\n')
                result_parts.append('\n'.join(lines[:8]))

    # Include signals (always short)
    for key, content in sections.items():
        if 'SIGNAL' in key and content.strip():
            result_parts.append(content)
            break

    # If nothing specific matched, return compact overview
    if len(result_parts) <= 1:  # only Fear & Greed
        return _compact_finance_overview(full_text)

    return '\n\n'.join(result_parts)


def _compact_finance_overview(full_text: str) -> str:
    """Create a compact overview when query is general ('how are markets?')."""
    sections = _split_sections(full_text)
    lines = []

    # Top 5 crypto
    for key, content in sections.items():
        if 'CRYPTO' in key:
            crypto_lines = content.split('\n')
            lines.extend(crypto_lines[:8])
            lines.append('')
            break

    # Fear & Greed
    for key, content in sections.items():
        if 'FEAR' in key:
            fg_lines = content.split('\n')
            lines.extend(fg_lines[:4])
            lines.append('')
            break

    # Signals
    for key, content in sections.items():
        if 'SIGNAL' in key and content.strip():
            lines.append(content)
            break

    return '\n'.join(lines) if lines else full_text[:800]


def filter_sports_context(query: str, full_text: str) -> str:
    """Filter sports data to the relevant team(s)."""
    query_lower = query.lower()

    team_map = {
        'eagles': 'Eagles', 'phillies': 'Phillies', 'sixers': '76ers',
        '76ers': '76ers', 'flyers': 'Flyers', 'union': 'Union',
        'football': 'Eagles', 'baseball': 'Phillies', 'basketball': '76ers',
        'hockey': 'Flyers', 'soccer': 'Union',
    }

    # Find which teams are mentioned
    mentioned = set()
    for keyword, team in team_map.items():
        if keyword in query_lower:
            mentioned.add(team)

    # If no specific team mentioned, return everything (it's already short)
    if not mentioned:
        return full_text

    # Filter to only mentioned teams
    lines = []
    for line in full_text.split('\n'):
        # Always keep headers and empty lines
        if line.startswith('===') or line.startswith('TODAY') or line.startswith('SEASON') or not line.strip():
            lines.append(line)
            continue
        # Keep lines that mention any of the target teams
        if any(team.lower() in line.lower() for team in mentioned):
            lines.append(line)
        # Also keep game lines (LIVE:, FINAL:, @ lines) if they mention the team
        if any(team.lower() in line.lower() for team in mentioned):
            lines.append(line)

    return '\n'.join(lines)


def filter_news_context(query: str, full_text: str) -> str:
    """Filter news to top 5 most relevant items."""
    # News is already aggregated — just limit to top 5 items
    lines = full_text.split('\n')
    result = []
    item_count = 0
    for line in lines:
        if line.startswith('[') and ']' in line:
            item_count += 1
            if item_count > 5:
                break
        result.append(line)
    return '\n'.join(result)


def filter_context(domain: str, query: str, full_text: str) -> str:
    """Filter domain data based on the query. Main entry point."""
    if not full_text:
        return ""

    if domain == "finance":
        filtered = filter_finance_context(query, full_text)
    elif domain == "sports":
        filtered = filter_sports_context(query, full_text)
    elif domain == "news":
        filtered = filter_news_context(query, full_text)
    else:
        filtered = full_text

    original_len = len(full_text)
    filtered_len = len(filtered)
    if original_len > 0:
        reduction = int((1 - filtered_len / original_len) * 100)
        log.info("[CONTEXT] %s: %d→%d chars (%d%% reduction)", domain, original_len, filtered_len, reduction)

    return filtered
