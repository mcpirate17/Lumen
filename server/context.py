"""Context filtering for domain data injection.

Instead of dumping full market snapshots (2000+ tokens) into small model prompts,
filter to only what's relevant to the specific query. Small models perform dramatically
better with 2 high-quality chunks than 8 marginally relevant ones.

Research: Optimal context for small models is 500-1000 tokens of highly relevant data.
"""

import re
import logging

log = logging.getLogger("lumen.context")


def filter_finance_context(query: str, full_text: str) -> str:
    """Filter finance data to what's relevant to the query."""
    query_lower = query.lower()
    sections = []

    # Always include Fear & Greed (short, always relevant)
    fg_match = re.search(r'--- FEAR & GREED.*?(?=---|$)', full_text, re.DOTALL)
    if fg_match:
        sections.append(fg_match.group().strip())

    # Check if asking about specific crypto
    crypto_keywords = re.findall(
        r'\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge|ada|bnb|crypto|coin)\b',
        query_lower
    )
    if crypto_keywords or 'crypto' in query_lower or 'coin' in query_lower:
        # Include crypto section but only top 10
        crypto_match = re.search(r'--- TOP CRYPTO.*?(?=---|$)', full_text, re.DOTALL)
        if crypto_match:
            lines = crypto_match.group().strip().split('\n')
            # Header + separator + top 10 coins
            sections.append('\n'.join(lines[:13]))

    # Check if asking about stocks
    stock_keywords = re.findall(
        r'\b(stock|stocks|market|nasdaq|dow|s&p|gainer|loser|active|trending|share)\b',
        query_lower
    )
    if stock_keywords:
        for label in ['TOP GAINERS', 'TOP LOSERS', 'MOST ACTIVE', 'TRENDING']:
            match = re.search(rf'--- {label}.*?(?=---|$)', full_text, re.DOTALL)
            if match:
                lines = match.group().strip().split('\n')
                sections.append('\n'.join(lines[:8]))  # header + top 5

    # Include signals section (always short and useful)
    sig_match = re.search(r'--- SIGNALS.*?(?=---|$)', full_text, re.DOTALL)
    if sig_match and sig_match.group().strip():
        sections.append(sig_match.group().strip())

    # If no specific filter matched, give a compact overview
    if not sections or (not crypto_keywords and not stock_keywords):
        return _compact_finance_overview(full_text)

    return '\n\n'.join(sections)


def _compact_finance_overview(full_text: str) -> str:
    """Create a compact overview when query is general ('how are markets?')."""
    lines = []

    # Top 5 crypto only
    crypto_match = re.search(r'--- TOP CRYPTO.*?(?=---|$)', full_text, re.DOTALL)
    if crypto_match:
        crypto_lines = crypto_match.group().strip().split('\n')
        lines.extend(crypto_lines[:8])  # header + 5 coins
        lines.append('')

    # Fear & Greed
    fg_match = re.search(r'--- FEAR & GREED.*?(?=---|$)', full_text, re.DOTALL)
    if fg_match:
        fg_lines = fg_match.group().strip().split('\n')
        lines.extend(fg_lines[:4])  # current + trend only
        lines.append('')

    # Signals only
    sig_match = re.search(r'--- SIGNALS.*?(?=---|$)', full_text, re.DOTALL)
    if sig_match:
        lines.append(sig_match.group().strip())

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
