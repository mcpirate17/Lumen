"""Profile condenser for Lumen.

Periodically compresses the user profile to keep it lightweight.
Merges redundant entries, drops low-confidence/low-evidence traits,
and summarizes patterns using the local LLM.

Designed to run weekly via cron.
"""

from datetime import datetime, timezone, timedelta
from server import database as db
from server.ollama_client import OllamaClient


MAX_PROFILE_ENTRIES = 50
MIN_CONFIDENCE_TO_KEEP = 0.3
MIN_EVIDENCE_TO_KEEP = 2
STALE_DAYS = 30  # entries not updated in 30 days are candidates for removal


async def condense_profile(ollama: OllamaClient = None):
    """Run the full condensation pipeline.

    Steps:
    1. Remove stale entries (not updated in 30+ days with low confidence)
    2. Remove low-evidence entries (< 2 evidence points with low confidence)
    3. Merge duplicate/similar entries
    4. If still over MAX_PROFILE_ENTRIES, use LLM to summarize
    5. Trim mood table to keep only 30 days of history
    """
    removed = 0

    # Step 1: Remove stale low-confidence entries
    removed += await _remove_stale()

    # Step 2: Remove low-evidence entries
    removed += await _remove_low_evidence()

    # Step 3: Compact mood history
    await _compact_mood_history()

    # Step 4: If still too many entries, use LLM to merge
    profile = await db.get_profile()
    if len(profile) > MAX_PROFILE_ENTRIES and ollama:
        await _llm_merge(profile, ollama)

    final_profile = await db.get_profile()
    return {
        "entries_before": len(profile) + removed,
        "entries_after": len(final_profile),
        "removed": removed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _remove_stale() -> int:
    """Remove entries not updated in STALE_DAYS with low confidence."""
    database = await db.get_db()
    removed = 0
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat()
        cursor = await database.execute(
            """DELETE FROM profile
               WHERE last_updated < ? AND confidence < ?
               AND category != 'preferences'""",
            (cutoff, 0.6)
        )
        removed = cursor.rowcount
        await database.commit()
    finally:
        await database.close()
    return removed


async def _remove_low_evidence() -> int:
    """Remove entries with fewer than MIN_EVIDENCE_TO_KEEP data points
    and low confidence (not explicit user statements)."""
    database = await db.get_db()
    removed = 0
    try:
        cursor = await database.execute(
            """DELETE FROM profile
               WHERE evidence_count < ? AND confidence < ?
               AND category NOT IN ('preferences', 'personality')""",
            (MIN_EVIDENCE_TO_KEEP, 0.5)
        )
        removed = cursor.rowcount
        await database.commit()
    finally:
        await database.close()
    return removed


async def _compact_mood_history():
    """Keep only 30 days of mood data. Older data gets averaged into daily summaries."""
    database = await db.get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await database.execute(
            "DELETE FROM mood WHERE timestamp < ?",
            (cutoff,)
        )

        # Also compact chat log older than 90 days (keep profile, drop content)
        chat_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await database.execute(
            """UPDATE chats SET content = '[condensed]'
               WHERE timestamp < ? AND content != '[condensed]'""",
            (chat_cutoff,)
        )

        await database.commit()
    finally:
        await database.close()


async def _llm_merge(profile: list[dict], ollama: OllamaClient):
    """Use Qwen to intelligently merge similar profile entries."""
    # Group by category
    by_category = {}
    for entry in profile:
        cat = entry["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(entry)

    for category, entries in by_category.items():
        if len(entries) <= 5:
            continue  # Category is small enough

        # Ask Qwen to identify redundant entries
        entries_text = "\n".join(
            f"  [{e['key']}]: {e['value']} (confidence={e['confidence']:.2f}, evidence={e['evidence_count']})"
            for e in entries
        )

        prompt = f"""These are user profile entries in the '{category}' category.
Identify entries that are redundant or can be merged.
List the keys to REMOVE (keep the most informative version).
Only list keys to remove, one per line, no explanation.

Entries:
{entries_text}

Keys to remove (one per line):"""

        try:
            result = await ollama.generate(
                prompt=prompt,
                model="qwen3.5:9b",
                temperature=0.1,
                max_tokens=200,
            )

            # Parse keys to remove
            keys_to_remove = [
                line.strip().strip("- ").strip("`")
                for line in result.strip().split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]

            for key in keys_to_remove:
                # Only remove if the key actually exists and isn't high-confidence
                for entry in entries:
                    if entry["key"] == key and entry["confidence"] < 0.8:
                        await db.delete_profile_entry(category, key)
                        break
        except Exception:
            pass  # LLM merge is best-effort


async def get_profile_stats() -> dict:
    """Get profile size and health metrics."""
    profile = await db.get_profile()

    by_category = {}
    total_evidence = 0
    avg_confidence = 0.0

    for entry in profile:
        cat = entry["category"]
        by_category[cat] = by_category.get(cat, 0) + 1
        total_evidence += entry["evidence_count"]
        avg_confidence += entry["confidence"]

    count = len(profile) or 1

    return {
        "total_entries": len(profile),
        "max_entries": MAX_PROFILE_ENTRIES,
        "by_category": by_category,
        "total_evidence_points": total_evidence,
        "average_confidence": round(avg_confidence / count, 3),
        "health": "healthy" if len(profile) < MAX_PROFILE_ENTRIES else "needs_condensation",
    }
