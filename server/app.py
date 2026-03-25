"""Lumen AI Assistant — FastAPI Server.

Routes:
  POST /api/chat         — Process a user message (classify → route → respond)
  GET  /api/chat/stream  — SSE stream for voice UI
  GET  /api/health       — System health check
  GET  /api/predictions  — Get proactive predictions
  GET  /api/profile      — Get user behavioral profile
  POST /api/profile/forget — Delete a profile entry
  GET  /api/costs        — Claude API usage this month
  POST /api/tts          — Proxy to Kokoro TTS
  GET  /                 — Serve the voice UI
"""

import json
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import lumen_core
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server.config import load_config, LumenConfig
from server.database import init_db, get_recent_topics, get_recent_mood, get_minutes_since_last_chat
from server import database as db
from server.ollama_client import OllamaClient
from server.claude_client import ClaudeClient
from server.router import Router
from server.observe import observer
from server.cache import cache

# Globals initialized at startup
config: LumenConfig = None
router: Router = None
ollama: OllamaClient = None
claude: ClaudeClient = None

UI_DIR = Path(__file__).parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global config, router, ollama, claude

    # Configure logging for observability
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("lumen.router").setLevel(logging.INFO)

    print("  Lumen starting up...")
    config = load_config()

    # Init database
    await init_db()
    print("  Database ready")

    # Init clients
    ollama = OllamaClient(config.ollama.base_url)
    claude = ClaudeClient(config.claude)
    router = Router(config, ollama, claude)

    # Health check
    if await ollama.is_healthy():
        print("  Ollama connected")
    else:
        print("  WARNING: Ollama not reachable at", config.ollama.base_url)

    if await claude.is_available():
        print("  Claude API key configured")
    else:
        print("  Claude API key not set — local-only mode")

    # Warm up emotion models (TinyBERT + NRCLex)
    try:
        from server.emotions import warmup as warmup_emotions
        await warmup_emotions()
        print("  Emotion models ready")
    except Exception as e:
        print(f"  Emotion models unavailable: {e}")

    # Start background data cache
    cache.start()
    print("  Data cache started (finance/sports/news)")

    print(f"  Lumen ready on http://{config.server.host}:{config.server.port}")
    yield

    # Shutdown
    await cache.stop()
    await ollama.close()
    await claude.close()
    print("  Lumen shut down")


app = FastAPI(title="Lumen", version="0.1.0", lifespan=lifespan)

# Serve static files
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


