# Plan B: Behavioral & Emotional Intelligence Engine

> Research-backed plan to make Lumen understand Tim's emotional state, communication
> patterns, and behavioral tendencies — ethically and locally.
> Sources: GoEmotions (ACL 2020), Kern et al. Big Five meta-analysis (2023), UMAP 2025, ACII 2024/2025.

---

## Architecture: 4-Layer Analysis Stack

```
Layer 1 — Always-on (instant, per-message):
  VADER sentiment + NRCLex emotions + style heuristics

Layer 2 — Fast classifier (~10-50ms, per-message):
  TinyBERT 6-emotion (93.5% accuracy, 14.5M params)
  all-MiniLM-L6-v2 embeddings (22M params)

Layer 3 — Conversation-level (async, per-conversation):
  Aggregate L1+L2 signals → profile update
  Drift detection vs baseline
  Short-term + long-term profile fusion

Layer 4 — On-demand (heavier, periodic):
  28-emotion GoEmotions classifier
  Big Five personality estimation
  Full profile regeneration with confidence scoring
```

---

## Phase 1: Upgrade Emotion Detection

Current state: VADER sentiment only (compound score, pos/neg/neu). No specific emotions.

### 1.1 Install TinyBERT Emotion Classifier
- [ ] Add `transformers` to requirements (likely already there for other uses)
- [ ] Download `AdamCodd/tinybert-emotion-balanced` (14.5M params, runs on CPU)
- [ ] 6 emotions: sadness, joy, love, anger, fear, surprise
- [ ] 93.5% accuracy — better than any LLM-based approach
- [ ] Target: <50ms per classification on CPU

### 1.2 Add NRCLex for Word-Level Emotion
- [ ] `pip install NRCLex` (27k word lexicon, instant lookup)
- [ ] 8 Plutchik emotions: anger, anticipation, disgust, fear, joy, sadness, surprise, trust
- [ ] Plus positive/negative sentiment
- [ ] Use for aggregate conversation-level emotion profiling

### 1.3 Integrate Into Router Pipeline
- [ ] After VADER sentiment (already in router), run TinyBERT emotion
- [ ] Store emotion label + confidence alongside sentiment in DB
- [ ] Add `detected_emotion` column to `mood` table
- [ ] Log: `[EMOTION] joy (0.92) | vader=0.65 | 12ms`

### 1.4 Add 28-Emotion Classifier (Layer 4, on-demand)
- [ ] Download `SamLowe/roberta-base-go_emotions` (~125M params)
- [ ] Multi-label: can detect curiosity + excitement simultaneously
- [ ] Run periodically (not per-message) for richer profiling
- [ ] Or trigger when TinyBERT confidence < 0.7

---

## Phase 2: Behavioral Feature Extraction

### 2.1 Per-Message Style Metrics
Extract these for every user message (cheap, rule-based):
- [ ] `word_count` — verbosity
- [ ] `sentence_count` — elaboration
- [ ] `avg_sentence_length` — complexity
- [ ] `question_marks` — curiosity/engagement
- [ ] `exclamation_marks` — intensity
- [ ] `pronoun_ratio_i` — self-focus (I/me/my/mine)
- [ ] `pronoun_ratio_we` — social orientation
- [ ] `pronoun_ratio_you` — other-directed
- [ ] `formality_score` — function word ratio (articles, prepositions)
- [ ] `emoji_count` — expressiveness
- [ ] `caps_ratio` — emphasis/intensity

### 2.2 Rolling Baselines
- [ ] Compute 7-day rolling average for each metric
- [ ] Flag deviations > 1 standard deviation as "notable"
- [ ] Example: Tim's avg message length is 12 words. Today it's 4 words → he's being terse → adapt

### 2.3 Engagement Signals
- [ ] Track time between user messages (response latency)
- [ ] Track follow-up questions (high engagement)
- [ ] Track conversation initiation patterns (who starts conversations, when)
- [ ] Track rephrase detection (user repeating question = frustration or misunderstanding)

---

## Phase 3: Longitudinal Profiling

### 3.1 Dual-Profile Architecture (UMAP 2025)
- [ ] **Short-term profile**: Last 7 days / 10 conversations
  - Captures current mood trends, recent interests, temporary patterns
  - High recency weight
- [ ] **Long-term profile**: All history with exponential decay
  - Captures stable traits: communication style, personality tendencies, consistent preferences
  - Decay rate: alpha = 0.02/day (half-life ~35 days)

### 3.2 Profile Fusion
- [ ] Embed both profiles using all-MiniLM-L6-v2
- [ ] Learn per-user attention weights (short vs long term)
  - Some users have rapidly changing interests → high short-term weight
  - Others are very stable → high long-term weight
- [ ] Start with equal weights (0.5/0.5), adjust based on observed variance

