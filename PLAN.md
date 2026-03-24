# Project Lumen — Personal AI Assistant

> Codename: **Lumen** (Latin for "light") — a local-first, voice-enabled personal AI assistant.
> Successor to the Jarvis/OpenClaw prototype. Built for a dedicated Mac Mini.
> GitHub-publishable. Cost target: ~$0-2/month in API usage.

---

## Vision

A personal assistant that **knows its user** — not through surveillance, but through attentive conversation. It tracks finances, sports, tech news, and code projects while building a lightweight psychological and behavioral profile to anticipate needs, adapt tone, and surface what matters before you ask.

Local models handle 95% of work for free. Claude API is the strategic brain, called only when reasoning demands it.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    LUMEN VOICE UI                       │
│              (Browser: Web Speech API)                  │
│         STT (mic) ←→ UI ←→ TTS (Kokoro)               │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
               ▼                      ▼
┌──────────────────────┐  ┌──────────────────────────────┐
│   ROUTER (Python)    │  │     KOKORO TTS SERVER        │
│                      │  │     (127.0.0.1:5050)         │
│  classify → route    │  │     kokoro-onnx, streaming   │
│  guardrails check    │  └──────────────────────────────┘
│  quality gate        │
└──────┬───────┬───────┘
       │       │
       ▼       ▼
┌────────┐ ┌────────────┐
│ OLLAMA │ │ CLAUDE API │
│ Qwen   │ │ (fallback) │
│ local  │ │ rare calls │
└────┬───┘ └─────┬──────┘
     │           │
     ▼           ▼
┌─────────────────────────────────────────────────────────┐
│                   CORE SERVICES                         │
│                                                         │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ Profile │ │ Finance  │ │ Sports   │ │ News       │ │
│  │ Engine  │ │ Monitor  │ │ Monitor  │ │ Aggregator │ │
│  └────┬────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
│       │           │            │              │        │
│       ▼           ▼            ▼              ▼        │
│  ┌─────────────────────────────────────────────────┐   │
│  │              SQLite Database                    │   │
│  │  profiles | chats | finance | sports | news     │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## What We Keep from OpenClaw Prototype

These scripts are well-written and worth porting:

| File | What it does | Status |
|------|-------------|--------|
| `jarvis.html` | Voice UI with orb animation, state machine, chat log | **Keep & adapt** — remove OpenClaw WebSocket, replace with direct HTTP to our router |
| `kokoro-server.py` | TTS server on :5050 | **Keep as-is** — upgrade to kokoro-onnx streaming later |
| `jarvis-classify.sh` | Rule-based request classification | **Port to Python** — embed in router for single-process simplicity |
| `jarvis-router.sh` | Two-pass routing (ack + response) | **Port to Python** — becomes the core router |
| `jarvis-acknowledge.sh` | Quick Qwen acknowledgment | **Port to Python** — inline in router |
| `collect.sh` | Finance data from CoinGecko/Yahoo/Fear&Greed | **Keep & improve** — add bonds, add Tim's specific tickers |
| `storyboard.sh` | AI-generated finance narrative | **Keep** — runs as cron job |
| `storyboard.html` | Finance dashboard | **Keep & adapt** |
| `search.sh` | DuckDuckGo web search | **Keep** — reliable, no API key needed |
| `search-and-summarize.sh` | Search + Qwen summary | **Keep** |
| `jarvis-health.sh` | Health monitor daemon | **Port to Python** — integrate into main server |
| `jarvis-status-server.py` | Health API on :5051 | **Merge into main server** |
| `dashboard.html` | Observability dashboard | **Keep & adapt** |
| `SOUL.md` | AI personality definition | **Keep & evolve** |
| `qwen-task.sh` | Model-specific task routing | **Port to Python** |

---

## Phase 1: Foundation (Decouple from OpenClaw)

**Goal:** Get the same functionality running without OpenClaw. Zero API cost.

