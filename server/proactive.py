"""Proactive intelligence for Lumen (Inner Thoughts framework).

Based on CHI 2025 "Proactive Conversational Agents with Inner Thoughts" (Liu et al.)
which was preferred by users 82% of the time over reactive-only approaches.

Triggers:
  - on_new_message: After processing a response, evaluate proactive suggestions
  - on_pause: After extended silence, check for time-sensitive info
  - on_schedule: Time-based triggers (morning brief, game alerts)

Evaluation heuristics (1-5 each, threshold to speak):
  1. Relevance — relates to current/recent conversation
  2. Information Gap — user likely doesn't know this yet
  3. Urgency — time-sensitive (game starting, market move)
  4. Expected Impact — how useful would this be
  5. Balance — not too many suggestions in a row

Delivery rules:
  - Always explain WHY you're suggesting
  - Always offer dismiss ("want updates?" not "I'll send updates")
  - Max 1 proactive suggestion per 10-minute window
  - Track accept/dismiss to calibrate future suggestions
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("lumen.proactive")


@dataclass(slots=True)
class Suggestion:
    text: str
    reason: str           # why this is being suggested
    category: str         # "game_alert", "market_move", "morning_brief", "interest_callback"
    urgency: int          # 1-5
    relevance: int        # 1-5
    action: str = ""      # optional action prompt ("Want score updates?")
    score: float = 0.0    # computed total score


@dataclass
class ProactiveState:
    last_suggestion_time: float = 0
    suggestions_today: int = 0
    accepted_count: int = 0
    dismissed_count: int = 0
    last_user_message_time: float = 0
    current_suggestions: list[Suggestion] = field(default_factory=list)


# Minimum seconds between proactive suggestions
MIN_SUGGESTION_GAP = 600  # 10 minutes

# Minimum score to deliver a suggestion
MIN_SCORE_THRESHOLD = 3.0

# Singleton state
_state = ProactiveState()


def record_user_activity():
    """Call when user sends a message — resets silence timer."""
    _state.last_user_message_time = time.monotonic()


def record_suggestion_response(accepted: bool):
    """Track whether user accepted or dismissed a suggestion."""
    if accepted:
        _state.accepted_count += 1
    else:
        _state.dismissed_count += 1


async def evaluate_after_response(domain: str, user_message: str) -> Suggestion | None:
    """Evaluate proactive suggestions after processing a user message.

    Called by the router after generating a response. Returns a suggestion
    if one passes the scoring threshold, or None.
    """
    now = time.monotonic()

    # Rate limit: no suggestion if we just gave one
    if now - _state.last_suggestion_time < MIN_SUGGESTION_GAP:
        return None

    suggestions = []

    # Check for game-related suggestions
    if domain != "sports":
        game_suggestion = await _check_game_alerts()
        if game_suggestion:
            suggestions.append(game_suggestion)

    # Check for notable market moves
    if domain != "finance":
        market_suggestion = await _check_market_alerts()
        if market_suggestion:
            suggestions.append(market_suggestion)

    # Check for interest callbacks (things user asked about before)
    callback = await _check_interest_callbacks(user_message)
    if callback:
        suggestions.append(callback)

    if not suggestions:
        return None

    # Score and pick the best
    for s in suggestions:
        s.score = (s.urgency * 0.4 + s.relevance * 0.3 +
                   (5 - min(_state.suggestions_today, 5)) * 0.3)

    best = max(suggestions, key=lambda s: s.score)

    if best.score < MIN_SCORE_THRESHOLD:
        return None

    _state.last_suggestion_time = now
    _state.suggestions_today += 1
    _state.current_suggestions.append(best)

    return best


async def evaluate_on_pause() -> Suggestion | None:
    """Evaluate suggestions after extended silence (10+ seconds of no user activity).

    Called by a background timer. Returns a suggestion if appropriate.
    """
    now = time.monotonic()
    silence_duration = now - _state.last_user_message_time

    # Only trigger after 30 seconds of silence, and not too often
    if silence_duration < 30:
        return None
    if now - _state.last_suggestion_time < MIN_SUGGESTION_GAP:
        return None

    suggestions = []

    # Time-based: morning brief
    hour = datetime.now(timezone.utc).hour - 5  # rough EST offset
    if 6 <= hour <= 9:
        suggestions.append(Suggestion(
            text="Good morning. Want your daily brief?",
            reason="It's morning and you haven't asked for a brief yet",
            category="morning_brief",
            urgency=2,
            relevance=4,
            action="Say 'brief' for market + sports + news summary",
        ))

    # Check game alerts
    game = await _check_game_alerts()
    if game:
        suggestions.append(game)

    if not suggestions:
        return None

    best = max(suggestions, key=lambda s: s.urgency * 0.5 + s.relevance * 0.5)
    if best.urgency + best.relevance < 5:
        return None

    _state.last_suggestion_time = now
    _state.suggestions_today += 1
    return best


async def _check_game_alerts() -> Suggestion | None:
    """Check if a Philly team game is about to start or is live."""
    from server.cache import cache
    if not cache.sports.data:
        return None

    snapshot = cache.sports.data
    for game in snapshot.games_today:
        if not game.is_philly_game:
            continue

        if game.status == "pre":
            return Suggestion(
                text=f"{game.away_team} {'@' if 'Philadelphia' in game.home_team else 'vs'} {game.home_team} — {game.detail}",
                reason=f"{game.philly_team.title()} game coming up",
                category="game_alert",
                urgency=3,
                relevance=4,
                action="Want score updates when it starts?",
            )
        elif game.status == "in":
            return Suggestion(
                text=f"Live: {game.away_team} {game.away_score} - {game.home_team} {game.home_score} ({game.detail})",
                reason=f"{game.philly_team.title()} game is live right now",
                category="game_alert",
                urgency=4,
                relevance=5,
                action="Want me to keep you posted?",
            )

    return None


async def _check_market_alerts() -> Suggestion | None:
    """Check for notable market moves from cached data."""
    from server.cache import cache
    if not cache.finance.data:
        return None

    snapshot = cache.finance.data

    # Check Fear & Greed for extreme readings
    if snapshot.fear_greed and snapshot.fear_greed.value <= 10:
        return Suggestion(
            text=f"Market Fear & Greed just hit {snapshot.fear_greed.value} — {snapshot.fear_greed.classification}.",
            reason="Extreme fear reading, historically significant",
            category="market_move",
            urgency=3,
            relevance=3,
            action="Want a market overview?",
        )

    # Check for big crypto moves
    signals = snapshot.signals
    if signals.get("oversold_crypto") and len(signals["oversold_crypto"]) >= 5:
        return Suggestion(
            text=f"{len(signals['oversold_crypto'])} coins in the top 50 are oversold (down >10% this week).",
            reason="Unusual number of oversold signals",
            category="market_move",
            urgency=2,
            relevance=3,
            action="Want details?",
        )

    return None


async def _check_interest_callbacks(user_message: str) -> Suggestion | None:
    """Check if there's something to follow up on from recent conversations.

    This is the "didn't you say you wanted to check that out?" pattern.
    Pulls recent topics from DB and matches against current cached data
    to find natural follow-up opportunities.
    """
    from server.database import get_recent_topics, get_db

    try:
        # Get topics from the last 24 hours
        recent_topics = await get_recent_topics(hours=24)

        # If user was talking about finance recently but isn't now, and market moved
        if recent_topics.get("finance", 0) >= 3:
            from server.cache import cache
            if cache.finance.data and cache.finance.data.fear_greed:
                fg = cache.finance.data.fear_greed
                if fg.value <= 15 and fg.trend == "DETERIORATING":
                    return Suggestion(
                        text=f"You've been following markets — Fear & Greed dropped to {fg.value}. That's historically low.",
                        reason="User has been asking about finance + extreme fear reading",
                        category="interest_callback",
                        urgency=3,
                        relevance=4,
                        action="Want a deeper look at what's moving?",
                    )

        # If user asked about sports recently, and a game result came in
        if recent_topics.get("sports", 0) >= 2:
            from server.cache import cache
            if cache.sports.data:
                for game in cache.sports.data.games_today:
                    if game.is_philly_game and game.status == "post":
                        winner = game.home_team if game.home_score > game.away_score else game.away_team
                        return Suggestion(
                            text=f"Final: {game.away_team} {game.away_score} - {game.home_team} {game.home_score}.",
                            reason=f"User was following {game.philly_team} + game just ended",
                            category="interest_callback",
                            urgency=3,
                            relevance=5,
                            action="Want the recap?",
                        )

        # Check for stated interests in profile
        db = await get_db()
        try:
            async with db.execute(
                """SELECT key, value FROM profile
                   WHERE category = 'interests' AND confidence >= 0.6
                   ORDER BY last_updated DESC LIMIT 5"""
            ) as cur:
                interests = await cur.fetchall()
        finally:
            await db.close()

        # Match interests against current news
        if interests:
            from server.cache import cache
            if cache.news.data:
                for interest in interests:
                    topic = interest["key"].lower()
                    for item in cache.news.data[:10]:
                        if topic in item.title.lower():
                            return Suggestion(
                                text=f"Saw something about {interest['key']}: {item.title[:60]}",
                                reason=f"Matches user interest: {interest['key']}",
                                category="interest_callback",
                                urgency=2,
                                relevance=4,
                                action="Want a summary?",
                            )

    except Exception as e:
        log.warning("[PROACTIVE] Interest callback check failed: %s", e)

    return None


def get_proactive_status() -> dict:
    """Get current proactive system status."""
    return {
        "suggestions_today": _state.suggestions_today,
        "accepted": _state.accepted_count,
        "dismissed": _state.dismissed_count,
        "seconds_since_last_suggestion": int(time.monotonic() - _state.last_suggestion_time)
            if _state.last_suggestion_time > 0 else -1,
        "acceptance_rate": (
            _state.accepted_count / max(_state.accepted_count + _state.dismissed_count, 1)
        ),
    }