### 3.3 Confidence Scoring Per Trait
```
confidence = min(observation_count / threshold, 1.0) * consistency_score * recency_weight
```
- [ ] Only surface traits with confidence > 0.6
- [ ] Increment confidence on consistent observations, decay on contradictions
- [ ] Already have `confidence` and `evidence_count` columns in profile table — use them properly

### 3.4 Big Five Personality Estimation
Research: LIWC markers explain ~5% of personality variance. Useful directionally over many conversations.
- [ ] Track over weeks, not per-message
- [ ] Markers:
  - **Neuroticism**: More "I/me", more negative emotion words
  - **Agreeableness**: More positive emotion, fewer negatives
  - **Conscientiousness**: Avoids negations, discrepancy words ("should", "would")
  - **Openness**: Longer words, tentative language ("maybe", "perhaps")
  - **Extraversion**: More social words, positive emotion
- [ ] Store as low-confidence hypotheses (confidence 0.3-0.5) until 50+ conversations
- [ ] Use for broad tone calibration, not specific predictions

---

## Phase 4: Adaptive Response Behavior

### 4.1 Invisible Adaptations (research-validated as helpful)
- [ ] **Match verbosity**: Terse user → concise responses. Elaborate user → fuller answers.
- [ ] **Match formality**: Casual user → relaxed tone. Formal user → professional tone.
- [ ] **Reduce enthusiasm when user is stressed**: Fewer exclamations, more measured tone.
- [ ] **Offer structure when user shows decision fatigue**: Bullet points, clear options.
- [ ] **Speed up when user is in a hurry**: Shorter responses, skip niceties.

### 4.2 Never Do These (research says users find them creepy)
- [ ] DON'T explicitly name detected emotions ("I can see you're anxious")
- [ ] DON'T reference historical emotional data ("Last Tuesday you were also frustrated")
- [ ] DON'T use excessive synthetic empathy or mirroring
- [ ] DON'T do unsolicited mental health check-ins
- [ ] DON'T make dramatic tone shifts that call attention to the adaptation

### 4.3 Tone Adjustment Implementation
- [ ] Map detected mood to tone parameters:
  - `frustrated` → shorter responses, more direct, skip pleasantries
  - `curious` → more detail, offer follow-up suggestions
  - `happy/excited` → match energy, be enthusiastic
  - `neutral` → default personality
  - `sad` → gentle, don't force positivity, be available
- [ ] Inject tone guidance into system prompt: "The user seems [mood]. Adjust accordingly: [specific guidance]."
- [ ] Keep the adjustment subtle — 1-2 words changed in the system prompt, not a personality overhaul

---

## Phase 5: Ethical Safeguards

### 5.1 User Control
- [ ] "What do you know about me?" → dump full profile in readable format (already partially implemented)
- [ ] "Forget X" → delete specific traits (already implemented)
- [ ] "Stop tracking emotions" → granular toggle per profiling dimension
- [ ] "Show me my mood history" → time series visualization

### 5.2 What We Never Track
- [ ] Medical/health conditions or diagnoses
- [ ] Political beliefs (unless user explicitly discusses)
- [ ] Relationship conflicts or personal drama
- [ ] Financial stress indicators (beyond stated preferences)
- [ ] Anything that could be used as a psychiatric assessment

### 5.3 Crisis Detection (Separate from Profiling)
- [ ] Maintain keyword/phrase detector for crisis signals
- [ ] When triggered: surface helpline numbers, encourage professional support
- [ ] Do NOT store the disclosure in the behavioral profile
- [ ] Do NOT attempt to counsel or diagnose

### 5.4 Decay by Default
- [ ] All behavioral data decays unless explicitly pinned by the user
- [ ] Short-term profile: 7-day window, no persistence
- [ ] Long-term profile: exponential decay (alpha=0.02/day)
- [ ] Old mood data: compress after 90 days (condenser already does this)

---

## New Dependencies

```
# requirements.txt additions
transformers>=4.40
NRCLex>=4.0
sentence-transformers>=3.0
```

Models to download (one-time, ~500MB total):
- `AdamCodd/tinybert-emotion-balanced` (~30MB)
- `all-MiniLM-L6-v2` (~80MB)
- `SamLowe/roberta-base-go_emotions` (~500MB, optional Layer 4)

---

## Implementation Order

| Phase | Items | Est. Effort |
|-------|-------|-------------|
| **Now** | 1.1-1.3 (TinyBERT + NRCLex + router integration) | 1 session |
| **Next** | 2.1-2.3 (style metrics + baselines + engagement) | 1 session |
| **Then** | 3.1-3.3 (dual profiles + confidence scoring) | 1-2 sessions |
| **Then** | 4.1-4.3 (adaptive responses) | 1 session |
| **Later** | 3.4 (Big Five), 1.4 (28-emotion), 5.1-5.4 (ethics UI) | 2 sessions |

---

*Sources: See HANDOFF.md session notes 2026-03-24 for full citation list.*
