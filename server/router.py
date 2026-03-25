"""Request router for Lumen. Classifies, routes, guardrails, and responds.
Emits deep observability traces for every request.
Domain agents inject live data (finance, sports, news) into LLM prompts."""

import time
import asyncio
import logging
import lumen_core
from server.ollama_client import OllamaClient
from server.claude_client import ClaudeClient
from server.config import LumenConfig
from server import database as db
from server.observe import observer, RequestTrace
from dataclasses import dataclass

log = logging.getLogger("lumen.router")

# Voice-optimized constraint appended to all prompts.
# Research: users disengage after 8-10 seconds / 3 sentences of spoken output (CHI 2022).
_VOICE_CONSTRAINT = (
    " IMPORTANT: Keep your response to 2-3 sentences maximum. "
    "This is a voice assistant — short, clear responses only. "
    "Never repeat the question back. Never use markdown, bullet points, or lists. "
    "If you don't know, say so briefly and suggest a next step."
)

# Domain-specific system prompts
SYSTEM_PROMPTS = {
    "finance": (
        "You are Lumen, Tim's personal finance assistant. "
        "Use ONLY the data provided. NEVER invent prices or numbers. "
        "If data is missing, say 'I don't have current data on that.'"
        + _VOICE_CONSTRAINT
    ),
    "sports": (
        "You are Lumen, Tim's assistant. He's a Philadelphia sports fan. "
        "Teams: Eagles, Phillies, Sixers, Flyers, Union. "
        "Be enthusiastic but factual. If you don't have live scores, say so."
        + _VOICE_CONSTRAINT
    ),
    "news": (
        "You are Lumen, Tim's tech and AI news assistant. "
        "Summarize concisely. Lead with the key point. "
        "Don't speculate."
        + _VOICE_CONSTRAINT
    ),
    "general": (
        "You are Lumen, Tim's personal AI assistant. "
        "Be helpful, direct, and conversational — like a knowledgeable friend. "
        "If you don't know something, say so and suggest a next step."
        + _VOICE_CONSTRAINT
    ),
    "code": (
        "You are Lumen, an expert software engineering assistant. "
        "Explain concisely."
        + _VOICE_CONSTRAINT
    ),
}

# Model config: (temperature, top_p, max_tokens, timeout_seconds)
# Qwen3.5 official recommendations:
#   Non-thinking: temp=0.7, top_p=0.8, top_k=20
#   Thinking: temp=0.6, top_p=0.95, top_k=20
#   Never use greedy decoding (temp=0) with thinking mode — causes loops
#
# Thinking mode is NOT set per-tier — it's set per-query based on complexity.
# Simple extraction ("bitcoin price?") = no thinking.
# Analysis ("compare BTC vs ETH trends") = thinking on 4B/9B.
MODEL_PARAMS = {
    "qwen:2b":  (0.7, 0.8,  200,  10),
    "qwen:4b":  (0.7, 0.8,  400,  30),
    "qwen:9b":  (0.7, 0.8,  600,  45),
    "claude":   (0.7, 1.0,  1024, 120),
}

# Reasons that benefit from thinking mode (4B+ only)
THINKING_REASONS = {
    "complex_reasoning", "opinion_request", "financial_advice",
    "code_task", "deep_explanation",
}


@dataclass
class RouterResponse:
    acknowledgment: str
    response: str
    model_used: str
    route_reason: str
    domain: str
    escalated: bool
    latency_ms: int
    sentiment_compound: float
    sentiment_mood: str
    guardrail_passed: bool
    needs_disclaimer: bool
    trace_id: int = 0


