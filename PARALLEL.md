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

### Session B Notes

