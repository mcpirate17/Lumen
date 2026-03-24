"""Predictive analytics layer for Lumen.

Combines Rust-powered prediction engine (lumen_core.predict_intent) with
Python-side context gathering and feedback tracking.

The predictor answers: "What does the user probably want right now?"
"""

from datetime import datetime, timezone
import lumen_core
from server import database as db


class Predictor:
    """Gathers context and runs predictions."""

    def __init__(self):
        self._prediction_history: list[dict] = []
        self._feedback_scores: dict[str, list[bool]] = {}

    async def predict(self, game_today: bool = False) -> list[dict]:
        """Run the full prediction pipeline.

        Gathers context from DB, runs Rust predictor, logs results.
        Returns list of predictions sorted by priority.
        """
        now = datetime.now(timezone.utc)

        # Gather context
        recent_topics = await db.get_recent_topics(hours=24)
        recent_mood = await db.get_recent_mood(hours=4)
        mins_since = await db.get_minutes_since_last_chat()

        # Get profile-based context
        profile = await db.get_profile()
        profile_topics = {}
        for entry in profile:
            if entry["category"] == "interests" and entry["confidence"] >= 0.5:
                topic = entry["key"]
                # Boost prediction confidence for known interests
                profile_topics[topic] = entry["evidence_count"]

        # Merge profile interests into recent topics (weighted)
        for topic, evidence in profile_topics.items():
            if topic not in recent_topics:
                recent_topics[topic] = max(1, evidence // 3)

        # Run Rust predictor
        raw_predictions = lumen_core.predict_intent(
            hour=now.hour,
            day_of_week=now.weekday(),
            recent_topics=recent_topics,
            mood_compound=recent_mood,
            minutes_since_last=mins_since,
            game_today=game_today,
        )

        # Adjust confidence based on historical accuracy
        predictions = []
        for p in raw_predictions:
            adjusted_confidence = self._adjust_confidence(p.action, p.confidence)
            pred = {
                "prediction": p.prediction,
                "confidence": adjusted_confidence,
                "basis": p.basis,
                "action": p.action,
                "priority": p.priority,
            }
            predictions.append(pred)

            # Log prediction
            await db.log_prediction(
                prediction=p.prediction,
                confidence=adjusted_confidence,
                basis=p.basis,
                action=p.action,
            )

        self._prediction_history = predictions
        return predictions

    def _adjust_confidence(self, action: str, base_confidence: float) -> float:
        """Adjust prediction confidence based on historical accuracy.

        If we've been wrong about this action type before, reduce confidence.
        If we've been right, boost it.
        """
        if action not in self._feedback_scores:
            return base_confidence

        history = self._feedback_scores[action]
        if not history:
            return base_confidence

        accuracy = sum(history) / len(history)
        # Blend base confidence with historical accuracy
        # Weight history more as we get more data points
        weight = min(0.5, len(history) * 0.05)
        return base_confidence * (1 - weight) + accuracy * weight

    async def record_feedback(self, action: str, was_useful: bool):
        """Record whether a prediction was useful.

        Called when the user acts on (or ignores) a proactive suggestion.
        """
        if action not in self._feedback_scores:
            self._feedback_scores[action] = []

        self._feedback_scores[action].append(was_useful)

        # Keep last 20 feedback entries per action
        if len(self._feedback_scores[action]) > 20:
            self._feedback_scores[action] = self._feedback_scores[action][-20:]

    async def get_proactive_suggestions(self, game_today: bool = False,
                                         min_confidence: float = 0.6) -> list[dict]:
        """Get predictions that are confident enough to show proactively.

        Only returns predictions above the confidence threshold.
        These are candidates for unprompted assistant messages.
        """
        predictions = await self.predict(game_today=game_today)
        return [p for p in predictions if p["confidence"] >= min_confidence]

    async def should_offer_briefing(self) -> dict | None:
        """Check if we should proactively offer a briefing.

        Returns the briefing prediction if conditions are met, None otherwise.
        """
        predictions = await self.predict()

        for p in predictions:
            if p["action"] in ("generate_morning_brief", "generate_catchup_brief",
                               "run_finance_brief", "generate_news_digest"):
                if p["confidence"] >= 0.7:
                    return p

        return None

    def get_accuracy_report(self) -> dict:
        """Get accuracy metrics for each prediction type."""
        report = {}
        for action, history in self._feedback_scores.items():
            if history:
                report[action] = {
                    "total_predictions": len(history),
                    "accuracy": sum(history) / len(history),
                    "recent_accuracy": (
                        sum(history[-5:]) / len(history[-5:])
                        if len(history) >= 5 else None
                    ),
                }
        return report
