# Parallel Session Coordination

> Two Claude Code sessions working simultaneously. Read this before touching ANY file.

---

## Active Sessions

| Session | Plan | Owner Files | Status |
|---------|------|------------|--------|
| **Session A** | Plan A: Assistant UX | `server/router.py`, `server/app.py`, `server/ollama_client.py`, `ui/static/lumen.js`, `ui/lumen.html`, `ui/static/lumen.css`, `config/personality.md` | ACTIVE |
| **Session B** | Plan B: Behavioral Engine | `profile/*`, `server/emotions.py` (new), `server/database.py`, `data/migrations/*` | ACTIVE |

---

## File Ownership Rules

### Session A OWNS (do not touch):
- `server/router.py` — modifying routing logic, context injection, voice editing
- `server/app.py` — adding endpoints, modifying SSE stream
- `server/ollama_client.py` — adding thinking budget, sampling params
- `server/cache.py` — cache modifications
- `ui/static/lumen.js` — voice UX changes
- `ui/lumen.html` — UI layout changes
- `ui/static/lumen.css` — styling changes
- `config/personality.md` — persona definition

### Session B OWNS (do not touch):
- `profile/sentiment.py` — upgrading emotion detection
- `profile/engine.py` — behavioral feature extraction
- `profile/condenser.py` — profile decay/fusion
- `profile/predictor.py` — adaptive behavior
- `server/emotions.py` — NEW file for TinyBERT + NRCLex integration
- `server/database.py` — adding columns/tables for emotions
- `data/migrations/002_emotions.sql` — NEW migration for emotion columns

### SHARED (coordinate before modifying):
- `server/config.py` — either can ADD new config fields, but don't modify existing ones
- `requirements.txt` — either can ADD dependencies, append to end, don't reorder
- `PARALLEL.md` — this file, both update with session notes
- `HANDOFF.md` — both update session notes section

### Integration Points (how the two plans connect):
- **Session B** will expose emotion data via functions in `server/emotions.py`
- **Session A** will call those functions from `server/router.py` to inject mood into system prompts
- The interface: `await get_current_emotion(text) -> dict` with keys `{emotion, confidence, mood_adjustment}`
- Session B builds the function. Session A calls it. Don't implement both sides independently.

---

## Rules

1. **Check this file before editing any shared file.**
2. **Never modify a file owned by the other session.**
3. **Append to requirements.txt, don't rewrite it.**
4. **New migrations: Session A uses 003+, Session B uses 002.**
5. **Git: commit your own work freely. If you see the other session's uncommitted changes in `git status`, leave them alone.**
6. **If you need something from the other session, leave a note in the Session Notes below.**

---

## Session Notes

*Add notes below as you work. Timestamp each entry.*

### Session A Notes
- **2026-03-24 20:15** — Completed first batch of Plan A work:
  - Pre-warmed 4B model (keep_alive=-1 alongside 0.8B)
  - Updated sampling params to Qwen3.5 official recommendations (temp=0.6/0.7, top_p=0.8/0.95, top_k=20)
  - Added thinking mode policy (false for 0.8B/2B, true for 4B/9B)
  - Created `server/context.py` — filters domain data to query-relevant subset before injection
  - Voice output capped at 35 words / 3 sentences (research-backed)
  - Switched to female voice (af_bella) + rewrote personality.md with stronger persona + example dialogues
  - **Integration point for Session B**: I will import `server/emotions.py:analyze_emotion()` from the router once Session B creates it
- **2026-03-24 20:35** — Second batch:
  - Speculative execution: 0.8B tries every query first with domain data. If draft passes relevancy → skip bigger model.
  - Fixed thinking mode: contextual, not per-tier. Simple extraction stays non-thinking.
  - Proactive intelligence (`server/proactive.py`): game alerts, market moves, morning briefs. Max 1/10min.
  - Updated personality for friend mode. New endpoints: `/api/proactive/status`, `/api/proactive/respond`