# --- Routes ---


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the main voice UI."""
    html_path = UI_DIR / "lumen.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Lumen</h1><p>UI not found. Place lumen.html in ui/</p>")


@app.post("/api/chat")
async def chat(request: Request):
    """Process a user message through the full pipeline."""
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    result = await router.process(message)

    return {
        "acknowledgment": result.acknowledgment,
        "response": result.response,
        "model": result.model_used,
        "domain": result.domain,
        "reason": result.route_reason,
        "escalated": result.escalated,
        "latency_ms": result.latency_ms,
        "sentiment": {
            "compound": result.sentiment_compound,
            "mood": result.sentiment_mood,
        },
        "guardrail_passed": result.guardrail_passed,
        "disclaimer": result.needs_disclaimer,
        "trace_id": result.trace_id,
    }


@app.get("/api/chat/stream")
async def chat_stream(request: Request):
    """SSE endpoint for progressive streaming to the voice UI.

    Sends events in order:
      1. classify  — route, domain, reason (instant, <1ms)
      2. sentiment — mood data (instant, <1ms)
      3. ack       — quick acknowledgment from fast model (~1s)
      4. sentence  — each complete sentence as it's generated (progressive)
      5. done      — final metadata (model, latency, guardrail, trace_id)
    """
    message = request.query_params.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    async def event_stream():
        import time as _time
        start = _time.monotonic()
        trace = observer.new_trace(message)

        # Step 1: Classify (Rust, <1ms)
        classification = lumen_core.classify_request(message)
        trace.route = classification.route
        trace.domain = classification.domain
        trace.route_reason = classification.reason
        trace.escalate = classification.escalate
        trace.confidence = getattr(classification, 'confidence', 0.0)

        yield f"data: {json.dumps({'type': 'classify', 'route': classification.route, 'domain': classification.domain, 'reason': classification.reason})}\n\n"

        # Step 2: Sentiment (Rust, <1ms)
        sentiment = lumen_core.analyze_sentiment(message)
        trace.sentiment_compound = sentiment.compound
        trace.sentiment_mood = sentiment.mood
        trace.sentiment_pos = sentiment.positive
        trace.sentiment_neg = sentiment.negative
        trace.sentiment_neu = sentiment.neutral

        yield f"data: {json.dumps({'type': 'sentiment', 'mood': sentiment.mood, 'compound': round(sentiment.compound, 3)})}\n\n"

        # Log user message + mood
        await db.log_chat(
            role="user", content=message,
            sentiment_compound=sentiment.compound,
            sentiment_mood=sentiment.mood,
            domain=classification.domain,
        )
        await db.log_mood(
            compound=sentiment.compound, pos=sentiment.positive,
            neg=sentiment.negative, neu=sentiment.neutral,
            mood_label=sentiment.mood, context=classification.domain,
        )

        # Step 3: Resolve model
        from server.router import SYSTEM_PROMPTS, MODEL_PARAMS
        model_map = {
            "qwen:2b": config.ollama.model_fast,
            "qwen:4b": config.ollama.model_general,
            "qwen:9b": config.ollama.model_analysis,
        }
        model_name = model_map.get(classification.route, config.ollama.model_analysis)
        tier = classification.route
        system = SYSTEM_PROMPTS.get(classification.domain, SYSTEM_PROMPTS["general"])
        temp, top_p, max_tok, _ = MODEL_PARAMS.get(tier, (0.7, 0.8, 400, 30))
        # Thinking mode only for complex tasks on 4B+ models
        from server.router import THINKING_REASONS
        think = (classification.reason in THINKING_REASONS and tier in ("qwen:4b", "qwen:9b"))
        if think:
            temp, top_p = 0.6, 0.95
        trace.model_selected = f"{tier} → {model_name}"
        trace.system_prompt = system

        # Step 4: Fetch conversation context (summary + recent messages)
        from server.memory import get_context_messages, get_core_memory
        is_trivial = classification.reason == "greeting_or_trivial"
        if is_trivial:
            history = []
        else:
            history = await get_context_messages(
                ollama_client=ollama,
                model=config.ollama.model_fast,
            )

        # Inject core memory into system prompt
        core_mem = await get_core_memory()
        if core_mem:
            system = f"{system}\n\n{core_mem}"

        # Step 4b: Fetch domain data for context injection (filtered to query)
        domain_context = await router._fetch_domain_data(classification.domain)
        augmented_message = message
        if domain_context:
            from server.context import filter_context
            domain_context = filter_context(classification.domain, message, domain_context)
            augmented_message = (
                f"[LIVE DATA — use this to answer the question]\n{domain_context}\n\n"
                f"[USER QUESTION]\n{message}"
            )

        # Step 5: Speculative execution strategy
        # For ALL queries: try 0.8B first (it's always in memory, ~500ms).
        # If the query has injected domain data, 0.8B can often extract the answer.
        # If 0.8B's answer passes relevancy check, use it — skip the bigger model entirely.
        # If not, fall back to 4B/9B streaming.
        needs_upgrade = tier != "qwen:2b"
        draft_accepted = False

        try:
            # Always try 0.8B first as a speculative draft
            import re as _re
            draft = await ollama.instant_response(
                augmented_message, config.ollama.model_fast,
                system=system, history=history,
            )
            draft = draft.strip()
            trace.ack_text = draft
            trace.ack_model = config.ollama.model_fast
            trace.ack_duration_ms = (ollama.last_trace.total_duration_ms
                                     if ollama.last_trace else 0)

            if not needs_upgrade:
                # 0.8B was the intended model — send the draft as final
                draft_accepted = True
                sents = _re.split(r'(?<=[.!?])\s+', draft)
                for i, s in enumerate(sents):
                    if s.strip() and len(s.strip()) > 2:
                        yield f"data: {json.dumps({'type': 'sentence', 'text': s.strip(), 'index': i+1})}\n\n"

            elif domain_context and draft and len(draft) > 20:
                # Domain query with injected data — check if 0.8B draft is good enough
                try:
                    relevant = await ollama.check_relevancy(
                        message, draft, config.ollama.model_fast
                    )
                    if relevant:
                        # Draft is relevant — use it, skip the bigger model!
                        draft_accepted = True
                        trace.speculative_accepted = True
                        sents = _re.split(r'(?<=[.!?])\s+', draft)
                        for i, s in enumerate(sents):
                            if s.strip() and len(s.strip()) > 2:
                                yield f"data: {json.dumps({'type': 'sentence', 'text': s.strip(), 'index': i+1})}\n\n"
                except Exception:
                    pass  # relevancy check failed — fall through to bigger model

            if not draft_accepted and needs_upgrade:
                # Draft wasn't good enough — send it as a quick ack while bigger model works
                yield f"data: {json.dumps({'type': 'ack', 'text': draft})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'ack', 'text': 'Working on that now.'})}\n\n"

        # Step 6: If upgrade needed (and draft wasn't accepted), stream from the bigger model
        use_claude = classification.escalate and await claude.is_available()
        full_response = ""
        actual_model = tier
        sentence_buffer = ""
        sentences_sent = 0
        MAX_VOICE_SENTENCES = 3  # Research: users disengage after 3 spoken sentences

        if draft_accepted:
            # Speculative draft was good enough — use it as the final response
            full_response = trace.ack_text or ""
            actual_model = "qwen:2b"
            actual_model = "qwen:2b"

        elif use_claude and not draft_accepted:
            try:
                full_response = await claude.generate(
                    prompt=augmented_message, system=system, reason=classification.reason,
                )
                actual_model = "claude"
            except Exception:
                use_claude = False

        if needs_upgrade and not use_claude and not draft_accepted:
            # Stream from the bigger Ollama model
            actual_model = tier
            gen_start = _time.monotonic()
            token_count = 0

            async for token in ollama.stream_generate(
                prompt=augmented_message, model=model_name,
                system=system, temperature=temp, top_p=top_p,
                max_tokens=max_tok, think=think, history=history,
            ):
                full_response += token
                sentence_buffer += token
                token_count += 1

                for end_char in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                    if end_char in sentence_buffer:
                        parts = sentence_buffer.split(end_char, 1)
                        complete = parts[0] + end_char[0]
                        sentence_buffer = parts[1] if len(parts) > 1 else ""

                        if len(complete.strip()) > 5:
                            sentences_sent += 1
                            # Only send to voice if under cap
                            if sentences_sent <= MAX_VOICE_SENTENCES:
                                yield f"data: {json.dumps({'type': 'sentence', 'text': complete.strip(), 'index': sentences_sent})}\n\n"
                        break

            gen_ms = (_time.monotonic() - gen_start) * 1000
            trace.gen_model = model_name
            trace.gen_tokens = token_count
            trace.gen_duration_ms = gen_ms
            if gen_ms > 0:
                trace.gen_tokens_per_second = token_count / (gen_ms / 1000)

        # Send any remaining text in buffer (if under voice cap)
        if sentence_buffer.strip() and len(sentence_buffer.strip()) > 5:
            sentences_sent += 1
            if sentences_sent <= MAX_VOICE_SENTENCES:
                yield f"data: {json.dumps({'type': 'sentence', 'text': sentence_buffer.strip(), 'index': sentences_sent})}\n\n"

        # If Claude was used (non-streamed), send the whole response as sentences
        if use_claude and full_response:
            import re
            sents = re.split(r'(?<=[.!?])\s+', full_response)
            for i, s in enumerate(sents):
                if s.strip():
                    sentences_sent += 1
                    if sentences_sent <= MAX_VOICE_SENTENCES:
                        yield f"data: {json.dumps({'type': 'sentence', 'text': s.strip(), 'index': sentences_sent})}\n\n"

        # Step 6: Guardrails check
        guardrail = lumen_core.check_output(
            full_response, classification.domain,
            config.guardrails.min_words, config.guardrails.min_chars,
        )
        trace.guardrail_safe = guardrail.safe
        trace.guardrail_reason = guardrail.reason
        trace.guardrail_quality = guardrail.quality_score
        trace.response_text = full_response
        trace.model_actual = actual_model

        latency = int((_time.monotonic() - start) * 1000)
        trace.total_latency_ms = latency

        # Step 7: Quick relevancy check (0.8B, ~300ms, non-blocking)
        if full_response and needs_upgrade:
            try:
                relevant = await ollama.check_relevancy(
                    message, full_response, config.ollama.model_fast
                )
                trace.relevancy_checked = True
                trace.relevancy_passed = relevant
            except Exception:
                pass

        # Log assistant response
        await db.log_chat(
            role="assistant", content=full_response,
            model_used=actual_model, route_reason=classification.reason,
            domain=classification.domain, latency_ms=latency,
        )

        observer.save(trace)

        # Step 8: Check for proactive suggestions (non-blocking)
        from server.proactive import evaluate_after_response, record_user_activity
        record_user_activity()
        try:
            suggestion = await evaluate_after_response(classification.domain, message)
            if suggestion:
                yield f"data: {json.dumps({'type': 'suggestion', 'text': suggestion.text, 'reason': suggestion.reason, 'action': suggestion.action, 'category': suggestion.category})}\n\n"
        except Exception:
            pass

        # Final event with metadata
        yield f"data: {json.dumps({'type': 'done', 'model': actual_model, 'domain': classification.domain, 'latency_ms': latency, 'guardrail_safe': guardrail.safe, 'trace_id': trace.id, 'full_text': full_response, 'relevant': trace.relevancy_passed, 'speculative': getattr(trace, 'speculative_accepted', False)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    """System health check."""
    ollama_ok = await ollama.is_healthy()
    claude_ok = await claude.is_available()

    # Check Kokoro TTS
    kokoro_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://{config.tts.host}:{config.tts.port}/ping"
            )
            kokoro_ok = resp.status_code == 200
    except Exception:
        pass

    components = {
        "ollama": {"status": "ok" if ollama_ok else "down"},
        "claude": {"status": "ok" if claude_ok else "unconfigured"},
        "kokoro_tts": {"status": "ok" if kokoro_ok else "down"},
        "database": {"status": "ok"},
    }

    all_healthy = ollama_ok  # Ollama is required; others are optional

    return {
        "status": "healthy" if all_healthy else "degraded",
        "components": components,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/predictions")
async def predictions():
    """Get proactive predictions based on current context."""
    now = datetime.now(timezone.utc)
    recent_topics = await get_recent_topics(hours=24)
    recent_mood = await get_recent_mood(hours=4)
    mins_since = await get_minutes_since_last_chat()

    # Check if a Philly team plays today (from cache — instant)
    game_today = cache._game_live or (
        cache.sports.data is not None and cache.sports.data.game_today_exists
    )

    preds = lumen_core.predict_intent(
        hour=now.hour,
        day_of_week=now.weekday(),
        recent_topics=recent_topics,
        mood_compound=recent_mood,
        minutes_since_last=mins_since,
        game_today=game_today,
    )

    # Log predictions
    for p in preds:
        await db.log_prediction(p.prediction, p.confidence, p.basis, p.action)

    return {
        "predictions": [
            {
                "prediction": p.prediction,
                "confidence": p.confidence,
                "basis": p.basis,
                "action": p.action,
                "priority": p.priority,
            }
            for p in preds
        ],
        "context": {
            "recent_topics": recent_topics,
            "mood": recent_mood,
            "minutes_since_last": mins_since,
        },
    }


@app.get("/api/profile")
async def get_profile():
    """Get the user's behavioral profile."""
    profile = await db.get_profile()
    return {"profile": profile}


