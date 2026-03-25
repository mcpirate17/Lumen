"""Sentiment analysis layer for Lumen.

Uses Rust lumen_core.analyze_sentiment() for per-message VADER analysis (<1ms).
Now also integrates TinyBERT emotion classification and NRCLex word-level
emotion via server.emotions for richer mood tracking.
"""

import logging
import lumen_core
from datetime import datetime, timezone, timedelta
from server import database as db
from server.emotions import analyze_emotion, get_mood_prompt_hint

logger = logging.getLogger("lumen.sentiment")


class SentimentTracker:
    """Tracks sentiment per message and detects mood trends.

    Combines VADER (instant, Rust), TinyBERT (6-emotion, ~10-50ms),
    and NRCLex (word-level Plutchik emotions).
    """

    def __init__(self):
        self._recent_scores: list[float] = []
        self._recent_emotions: list[str] = []
        self._window_size = 20  # rolling window for trend detection

    def analyze(self, text: str) -> lumen_core.SentimentResult:
        """Analyze a single message. Returns SentimentResult from Rust engine."""
        result = lumen_core.analyze_sentiment(text)
        self._recent_scores.append(result.compound)
        if len(self._recent_scores) > self._window_size:
            self._recent_scores.pop(0)
        return result

    async def analyze_and_log(self, text: str, context: str = None) -> dict:
        """Analyze with all layers and persist mood data point.

        Returns the full emotion result dict from server.emotions.
        """
        # Run full emotion analysis (VADER + TinyBERT + NRCLex)
        emotion_result = await analyze_emotion(text)

        # Track VADER scores for trend detection
        self._recent_scores.append(emotion_result["vader_compound"])
        if len(self._recent_scores) > self._window_size:
            self._recent_scores.pop(0)

        # Track emotion labels for emotion trend
        self._recent_emotions.append(emotion_result["emotion"])
        if len(self._recent_emotions) > self._window_size:
            self._recent_emotions.pop(0)

        # Persist with the new emotion-aware log function
        await db.log_mood_with_emotion(
            compound=emotion_result["vader_compound"],
            pos=0.0,  # VADER sub-scores not in emotion result; use compound
            neg=0.0,
            neu=0.0,
            mood_label=emotion_result["vader_mood"],
            emotion_label=emotion_result["emotion"],
            emotion_confidence=emotion_result["confidence"],
            nrc_emotions=emotion_result["nrc_emotions"],
            context=context,
        )

        logger.debug(
            f"[MOOD] {emotion_result['emotion']} ({emotion_result['confidence']:.2f}) "
            f"| vader={emotion_result['vader_compound']:.2f} "
            f"| adj={emotion_result['mood_adjustment']}"
        )

        return emotion_result

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

    @property
    def dominant_emotion(self) -> str:
        """Get the most frequent recent emotion label."""
        if not self._recent_emotions:
            return "neutral"
        from collections import Counter
        counts = Counter(self._recent_emotions)
        return counts.most_common(1)[0][0]

    def should_adjust_tone(self) -> dict | None:
        """Check if the assistant should adjust its tone based on mood + emotion.

        Uses both VADER trend and TinyBERT emotion for richer adjustment.
        Returns adjustment dict or None if no change needed.
        """
        mood = self.current_mood
        trend = self.trend
        emotion = self.dominant_emotion

        if mood == "very_negative" or (mood == "negative" and trend == "declining"):
            return {
                "action": "be_direct",
                "reason": f"User mood is {mood}, trend {trend}, emotion {emotion}",
                "style": "Keep responses short and direct. No small talk. Be helpful and efficient.",
            }

        if emotion in ("anger", "fear") and trend in ("declining", "volatile"):
            return {
                "action": "be_calm",
                "reason": f"Detected {emotion} with {trend} trend",
                "style": "Use a calm, steady tone. Avoid exclamation marks. Be grounding.",
            }

        if trend == "volatile":
            return {
                "action": "be_calm",
                "reason": f"User mood is volatile ({mood})",
                "style": "Use a calm, steady tone. Avoid exclamation marks. Be grounding.",
            }

        if emotion == "sadness" and mood in ("negative", "very_negative"):
            return {
                "action": "be_gentle",
                "reason": f"Detected sadness with {mood} mood",
                "style": "Be gentle and available. Don't force positivity.",
            }

        if mood == "very_positive" or emotion in ("joy", "love"):
            return {
                "action": "match_energy",
                "reason": f"User mood is {mood}, emotion {emotion}",
                "style": "Match the user's positive energy. Be enthusiastic but not sycophantic.",
            }

        return None