- **2026-03-24 20:50** — Integrated Session B's `server/emotions.py`: mood hints in system prompt, warmup at startup
- **2026-03-24 21:00** — Self-repair, guardrail override, semantic endpointing
- **2026-03-24 21:20** — Plan C work:
  - Skip 2B: config now routes general → 4B (0.8B→4B jump per research)
  - Self-consistency voting: `self_consistency_check()` generates N responses, returns majority
  - JSON constrained decoding: `json_format=True` + `structured_classify()` helper
  - Compact key-value context format for better small-model parsing
  - Interest callbacks in proactive system (matches topics against cached data)
  - Integrated Session B's finance analytics: `/api/finance/analyze/{symbol}`, `/api/finance/analyze/crypto/{coin_id}`
  - Classifier regex updated for analytics keywords (analyze, rsi, macd, etc.)
- **SESSION A DONE** — All Plan A + Plan C items implemented. Remaining: MLX evaluation (needs benchmarking), LoRA fine-tuning (needs training data collection)

### Session B Notes
- **2026-03-24 20:30** — Completed Plan B Phases 1-3 (Behavioral Engine):
  - `server/emotions.py` — unified emotion API: `analyze_emotion(text) -> dict` (TinyBERT + NRCLex + VADER)
  - `profile/engine.py` — added `extract_style_metrics()` + rolling baselines + drift detection
  - `profile/sentiment.py` — upgraded to 3-layer analysis, emotion trend tracking
  - `data/migrations/002_emotions.sql` — emotion columns on mood, new behavioral_metrics + baselines tables
  - `server/database.py` — 5 new query functions for emotions and behavioral metrics
  - `server/config.py` — added `EmotionConfig` dataclass
  - `requirements.txt` — appended transformers, NRCLex, torch
  - **Session A**: `server/emotions.py` is ready. Import `analyze_emotion` and `get_mood_prompt_hint`. Call `warmup()` from app lifespan if desired.
- **2026-03-24 20:45** — Personality direction update per Tim:
  - Updated `docs/PLAN-BEHAVIORAL-ENGINE.md` Phase 4 — "Friend Mode" replaces the old "Never Do These" rules
  - Updated mood prompt hints in `server/emotions.py` to be friendlier (acknowledge mood naturally, not invisibly)
  - **TODO FOR SESSION A — Guardrail passcode override**: Tim wants a passcode that disables app-layer guardrails (output quality gates, enthusiasm limits, formality enforcement). Qwen3Guard stays active always. Suggested approach: add `guardrail_override_passcode` field to `server/config.py` → check in router before running app-layer guards. Config value lives in `lumen.yaml` (gitignored). Also update `personality.md` to reflect friend mode — natural emotional acknowledgment, interest callbacks, light check-ins are all encouraged.
- **2026-03-24 21:15** — Finance analytics engine:
  - `agents/finance/analytics.py` — NEW: full technical + fundamental analysis
    - **Technical**: RSI (14), MACD (12/26/9), Bollinger Bands %B, Stochastic (14/3), ADX trend strength, SMA 50/200, golden/death cross detection
    - **Fundamental**: P/E, PEG (with manual fallback), P/B, EV/EBITDA, D/E, ROE, beta, 52-week position
    - **Crypto**: fetches CoinGecko daily history, applies same technical indicators
    - **Scoring**: each symbol gets risk_score (0-1) and opportunity_score (0-1) from signal aggregation
    - Key functions: `analyze_stock(symbol)`, `analyze_crypto(coin_id)`, `analyze_watchlist(items)`, `analyze_top_movers(symbols)`, `analysis_to_text(results)`, `signals_summary(results)`
  - Updated `agents/finance/storyboard.py`:
    - `generate_storyboard()` now accepts optional `watchlist` param, runs analytics on watchlist + top movers + top crypto concurrently
    - LLM prompt now includes TECHNICAL SIGNALS and VALUATIONS sections
    - New `generate_analytics_report(symbols)` for standalone analysis ("analyze NVDA")
    - Max tokens bumped 600→800 to accommodate analytics sections
  - **Deps**: appended `ta>=0.11`, `yfinance>=0.2.40`, `pandas>=2.0` to requirements.txt
  - **NOTE FOR SESSION A**: `generate_storyboard()` signature changed — now takes optional `watchlist: list[dict]` param. Also new `generate_analytics_report(symbols, asset_type)` available for direct "analyze X" queries from router.

