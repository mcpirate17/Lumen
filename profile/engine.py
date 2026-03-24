"""Behavioral profiling engine for Lumen.

Extracts and maintains a lightweight user profile from everyday conversations.
Tracks: interests, communication style, personality traits, schedule patterns,
preferences, and frustration triggers.

All profiling is transparent — the user can ask "what do you know about me?"
and get a full dump, or say "forget X" to delete any entry.
"""

import re
import json
from datetime import datetime, timezone
from collections import Counter
from server import database as db
from server.ollama_client import OllamaClient


# Topic detection patterns
TOPIC_PATTERNS = {
    "finance": re.compile(
        r"(?i)\b(stock|market|crypto|bitcoin|portfolio|invest|dividend|"
        r"earnings|nasdaq|s&p|dow|bond|yield|treasury|forex|trading)\b"
    ),
    "sports": re.compile(
        r"(?i)\b(eagles|phillies|sixers|76ers|flyers|union|nfl|nba|mlb|nhl|"
        r"game|score|playoff|draft|season|touchdown|homerun)\b"
    ),
    "tech": re.compile(
        r"(?i)\b(ai|machine learning|openai|anthropic|google|apple|python|"
        r"rust|javascript|api|deploy|kubernetes|docker|cloud|gpu|model)\b"
    ),
    "code": re.compile(
        r"(?i)\b(code|function|bug|error|debug|compile|refactor|git|"
        r"commit|merge|test|deploy|database|sql|api)\b"
    ),
    "health": re.compile(
        r"(?i)\b(workout|exercise|run|gym|sleep|diet|calories|weight|"
        r"steps|meditation|stress|headache|tired)\b"
    ),
    "entertainment": re.compile(
        r"(?i)\b(movie|film|show|series|netflix|book|read|music|song|"
        r"album|concert|game|play|watch)\b"
    ),
}

# Communication style signals
FORMALITY_MARKERS = {
    "formal": re.compile(r"(?i)\b(please|kindly|could you|would you|I would like|thank you)\b"),
    "casual": re.compile(r"(?i)\b(hey|yo|gonna|wanna|gotta|lol|haha|nah|yeah|yep|nope|dude|bro)\b"),
    "terse": re.compile(r"^.{1,15}$"),  # very short messages
    "verbose": re.compile(r"^.{200,}$"),  # very long messages
}

# Explicit preference signals
LIKE_PATTERN = re.compile(r"(?i)\b(i (?:really )?(?:like|love|enjoy|prefer|want)|that'?s (?:great|perfect|exactly))\b")
DISLIKE_PATTERN = re.compile(r"(?i)\b(i (?:don'?t |really don'?t )?(?:like|want|need|care about)|(?:stop|quit|enough|no more))\b")


