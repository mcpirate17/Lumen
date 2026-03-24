# Lumen Voice UX — Remaining Work

> Created 2026-03-22. Resume with "Let's work on the voice UX."

## Critical: Self-Talk Loop
The mic picks up Lumen's TTS output and feeds it back as user input, creating infinite loops.

**Current mitigations (may still be incomplete):**
- `conversationLock` blocks mic during entire request→response→speech cycle
- Echo detection via word overlap matching against recent Lumen speech
- Push-to-talk only (autoListen disabled)
- 800ms cooldown after speech ends before mic can activate

**If it still loops, investigate:**
- [ ] Is `recognition` somehow still running after `abort()`? Log `recognition.onstart` events
- [ ] Is Kokoro audio bleeding into mic? May need to use headphones or add AEC (echo cancellation)
- [ ] Does the barge-in `speechstart` handler fire on TTS playback? Disable barge-in entirely as test
- [ ] Add a hard lock: `recognition.abort()` inside `speakSentence()` before every TTS call
- [ ] Test with Web Speech TTS disabled and Kokoro only (or vice versa) to isolate
- [ ] Consider browser-level echo cancellation: `navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true}})`

## Latency Optimization
Current voice-to-voice times:
- Greeting (0.8B): ~450ms — **good**
- General (2B): ~2.5s — acceptable
- Domain (4B): ~3-6s — **needs work**

**Strategies to try:**
- [ ] Pre-warm 2B model alongside 0.8B (keep both in memory)
- [ ] Stream TTS while tokens are still generating (don't wait for full sentence)
- [ ] Use Kokoro audio caching for common ack phrases
- [ ] Measure time-to-first-token vs time-to-first-sentence — optimize the gap
- [ ] Consider speculative execution: start 2B generation immediately, cancel if 0.8B was sufficient

## Voice Quality
- [ ] Ack still sometimes gives mini-answers instead of backchannels (0.8B ignores "NEVER give an answer" instruction)
  - Fix: Use the JS instant phrases (rule-based) for acks instead of model-generated ones
  - Or: Fine-tune/few-shot the ack prompt more aggressively
- [ ] Kokoro TTS doesn't return `duration_ms` (old server running, not new one)
  - Fix: Either start the new `server/tts_server.py` or install kokoro-onnx in lumen venv
  - Current workaround: JS estimates duration from word count (~320ms/word)
- [ ] Pauses between sentences need tuning (currently 350-500ms based on punctuation)
- [ ] Response tone: sometimes too robotic or generic. Needs personality injection from SOUL file
- [ ] **Switch to female voice** — Jarvis used male (bm_george), Lumen should be female
  - Kokoro voices: try `af_bella`, `af_sarah`, `af_nicole`, or `af_sky`
  - Update `config/lumen.yaml` voice setting
  - Update `config/personality.md` to reflect female persona

## Conversation Context
- [ ] Implement sliding window + summary (Tim's idea: compact start, raw middle, summary end)
  - Use 2B for summarization of older history
  - Use 4B+ for actual thinking/reasoning
- [ ] Session boundary detection (topic drift via embeddings, cosine threshold 0.38)
- [ ] Skip history for greetings (partially done — `greeting_or_trivial` skips)

## Analytics (from research)
- [x] Session-level sentiment (rolling average, trend detection)
- [x] Response relevancy check (0.8B scores if answer addresses question)
- [ ] Track conversation relevancy score: relevant turns / total turns
- [ ] Track knowledge retention: does it remember earlier context correctly
- [ ] Topic drift detection via embeddings (paraphrase-MiniLM-L6-v2, 30ms per embed)
- [ ] Time-to-first-word metric in observe dashboard
- [ ] User satisfaction signals: rephrase detection, frustration keywords

## Key Research Numbers
| Metric | Target |
|--------|--------|
| Voice-to-voice latency | < 800ms (gold: < 500ms) |
| Max voice response | 3 sentences / 8-10 seconds |
| Turn gap | ~200ms natural |
| Barge-in stop | < 200ms |
| Post-speech cooldown | 300-500ms |
| Topic same | cosine > 0.38 |
| Topic new | cosine < 0.15 |

## Model Tier Architecture
| Model | Role | Keep Alive |
|-------|------|-----------|
| 0.8B | Always on. Instant acks, greetings, relevancy checks | Permanent (-1) |
| 2B | Summaries, condensing, simple tasks | 5 min |
| 4B | Domain queries, analysis, thinking | 5 min |
| 9B | Reserved for deep analysis (not currently used in default routing) | On demand |
