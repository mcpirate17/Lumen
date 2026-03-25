# Plan A: World-Class Assistant UX

> Research-backed improvements to make Lumen a genuinely great voice assistant.
> Sources: CHI 2022/2025, Google Conversation Design, Ultravox latency research, Mem0, LiveKit.

---

## Priority 1: Fix Latency (Target <600ms TTFA)

Current state: ~2s for greetings, 4-12s for domain queries.
Research says: **<600ms time-to-first-audio** is the threshold for natural conversation feel. >1s stops feeling like a conversation.

### 1.1 Streaming Pipeline (not sequential)
- [ ] Overlap STT → LLM → TTS instead of waiting for each to complete
- [ ] Start TTS on the *first sentence* while the model is still generating the second
- [ ] Current implementation already does sentence-by-sentence SSE — verify TTS starts on first sentence arrival, not after full response

### 1.2 Prefix Caching
- [ ] Cache KV states for system prompts (same system prompt every time = wasted recomputation)
- [ ] Research shows **5.8x TTFT speedup** from prefix caching
- [ ] Ollama supports `keep_alive: -1` (already using for 0.8B) — investigate KV cache reuse across requests

### 1.3 Speculative Execution
- [ ] For queries classified as needing 4B+: start 0.8B generation immediately as a fast draft
- [ ] If 4B returns before 0.8B finishes speaking, seamlessly switch
- [ ] If 0.8B answer is sufficient (relevancy check passes), cancel the 4B request

### 1.4 Pre-warm Models
- [ ] Keep 0.8B AND 4B in memory permanently (not just 0.8B)
- [ ] 4B at Q8 = ~4GB RAM — Mac Mini has plenty
- [ ] Eliminates cold-start latency on first domain query

---

## Priority 2: Response Quality for Voice

Research: **Keep spoken responses under 35 words / 3 sentences.** Users disengage after 8-10 seconds of spoken output.

### 2.1 Ruthless Voice Editing
- [ ] Post-process LLM output before TTS: strip filler, compress to ≤35 words
- [ ] Use 0.8B as a "voice editor" — take the full response and compress it for speech
- [ ] Keep the full response in chat log, only speak the compressed version

### 2.2 Structured Response Format
- [ ] Train prompts to produce: **[Key Point]. [Supporting Detail]. [Next Step/Question].**
- [ ] 3-part structure maps perfectly to 3-sentence voice cap

### 2.3 Adaptive Length
- [ ] Short questions get short answers (mirror user verbosity)
- [ ] "What's bitcoin at?" → 1 sentence
- [ ] "Give me a market overview" → 3 sentences spoken, full brief in chat log

---

## Priority 3: Conversation Memory (Sliding Window + Summary)

Current: 8 raw messages from DB. No summarization. Context bleeds across topics.

### 3.1 Recursive Summarization (arXiv 2308.15022)
- [ ] As conversation grows, recursively summarize older turns
- [ ] Architecture: `[System Prompt] + [Condensed History Summary] + [Last 4 Raw Messages] + [User Query]`
- [ ] Use 2B model for summarization (fast, adequate for compression)
- [ ] Re-summarize every 8 turns (not every turn — too expensive)

### 3.2 Topic Segmentation
- [ ] Use all-MiniLM-L6-v2 embeddings (22M params, CPU) to detect topic boundaries
- [ ] Cosine similarity < 0.15 between consecutive messages = new topic
- [ ] Summarize completed topics, keep active topic in full
- [ ] Already referenced in VOICE-TODO.md — implement the cosine threshold approach

### 3.3 Core Memory (Letta/MemGPT pattern)
- [ ] **Always in context**: User name, timezone, teams, key preferences (from profile DB)
- [ ] **Recall memory**: Searchable conversation history via embeddings
- [ ] **Archival memory**: Long-term knowledge (profile condensation already does this)
- [ ] Inject core memory into every system prompt — makes Lumen feel like it *knows* Tim

---

## Priority 4: Proactive Intelligence (Inner Thoughts Framework)

Research: CHI 2025 "Inner Thoughts" framework preferred by users **82% of the time**.

