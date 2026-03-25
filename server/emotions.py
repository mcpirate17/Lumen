"""Unified emotion analysis interface for Lumen.

Combines three layers:
  1. VADER sentiment (via Rust lumen_core) — instant, <1ms
  2. NRCLex word-level emotion — instant, lexicon-based
  3. TinyBERT emotion classifier — ~10-50ms, 93.5% accuracy

Session A calls analyze_emotion() from the router. This is the single
entry point for all emotion/sentiment analysis.
"""

import logging
import time
from functools import lru_cache

import lumen_core

logger = logging.getLogger("lumen.emotions")

# Lazy-loaded heavy models (loaded on first call, cached forever)
_tinybert_pipeline = None
_nrclex_available = False

# TinyBERT emotion labels → mood adjustment mapping
_MOOD_ADJUSTMENTS = {
    "joy": "match_energy",
    "love": "warm_tone",
    "surprise": "engaged",
    "anger": "be_calm",
    "fear": "reassure",
    "sadness": "gentle",
}


def _load_tinybert():
    """Load TinyBERT emotion classifier on first use."""
    global _tinybert_pipeline
    if _tinybert_pipeline is not None:
        return _tinybert_pipeline

    try:
        from transformers import pipeline
        logger.info("Loading TinyBERT emotion classifier...")
        t0 = time.monotonic()
        _tinybert_pipeline = pipeline(
            "text-classification",
            model="AdamCodd/tinybert-emotion-balanced",
            top_k=None,  # return all scores
            device=-1,   # CPU
        )
        elapsed = (time.monotonic() - t0) * 1000
        logger.info(f"TinyBERT loaded in {elapsed:.0f}ms")
    except Exception as e:
        logger.warning(f"TinyBERT unavailable: {e}. Falling back to VADER-only.")
        _tinybert_pipeline = False  # sentinel: tried and failed
    return _tinybert_pipeline


def _get_nrclex_emotions(text: str) -> dict[str, float]:
    """Get NRCLex word-level emotion scores. Returns empty dict on failure."""
    try:
        from nrclex import NRCLex
        obj = NRCLex(text)
        freqs = obj.affect_frequencies
        # Return the 8 Plutchik emotions (skip positive/negative, VADER covers those)
        plutchik = (
            "anger", "anticipation", "disgust", "fear",
            "joy", "sadness", "surprise", "trust",
        )
        return {e: round(freqs.get(e, 0.0), 4) for e in plutchik}
    except Exception as e:
        logger.debug(f"NRCLex unavailable: {e}")
        return {}


def _classify_tinybert(text: str) -> tuple[str, float, dict[str, float]]:
    """Run TinyBERT emotion classification.

    Returns (top_emotion, confidence, all_scores) or fallback values.
    """
    pipe = _load_tinybert()
    if not pipe:
        return "neutral", 0.0, {}

    try:
        t0 = time.monotonic()
        results = pipe(text[:512])  # TinyBERT max length
        elapsed = (time.monotonic() - t0) * 1000

        if results and isinstance(results[0], list):
            scores = results[0]
        else:
            scores = results

        all_scores = {item["label"]: round(item["score"], 4) for item in scores}
        top = max(scores, key=lambda x: x["score"])
        top_emotion = top["label"]
        confidence = round(top["score"], 4)

        logger.debug(
            f"[EMOTION] {top_emotion} ({confidence:.2f}) | {elapsed:.1f}ms"
        )
        return top_emotion, confidence, all_scores
    except Exception as e:
        logger.warning(f"TinyBERT inference failed: {e}")
        return "neutral", 0.0, {}


async def analyze_emotion(text: str) -> dict:
    """Analyze emotion from text using all three layers.

    This is the public interface that Session A's router calls.

    Returns:
        {
            "emotion": str,          # top TinyBERT emotion (joy/sadness/anger/fear/love/surprise)
            "confidence": float,     # TinyBERT confidence 0-1
            "vader_compound": float, # VADER compound score -1 to 1
            "nrc_emotions": dict,    # {anger: 0.1, joy: 0.3, ...} word-level
            "style_metrics": dict,   # reserved for Phase 2 behavioral features
            "mood_adjustment": str,  # tone guidance for system prompt
        }
    """
    # Layer 1: VADER via Rust (instant, <1ms)
    vader = lumen_core.analyze_sentiment(text)

    # Layer 2: NRCLex word-level emotions (instant, lexicon lookup)
    nrc = _get_nrclex_emotions(text)

    # Layer 3: TinyBERT classifier (~10-50ms)
    emotion, confidence, all_scores = _classify_tinybert(text)

    # Determine mood adjustment
    # Use TinyBERT if confident, fall back to VADER mood
    if confidence >= 0.5:
        mood_adj = _MOOD_ADJUSTMENTS.get(emotion, "neutral")
    elif vader.compound <= -0.5:
        mood_adj = "be_calm"
    elif vader.compound >= 0.5:
        mood_adj = "match_energy"
    else:
        mood_adj = "neutral"

    return {
        "emotion": emotion,
        "confidence": confidence,
        "emotion_scores": all_scores,
        "vader_compound": vader.compound,
        "vader_mood": vader.mood,
        "nrc_emotions": nrc,
        "style_metrics": {},  # Populated by Phase 2 engine.py
        "mood_adjustment": mood_adj,
    }


def get_mood_prompt_hint(emotion_result: dict) -> str:
    """Convert emotion analysis into a system prompt hint.

    Used by the router to inject mood awareness into the LLM prompt.
    Lumen acts like a friend — it's okay to acknowledge mood naturally,
    just don't be clinical about it ("I detect anger at 0.87").
    """
    adj = emotion_result.get("mood_adjustment", "neutral")

    hints = {
        "match_energy": "The user is in a great mood. Match their energy — be enthusiastic, celebrate with them, have fun.",
        "warm_tone": "The user is being warm and open. Be a good friend — warm, genuine, engaged.",
        "engaged": "The user is curious and engaged. Go deeper, offer follow-ups, share interesting angles.",
        "be_calm": "The user seems frustrated or stressed. Be direct and helpful. It's okay to acknowledge it naturally ('rough one?') but don't dwell on it. Just be solid.",
        "reassure": "The user seems uneasy. Be steady, clear, and reassuring. A calm friend, not a therapist.",
        "gentle": "The user seems down. Be present and genuine. Don't force positivity. Gentle humor is okay if it fits. A friend, not a counselor.",
        "neutral": "",
    }
    return hints.get(adj, "")


async def warmup():
    """Pre-load models at server startup. Call from app.py lifespan."""
    logger.info("Warming up emotion models...")
    _load_tinybert()
    # Warm NRCLex import
    _get_nrclex_emotions("hello")
    logger.info("Emotion models ready.")