@app.post("/api/profile/forget")
async def forget_profile(request: Request):
    """Delete a profile entry. User says 'forget X'."""
    body = await request.json()
    category = body.get("category", "")
    key = body.get("key", "")
    if not category or not key:
        return JSONResponse(
            {"error": "category and key required"}, status_code=400
        )
    deleted = await db.delete_profile_entry(category, key)
    return {"deleted": deleted}


@app.get("/api/costs")
async def get_costs():
    """Get Claude API costs for the current month."""
    cost = await db.get_claude_monthly_cost()
    return {
        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "cost_usd": round(cost, 4),
        "budget_usd": config.claude.max_monthly_budget,
        "remaining_usd": round(config.claude.max_monthly_budget - cost, 4),
    }


@app.post("/api/tts")
async def tts_proxy(request: Request):
    """Proxy TTS requests to Kokoro server with Rust text preprocessing."""
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    # Preprocess text in Rust before sending to TTS
    prepped = lumen_core.prepare_for_tts(text)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"http://{config.tts.host}:{config.tts.port}/tts",
                json={"text": prepped.text},
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(
            {"error": f"TTS unavailable: {str(e)}"}, status_code=503
        )


@app.post("/api/tts/prepare")
async def tts_prepare(request: Request):
    """Preprocess text for TTS using the Rust engine (lumen_core.prepare_for_tts).

    Returns cleaned text, sentences, and estimated duration — useful for clients
    that need to do their own audio handling.
    """
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    result = lumen_core.prepare_for_tts(text)
    return {
        "text": result.text,
        "sentences": result.sentences,
        "estimated_duration_ms": result.estimated_duration_ms,
    }


