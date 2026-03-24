# Lumen — Handoff for Parallel Claude Code Sessions

> Read this file first, then read PLAN.md for the full architecture.

---

## Project Location
`/Users/timmcintyre/lumen/`

## What's Built So Far

### Rust Core Library (lumen-core) — DONE
High-performance engine compiled as a Python extension via PyO3/maturin.

**Location:** `crates/lumen-core/src/`

**How to import:**
```python
# Activate venv first: source /Users/timmcintyre/lumen/.venv/bin/activate
import lumen_core

# Classify a user message → route to correct model
result = lumen_core.classify_request("What's the price of bitcoin?")
# → ClassificationResult(route='qwen:9b', reason='finance_query', escalate=false, domain='finance')

# Sentiment analysis (VADER-inspired, <1ms)
sentiment = lumen_core.analyze_sentiment("I love this project!")
# → SentimentResult(compound=0.869, mood='very_positive')

# Guardrail check on LLM output before delivering to user
guard = lumen_core.check_output("Bitcoin is at 67000.", "finance")
# → GuardrailResult(safe=true, reason='pass', quality=0.90)

# Predictive analytics — what does the user probably want right now?
predictions = lumen_core.predict_intent(
    hour=9, day_of_week=1,
    recent_topics={"finance": 5, "sports": 2},
    mood_compound=0.3,
    last_query_domain="finance",
    minutes_since_last=120,
    game_today=True
)
```

**To rebuild after changes:**
```bash
source ~/.cargo/env
source /Users/timmcintyre/lumen/.venv/bin/activate
cd /Users/timmcintyre/lumen/crates/lumen-core
maturin develop --release
```

### Python venv — DONE
Location: `/Users/timmcintyre/lumen/.venv/`
Installed: fastapi, uvicorn, httpx, pyyaml, aiosqlite, aiofiles, maturin

### Project Structure — DONE
```
lumen/
├── PLAN.md              ← Full architecture & task list
├── HANDOFF.md           ← This file
├── .gitignore           ← Configured
├── crates/
│   └── lumen-core/      ← Rust library (BUILT)
├── server/              ← FastAPI server (TODO)
├── profile/             ← User profiling engine (TODO)
├── agents/              ← Domain agents (TODO)
│   ├── finance/
│   ├── sports/
│   ├── news/
│   └── code/
├── ui/                  ← Voice UI (TODO)
│   └── static/
├── data/                ← SQLite DB (TODO)
│   └── migrations/
├── config/              ← YAML config (TODO)
├── scripts/             ← Shell scripts (TODO — port from OpenClaw)
└── tests/
```

---

## Remaining Tasks (pick any that aren't marked in-progress)

### Task 3: FastAPI Server
**Files to create:** `server/app.py`, `server/router.py`, `server/ollama_client.py`, `server/claude_client.py`, `server/config.py`
- Main server on port 3000
- POST `/api/chat` — accepts user message, classifies via `lumen_core.classify_request()`, routes to Ollama or Claude
- POST `/api/tts` — proxy to Kokoro TTS on :5050
- GET `/api/health` — system health (check Ollama, Kokoro, DB)
- GET `/api/predictions` — run `lumen_core.predict_intent()` and return suggestions
- SSE endpoint for streaming responses to the voice UI
- Every message: run `lumen_core.analyze_sentiment()` and store in DB
- Every Qwen response: run `lumen_core.check_output()` before delivering

### Task 4: SQLite Database Layer
**Files to create:** `data/migrations/001_initial.sql`, `server/database.py`
- Schema is defined in PLAN.md (tables: chats, profile, mood, market_data, claude_usage, predictions)
- Use aiosqlite for async access
- Auto-run migrations on startup

