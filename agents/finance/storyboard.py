"""Finance storyboard generator. Ported from OpenClaw storyboard.sh.

Generates a structured market narrative from raw data using Ollama.
Now includes technical + fundamental analytics signals (RSI, MACD,
Bollinger Bands, P/E, PEG, etc.) for overbought/oversold/overvalued detection.
"""

import logging
from datetime import datetime, timezone

from agents.finance.collector import collect_all, snapshot_to_text
from agents.finance.analytics import (
    analyze_watchlist, analyze_top_movers, analysis_to_text, signals_summary,
)
from agents.finance.macro import get_macro_snapshot, macro_to_text

log = logging.getLogger("lumen.finance.storyboard")

_STORYBOARD_PROMPT = """You are a concise financial analyst writing a market brief for Tim.
You're also his friend — flag risks and opportunities directly, don't hedge everything.

STRICT RULES:
- Use ONLY numbers and facts from the data below. NEVER invent prices or percentages.
- If data is missing for something, say "data unavailable" — do NOT guess.
- Keep each section to 2-3 sentences max.
- Use plain language, no jargon. Be direct about what looks good and what doesn't.

Write the following sections:
1. MACRO BACKDROP (rates, yields, VIX, dollar — only if macro data present. What it means for investing today.)
2. MARKET PULSE (2-3 sentences on overall market direction)
3. CRYPTO HIGHLIGHTS (top 3 movers, with actual numbers)
4. STOCK MOVERS (notable gainers/losers from the data)
5. SENTIMENT (Fear & Greed + put/call ratio — what the crowd is doing)
6. TECHNICAL SIGNALS (overbought/oversold/trend signals from the analytics data — only include if analytics data is present)
7. VALUATIONS (overvalued/undervalued from fundamentals — only include if analytics data is present)
8. WATCH LIST (3-5 things worth monitoring, incorporating risk/opportunity signals and macro context)

DATA:
{data}
"""


async def generate_storyboard(ollama_client, model: str,
                               watchlist: list[dict] = None) -> str:
    """Collect market data, run analytics, and generate a narrative storyboard.

    Args:
        ollama_client: Ollama client for LLM generation
        model: Model to use for narrative
        watchlist: Optional watchlist items to analyze. If None, only
                   analyzes top movers from the market snapshot.
    """
    import asyncio

    # Collect market data, macro data, and run analytics concurrently
    snapshot_task = asyncio.create_task(collect_all())
    macro_task = asyncio.create_task(get_macro_snapshot())

    snapshot = await snapshot_task
    data_text = snapshot_to_text(snapshot)

    # Add macro data
    macro_text = ""
    try:
        macro = await macro_task
        macro_text = "\n" + macro_to_text(macro)
    except Exception as e:
        log.warning(f"Macro data failed: {e}")

    # Gather symbols to analyze
    analytics_text = ""
    try:
        tasks = []

        # Analyze watchlist symbols
        if watchlist:
            tasks.append(analyze_watchlist(watchlist))

        # Analyze top movers from snapshot (gainers + losers)
        mover_symbols = []
        for q in (snapshot.gainers[:5] + snapshot.losers[:5]):
            if q.symbol not in mover_symbols:
                mover_symbols.append(q.symbol)
        if mover_symbols:
            tasks.append(analyze_top_movers(mover_symbols, "stock"))

        # Analyze top crypto movers
        crypto_movers = [c.name.lower() for c in snapshot.crypto[:5]]
        if crypto_movers:
            tasks.append(analyze_top_movers(crypto_movers, "crypto"))

        if tasks:
            results_lists = await asyncio.gather(*tasks, return_exceptions=True)
            all_results = []
            for r in results_lists:
                if isinstance(r, list):
                    all_results.extend(r)
                elif isinstance(r, Exception):
                    log.warning(f"Analytics task failed: {r}")

            if all_results:
                analytics_text = "\n" + analysis_to_text(all_results)
                summary = signals_summary(all_results)
                if summary.get("overbought") or summary.get("oversold"):
                    log.info(
                        f"[ANALYTICS] Overbought: {[s['symbol'] for s in summary['overbought']]} "
                        f"Oversold: {[s['symbol'] for s in summary['oversold']]}"
                    )
    except Exception as e:
        log.warning(f"Analytics failed (storyboard continues without): {e}")

    # Combine market data + macro + analytics for LLM
    full_data = data_text + macro_text + analytics_text
    prompt = _STORYBOARD_PROMPT.format(data=full_data)

    storyboard = await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="You are a financial analyst and Tim's friend. Use only the provided data. Never speculate or invent numbers. Be direct about risks and opportunities.",
        temperature=0.1,
        max_tokens=800,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"# Market Brief — {timestamp}\n\n{storyboard}"


async def generate_quick_brief(ollama_client, model: str) -> str:
    """Generate a short 2-3 sentence market summary for voice responses."""
    snapshot = await collect_all()
    data_text = snapshot_to_text(snapshot)

    prompt = (
        "Give a 2-3 sentence market summary based on this data. "
        "Lead with the most important thing. Use actual numbers.\n\n"
        f"{data_text}"
    )

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="Concise financial summary. Only use provided data. No speculation.",
        temperature=0.1,
        max_tokens=150,
    )


async def generate_analytics_report(symbols: list[str],
                                     asset_type: str = "stock") -> str:
    """Generate a standalone analytics report for specific symbols.

    Useful when Tim asks "analyze NVDA" or "how's my watchlist looking?"
    Returns formatted text without LLM narrative — just raw signals.
    """
    if asset_type == "crypto":
        results = await analyze_top_movers(symbols, "crypto")
    else:
        results = await analyze_top_movers(symbols, "stock")

    if not results:
        return f"No analytics data available for {', '.join(symbols)}."

    text = analysis_to_text(results)
    summary = signals_summary(results)

    # Add a quick verdict section
    lines = [text]
    if summary["overbought"]:
        syms = ", ".join(s["symbol"] for s in summary["overbought"])
        lines.append(f"⚠ OVERBOUGHT (multiple indicators confirm): {syms}")
    if summary["oversold"]:
        syms = ", ".join(s["symbol"] for s in summary["oversold"])
        lines.append(f"👀 OVERSOLD (potential bounce candidates): {syms}")
    if summary["overvalued"]:
        syms = ", ".join(s["symbol"] for s in summary["overvalued"])
        lines.append(f"📈 OVERVALUED (high P/E + high PEG): {syms}")
    if summary["undervalued"]:
        syms = ", ".join(s["symbol"] for s in summary["undervalued"])
        lines.append(f"💰 UNDERVALUED (low PEG or below book): {syms}")

    return "\n".join(lines)
