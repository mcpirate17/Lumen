"""Game recaps for Philadelphia teams via search + LLM summary."""

import logging

from server.search import search_text

log = logging.getLogger("lumen.sports.recap")


async def get_game_recap(team: str, ollama_client, model: str) -> str:
    """Search for and summarize a recent game recap."""
    query = f"{team} game recap score highlights today"
    search_results = await search_text(query, max_results=5)

    if search_results.startswith("No search results"):
        return f"No recent game recap found for {team}."

    prompt = (
        f"Based on these search results, give a concise game recap for the {team}. "
        "Include the final score, key plays, and standout players. 3-4 sentences max.\n\n"
        f"Search results:\n{search_results}"
    )

    return await ollama_client.generate(
        prompt=prompt,
        model=model,
        system="Concise sports recap. Use only information from the search results.",
        temperature=0.3,
        max_tokens=200,
    )