### 1.1 Project Structure
```
lumen/
├── server/
│   ├── app.py              ← FastAPI main server (router + API)
│   ├── router.py           ← Request classification & model routing
│   ├── guardrails.py       ← Output quality gates & safety checks
│   ├── ollama_client.py    ← Ollama API wrapper
│   ├── claude_client.py    ← Anthropic API client (lazy, rare)
│   ├── tts.py              ← Kokoro TTS integration
│   ├── search.py           ← DuckDuckGo web search
│   └── config.py           ← All configurable settings (YAML)
├── profile/
│   ├── engine.py           ← Behavioral/psychological profiling
│   ├── sentiment.py        ← VADER + periodic DistilBERT analysis
│   ├── predictor.py        ← Intent prediction from patterns
│   └── condenser.py        ← Profile compression & summarization
├── agents/
│   ├── finance/
│   │   ├── collector.py    ← Market data fetcher (ported from collect.sh)
│   │   ├── storyboard.py   ← AI narrative generator
│   │   └── watchlist.py    ← User's personal watchlist
│   ├── sports/
│   │   ├── scores.py       ← Philadelphia teams live scores
│   │   ├── schedule.py     ← Upcoming games
│   │   └── recap.py        ← Game recaps via search + summarize
│   ├── news/
│   │   ├── aggregator.py   ← RSS + HN + search-based news
│   │   ├── summarizer.py   ← Qwen-powered summaries
│   │   └── briefing.py     ← Daily/on-demand briefing generator
│   └── code/
│       ├── scaffold.py     ← Project scaffolding
│       └── reviewer.py     ← Code review / QA via Claude (rare)
├── ui/
│   ├── lumen.html          ← Main voice UI (adapted from jarvis.html)
│   ├── finance.html        ← Finance dashboard
│   ├── sports.html         ← Sports dashboard
│   ├── health.html         ← System health dashboard
│   └── static/
│       ├── lumen.css
│       └── lumen.js
├── data/
│   ├── lumen.db            ← SQLite database (all state)
│   └── migrations/         ← Schema versioning
├── config/
│   ├── lumen.yaml          ← Main configuration
│   ├── personality.md      ← SOUL.md equivalent
│   └── user.yaml           ← User profile seed (timezone, teams, tickers)
├── scripts/
│   ├── search.sh           ← DuckDuckGo search (kept as-is)
│   └── install.sh          ← One-command setup
├── tests/
├── requirements.txt
├── Makefile                 ← start, stop, test, update, logs
├── LICENSE
└── README.md
```

### 1.2 Configuration (lumen.yaml)
```yaml
# All settings in one place — no hidden state
server:
  host: 127.0.0.1
  port: 3000

models:
  ollama:
    base_url: http://127.0.0.1:11434
    models:
      fast: qwen3.5:2b      # quick facts, acks
      general: qwen3.5:4b   # most tasks
      analysis: qwen3.5:9b  # finance, news, deep analysis
      guard: qwen3guard:0.6b # output safety filter
  claude:
    # Only used when Qwen can't handle it
    # Set via environment variable ANTHROPIC_API_KEY
    model: claude-sonnet-4-6
    max_monthly_budget: 2.00  # hard cap in dollars
    usage_log: data/claude_usage.json

tts:
  engine: kokoro
  voice: bm_george
  port: 5050
  streaming: true

profile:
  enabled: true
  sentiment_model: vader         # vader (fast) or distilbert (accurate)
  deep_analysis_interval: daily  # how often to run DistilBERT
  condense_interval: weekly      # compress profile summaries
  max_profile_size_kb: 50        # keep it lightweight

guardrails:
  enabled: true
  engine: qwen3guard             # qwen3guard-0.6b runs alongside
  fallback: rule_based           # if guard model unavailable
  block_categories:
    - harmful_content
    - financial_advice_without_disclaimer
    - hallucinated_data
  quality_checks:
    min_words: 4
    min_chars: 20
    must_end_punctuation: true
    coherence_check: true        # Qwen self-certainty check for factual claims

routing:
  escalation_keywords:
    - think
    - analyze
    - plan
    - strategy
    - advise
    - compare
    - "what do you think"
    - "your opinion"
  escalation_rules:
    max_words_for_local: 40
    max_questions_for_local: 1
    frustration_keywords:
      - wrong
      - stupid
      - idiot
      - "pass to claude"
      - "use claude"
  always_local:
    - time
    - date
    - weather
    - sports scores
    - stock price
    - crypto price

user:
  name: Tim
  timezone: America/New_York
  teams:
    - Eagles
    - Phillies
    - Sixers
    - Flyers
    - Union
  watchlist:
    stocks: []     # user adds over time
    crypto: []     # user adds over time
    bonds: []      # user adds over time

cron:
  finance_brief: "*/30 * * * *"    # every 30 min
  sports_check: "*/15 * * * *"     # every 15 min on game days
  news_digest: "0 8,12,18 * * *"   # 8am, noon, 6pm
  profile_condense: "0 3 * * 0"    # weekly Sunday 3am
  health_check: "*/1 * * * *"      # every minute
```