### 4.1 Trigger System
- [ ] `on_new_message`: After processing, check if proactive suggestions are relevant
- [ ] `on_pause`: After 10+ seconds of silence, evaluate proactive candidates
- [ ] `on_schedule`: Time-based triggers (morning brief, game day alerts)

### 4.2 Thought Evaluation (8 heuristics, scored 1-5)
- [ ] Relevance: Does this relate to what the user is doing/discussing?
- [ ] Information Gap: Does the user likely not know this?
- [ ] Expected Impact: How useful would this be?
- [ ] Urgency: Is this time-sensitive?
- [ ] Coherence: Does this fit the conversation flow?
- [ ] Originality: Is this new information (not repeating)?
- [ ] Balance: Not too many suggestions in a row
- [ ] Dynamics: Is the user in a receptive state (not frustrated, not in flow)?

### 4.3 Delivery Rules
- [ ] Always explain *why* you're suggesting ("I noticed the Flyers play in 30 minutes...")
- [ ] Always offer dismiss ("Want updates?" not "I'll send updates")
- [ ] Never interrupt processing/thinking state
- [ ] Max 1 proactive suggestion per 10-minute window
- [ ] Track accept/dismiss to calibrate future suggestions

---

## Priority 5: Personality Consistency

### 5.1 Persona Definition
- [ ] Already have `config/personality.md` — review and strengthen
- [ ] Define 3-5 core traits: warm, knowledgeable, concise, dry humor, honest
- [ ] Create 10+ example dialogues showing personality across contexts (greeting, error, complex question, bad news, good news)
- [ ] Inject personality examples into system prompt

### 5.2 Uncertainty Calibration
Research: **Medium verbalized uncertainty** produces highest trust.
- [ ] When confident: speak directly, no hedging
- [ ] When uncertain: "I'm not sure, but..." — don't over-hedge
- [ ] When unknown: "I don't have that information" — clean admission
- [ ] Map model self-certainty check results to uncertainty language

### 5.3 Voice-Personality Alignment
- [ ] Switch to female voice (already in VOICE-TODO.md) — try `af_bella`, `af_sarah`
- [ ] Voice must match personality traits (warm, calm, not robotic)
- [ ] Test multiple voices with same content, pick the one that matches persona

---

## Priority 6: Turn-Taking and Repair

### 6.1 Semantic Endpointing
Current: Fixed silence threshold (VAD). Research: Semantic endpointing dramatically improves naturalness.
- [ ] Instead of fixed 500ms silence = end of turn, analyze *what* was said
- [ ] "The price of..." + pause → user isn't done (trailing preposition)
- [ ] "What time is the game" + pause → user is done (complete thought)
- [ ] Can use 0.8B for a quick "is this utterance complete?" classification

### 6.2 Self-Repair
Research: Self-repair significantly improves interaction quality.
- [ ] If guardrail fails or self-check fails, don't just escalate silently
- [ ] Say "Actually, let me check that again" before giving the corrected answer
- [ ] Users prefer transparent correction over silent re-generation

### 6.3 Graceful Barge-in
- [ ] When user interrupts: stop TTS within 200ms (already implemented)
- [ ] Capture the interruption text
- [ ] Generate new response incorporating context of what was being said when interrupted
- [ ] Currently barge-in is blocked during conversationLock — consider allowing it but only processing after current TTS stops

---

## Implementation Order

| Phase | Items | Est. Effort |
|-------|-------|-------------|
| **Now** | 1.4 (pre-warm 4B), 2.1-2.3 (voice editing), 5.3 (female voice) | 1 session |
| **Next** | 3.1-3.3 (conversation memory), 1.2 (prefix caching) | 1-2 sessions |
| **Then** | 4.1-4.3 (proactive intelligence), 5.1-5.2 (persona/uncertainty) | 1-2 sessions |
| **Later** | 6.1 (semantic endpointing), 1.3 (speculative execution), 6.2-6.3 (repair) | 2 sessions |

---

*Sources: See HANDOFF.md session notes 2026-03-24 for full citation list.*