# --- Observability Routes ---


@app.get("/api/observe/traces")
async def observe_traces(n: int = 50):
    """Get recent request traces for the observability dashboard."""
    return {"traces": observer.get_recent(n)}


@app.get("/api/observe/trace/{trace_id}")
async def observe_trace(trace_id: int):
    """Get a single trace by ID."""
    trace = observer.get_trace(trace_id)
    if trace is None:
        return JSONResponse({"error": "trace not found"}, status_code=404)
    return trace


@app.get("/api/observe/stats")
async def observe_stats():
    """Get aggregate pipeline stats."""
    return observer.get_stats()


@app.get("/observe", response_class=HTMLResponse)
async def observe_dashboard():
    """Serve the observability dashboard."""
    html_path = UI_DIR / "observe.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Observability dashboard not found</h1>")


# --- Cache Status ---


@app.get("/api/cache/status")
async def cache_status():
    """Get the current state of all data caches.
    Used by the UI to show refresh indicators."""
    return cache.get_status()


# --- Proactive Intelligence ---


@app.get("/api/proactive/status")
async def proactive_status():
    """Get proactive suggestion system status."""
    from server.proactive import get_proactive_status
    return get_proactive_status()


@app.post("/api/proactive/respond")
async def proactive_respond(request: Request):
    """Record user response to a proactive suggestion."""
    from server.proactive import record_suggestion_response
    body = await request.json()
    accepted = body.get("accepted", False)
    record_suggestion_response(accepted)
    return {"recorded": True, "accepted": accepted}