class Router:
    def __init__(self, config: LumenConfig, ollama: OllamaClient, claude: ClaudeClient):
        self.config = config
        self.ollama = ollama
        self.claude = claude
        self._guardrails_overridden = False

    def set_guardrail_override(self, active: bool):
        """Toggle app-layer guardrail override. Qwen3Guard (model-level) stays active."""
        self._guardrails_overridden = active
        log.info("[GUARDRAIL] Override %s", "ACTIVE" if active else "INACTIVE")

    async def process(self, user_message: str) -> RouterResponse:
        """Full pipeline: classify → ack → route → guardrail → respond."""
        start = time.monotonic()
        trace = observer.new_trace(user_message)

        # Step 1: Emotion analysis (VADER + TinyBERT + NRCLex via server/emotions.py)
        t0 = time.monotonic()
        try:
            from server.emotions import analyze_emotion, get_mood_prompt_hint
            emotion_result = await analyze_emotion(user_message)
            mood_hint = get_mood_prompt_hint(emotion_result)
        except Exception as e:
            log.warning("[EMOTION] Analysis failed: %s — using VADER only", e)
            emotion_result = None
            mood_hint = ""

        # VADER is always available via Rust
        sentiment = lumen_core.analyze_sentiment(user_message)
        sentiment_ms = (time.monotonic() - t0) * 1000

        trace.sentiment_compound = sentiment.compound
        trace.sentiment_mood = sentiment.mood
        trace.sentiment_pos = sentiment.positive
        trace.sentiment_neg = sentiment.negative
        trace.sentiment_neu = sentiment.neutral

        if emotion_result:
            log.info(
                "[EMOTION] %s (%.2f) | vader=%.3f %s | mood_adj=%s (%.1fms)",
                emotion_result["emotion"], emotion_result["confidence"],
                sentiment.compound, sentiment.mood,
                emotion_result["mood_adjustment"], sentiment_ms,
            )
        else:
            log.info(
                "[SENTIMENT] compound=%.3f mood=%s (%.1fms)",
                sentiment.compound, sentiment.mood, sentiment_ms,
            )

        # Step 2: Classify (Rust, <1ms)
        t0 = time.monotonic()
        classification = lumen_core.classify_request(user_message)
        classify_ms = (time.monotonic() - t0) * 1000

        trace.route = classification.route
        trace.domain = classification.domain
        trace.route_reason = classification.reason
        trace.escalate = classification.escalate
        trace.confidence = getattr(classification, 'confidence', 0.0)

        log.info(
            "[CLASSIFY] route=%s domain=%s reason=%s escalate=%s confidence=%.2f (%.1fms)",
            classification.route, classification.domain,
            classification.reason, classification.escalate,
            trace.confidence, classify_ms,
        )

        # Step 3: Log user message
        await db.log_chat(
            role="user",
            content=user_message,
            sentiment_compound=sentiment.compound,
            sentiment_mood=sentiment.mood,
            domain=classification.domain,
        )

        # Step 4: Log mood
        await db.log_mood(
            compound=sentiment.compound,
            pos=sentiment.positive,
            neg=sentiment.negative,
            neu=sentiment.neutral,
            mood_label=sentiment.mood,
            context=classification.domain,
        )

        # Step 5: Resolve model name to actual Ollama model
        model_name = self._resolve_model(classification.route)
        trace.model_selected = f"{classification.route} → {model_name}"

        # Fetch conversation context (summary + recent messages)
        from server.memory import get_context_messages, get_core_memory
        is_trivial = classification.reason == "greeting_or_trivial"
        if is_trivial:
            history = []
        else:
            history = await get_context_messages(
                ollama_client=self.ollama,
                model=self.config.ollama.model_fast,
            )

        # Inject core memory (persistent user facts) into system prompt
        core_mem = await get_core_memory()

        # Step 5b: Fetch domain-specific live data (non-blocking, with timeout)
        domain_context = await self._fetch_domain_data(classification.domain)
        if domain_context:
            # Filter to only what's relevant to the query (saves tokens for small models)
            from server.context import filter_context
            domain_context = filter_context(classification.domain, user_message, domain_context)
            log.info("[DOMAIN] injected %d chars of %s data", len(domain_context), classification.domain)

        log.info("[MODEL] selected %s (Ollama model: %s) with %d history msgs", classification.route, model_name, len(history))

        # Step 6: Generate acknowledgment (fast model)
        ack = ""
        if classification.route != "qwen:2b":
            t0 = time.monotonic()
            try:
                ack = await self.ollama.acknowledge(
                    user_message, self.config.ollama.model_fast
                )
                ack = ack.strip().strip('"').strip()
                ack_ms = (time.monotonic() - t0) * 1000
                trace.ack_text = ack
                trace.ack_model = self.config.ollama.model_fast
                trace.ack_duration_ms = ack_ms
                log.info("[ACK] '%s' via %s (%.0fms)", ack, self.config.ollama.model_fast, ack_ms)
            except Exception as e:
                ack = "Working on that now."
                trace.ack_text = ack
                trace.errors.append(f"ack_error: {e}")
                log.warning("[ACK] failed: %s — using fallback", e)

        # Step 7: Generate response
        system = SYSTEM_PROMPTS.get(classification.domain, SYSTEM_PROMPTS["general"])
        if core_mem:
            system = f"{system}\n\n{core_mem}"
        if mood_hint:
            system = f"{system}\n\n[Mood context: {mood_hint}]"
        trace.system_prompt = system
        response = ""
        actual_model = classification.route
        escalated = classification.escalate

        # Augment prompt with domain data if available
        augmented_message = user_message
        if domain_context:
            augmented_message = (
                f"[LIVE DATA — use this to answer the question]\n{domain_context}\n\n"
                f"[USER QUESTION]\n{user_message}"
            )

        t0 = time.monotonic()
        if classification.escalate and await self.claude.is_available():
            # Claude path
            log.info("[GENERATE] escalating to Claude (reason: %s)", classification.reason)
            try:
                response = await self.claude.generate(
                    prompt=augmented_message,
                    system=system,
                    reason=classification.reason,
                )
                actual_model = "claude"
                gen_ms = (time.monotonic() - t0) * 1000
                trace.gen_model = "claude"
                trace.gen_duration_ms = gen_ms
                log.info("[GENERATE] Claude responded (%d chars, %.0fms)", len(response), gen_ms)
            except Exception as e:
                log.warning("[GENERATE] Claude failed: %s — falling back to local", e)
                trace.errors.append(f"claude_error: {e}")
                response = await self._local_generate(
                    augmented_message, self.config.ollama.model_analysis,
                    system, "qwen:9b", trace, history,
                    reason=classification.reason,
                )
                actual_model = "qwen:9b"
                escalated = False
        else:
            # Local model path
            response = await self._local_generate(
                augmented_message, model_name, system, classification.route, trace, history,
                reason=classification.reason,
            )
            actual_model = classification.route

        trace.response_text = response
        trace.model_actual = actual_model

        # Step 8: Guardrails check (Rust, <1ms)
        # When override is active, skip app-layer quality gates.
        # Qwen3Guard at model level is NOT affected by override.
        t0 = time.monotonic()
        guardrail = lumen_core.check_output(
            response, classification.domain,
            self.config.guardrails.min_words,
            self.config.guardrails.min_chars,
        )
        guard_ms = (time.monotonic() - t0) * 1000

        if self._guardrails_overridden:
            guardrail_safe = True  # bypass app-layer check
            log.info("[GUARDRAIL] OVERRIDDEN — skipping app-layer checks (%.1fms)", guard_ms)
        else:
            guardrail_safe = guardrail.safe

        trace.guardrail_safe = guardrail_safe
        trace.guardrail_reason = guardrail.reason
        trace.guardrail_quality = guardrail.quality_score
        trace.guardrail_needs_disclaimer = guardrail.needs_disclaimer
        needs_disclaimer = guardrail.needs_disclaimer

        log.info(
            "[GUARDRAIL] safe=%s reason=%s quality=%.2f disclaimer=%s (%.1fms)%s",
            guardrail_safe, guardrail.reason, guardrail.quality_score,
            guardrail.needs_disclaimer, guard_ms,
            " [OVERRIDE]" if self._guardrails_overridden else "",
        )

        if not guardrail_safe and actual_model != "claude":
            log.warning(
                "[GUARDRAIL] FAILED — response blocked (reason: %s). Self-repairing.",
                guardrail.reason,
            )
            trace.escalation_reason = f"guardrail:{guardrail.reason}"

            # Self-repair: try again with a bigger model, prepend repair phrase
            repair_prefix = "Actually, let me double-check that. "

            if await self.claude.is_available():
                try:
                    repaired = await self.claude.generate(
                        prompt=augmented_message,
                        system=system,
                        reason=f"guardrail_escalation:{guardrail.reason}",
                    )
                    response = repair_prefix + repaired
                    actual_model = "claude"
                    escalated = True
                    trace.was_escalated = True
                    trace.model_actual = "claude"
                    trace.response_text = response
                    guardrail = lumen_core.check_output(
                        repaired, classification.domain,
                        self.config.guardrails.min_words,
                        self.config.guardrails.min_chars,
                    )
                    trace.guardrail_safe = guardrail.safe
                    log.info("[SELF-REPAIR] re-generated via Claude: safe=%s", guardrail.safe)
                except Exception as e:
                    response = "I'm having trouble with that one. Can you rephrase?"
                    trace.errors.append(f"escalation_error: {e}")
            else:
                # No Claude — try the next tier up locally
                try:
                    repaired = await self._local_generate(
                        augmented_message, self.config.ollama.model_analysis,
                        system, "qwen:9b", trace, history,
                        reason=classification.reason,
                    )
                    if repaired and len(repaired.strip()) > 10:
                        response = repair_prefix + repaired
                        actual_model = "qwen:9b"
                        log.info("[SELF-REPAIR] re-generated via 9B")
                    else:
                        response = "I'm not confident in my answer on that one. Could you try rephrasing?"
                except Exception:
                    response = "I'm not confident in my answer on that one. Could you try rephrasing?"
                    log.warning("[SELF-REPAIR] local fallback also failed")

        # Step 9: Self-certainty check for factual claims (skipped when override active)
        if (
            self.config.guardrails.self_certainty_check
            and not self._guardrails_overridden
            and actual_model in ("qwen:4b", "qwen:9b")
            and classification.domain in ("finance", "sports", "news")
        ):
            trace.self_check_ran = True
            t0 = time.monotonic()
            try:
                is_certain = await self.ollama.self_check(
                    user_message, response, model_name
                )
                check_ms = (time.monotonic() - t0) * 1000
                trace.self_check_passed = is_certain
                log.info("[SELF-CHECK] certain=%s (%.0fms)", is_certain, check_ms)

                if not is_certain:
                    # Self-repair: model isn't confident, try to get a better answer
                    if await self.claude.is_available():
                        try:
                            repaired = await self.claude.generate(
                                prompt=augmented_message,
                                system=system,
                                reason="self_certainty_fail",
                            )
                            response = "Hmm, let me re-check. " + repaired
                            actual_model = "claude"
                            escalated = True
                            trace.was_escalated = True
                            trace.model_actual = "claude"
                            trace.response_text = response
                            trace.escalation_reason = "self_certainty_fail"
                        except Exception:
                            pass  # keep original response if Claude fails
                    else:
                        # No Claude — just flag uncertainty in the response
                        response = "I'm not 100% sure on this, but — " + response
            except Exception as e:
                log.warning("[SELF-CHECK] failed: %s (non-fatal)", e)
                trace.errors.append(f"self_check_error: {e}")

        # Step 10: Append disclaimer if needed
        if needs_disclaimer:
            response += "\n\n*This is not financial advice. Always do your own research.*"

        latency = int((time.monotonic() - start) * 1000)
        trace.total_latency_ms = latency

        # Step 11: Log assistant response
        await db.log_chat(
            role="assistant",
            content=response,
            model_used=actual_model,
            route_reason=classification.reason,
            domain=classification.domain,
            latency_ms=latency,
        )

        log.info(
            "[DONE] model=%s domain=%s latency=%dms guardrail=%s response_len=%d",
            actual_model, classification.domain, latency,
            "PASS" if trace.guardrail_safe else "FAIL", len(response),
        )

        # Save trace for dashboard
        observer.save(trace)

        return RouterResponse(
            acknowledgment=ack,
            response=response,
            model_used=actual_model,
            route_reason=classification.reason,
            domain=classification.domain,
            escalated=escalated,
            latency_ms=latency,
            sentiment_compound=sentiment.compound,
            sentiment_mood=sentiment.mood,
            guardrail_passed=guardrail.safe,
            needs_disclaimer=needs_disclaimer,
            trace_id=trace.id,
        )

    async def _local_generate(
        self, prompt: str, model: str, system: str, tier: str,
        trace: RequestTrace | None = None,
        history: list[dict] | None = None,
        reason: str = "",
    ) -> str:
        """Generate via Ollama with tier-appropriate params."""
        temp, top_p, max_tok, _ = MODEL_PARAMS.get(tier, (0.7, 0.8, 400, 30))
        # Thinking mode only for complex tasks on 4B+ models
        think = (reason in THINKING_REASONS and tier in ("qwen:4b", "qwen:9b"))
        if think:
            temp, top_p = 0.6, 0.95  # Qwen3.5 thinking-mode sampling
        log.info(
            "[GENERATE] local model=%s tier=%s temp=%.1f top_p=%.2f max_tokens=%d think=%s reason=%s history=%d",
            model, tier, temp, top_p, max_tok, think, reason, len(history) if history else 0,
        )

        result = await self.ollama.generate(
            prompt=prompt,
            model=model,
            system=system,
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tok,
            think=think,
            history=history,
        )

        # Capture Ollama trace details
        if trace and self.ollama.last_trace:
            ot = self.ollama.last_trace
            trace.gen_model = ot.model
            trace.gen_tokens = ot.eval_count
            trace.gen_prompt_tokens = ot.prompt_eval_count
            trace.gen_duration_ms = ot.total_duration_ms
            trace.gen_tokens_per_second = ot.tokens_per_second
            trace.response_thinking = ot.thinking

            log.info(
                "[GENERATE] done — %d tokens, %.1f tok/s, %dms total, %dms eval, %dms prompt_eval",
                ot.eval_count, ot.tokens_per_second, ot.total_duration_ms,
                ot.eval_duration_ms, ot.prompt_eval_duration_ms,
            )
            if ot.thinking:
                log.debug("[GENERATE] thinking: %s", ot.thinking[:200])

        return result

    async def _fetch_domain_data(self, domain: str) -> str:
        """Get cached domain data to inject into the LLM prompt.
        Reads from the background cache (instant) instead of fetching per-request.
        Returns empty string if cache has no data yet."""
        from server.cache import cache

        try:
            if domain == "finance" and cache.finance.text:
                log.info("[DOMAIN] Using cached finance data (age: %ds)",
                         int(time.monotonic() - cache.finance.last_updated))
                return cache.finance.text

            elif domain == "sports" and cache.sports.text:
                log.info("[DOMAIN] Using cached sports data (age: %ds)",
                         int(time.monotonic() - cache.sports.last_updated))
                return cache.sports.text

            elif domain == "news" and cache.news.text:
                log.info("[DOMAIN] Using cached news data (age: %ds)",
                         int(time.monotonic() - cache.news.last_updated))
                return cache.news.text

        except Exception as e:
            log.warning("[DOMAIN] Cache read failed for %s: %s", domain, e)

        # Cache miss — data not loaded yet (first few seconds after startup)
        # Fall back to a quick fetch
        log.info("[DOMAIN] Cache miss for %s — fetching directly", domain)
        try:
            if domain == "finance":
                from agents.finance.collector import collect_all, snapshot_to_text
                snapshot = await asyncio.wait_for(collect_all(), timeout=10.0)
                return snapshot_to_text(snapshot)
            elif domain == "sports":
                from agents.sports.scores import get_philly_snapshot, snapshot_to_text
                snapshot = await asyncio.wait_for(get_philly_snapshot(), timeout=8.0)
                return snapshot_to_text(snapshot)
            elif domain == "news":
                from agents.news.aggregator import get_all_news, news_to_text
                items = await asyncio.wait_for(get_all_news(hn_count=10), timeout=8.0)
                return news_to_text(items, max_items=10)
        except Exception as e:
            log.warning("[DOMAIN] Fallback fetch failed for %s: %s", domain, e)

        return ""

    def _resolve_model(self, route: str) -> str:
        """Map route tier to actual Ollama model name."""
        return {
            "qwen:2b": self.config.ollama.model_fast,
            "qwen:4b": self.config.ollama.model_general,
            "qwen:9b": self.config.ollama.model_analysis,
        }.get(route, self.config.ollama.model_analysis)