### 1.3 Database Schema (SQLite)
```sql
-- Chat history (for profiling, not replay)
CREATE TABLE chats (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  role TEXT NOT NULL,           -- 'user' or 'assistant'
  content TEXT NOT NULL,
  model_used TEXT,              -- 'qwen:2b', 'qwen:9b', 'claude', etc.
  route_reason TEXT,            -- why this model was chosen
  sentiment_score REAL,         -- VADER compound score (-1 to 1)
  tokens_used INTEGER
);

-- User profile (condensed behavioral data)
CREATE TABLE profile (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL,       -- 'personality', 'preferences', 'mood', 'interests'
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL DEFAULT 0.5,  -- 0-1, increases with more evidence
  evidence_count INTEGER DEFAULT 1,
  first_seen TEXT NOT NULL,
  last_updated TEXT NOT NULL,
  UNIQUE(category, key)
);

-- Mood tracking (time series)
CREATE TABLE mood (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  vader_compound REAL,
  vader_pos REAL,
  vader_neg REAL,
  vader_neu REAL,
  detected_emotion TEXT,        -- joy, anger, sadness, etc. (from DistilBERT)
  context TEXT                  -- what was being discussed
);

-- Finance data
CREATE TABLE market_data (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  source TEXT NOT NULL,         -- 'coingecko', 'yahoo', 'fear_greed'
  data_type TEXT NOT NULL,      -- 'crypto', 'stock', 'sentiment'
  symbol TEXT,
  payload TEXT NOT NULL         -- JSON blob
);

-- Claude API usage tracking
CREATE TABLE claude_usage (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  cost_usd REAL,
  reason TEXT                   -- why Claude was needed
);

-- Predictions (what the assistant thinks the user wants)
CREATE TABLE predictions (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  prediction TEXT NOT NULL,     -- "user likely wants morning finance brief"
  confidence REAL,
  basis TEXT,                   -- what evidence led to this
  was_correct BOOLEAN           -- feedback loop
);
```

### 1.4 Tasks
- [ ] Set up project directory structure
- [ ] Create FastAPI server with health endpoint
- [ ] Port classification logic from `jarvis-classify.sh` to Python
- [ ] Port routing logic from `jarvis-router.sh` to Python
- [ ] Port acknowledgment logic to Python
- [ ] Create Ollama client wrapper
- [ ] Adapt `jarvis.html` → `lumen.html` (replace WebSocket with HTTP/SSE)
- [ ] Initialize SQLite database with schema
- [ ] Create Makefile (start/stop/logs)
- [ ] Write `install.sh` for one-command setup
- [ ] Create `lumen.yaml` config with defaults

---

## Phase 2: Guardrails & Quality Control

**Goal:** Ensure Qwen never says anything stupid, offensive, or hallucinated.

### 2.1 Multi-Layer Defense

```
User Input
    │
    ▼
┌─────────────────────────────┐
│  Layer 1: INPUT FILTERING   │
│  - Prompt injection detect  │
│  - Classify intent          │
│  - Block adversarial inputs │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Layer 2: SYSTEM PROMPT     │
│  - Personality constraints  │
│  - "Never speculate"        │
│  - "Say 'I don't know'"    │
│  - Domain-specific rules    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Layer 3: GENERATION        │
│  - Low temperature (0.1-0.4)│
│  - Token limits per tier    │
│  - Stop sequences           │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Layer 4: OUTPUT GUARD      │
│  - Qwen3Guard-0.6B check   │
│  - Quality heuristics       │
│  - Self-certainty check     │
│  - Sentiment check (angry?) │
└──────────┬──────────────────┘
           │
     ┌─────┴──────┐
     │ PASS?      │
     ├─YES────────┼──→ Deliver to user
     └─NO─────────┼──→ Escalate to Claude
                   └──→ Or generic safe response
```

