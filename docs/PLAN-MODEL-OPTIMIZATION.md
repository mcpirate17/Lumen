# Plan C: Small Model Intelligence & Efficiency

> Research-backed strategies to make Qwen 3.5 (0.8B-9B) punch above their weight.
> Sources: Qwen3 technical report, TinyAgent (EMNLP 2024), Apple MLX research, arxiv 2601.19139.

---

## Current Model Architecture

| Model | Role | Keep Alive | Throughput (est.) |
|-------|------|-----------|-------------------|
| Qwen3.5-0.8B | Routing, acks, relevancy checks | Permanent | ~500 tok/s |
| Qwen3.5-2B | Summaries, condensing | 5 min | ~300 tok/s |
| Qwen3.5-4B | Domain queries, analysis | 5 min | ~160 tok/s |
| Qwen3.5-9B | Complex reasoning, storyboards | On demand | ~90 tok/s |

---

## Priority 1: Inference Backend (MLX Migration)

Research: MLX is **21-87% faster than llama.cpp** and **~50% faster than Ollama** on Apple Silicon.

### 1.1 Evaluate MLX vs Ollama
- [ ] Benchmark Qwen3.5-4B on current Mac Mini: Ollama tok/s vs MLX tok/s
- [ ] Benchmark TTFT (time to first token) — this matters most for voice
- [ ] Test multi-model concurrent loading in MLX
- [ ] If MLX is significantly faster, build an MLX client alongside Ollama client

### 1.2 MLX Migration Path
- [ ] Install `mlx-lm` package
- [ ] Download Qwen3.5 models in MLX format from HuggingFace
- [ ] Create `server/mlx_client.py` mirroring `ollama_client.py` API
- [ ] Feature-flag: config switch between Ollama and MLX backends
- [ ] Keep Ollama as fallback (better ecosystem, easier model management)

### 1.3 Quantization Strategy
Research: Q8 for small models (quality matters more when params are few), Q4_K_M for large.
- [ ] 0.8B: **Q8** (already tiny, keep quality)
- [ ] 2B: **Q8** (still fast at Q8)
- [ ] 4B: **Q8** (~4GB, fits easily)
- [ ] 9B: **Q4_K_M** (best speed/quality balance, ~5GB)

---

## Priority 2: Thinking Mode Optimization

Research: Qwen3.5 thinking mode has diminishing returns below 4B. Never use on 0.8B.

### 2.1 Per-Model Thinking Policy
- [ ] **0.8B**: Always `think=false` (already doing this)
- [ ] **2B**: Always `think=false` (reasoning too weak to help)
- [ ] **4B**: `think=true` for domain queries (finance, sports analysis). `think=false` for simple generation.
- [ ] **9B**: `think=true` with budget control for complex tasks

### 2.2 Thinking Budget Control
Qwen3.5 supports configurable reasoning token budgets:
- [ ] Simple queries: 0 thinking tokens
- [ ] Medium complexity: 256-512 thinking tokens (4B)
- [ ] Hard reasoning: 1024-4096 thinking tokens (9B only)
- [ ] Add `thinking_budget` parameter to Ollama client
- [ ] Router sets budget based on query complexity classification

### 2.3 Sampling Parameters (Qwen3.5 official recommendations)
- [ ] Thinking mode: `temperature=0.6, top_p=0.95, top_k=20` (precise tasks)
- [ ] Non-thinking mode: `temperature=0.7, top_p=0.8, top_k=20`
- [ ] **Never use greedy decoding (temp=0) with thinking mode** — causes loops
- [ ] Update `router.py` MODEL_PARAMS to use these values

---

## Priority 3: Smarter Routing

Current: Rust classifier → fixed model tier. No confidence-based escalation.

### 3.1 Confidence-Based Escalation
- [ ] After 0.8B/2B generates a response, check token entropy/logprobs
- [ ] If confidence below threshold → escalate to next tier
- [ ] Ollama exposes logprobs — extract and use them
- [ ] Skip 2B in most routing — research shows 0.8B→4B jump is more valuable

### 3.2 Revised Routing Table
Based on capability benchmarks:

| Task | Current Route | Better Route | Rationale |
|------|--------------|-------------|-----------|
| Classification, acks | 0.8B | 0.8B | Fast, adequate |
| Simple Q&A over injected data | 4B | 0.8B or 4B | 0.8B can extract if data is clean |
| Summarization | 2B | 4B | 4B summaries significantly better |
| Math, multi-step | 9B | 4B (think=true) | 4B HMMT=74, good enough |
| Complex reasoning | Claude | 9B (think=true) | 9B MMLU-Pro=82.5, try before Claude |
| Finance storyboard | 9B | 4B (think=true) | Save 9B for harder tasks |

### 3.3 Self-Consistency Voting
- [ ] For critical outputs (finance numbers, factual claims): generate 3 responses from 0.8B
- [ ] If all 3 agree → high confidence, deliver
- [ ] If disagreement → escalate to 4B
- [ ] At 500 tok/s, 3 short responses from 0.8B takes <1s

---

## Priority 4: RAG and Context Optimization

### 4.1 Optimal Context Injection for Small Models
Research: Small models get lost in long contexts. Fewer, higher-quality chunks beat many chunks.
- [ ] Finance: inject only the relevant section (crypto OR stocks, not both unless asked)
- [ ] Sports: inject only the asked-about team, not all 5 teams
- [ ] News: inject top 5 items, not top 20
- [ ] Add a "context selector" that filters cached data based on the specific query

