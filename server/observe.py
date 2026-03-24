"""Deep observability for Lumen. Captures full traces of every request through the pipeline."""

import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class PipelineStep:
    """A single step in the pipeline with timing."""
    name: str
    started_at: float = 0.0
    duration_ms: float = 0.0
    status: str = "pending"  # pending, ok, warn, fail
    detail: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class RequestTrace:
    """Full trace of a single request through the Lumen pipeline."""
    id: int = 0
    timestamp: str = ""
    user_message: str = ""

    # Classification
    route: str = ""
    domain: str = ""
    route_reason: str = ""
    escalate: bool = False
    confidence: float = 0.0

    # Sentiment
    sentiment_compound: float = 0.0
    sentiment_mood: str = ""
    sentiment_pos: float = 0.0
    sentiment_neg: float = 0.0
    sentiment_neu: float = 0.0

    # Model selection
    model_selected: str = ""
    model_actual: str = ""
    system_prompt: str = ""

    # Acknowledgment
    ack_text: str = ""
    ack_model: str = ""
    ack_duration_ms: float = 0.0

    # Generation
    response_text: str = ""
    response_thinking: str = ""
    gen_model: str = ""
    gen_tokens: int = 0
    gen_prompt_tokens: int = 0
    gen_duration_ms: float = 0.0
    gen_tokens_per_second: float = 0.0

    # Guardrails
    guardrail_safe: bool = True
    guardrail_reason: str = ""
    guardrail_quality: float = 0.0
    guardrail_needs_disclaimer: bool = False

    # Self-certainty check
    self_check_ran: bool = False
    self_check_passed: bool = True

    # Escalation
    was_escalated: bool = False
    escalation_reason: str = ""

    # Relevancy
    relevancy_checked: bool = False
    relevancy_passed: bool = True

    # Overall
    total_latency_ms: int = 0
    steps: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class SessionSentiment:
    """Track sentiment across a conversation session (rolling window)."""

    def __init__(self, window_size: int = 10):
        self._scores: deque[float] = deque(maxlen=window_size)
        self._window_size = window_size

    def add(self, compound: float):
        self._scores.append(compound)

    @property
    def average(self) -> float:
        if not self._scores:
            return 0.0
        return sum(self._scores) / len(self._scores)

    @property
    def trend(self) -> str:
        """Detect sentiment trend: improving, declining, stable, volatile."""
        if len(self._scores) < 3:
            return "insufficient_data"
        scores = list(self._scores)
        first_half = scores[:len(scores)//2]
        second_half = scores[len(scores)//2:]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        diff = avg_second - avg_first

        # Check volatility (large swings)
        max_swing = max(scores) - min(scores)
        if max_swing > 1.0:
            return "volatile"
        if diff > 0.2:
            return "improving"
        if diff < -0.2:
            return "declining"
        return "stable"

    @property
    def tone_adjustment(self) -> str:
        """Suggest tone adjustment based on session sentiment."""
        avg = self.average
        trend = self.trend
        if avg < -0.3 or trend == "declining":
            return "be_calm_and_direct"
        if avg > 0.5 and trend == "improving":
            return "match_energy"
        return "neutral"

    def to_dict(self) -> dict:
        return {
            "average": round(self.average, 3),
            "trend": self.trend,
            "tone_adjustment": self.tone_adjustment,
            "sample_count": len(self._scores),
            "recent_scores": [round(s, 3) for s in self._scores],
        }


class Observer:
    """Collects and stores pipeline traces for observability."""

    def __init__(self, max_traces: int = 200):
        self._traces: deque[RequestTrace] = deque(maxlen=max_traces)
        self._counter = 0
        self.session_sentiment = SessionSentiment(window_size=10)

    def new_trace(self, user_message: str) -> RequestTrace:
        self._counter += 1
        trace = RequestTrace(
            id=self._counter,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_message=user_message,
        )
        return trace

    def save(self, trace: RequestTrace):
        self._traces.appendleft(trace)
        # Track session-level sentiment
        if trace.sentiment_compound != 0.0 or trace.sentiment_mood != "":
            self.session_sentiment.add(trace.sentiment_compound)

    def get_recent(self, n: int = 50) -> list[dict]:
        return [t.to_dict() for t in list(self._traces)[:n]]

    def get_trace(self, trace_id: int) -> dict | None:
        for t in self._traces:
            if t.id == trace_id:
                return t.to_dict()
        return None

    def get_stats(self) -> dict:
        """Aggregate stats across recent traces."""
        if not self._traces:
            return {"total_requests": 0}

        traces = list(self._traces)
        latencies = [t.total_latency_ms for t in traces]
        models = {}
        domains = {}
        guardrail_fails = 0
        escalations = 0

        for t in traces:
            m = t.model_actual or t.model_selected
            models[m] = models.get(m, 0) + 1
            domains[t.domain] = domains.get(t.domain, 0) + 1
            if not t.guardrail_safe:
                guardrail_fails += 1
            if t.was_escalated:
                escalations += 1

        return {
            "total_requests": len(traces),
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "min_latency_ms": min(latencies) if latencies else 0,
            "max_latency_ms": max(latencies) if latencies else 0,
            "model_distribution": models,
            "domain_distribution": domains,
            "guardrail_fail_count": guardrail_fails,
            "escalation_count": escalations,
            "session_sentiment": self.session_sentiment.to_dict(),
        }


# Global observer instance
observer = Observer()