# --- Domain Agent Routes ---


@app.get("/api/finance/snapshot")
async def finance_snapshot():
    """Get current market data snapshot (from cache)."""
    snapshot = cache.finance.data
    if snapshot is None:
        from agents.finance.collector import collect_all
        snapshot = await collect_all()
    return {
        "timestamp": snapshot.timestamp,
        "text": cache.finance.text or "",
        "crypto_count": len(snapshot.crypto),
        "fear_greed": {
            "value": snapshot.fear_greed.value,
            "classification": snapshot.fear_greed.classification,
            "trend": snapshot.fear_greed.trend,
        } if snapshot.fear_greed else None,
        "source_stock": snapshot.source_stock,
        "cache_age_s": int(time.monotonic() - cache.finance.last_updated) if cache.finance.last_updated else -1,
    }


@app.get("/api/finance/brief")
async def finance_brief():
    """Generate a market brief narrative."""
    from agents.finance.storyboard import generate_quick_brief
    brief = await generate_quick_brief(ollama, config.ollama.model_analysis)
    return {"brief": brief}


@app.get("/api/finance/watchlist")
async def get_watchlist():
    """Get watchlist with current prices."""
    from agents.finance.watchlist import fetch_watchlist_quotes, watchlist_to_text
    quotes = await fetch_watchlist_quotes()
    return {
        "quotes": [{"symbol": q.symbol, "name": q.name, "price": q.price,
                     "change_pct": q.change_pct, "type": q.asset_type} for q in quotes],
        "text": watchlist_to_text(quotes),
    }


