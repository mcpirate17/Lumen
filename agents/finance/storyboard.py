"""Finance storyboard generator. Ported from OpenClaw storyboard.sh.

Generates a structured market narrative from raw data using Ollama.
"""

import logging
from datetime import datetime, timezone

from agents.finance.collector import collect_all, snapshot_to_text

log = logging.getLogger("lumen.finance.storyboard")

_STORYBOARD_PROMPT = """You are a concise financial analyst writing a market brief for Tim.

STRICT RULES:
- Use ONLY numbers and facts from the data below. NEVER invent prices or percentages.
- If data is missing for something, say "data unavailable" — do NOT guess.
- Keep each section to 2-3 sentences max.
- Use plain language, no jargon.

Write the following sections:
1. MARKET PULSE (2-3 sentences on overall market direction)
2. CRYPTO HIGHLIGHTS (top 3 movers, with actual numbers)
3. STOCK MOVERS (notable gainers/losers from the data)
4. SENTIMENT (Fear & Greed reading and what it means)
5. WATCH LIST (3-5 things worth monitoring today)

DATA:
{data}
"""


async def generate_storyboard(ollama_client, model: str) -> str:
    """Collect market data and generate a narrative storyboard."""
    snapshot = await collect_all()
    data_text = snapshot_to_text(snapshot)

    prompt = _STORYBOARD_PROMPT.format(data=data_text)

    storyboard = await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="You are a financial analyst. Use only the provided data. Never speculate or invent numbers.",
        temperature=0.1,
        max_tokens=600,
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
