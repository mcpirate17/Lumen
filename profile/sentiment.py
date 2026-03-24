"""Sentiment analysis layer for Lumen.

Uses Rust lumen_core.analyze_sentiment() for per-message VADER analysis (<1ms).
Provides higher-level mood tracking, trend detection, and emotional context.
"""

import lumen_core
from datetime import datetime, timezone, timedelta
from server import database as db


class SentimentTracker:
    """Tracks sentiment per message and detects mood trends."""

    def __init__(self):
        self._recent_scores: list[float] = []
        self._window_size = 20  # rolling window for trend detection

    def analyze(self, text: str) -> lumen_core.SentimentResult:
        """Analyze a single message. Returns SentimentResult from Rust engine."""
        result = lumen_core.analyze_sentiment(text)
        self._recent_scores.append(result.compound)
        if len(self._recent_scores) > self._window_size:
            self._recent_scores.pop(0)
        return result

    async def analyze_and_log(self, text: str, context: str = None) -> lumen_core.SentimentResult:
        """Analyze and persist mood data point."""
        result = self.analyze(text)
        await db.log_mood(
            compound=result.compound,
            pos=result.positive,
            neg=result.negative,
            neu=result.neutral,
            mood_label=result.mood,
            context=context,
        )
        return result

    @property
    def trend(self) -> str:
        """Detect mood trend from recent scores.

        Returns: 'improving', 'declining', 'stable', 'volatile', or 'unknown'
        """
        if len(self._recent_scores) < 5:
            return "unknown"

        recent = self._recent_scores[-5:]
        older = self._recent_scores[:-5] if len(self._recent_scores) > 5 else recent

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        diff = recent_avg - older_avg

        # Check volatility (large swings)
        if len(recent) >= 3:
            swings = sum(abs(recent[i] - recent[i-1]) for i in range(1, len(recent)))
            avg_swing = swings / (len(recent) - 1)
            if avg_swing > 0.4:
                return "volatile"

        if diff > 0.15:
            return "improving"
        elif diff < -0.15:
            return "declining"
        return "stable"

    @property
    def current_mood(self) -> str:
        """Get the current overall mood based on recent messages."""
        if not self._recent_scores:
            return "neutral"
        avg = sum(self._recent_scores) / len(self._recent_scores)
        if avg >= 0.5:
            return "very_positive"
        elif avg >= 0.1:
            return "positive"
        elif avg <= -0.5:
            return "very_negative"
        elif avg <= -0.1:
            return "negative"
        return "neutral"

    @property
    def average_compound(self) -> float:
        """Rolling average compound score."""
        if not self._recent_scores:
            return 0.0
        return sum(self._recent_scores) / len(self._recent_scores)

    def should_adjust_tone(self) -> dict | None:
        """Check if the assistant should adjust its tone based on mood.

        Returns adjustment dict or None if no change needed.
        """
        mood = self.current_mood
        trend = self.trend

        if mood == "very_negative" or (mood == "negative" and trend == "declining"):
            return {
                "action": "be_direct",
                "reason": f"User mood is {mood}, trend {trend}",
                "style": "Keep responses short and direct. No small talk. Be helpful and efficient.",
            }

        if trend == "volatile":
            return {
                "action": "be_calm",
                "reason": f"User mood is volatile ({mood})",
                "style": "Use a calm, steady tone. Avoid exclamation marks. Be grounding.",
            }

        if mood == "very_positive":
            return {
                "action": "match_energy",
                "reason": f"User mood is {mood}",
                "style": "Match the user's positive energy. Be enthusiastic but not sycophantic.",
            }

        return None