class ProfileEngine:
    """Extracts and maintains a behavioral profile from conversations."""

    def __init__(self, ollama: OllamaClient = None):
        self._ollama = ollama
        self._message_count = 0
        self._session_topics = Counter()
        self._session_style = Counter()
        self._hour_distribution = Counter()

    async def process_message(self, text: str, sentiment_mood: str = "neutral",
                              sentiment_compound: float = 0.0):
        """Process a user message to extract profile signals.

        Call this on every user message. It's lightweight and fast.
        """
        self._message_count += 1
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Track time-of-day patterns
        self._hour_distribution[hour] += 1
        if self._message_count % 20 == 0:
            await self._update_schedule_pattern()

        # Extract topic interests
        for topic, pattern in TOPIC_PATTERNS.items():
            if pattern.search(text):
                self._session_topics[topic] += 1
                await db.update_profile(
                    category="interests",
                    key=topic,
                    value=f"Discusses {topic} frequently",
                    confidence=min(0.9, 0.4 + self._session_topics[topic] * 0.05),
                )

        # Extract communication style
        word_count = len(text.split())
        if FORMALITY_MARKERS["formal"].search(text):
            self._session_style["formal"] += 1
        if FORMALITY_MARKERS["casual"].search(text):
            self._session_style["casual"] += 1
        if word_count <= 5:
            self._session_style["terse"] += 1
        elif word_count >= 40:
            self._session_style["verbose"] += 1

        if self._message_count % 10 == 0:
            await self._update_style_profile()

        # Extract explicit preferences
        await self._extract_preferences(text)

        # Track frustration triggers
        if sentiment_compound < -0.3:
            await self._track_frustration(text, sentiment_compound)

    async def _extract_preferences(self, text: str):
        """Extract explicit likes and dislikes from user statements."""
        if LIKE_PATTERN.search(text):
            # Try to extract what they like
            # Simple heuristic: grab the noun phrase after the like/love/enjoy verb
            match = re.search(
                r"(?i)(?:like|love|enjoy|prefer)\s+(.{3,40?)(?:\.|,|!|\?|$)",
                text,
            )
            if match:
                thing = match.group(1).strip().rstrip(".,!?")
                await db.update_profile(
                    category="preferences",
                    key=f"likes:{thing.lower()[:50]}",
                    value=f"Expressed liking for: {thing}",
                    confidence=0.8,
                )

        if DISLIKE_PATTERN.search(text):
            match = re.search(
                r"(?i)(?:don'?t (?:like|want|need|care about)|stop|no more)\s+(.{3,40?})(?:\.|,|!|\?|$)",
                text,
            )
            if match:
                thing = match.group(1).strip().rstrip(".,!?")
                await db.update_profile(
                    category="preferences",
                    key=f"dislikes:{thing.lower()[:50]}",
                    value=f"Expressed dislike for: {thing}",
                    confidence=0.8,
                )

    async def _track_frustration(self, text: str, compound: float):
        """Track what causes user frustration for future avoidance."""
        # Extract context of frustration
        words = text.lower().split()
        context_words = [w for w in words if len(w) > 3][:5]
        context = " ".join(context_words) if context_words else "unknown"

        await db.update_profile(
            category="mood",
            key=f"frustration_trigger:{context[:50]}",
            value=f"User frustrated (sentiment={compound:.2f}) in context: {context}",
            confidence=min(0.9, abs(compound)),
        )

    async def _update_style_profile(self):
        """Update the communication style profile based on accumulated signals."""
        total = sum(self._session_style.values()) or 1
        dominant = self._session_style.most_common(1)
        if dominant:
            style, count = dominant[0]
            if count / total > 0.4:
                await db.update_profile(
                    category="style",
                    key="communication_style",
                    value=f"Tends toward {style} communication",
                    confidence=min(0.85, count / total),
                )

    async def _update_schedule_pattern(self):
        """Detect when the user is most active."""
        if not self._hour_distribution:
            return
        peak_hour = self._hour_distribution.most_common(1)[0][0]
        total = sum(self._hour_distribution.values())
        peak_pct = self._hour_distribution[peak_hour] / total

        period = "morning" if 5 <= peak_hour < 12 else \
                 "afternoon" if 12 <= peak_hour < 17 else \
                 "evening" if 17 <= peak_hour < 22 else "night"

        await db.update_profile(
            category="schedule",
            key="peak_activity",
            value=f"Most active during {period} (peak hour: {peak_hour}:00 UTC)",
            confidence=min(0.8, peak_pct + 0.3),
        )

    async def get_profile_summary(self) -> dict:
        """Get a structured summary of the user profile.

        This is what gets returned when the user asks "what do you know about me?"
        """
        profile = await db.get_profile()

        summary = {}
        for entry in profile:
            cat = entry["category"]
            if cat not in summary:
                summary[cat] = []
            summary[cat].append({
                "trait": entry["key"],
                "description": entry["value"],
                "confidence": entry["confidence"],
                "evidence": entry["evidence_count"],
                "since": entry["first_seen"],
            })

        return summary

    async def get_context_for_prompt(self) -> str:
        """Generate a compact context string to inject into LLM system prompts.

        This lets the assistant adapt its behavior based on what it knows.
        """
        profile = await db.get_profile()
        if not profile:
            return ""

        lines = ["[User Profile Context]"]

        # High-confidence traits only (>0.5)
        for entry in profile:
            if entry["confidence"] >= 0.5:
                lines.append(f"- {entry['value']} (confidence: {entry['confidence']:.0%})")

        if len(lines) == 1:
            return ""

        return "\n".join(lines[:15])  # Cap at 15 lines to keep prompts lean

    async def analyze_personality(self, ollama: OllamaClient = None):
        """Run a Big Five personality analysis on recent chat history.

        Uses Qwen 9B to analyze conversation patterns. Run periodically (daily).
        """
        client = ollama or self._ollama
        if not client:
            return

        # Get recent chat messages
        recent_db = await db.get_db()
        try:
            async with recent_db.execute(
                """SELECT content FROM chats WHERE role = 'user'
                   ORDER BY id DESC LIMIT 50"""
            ) as cur:
                rows = await cur.fetchall()
        finally:
            await recent_db.close()

        if len(rows) < 10:
            return  # Not enough data

        messages = "\n".join(f"- {row[0]}" for row in reversed(rows))

        prompt = f"""Analyze these user messages and estimate their Big Five personality traits.
Rate each trait from 1-10 with a brief justification.

Messages:
{messages}

Respond in this exact format (no other text):
Openness: N - reason
Conscientiousness: N - reason
Extraversion: N - reason
Agreeableness: N - reason
Neuroticism: N - reason"""

        try:
            result = await client.generate(
                prompt=prompt,
                model="qwen3.5:9b",
                temperature=0.2,
                max_tokens=300,
            )

            # Parse the result
            for line in result.strip().split("\n"):
                line = line.strip()
                if ":" not in line:
                    continue
                trait, rest = line.split(":", 1)
                trait = trait.strip().lower()
                if trait in ("openness", "conscientiousness", "extraversion",
                             "agreeableness", "neuroticism"):
                    await db.update_profile(
                        category="personality",
                        key=trait,
                        value=rest.strip(),
                        confidence=0.6,
                    )
        except Exception:
            pass  # Personality analysis is best-effort

    async def forget(self, what: str) -> bool:
        """Handle user's request to forget something.

        Searches profile entries matching the query and deletes them.
        Returns True if anything was deleted.
        """
        profile = await db.get_profile()
        deleted = False

        what_lower = what.lower()
        for entry in profile:
            if (what_lower in entry["key"].lower() or
                    what_lower in entry["value"].lower()):
                await db.delete_profile_entry(entry["category"], entry["key"])
                deleted = True

        return deleted