@app.post("/api/finance/watchlist")
async def add_watchlist(request: Request):
    """Add a symbol to the watchlist."""
    from agents.finance.watchlist import add_to_watchlist
    body = await request.json()
    symbol = body.get("symbol", "").upper()
    asset_type = body.get("type", "stock")
    name = body.get("name", "")
    if not symbol:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    await add_to_watchlist(None, symbol, asset_type, name)
    return {"added": symbol}


@app.delete("/api/finance/watchlist/{symbol}")
async def remove_watchlist(symbol: str):
    """Remove a symbol from the watchlist."""
    from agents.finance.watchlist import remove_from_watchlist
    removed = await remove_from_watchlist(symbol.upper())
    return {"removed": removed, "symbol": symbol.upper()}


@app.get("/api/sports/scores")
async def sports_scores():
    """Get live scores and records for Philly teams (from cache)."""
    snapshot = cache.sports.data
    if snapshot is None:
        from agents.sports.scores import get_philly_snapshot
        snapshot = await get_philly_snapshot()
    return {
        "text": cache.sports.text or "",
        "games_today": [
            {"home": g.home_team, "away": g.away_team,
             "home_score": g.home_score, "away_score": g.away_score,
             "status": g.status, "detail": g.detail}
            for g in snapshot.games_today
        ],
        "records": {
            k: {"wins": v.wins, "losses": v.losses, "ties": v.ties,
                "standing": v.standing}
            for k, v in snapshot.records.items()
        },
        "game_today": snapshot.game_today_exists,
        "game_live": cache._game_live,
        "cache_age_s": int(time.monotonic() - cache.sports.last_updated) if cache.sports.last_updated else -1,
    }


@app.get("/api/sports/schedule")
async def sports_schedule():
    """Get upcoming games for Philly teams."""
    from agents.sports.schedule import get_all_upcoming, schedule_to_text
    schedule = await get_all_upcoming(count_per_team=3)
    return {
        "text": schedule_to_text(schedule),
        "schedule": {
            k: [{"opponent": g.opponent, "date": g.date, "time": g.time,
                 "home_away": g.home_away, "venue": g.venue}
                for g in games]
            for k, games in schedule.items()
        },
    }


@app.get("/api/sports/recap/{team}")
async def sports_recap(team: str):
    """Get a game recap for a specific team."""
    from agents.sports.recap import get_game_recap
    recap = await get_game_recap(team, ollama, config.ollama.model_analysis)
    return {"team": team, "recap": recap}


@app.get("/api/news/feed")
async def news_feed():
    """Get aggregated news from all sources (from cache)."""
    items = cache.news.data
    if items is None:
        from agents.news.aggregator import get_all_news
        items = await get_all_news(hn_count=20)
    return {
        "text": cache.news.text or "",
        "items": [
            {"title": i.title, "url": i.url, "source": i.source,
             "score": i.score, "comments": i.comments}
            for i in items[:20]
        ],
        "cache_age_s": int(time.monotonic() - cache.news.last_updated) if cache.news.last_updated else -1,
    }


@app.get("/api/news/briefing")
async def news_briefing():
    """Generate a news briefing."""
    from agents.news.briefing import generate_briefing
    briefing = await generate_briefing(ollama, config.ollama.model_analysis)
    return {"briefing": briefing}


@app.get("/api/news/topic/{topic}")
async def news_topic(topic: str):
    """Get a summary on a specific topic."""
    from agents.news.summarizer import summarize_topic
    summary = await summarize_topic(topic, ollama, config.ollama.model_analysis)
    return {"topic": topic, "summary": summary}


@app.post("/api/search")
async def web_search(request: Request):
    """DuckDuckGo web search."""
    from server.search import search_text
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    results = await search_text(query)
    return {"query": query, "results": results}


def main():
    """Entry point for running the server."""
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "server.app:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
