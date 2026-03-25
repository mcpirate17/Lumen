"""Conversation memory management for Lumen.

Replaces the raw 8-message sliding window with a smarter approach:
  [System Prompt] + [Core Memory] + [Condensed History] + [Last N Raw Messages] + [User Query]

Research (arXiv 2308.15022): Recursive summarization of older turns preserves context
while keeping prompt size manageable for small models. The entire memory is regenerated
holistically each cycle, not just appended/deleted.

Architecture:
  - Last 4 messages: kept verbatim (recent context)
  - Older messages: recursively summarized into a 2-3 sentence condensation
  - Core memory: user profile traits injected every turn (name, preferences, teams)
  - Topic tracking: detect topic shifts to know when to reset vs continue context
"""

import logging
from server import database as db

log = logging.getLogger("lumen.memory")

# How many recent messages to keep verbatim
RAW_TAIL_SIZE = 4

# How many total messages to fetch for summarization
FETCH_SIZE = 20

# Summarize when history exceeds this many messages
SUMMARIZE_THRESHOLD = 8

# Cache the current conversation summary
_current_summary: str = ""
_summary_message_count: int = 0


async def get_context_messages(ollama_client=None, model: str = "") -> list[dict]:
    """Build the optimal conversation context for the next LLM call.

    Returns a list of message dicts ready to inject into the Ollama messages array.
    Format: [summary_message (if any)] + [last N raw messages]
    """
    global _current_summary, _summary_message_count

    # Fetch recent history
    history = await db.get_recent_chat_history(limit=FETCH_SIZE)

    if not history:
        return []

    total = len(history)

    # If short conversation, just return raw messages (no summarization needed)
    if total <= RAW_TAIL_SIZE:
        return history

    # Split: older messages to summarize, recent to keep raw
    older = history[:-RAW_TAIL_SIZE]
    recent = history[-RAW_TAIL_SIZE:]

    # Check if we need to regenerate the summary
    if total > _summary_message_count + SUMMARIZE_THRESHOLD and ollama_client and model:
        _current_summary = await _summarize_history(older, ollama_client, model)
        _summary_message_count = total
        log.info("[MEMORY] Regenerated summary (%d older msgs → %d chars)",
                 len(older), len(_current_summary))

    # Build context: summary (as system-ish message) + raw recent
    context = []
    if _current_summary:
        context.append({
            "role": "system",
            "content": f"[Previous conversation summary: {_current_summary}]"
        })
    context.extend(recent)

    return context


async def get_core_memory() -> str:
    """Get persistent user facts to inject into every system prompt.

    Core memory = the things Lumen should always know about Tim,
    regardless of conversation topic. Pulled from the profile DB.
    """
    profile = await db.get_profile()
    if not profile:
        return ""

    # Build compact core memory string
    parts = []
    for entry in profile:
        if entry.get("confidence", 0) >= 0.6:
            cat = entry.get("category", "")
            key = entry.get("key", "")
            val = entry.get("value", "")
            if cat == "preferences" or cat == "personality":
                parts.append(f"{key}: {val}")

    if not parts:
        return ""

    return "User context: " + ". ".join(parts[:10])  # cap at 10 traits


async def _summarize_history(messages: list[dict], ollama_client, model: str) -> str:
    """Summarize older conversation messages into a concise paragraph.

    Uses the fast model (0.8B) for summarization — it's adequate for compression
    and keeps latency low.
    """
    # Format messages for the summarizer
    formatted = []
    for msg in messages[-12:]:  # cap at last 12 older messages
        role = msg.get("role", "user")
        content = msg.get("content", "")[:200]  # truncate long messages
        formatted.append(f"{role}: {content}")

    conversation_text = "\n".join(formatted)

    prompt = (
        "Summarize this conversation in 2-3 sentences. "
        "Capture the key topics discussed, any decisions made, and important context. "
        "Be concise.\n\n"
        f"{conversation_text}"
    )

    try:
        summary = await ollama_client.generate(
            prompt=prompt,
            model=model,
            system="You are a conversation summarizer. Output only the summary, nothing else.",
            temperature=0.3,
            max_tokens=100,
        )
        return summary.strip()
    except Exception as e:
        log.warning("[MEMORY] Summarization failed: %s", e)
        return ""


def reset_summary():
    """Reset the conversation summary (e.g., on topic change or new session)."""
    global _current_summary, _summary_message_count
    _current_summary = ""
    _summary_message_count = 0
    log.info("[MEMORY] Summary reset")
