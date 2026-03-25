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


async def log_mood_with_emotion(compound: float, pos: float, neg: float,
                                neu: float, mood_label: str,
                                emotion_label: str = None,
                                emotion_confidence: float = None,
                                nrc_emotions: dict = None,
                                context: str = None):
    """Log a mood data point with emotion detection results."""
    nrc = nrc_emotions or {}
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO mood (vader_compound, vader_pos, vader_neg, vader_neu,
               mood_label, emotion_label, emotion_confidence,
               nrc_joy, nrc_anger, nrc_fear, nrc_sadness,
               nrc_surprise, nrc_trust, nrc_anticipation, nrc_disgust,
               context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (compound, pos, neg, neu, mood_label, emotion_label,
             emotion_confidence,
             nrc.get("joy", 0.0), nrc.get("anger", 0.0),
             nrc.get("fear", 0.0), nrc.get("sadness", 0.0),
             nrc.get("surprise", 0.0), nrc.get("trust", 0.0),
             nrc.get("anticipation", 0.0), nrc.get("disgust", 0.0),
             context)
        )
        await db.commit()
    finally:
        await db.close()


async def get_recent_emotions(hours: int = 4) -> list[dict]:
    """Get recent emotion readings for trend detection."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT emotion_label, emotion_confidence, vader_compound,
                      mood_label, timestamp
               FROM mood
               WHERE timestamp >= datetime('now', ?)
               AND emotion_label IS NOT NULL
               ORDER BY timestamp DESC""",
            (f"-{hours} hours",)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        await db.close()


async def log_behavioral_metrics(chat_id: int = None, **metrics):
    """Log per-message behavioral style metrics."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO behavioral_metrics
               (chat_id, word_count, sentence_count, avg_sentence_length,
                question_marks, exclamation_marks,
                pronoun_ratio_i, pronoun_ratio_we, pronoun_ratio_you,
                formality_score, emoji_count, caps_ratio, engagement_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id,
             metrics.get("word_count", 0),
             metrics.get("sentence_count", 0),
             metrics.get("avg_sentence_length", 0.0),
             metrics.get("question_marks", 0),
             metrics.get("exclamation_marks", 0),
             metrics.get("pronoun_ratio_i", 0.0),
             metrics.get("pronoun_ratio_we", 0.0),
             metrics.get("pronoun_ratio_you", 0.0),
             metrics.get("formality_score", 0.5),
             metrics.get("emoji_count", 0),
             metrics.get("caps_ratio", 0.0),
             metrics.get("engagement_score", 0.5))
        )
        await db.commit()
    finally:
        await db.close()


async def update_behavioral_baseline(metric_name: str, new_value: float):
    """Update rolling baseline for a behavioral metric using Welford's algorithm."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT rolling_mean, rolling_std, sample_count FROM behavioral_baselines WHERE metric_name = ?",
            (metric_name,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            n = row["sample_count"] + 1
            old_mean = row["rolling_mean"]
            # Welford's online algorithm for mean and variance
            delta = new_value - old_mean
            new_mean = old_mean + delta / n
            delta2 = new_value - new_mean
            # Running M2 approximation via std
            old_var = row["rolling_std"] ** 2 * (n - 1) if n > 1 else 0
            new_var = (old_var + delta * delta2) / n if n > 0 else 0
            new_std = new_var ** 0.5

            await db.execute(
                """UPDATE behavioral_baselines
                   SET rolling_mean = ?, rolling_std = ?, sample_count = ?,
                       last_updated = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE metric_name = ?""",
                (new_mean, new_std, n, metric_name)
            )
        else:
            await db.execute(
                """INSERT INTO behavioral_baselines (metric_name, rolling_mean, rolling_std, sample_count)
                   VALUES (?, ?, 0.0, 1)""",
                (metric_name, new_value)
            )
        await db.commit()
    finally:
        await db.close()


async def get_behavioral_baselines() -> dict[str, dict]:
    """Get all behavioral baselines for drift detection."""
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM behavioral_baselines") as cur:
            rows = await cur.fetchall()
            return {
                r["metric_name"]: {
                    "mean": r["rolling_mean"],
                    "std": r["rolling_std"],
                    "n": r["sample_count"],
                }
                for r in rows
            }
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