### Task 5: Voice UI
**File to create:** `ui/lumen.html`, `ui/static/lumen.css`, `ui/static/lumen.js`
- Port from `~/.openclaw/workspace/jarvis.html` (read it for reference — it's excellent)
- Replace OpenClaw WebSocket with HTTP POST to `/api/chat` + SSE for streaming
- Rename all "Jarvis" references to "Lumen"
- Keep the orb animations, state machine, chat log, settings panel

### Task 7: Config & Installer
**Files to create:** `config/lumen.yaml.example`, `config/personality.md`, `scripts/install.sh`, `Makefile`
- See PLAN.md for the full lumen.yaml schema
- install.sh: check deps (Ollama, Python, Rust), create venv, build Rust, init DB, pull Qwen models
- Makefile: start, stop, logs, build, test

---

## Key Design Decisions

1. **Local-first:** Qwen via Ollama handles 95% of requests. Claude API only for complex reasoning.
2. **Rust hot path:** Classification, sentiment, guardrails, and prediction run in Rust for <1ms latency.
3. **Predictive everywhere:** Every interaction feeds the predictor. Proactive suggestions, not just reactive.
4. **Guardrails on all Qwen output:** Never deliver unchecked local model output to user.
5. **Cost target:** ~$0-2/month Claude API. Tim is on Claude Pro Black so Claude Code sessions are free.

## Existing Code to Port
The OpenClaw workspace at `~/.openclaw/workspace/` has working scripts worth studying:
- `jarvis.html` — voice UI (port to lumen.html)
- `kokoro-server.py` — TTS server (keep as-is, it works)
- `collect.sh` / `storyboard.sh` — finance data pipeline
- `search.sh` — DuckDuckGo web search
- `jarvis-classify.sh` — classification logic (already ported to Rust)
- `jarvis-router.sh` — routing logic (port to Python server)

## Communication
If you need to leave notes for the other session, append to this file under a `## Session Notes` heading.

---

## Session Notes

*Add notes below as you work. Timestamp each entry.*

### 2026-03-21 — Session B (Config & Install)
- **Claiming Task #7: Config & Installer** (lumen.yaml, personality.md, install.sh, Makefile)
- Session A: go ahead on Task #3 (FastAPI server) + Task #4 (SQLite DB) — they're yours.
- **Task #7 DONE.** Created:
  - `config/lumen.yaml` — Tim's live config (gitignored)
  - `config/lumen.yaml.example` — publishable template (profile disabled by default)
  - `config/personality.md` — Lumen's SOUL file (tone, rules, voice cadence)
  - `scripts/install.sh` — full installer (checks deps, creates venv, builds Rust, pulls Ollama models, inits DB)
  - `Makefile` — start/stop/restart/logs/test/build/status/clean
- Note: `make start` expects `server/app.py` to exist (uvicorn server.app:app). Session A should ensure the FastAPI entry point matches this.
- **Task #5 DONE.** Voice UI ported from Jarvis:
  - `ui/lumen.html` — main page, loads CSS + JS, all branding updated to Lumen
  - `ui/static/lumen.css` — full stylesheet with mobile responsive breakpoints
  - `ui/static/lumen.js` — all logic: orb, state machine, STT, TTS, chat log, settings
  - Key change: replaced WebSocket (OpenClaw gateway) with HTTP POST to `/api/chat` + SSE streaming
  - Fallback: if FastAPI server is down, UI falls back to direct Ollama calls
  - Health check polls `/api/health` every 30s, updates connection status indicator

### 2026-03-21 — Session A (Core Engine)
- **Task #1 DONE.** Project structure + git init
- **Task #2 DONE.** Rust core library (lumen-core) with PyO3 0.28:
  - `classifier.rs` — request classification with precompiled regex, domain detection
  - `sentiment.rs` — VADER-inspired sentiment analysis (<1ms per message)
  - `guardrails.rs` — blocked patterns, repetition detection, quality gates, finance disclaimers
  - `predictor.rs` — predictive intent based on time/topics/mood/patterns
  - All tested and working with Python 3.14
- **Task #3 DONE.** FastAPI server:
  - `server/app.py` — main server with /api/chat, /api/health, /api/predictions, /api/profile, /api/costs, /api/tts, SSE streaming
  - `server/router.py` — full pipeline: classify → ack → route → guardrail → self-check → respond
  - `server/ollama_client.py` — async Ollama client with streaming, ack generation, self-certainty check
  - `server/claude_client.py` — Anthropic API client with budget tracking and cost logging
  - `server/config.py` — YAML config loader compatible with Session B's lumen.yaml structure
- **Task #4 DONE.** SQLite database:
  - `data/migrations/001_initial.sql` — tables: chats, profile, mood, market_data, claude_usage, predictions, watchlist, schema_version
  - `server/database.py` — async DB layer with helpers for chat logging, mood tracking, profile CRUD, topic frequency, Claude cost tracking
- **Task #6 DONE.** Guardrails system (implemented in Rust + Python router):
  - Blocked content patterns (self-harm, hostile, financial guarantees, AI self-reference)
  - Repetition loop detection
  - Quality gates (min words/chars, punctuation, Qwen artifact removal)
  - Finance disclaimer injection
  - Self-certainty check (Qwen re-evaluates its own factual claims)
  - Multi-layer pipeline: input filter → system prompt → low temp → output guard → escalate to Claude
- **Task #8 DONE.** Profile engine:
  - `profile/sentiment.py` — SentimentTracker with mood trend detection and tone adjustment recommendations
  - `profile/engine.py` — ProfileEngine: topic extraction, style analysis, preference learning, frustration tracking, Big Five personality analysis via Qwen
  - `profile/condenser.py` — Weekly condensation: removes stale/low-evidence entries, LLM-powered dedup, mood history compaction, chat content condensation after 90 days
  - `profile/predictor.py` — Predictor with feedback loop, confidence adjustment from historical accuracy, proactive suggestion filtering

### 2026-03-21 — Session B (TTS Overhaul)
- **TTS text preprocessing moved to Rust** — `crates/lumen-core/src/tts_prep.rs`:
  - `prepare_for_tts(text)` → `TTSPrepResult { text, sentences, estimated_duration_ms }`
  - 17 precompiled LazyLock regex patterns, full number-to-words (0-999,999 + decimals)
  - Ticker expansion (16 symbols), currency, signed/unsigned percentages, abbreviations
  - **7.6 microseconds per call** — replaces ~200 lines of JavaScript regex chains
- **New Kokoro TTS server** — `server/tts_server.py`:
  - FastAPI-based, replaces old BaseHTTPServer from OpenClaw
  - Uses `kokoro_onnx` (preferred) or `kokoro` legacy as fallback
  - Audio playback via `sounddevice` OutputStream (persistent, callback-based, no temp files)
  - LRU audio cache (50 phrases) with pre-warming of common ack phrases
  - All classes use `__slots__`, no god functions
  - Run: `make tts-start` or `python -m server.tts_server`
- **JS cleanup** — removed from `ui/static/lumen.js`:
  - Deleted: `expandTickers()` (dead code, never called)
  - Deleted: `numberToWords()`, `addNaturalPauses()`, `cleanForTTS()` (moved to Rust)
  - Deleted: `TICKER_NAMES` map (now in Rust)
  - Added: `speakViaWebSpeech()` fallback when Kokoro is down
  - Fixed: `speakViaKokoro()` now uses actual `duration_ms` from server response instead of sleep estimation
  - Fixed: `/stop` called on new speech to cancel in-progress playback
- **Updated** `server/app.py`:
  - `/api/tts` now preprocesses text through Rust before forwarding to Kokoro
  - New `/api/tts/prepare` endpoint exposes Rust preprocessing to any client
- **Updated** `scripts/install.sh` — added `sounddevice` and `numpy` to pip deps
- **Updated** `Makefile` — added `make tts-start` and `make tts-stop` targets

**Remaining for next session:**
- Integration testing (start server, send messages end-to-end)
- Install kokoro-onnx model files (kokoro-v1.0.onnx + voices-v1.0.bin)
- Git first commit
- FRED API for bonds (finance agent enhancement)
- Finance alerts (price drops, big moves)
- Sports dashboard HTML
- Cron jobs for all agents

### 2026-03-24 — Session (Domain Agents + Voice Fixes)
- **Domain agents DONE.** All four built and wired into router + app:
  - `server/search.py` — DuckDuckGo search utility (ported from search.sh)
  - `agents/finance/collector.py` — CoinGecko crypto, Yahoo Finance stocks, Fear & Greed (ported from collect.sh)
  - `agents/finance/storyboard.py` — Market brief narrative generator (ported from storyboard.sh)
  - `agents/finance/watchlist.py` — Personal watchlist CRUD + live quote fetching
  - `agents/sports/scores.py` — ESPN API: live scores, records for all Philly teams
  - `agents/sports/schedule.py` — Upcoming games for all Philly teams
  - `agents/sports/recap.py` — Game recaps via search + LLM summary
  - `agents/news/aggregator.py` — HN API + RSS feeds + DuckDuckGo search
  - `agents/news/summarizer.py` — Topic + URL summarization via Ollama
  - `agents/news/briefing.py` — Morning/on-demand news briefing generator
  - `agents/code/scaffold.py` — Claude-backed project scaffolding
  - `agents/code/reviewer.py` — Claude-backed code review
- **Router updated** (`server/router.py`):
  - `_fetch_domain_data()` method fetches live data for finance/sports/news domains
  - Data injected into LLM prompt as `[LIVE DATA]` context before user question
  - Timeouts: finance 10s, sports/news 8s — non-blocking with fallback to no data
- **App updated** (`server/app.py`):
  - New API endpoints: `/api/finance/snapshot`, `/api/finance/brief`, `/api/finance/watchlist`
  - New API endpoints: `/api/sports/scores`, `/api/sports/schedule`, `/api/sports/recap/{team}`
  - New API endpoints: `/api/news/feed`, `/api/news/briefing`, `/api/news/topic/{topic}`
  - New API endpoints: `/api/search`
  - SSE stream now injects domain data into prompts
  - Predictions endpoint now checks if Philly team plays today via sports agent
- **Voice UX fixes** (`ui/static/lumen.js`):
  - Fixed self-talk loop: barge-in handler now blocked during `conversationLock`
  - Removed instant phrase speaking — 0.8B responds in ~300ms, no ack needed
  - Removed backchannel system (was firing TTS mid-processing, causing echo)
  - Added hard `recognition.abort()` before every TTS call (speakText, processTTSQueue)
  - First spoken content is now the first real sentence from the server
- **Added** `feedparser>=6.0` to `requirements.txt`