### 4.2 Context Window Budget
- [ ] System prompt: ~200 tokens
- [ ] Core memory (user profile): ~100 tokens
- [ ] Conversation history summary: ~200 tokens
- [ ] Domain data: ~500-1000 tokens (filtered)
- [ ] User query: ~50 tokens
- [ ] **Total prompt: ~1000-1500 tokens** — leaves plenty of room for generation
- [ ] Currently injecting full snapshots (2000+ tokens) — wasteful for small models

### 4.3 Structured Data Format
- [ ] Format injected data as concise key-value pairs, not prose
- [ ] `BTC: $70,730 (-5.3% 7d) | ETH: $1,842 (-3.1% 7d) | Fear: 11/100 (Extreme Fear)`
- [ ] Small models parse structured data more reliably than natural language tables

---

## Priority 5: Fine-Tuning (Medium Term)

### 5.1 Domain LoRA for 4B
- [ ] Use MLX-Tune (works on Mac Mini, Apple Silicon native)
- [ ] Fine-tune Qwen3.5-4B on Lumen-specific tasks:
  - Tool calling schema (function signatures for finance/sports/news agents)
  - Lumen personality (tone, response format, uncertainty expressions)
  - Domain-specific output formats
- [ ] **Data needed: 500-1,500 examples** (LoRA sweet spot for sub-7B)
- [ ] LoRA config: rank=16, alpha=16, target all projection layers

### 5.2 Collect Training Data
- [ ] Log all successful interactions (user query + final response + domain + model)
- [ ] Flag high-quality responses for training data
- [ ] Create synthetic examples for edge cases
- [ ] Build a curation UI or CLI to review/approve training pairs

### 5.3 Evaluation
- [ ] Hold out 10% of data for evaluation
- [ ] Measure: task success rate, response relevancy, hallucination rate
- [ ] A/B test: base 4B vs LoRA 4B on same queries

---

## Priority 6: Self-Verification

### 6.1 Cross-Model Verification
- [ ] Generate with 0.8B/4B → verify with 4B/9B
- [ ] Verification is cheaper than generation (shorter prompt, yes/no output)
- [ ] Already have `self_check()` in Ollama client — extend it

### 6.2 Constrained Decoding
Research: "Enables small models to perform comparably to much larger alternatives" on structured tasks.
- [ ] For tool calls / function calling: enforce JSON schema
- [ ] Ollama supports `format: "json"` — use it for all structured outputs
- [ ] Consider Outlines/XGrammar for stricter schema enforcement

### 6.3 Hallucination Mitigation Stack
1. **RAG grounding** (most effective) — already doing this with domain data injection
2. **Constrained decoding** — enforce structure
3. **Explicit refusal training** — fine-tune to say "I don't know" when appropriate
4. **Cross-model verification** — cheap model generates, better model checks
5. **Self-consistency** — 3 samples, majority vote on critical outputs

---

## Priority 7: Agentic Tool Use (Future)

### 7.1 ToolRAG Pattern
Research: Don't dump all tool descriptions into the prompt. Retrieve only relevant tools.
- [ ] Define tool schemas for each domain agent
- [ ] Use embedding similarity to select 2-3 relevant tools per query
- [ ] Small models hallucinate when given irrelevant tool descriptions

### 7.2 Function Calling Fine-Tune
Research: TinyAgent 1.1B achieved 78.89% function calling success with 40K LoRA examples.
- [ ] Define Lumen's function calling schema
- [ ] Generate training examples from existing successful interactions
- [ ] Fine-tune 4B to call domain agent functions directly
- [ ] This would allow: user says "watch NVDA" → 4B outputs `{"tool": "watchlist_add", "symbol": "NVDA"}`

---

## Key Numbers

| Metric | Value | Source |
|--------|-------|--------|
| MLX vs Ollama speedup | ~50% | arxiv 2601.19139 |
| Prefix caching TTFT speedup | 5.8x | arxiv 2601.19139 |
| Qwen3.5-4B MMLU-Pro | 79.1 | HuggingFace |
| Qwen3.5-9B MMLU-Pro | 82.5 | HuggingFace |
| Q4_K_M vs Q8 speed | ~1.6x faster | llama.cpp benchmarks |
| LoRA data sweet spot | 500-1,500 examples | Practitioner reports |
| TinyAgent 1.1B tool calling | 78.89% success | EMNLP 2024 |
| Self-consistency 3-sample cost at 500 tok/s | <1 second | Estimated |

---

## Implementation Order

| Phase | Items | Est. Effort |
|-------|-------|-------------|
| **Now** | 2.1-2.3 (thinking mode policy + sampling params), 4.1-4.3 (context optimization) | 1 session |
| **Next** | 3.1-3.3 (smarter routing + self-consistency), 6.1-6.2 (verification) | 1 session |
| **Then** | 1.1-1.3 (MLX evaluation + migration) | 1-2 sessions |
| **Later** | 5.1-5.3 (LoRA fine-tuning), 7.1-7.2 (agentic tool use) | 2-3 sessions |

---

*Sources: See HANDOFF.md session notes 2026-03-24 for full citation list.*
