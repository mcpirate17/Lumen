"""Async SQLite database layer for Lumen."""

import aiosqlite
import os
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent.parent / "data" / "lumen.db"
MIGRATIONS_DIR = Path(__file__).parent.parent / "data" / "migrations"


async def get_db() -> aiosqlite.Connection:
    """Get a database connection with WAL mode and foreign keys."""
    db = await aiosqlite.connect(str(DB_PATH))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    """Run all pending migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await get_db()
    try:
        # Check current schema version
        try:
            async with db.execute("SELECT MAX(version) FROM schema_version") as cur:
                row = await cur.fetchone()
                current = row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            current = 0

        # Run pending migrations in order
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for mf in migration_files:
            version = int(mf.stem.split("_")[0])
            if version > current:
                sql = mf.read_text()
                await db.executescript(sql)
                print(f"  Applied migration {mf.name}")

        await db.commit()
    finally:
        await db.close()


async def log_chat(role: str, content: str, model_used: str = None,
                   route_reason: str = None, sentiment_compound: float = None,
                   sentiment_mood: str = None, domain: str = None,
                   tokens_used: int = None, latency_ms: int = None):
    """Log a chat message with metadata."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO chats (role, content, model_used, route_reason,
               sentiment_compound, sentiment_mood, domain, tokens_used, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (role, content, model_used, route_reason, sentiment_compound,
             sentiment_mood, domain, tokens_used, latency_ms)
        )
        await db.commit()
    finally:
        await db.close()


async def log_mood(compound: float, pos: float, neg: float, neu: float,
                   mood_label: str, context: str = None):
    """Log a mood data point."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO mood (vader_compound, vader_pos, vader_neg, vader_neu,
               mood_label, context) VALUES (?, ?, ?, ?, ?, ?)""",
            (compound, pos, neg, neu, mood_label, context)
        )
        await db.commit()
    finally:
        await db.close()


async def log_claude_usage(prompt_tokens: int, completion_tokens: int,
                           cost_usd: float, reason: str = None):
    """Track Claude API usage for cost monitoring."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO claude_usage (prompt_tokens, completion_tokens, cost_usd, reason)
               VALUES (?, ?, ?, ?)""",
            (prompt_tokens, completion_tokens, cost_usd, reason)
        )
        await db.commit()
    finally:
        await db.close()


async def get_claude_monthly_cost() -> float:
    """Get total Claude API cost for the current month."""
    db = await get_db()
    try:
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0).isoformat()
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM claude_usage WHERE timestamp >= ?",
            (month_start,)
        ) as cur:
            row = await cur.fetchone()
            return row[0]
    finally:
        await db.close()


async def get_recent_topics(hours: int = 24) -> dict[str, int]:
    """Get topic frequency from recent chats for prediction engine."""
    db = await get_db()
    try:
        cutoff = datetime.now(timezone.utc).isoformat()
        async with db.execute(
            """SELECT domain, COUNT(*) as cnt FROM chats
               WHERE role = 'user' AND domain IS NOT NULL
               AND timestamp >= datetime('now', ?)
               GROUP BY domain""",
            (f"-{hours} hours",)
        ) as cur:
            rows = await cur.fetchall()
            return {row["domain"]: row["cnt"] for row in rows}
    finally:
        await db.close()


async def get_recent_mood(hours: int = 4) -> float:
    """Get average mood compound score from recent interactions."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT AVG(vader_compound) FROM mood
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{hours} hours",)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row[0] is not None else 0.0
    finally:
        await db.close()


async def get_minutes_since_last_chat() -> int:
    """Get minutes since the user's last message."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT timestamp FROM chats WHERE role = 'user'
               ORDER BY id DESC LIMIT 1"""
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return 9999
            last = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - last
            return int(delta.total_seconds() / 60)
    finally:
        await db.close()


async def update_profile(category: str, key: str, value: str,
                         confidence: float = 0.5):
    """Upsert a profile trait. Increments evidence count on update."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO profile (category, key, value, confidence)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(category, key) DO UPDATE SET
                 value = excluded.value,
                 confidence = MIN(1.0, confidence + 0.05),
                 evidence_count = evidence_count + 1,
                 last_updated = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (category, key, value, confidence)
        )
        await db.commit()
    finally:
        await db.close()


async def get_profile() -> list[dict]:
    """Get the full user profile."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM profile ORDER BY category, confidence DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_profile_entry(category: str, key: str) -> bool:
    """Delete a profile entry (user says 'forget X')."""
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM profile WHERE category = ? AND key = ?",
            (category, key)
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def get_recent_chat_history(limit: int = 10) -> list[dict]:
    """Get recent chat messages for conversation context."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT role, content FROM chats
               ORDER BY id DESC LIMIT ?""",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
            # Reverse so oldest is first (chronological order)
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        await db.close()


async def log_prediction(prediction: str, confidence: float,
                         basis: str, action: str):
    """Log a prediction for feedback tracking."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO predictions (prediction, confidence, basis, action)
               VALUES (?, ?, ?, ?)""",
            (prediction, confidence, basis, action)
        )
        await db.commit()
    finally:
        await db.close()