### 2.2 Qwen3Guard Integration

[Qwen3Guard](https://github.com/QwenLM/Qwen3Guard) is purpose-built for Qwen models:
- **0.6B model** — tiny, runs alongside main model with negligible overhead
- **Stream variant** — monitors token-by-token during generation
- Labels: Safe / Unsafe / Controversial
- Runs via Ollama (pull `qwen3guard:0.6b`)

### 2.3 Rule-Based Fallbacks (when guard model unavailable)
```python
BLOCKED_PATTERNS = [
    r"(?i)(kill|harm|hurt)\s+(yourself|himself|herself|themselves)",
    r"(?i)i\s+(hate|despise)\s+you",
    r"(?i)(racial|ethnic)\s+slur",
    r"(?i)financial\s+advice.*guarantee",
]

QUALITY_CHECKS = {
    "min_words": 4,
    "min_chars": 20,
    "ends_with_punctuation": True,
    "no_repetition": True,       # detect "the the the" loops
    "no_empty_response": True,
    "no_refusal_loops": True,    # detect "I can't help with that" spam
}
```

### 2.4 Finance-Specific Guardrails
- Every financial statement must cite its data source
- Include "This is not financial advice" disclaimer on buy/sell signals
- Never extrapolate or predict prices without explicit data basis
- Temperature locked to 0.1 for finance responses
- If data is stale (>1hr), say so

### 2.5 Tasks
- [ ] Pull `qwen3guard:0.6b` via Ollama
- [ ] Build `guardrails.py` with multi-layer pipeline
- [ ] Implement Qwen3Guard client (Ollama API)
- [ ] Implement rule-based fallback patterns
- [ ] Add quality gate to router (min words, coherence, etc.)
- [ ] Add self-certainty check for factual claims
- [ ] Add finance-specific guardrail rules
- [ ] Test with adversarial inputs

---

## Phase 3: User Profiling Engine

**Goal:** Build a lightweight, ethical behavioral profile from everyday conversations.

### 3.1 What We Track (and Why)

| Signal | Method | Storage | Purpose |
|--------|--------|---------|---------|
| **Mood over time** | VADER per message, DistilBERT daily | `mood` table (time series) | Adapt tone, detect bad days |
| **Topic interests** | Keyword frequency + recency weighting | `profile` table | Surface relevant content proactively |
| **Communication style** | Message length, formality, emoji use | `profile` table | Mirror user's style |
| **Big Five personality** | Periodic LLM analysis of chat history | `profile` table | Understand decision-making style |
| **Schedule patterns** | When they talk, what they ask by time | `profile` table | Predict needs by time of day |
| **Frustration triggers** | Track what causes negative sentiment | `profile` table | Avoid those patterns |
| **Confirmed preferences** | Explicit "I like/don't like X" | `profile` table (confidence=1.0) | Hard preferences, never override |

### 3.2 Ethical Guardrails for Profiling

```yaml
profiling_rules:
  transparency:
    - User can ask "what do you know about me?" at any time
    - User can ask "forget X" and it's deleted immediately
    - User can disable profiling entirely in config
  boundaries:
    - Never profile health conditions or diagnoses
    - Never profile political beliefs (unless user explicitly discusses)
    - Never profile relationships or personal conflicts
    - Never share profile data externally (it stays in local SQLite)
  consent:
    - Profiling is opt-in (enabled in config, off by default for published version)
    - First-run explains what profiling does and asks for confirmation
```

### 3.3 Profile Condensation

Profiles must stay lightweight. Weekly condensation job:

```
Raw signals (thousands of data points)
    │
    ▼
Qwen 9B summarization prompt:
    "Given these behavioral signals from the past week,
     update this user profile. Keep each trait to one sentence.
     Remove anything with <3 evidence points.
     Merge redundant traits. Max 50 traits total."
    │
    ▼
Condensed profile (≤50KB JSON)
```

### 3.4 Prediction Engine

Simple pattern matching first, ML later:

**Phase 3a — Rule-based predictions:**
- Morning + weekday → "Want your finance brief?"
- Game day + evening → "Eagles play at 8pm, want updates?"
- Haven't checked news today → "Want a tech news roundup?"
- Repeated topic this week → Surface related content

**Phase 3b — ML predictions (future):**
- Train a small classifier on (time, day, recent_topics, mood) → predicted_intent
- Use scikit-learn RandomForest or a tiny neural net
- Feedback loop: track if predictions were acted on

### 3.5 Tasks
- [ ] Implement VADER sentiment analysis per message
- [ ] Build profile engine (extract interests, style, preferences from chats)
- [ ] Create mood tracking table and time-series storage
- [ ] Implement "what do you know about me?" command
- [ ] Implement "forget X" command
- [ ] Build weekly profile condensation job
- [ ] Add Big Five personality analysis (periodic, via Qwen 9B)
- [ ] Build rule-based prediction engine
- [ ] Add prediction feedback loop (was it useful? y/n)

---

## Phase 4: Domain Agents

### 4.1 Finance Agent (enhance existing)

**Keep:** `collect.sh`, `storyboard.sh`, `storyboard.html`
**Add:**
- Personal watchlist (user says "watch NVDA" → tracked)
- Portfolio tracking (optional: user enters holdings)
- Bond yield monitoring (10Y Treasury via FRED API — free)
- Alerts: "NVDA dropped 5% today" pushed proactively
- Historical data in SQLite for trend analysis

**Data sources (all free, no API keys):**
| Source | Data | Endpoint |
|--------|------|----------|
| CoinGecko | Crypto prices, market cap | `api.coingecko.com/api/v3/` |
| Alternative.me | Fear & Greed Index | `api.alternative.me/fng/` |
| Yahoo Finance | Stock screeners, quotes | `query2.finance.yahoo.com` |
| FRED | Bond yields, economic data | `api.stlouisfed.org` (free key) |
| DuckDuckGo | Fallback for any data | `lite.duckduckgo.com` |

### 4.2 Sports Agent (new)

**Philadelphia teams:** Eagles (NFL), Phillies (MLB), Sixers (NBA), Flyers (NHL), Union (MLS)

**Data sources (free):**
| Source | Data | Method |
|--------|------|--------|
| ESPN API | Scores, schedules, standings | `site.api.espn.com/apis/site/v2/sports/` |
| DuckDuckGo | Game recaps, news | `search.sh` |

**Features:**
- Live scores during games (poll every 2 min)
- Today's schedule: "Phillies play at 7:05pm vs Mets"
- Post-game recaps via search + Qwen summary
- Season standings
- "How are the Eagles doing?" → season record + recent results

### 4.3 News Agent (new)

**Data sources (free):**
| Source | Data | Method |
|--------|------|--------|
| Hacker News API | Tech/AI news | `hacker-news.firebaseio.com/v0/` |
| RSS feeds | Configurable sources | Python `feedparser` |
| DuckDuckGo | Topic-specific search | `search.sh` |

**Features:**
- Morning briefing: top 5 AI/tech stories
- On-demand: "What's the latest on OpenAI?"
- Trending detection: what's being talked about most
- Summarization via Qwen 9B

### 4.4 Code Agent (new, Claude-powered)

This is the one agent that intentionally uses Claude API:
- "Start a new Python project for X" → scaffold via Claude
- "Review this code" → Claude analysis
- "Help me debug X" → Claude reasoning
- Budget-capped: tracks usage, warns at 80% of monthly limit

### 4.5 Tasks
- [ ] Enhance finance collector with bonds (FRED API)
- [x] Add personal watchlist feature
- [ ] Add finance alerts (price drops, big moves)
- [x] Build sports agent (ESPN API integration)
- [x] Build sports schedule and live score polling
- [x] Build news agent (HN API + RSS)
- [x] Build morning briefing generator
- [x] Build code agent (Claude-backed, budget-capped)
- [ ] Create sports dashboard HTML
- [ ] Add cron jobs for all agents

---

## Phase 5: Voice & UX Polish

### 5.1 Voice Improvements

**Upgrade Kokoro to ONNX streaming:**
- Replace current `kokoro` Python lib with `kokoro-onnx`
- Enable `create_stream()` for real-time audio
- Pre-cache common phrases (greetings, acks) as WAV files
- Result: first audio plays in <200ms

**Wake word (future, optional):**
- Use browser-based always-listening with a keyword trigger
- Or just use a physical button / keyboard shortcut

### 5.2 UI Improvements

- Keep the orb design (it's great)
- Add dashboard tabs: Voice | Finance | Sports | News | Health | Profile
- Mobile-responsive layout (access from phone on local network)
- Dark mode only (it's a personal HUD, not a SaaS app)

### 5.3 Proactive Notifications

Based on profile predictions:
```
[Morning, weekday]
"Good morning Tim. Markets are down 2% pre-market — Fear & Greed at 15.
Phillies won 6-3 last night. No Eagles news. Want the full brief?"

[Game day evening]
"Sixers tip off in 30 minutes against the Celtics. Want score updates?"

[After detecting frustrated mood]
*Adjusts tone to be more direct, less chatty*
```

### 5.4 Tasks
- [ ] Upgrade Kokoro to ONNX streaming
- [ ] Pre-cache common audio phrases
- [ ] Add dashboard tabs to UI
- [ ] Build proactive notification system
- [ ] Add mood-adaptive response tone
- [ ] Mobile-responsive CSS

---

## Phase 6: GitHub Publishing

### 6.1 Pre-publish Checklist
- [ ] Remove all personal data (API keys, tokens, Tim-specific config)
- [ ] Create `lumen.yaml.example` with placeholder values
- [ ] Write README with screenshots, architecture diagram, setup instructions
- [ ] Write `install.sh` that handles Ollama, Python deps, Kokoro, SQLite
- [ ] Add MIT or Apache 2.0 license
- [ ] Add `.gitignore` (data/, *.db, config/user.yaml, .env)
- [ ] Test fresh install on clean macOS
- [ ] Create GitHub repo as `lumen-ai` or similar (check availability)

### 6.2 What Makes This Different from Other Projects
- **Local-first with intelligent cloud fallback** — not just "use GPT for everything"
- **Behavioral profiling** — learns from conversation, not explicit configuration
- **Cost-conscious architecture** — designed to run for free 95% of the time
- **Voice-native** — not a chatbot with TTS bolted on
- **Guardrailed local models** — Qwen3Guard integration prevents garbage output
- **Opinionated personality** — not a generic assistant, has character

---

## Implementation Priority

| Priority | Phase | Effort | Impact |
|----------|-------|--------|--------|
| **P0** | Phase 1: Foundation | 2-3 sessions | Removes OpenClaw dependency, everything works |
| **P1** | Phase 2: Guardrails | 1-2 sessions | Prevents embarrassing Qwen outputs |
| **P1** | Phase 4.1: Finance | 1 session | Already mostly built, just enhance |
| **P2** | Phase 3: Profiling | 2-3 sessions | The differentiator, but not blocking |
| **P2** | Phase 4.2: Sports | 1 session | Quick win, Tim wants this |
| **P2** | Phase 4.3: News | 1 session | Quick win |
| **P3** | Phase 5: Voice polish | 1-2 sessions | Nice to have, current voice works |
| **P3** | Phase 4.4: Code agent | 1 session | Uses Claude API, lower priority |
| **P4** | Phase 6: GitHub | 1 session | Do after everything works |

---

## How to Resume This Plan

This plan is designed to be picked up in any future Claude Code session. Just say:

> "Let's work on Lumen. Pick up from the plan."

Claude Code will read this file, check what's done, and continue from where we left off. Mark tasks with `[x]` as they're completed.

---

*Last updated: 2026-03-21*
*Author: Tim + Claude Code*
