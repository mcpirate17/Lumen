// ══════════════════════════════════════════════════════════════════════════════
// LUMEN — Voice UI JavaScript
// Ported from Jarvis/OpenClaw. Uses HTTP POST + SSE instead of WebSocket.
// ══════════════════════════════════════════════════════════════════════════════

// ── CONFIG ───────────────────────────────────────────────────────────────────
const CFG = {
  serverUrl:  'http://127.0.0.1:3000',
  ttsPort:    5050,
  voice:      'Google UK English Female',
  ttsRate:    0.9,
  ttsPitch:   0.85,
  autoListen: false,
};

// ── STATE ────────────────────────────────────────────────────────────────────
let serverOnline = false;
let listening = false;
let speaking = false;
let recognition = null;
let speechSupported = false;
let currentState = 'idle'; // idle | listening | processing | speaking | working | error
let isWorking = false;
let micMuted = false;
let micMuteTimer = null;
let asleep = false;
let orbClickTimer = null;

// ══════════════════════════════════════════════════════════════════════════════
// CLOCK
// ══════════════════════════════════════════════════════════════════════════════
function updateClock() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2,'0');
  const mm = String(now.getMinutes()).padStart(2,'0');
  const ss = String(now.getSeconds()).padStart(2,'0');
  document.getElementById('clock').textContent = `${hh}:${mm}:${ss}`;
  const days = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  document.getElementById('date-display').textContent =
    `${days[now.getDay()]} \u00B7 ${String(now.getDate()).padStart(2,'0')} ${months[now.getMonth()]} ${now.getFullYear()}`;
}
updateClock();
setInterval(updateClock, 1000);

// ══════════════════════════════════════════════════════════════════════════════
// CANVAS BACKGROUND (particles + grid lines)
// ══════════════════════════════════════════════════════════════════════════════
(function initCanvas() {
  const canvas = document.getElementById('bg-canvas');
  const ctx = canvas.getContext('2d');
  let W, H, particles = [], lines = [];

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  for (let i = 0; i < 80; i++) {
    particles.push({
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.5 + 0.3,
      a: Math.random() * 0.4 + 0.1,
    });
  }

  const gridSpacing = 80;
  function buildGrid() {
    lines = [];
    for (let x = 0; x < window.innerWidth; x += gridSpacing) {
      lines.push({ x1: x, y1: 0, x2: x, y2: window.innerHeight });
    }
    for (let y = 0; y < window.innerHeight; y += gridSpacing) {
      lines.push({ x1: 0, y1: y, x2: window.innerWidth, y2: y });
    }
  }
  buildGrid();
  window.addEventListener('resize', buildGrid);

  let t = 0;
  function draw() {
    ctx.clearRect(0, 0, W, H);
    t += 0.005;

    ctx.strokeStyle = 'rgba(0,100,160,0.04)';
    ctx.lineWidth = 1;
    for (const l of lines) {
      ctx.beginPath();
      ctx.moveTo(l.x1, l.y1);
      ctx.lineTo(l.x2, l.y2);
      ctx.stroke();
    }

    for (const p of particles) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
      if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(0,212,255,${p.a * (0.6 + 0.4 * Math.sin(t + p.x))})`;
      ctx.fill();
    }

    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 100) {
          ctx.beginPath();
          ctx.strokeStyle = `rgba(0,180,255,${0.06 * (1 - dist/100)})`;
          ctx.lineWidth = 0.5;
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

// ══════════════════════════════════════════════════════════════════════════════
// SIDE BARS
// ══════════════════════════════════════════════════════════════════════════════
(function buildSideBars() {
  const leftEl  = document.getElementById('side-left');
  const rightEl = document.getElementById('side-right');
  const widths = [40, 28, 18, 34, 12, 22, 38, 16, 26, 14];
  const delays = [0, 0.5, 1.2, 0.3, 0.8, 1.5, 0.1, 0.9, 1.1, 0.6];
  widths.forEach((w, i) => {
    ['left','right'].forEach(side => {
      const bar = document.createElement('div');
      bar.className = 'side-bar';
      bar.style.setProperty('--w', w + 'px');
      bar.style.width = w + 'px';
      bar.style.animationDelay = delays[i] + 's';
      bar.style.animationDuration = (2 + i * 0.3) + 's';
      (side === 'left' ? leftEl : rightEl).appendChild(bar);
    });
  });
})();

// ══════════════════════════════════════════════════════════════════════════════
// STATE MACHINE
// ══════════════════════════════════════════════════════════════════════════════
const statusLabels = {
  idle:       'LUMEN ONLINE',
  listening:  'LISTENING...',
  processing: 'PROCESSING...',
  speaking:   'SPEAKING...',
  working:    'WORKING \u2014 STAND BY...',
  error:      'SERVER OFFLINE',
  offline:    'OFFLINE \u2014 RETRYING...',
};

function muteMicFor(ms) {
  micMuted = true;
  if (listening) stopListening();
  clearTimeout(micMuteTimer);
  micMuteTimer = setTimeout(() => {
    micMuted = false;
    if (CFG.autoListen && shouldAutoListen()) startListening();
  }, ms);
}

function setState(state) {
  currentState = state;
  const orbWrap = document.getElementById('orb-wrap');
  const statusEl = document.getElementById('status-text');

  orbWrap.className = '';
  statusEl.className = '';

  if (state === 'listening')  { orbWrap.classList.add('listening');  statusEl.classList.add('listening'); }
  if (state === 'processing') { orbWrap.classList.add('processing'); statusEl.classList.add('processing'); }
  if (state === 'speaking')   { orbWrap.classList.add('speaking');   statusEl.classList.add('speaking'); }
  if (state === 'working')    { orbWrap.classList.add('working');    statusEl.classList.add('processing'); }
  if (state === 'error' || state === 'offline') { statusEl.classList.add('error'); }

  statusEl.textContent = statusLabels[state] || 'LUMEN ONLINE';
  updateOrbState(state);
}

// ══════════════════════════════════════════════════════════════════════════════
// TOAST
// ══════════════════════════════════════════════════════════════════════════════
let toastTimer = null;
function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = type + ' visible';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 3000);
}

// ══════════════════════════════════════════════════════════════════════════════
// CHAT LOG
// ══════════════════════════════════════════════════════════════════════════════
const MAX_MESSAGES = 6;
let messages = [];

function normalizeMsg(text) {
  return text.trim().toLowerCase().replace(/\s+/g, ' ');
}

const recentMessages = new Map();

function addMessage(role, text, badge) {
  if (!text || !text.trim()) return;
  const norm = normalizeMsg(text);

  const last = messages[messages.length - 1];
  if (last && last.role === role && normalizeMsg(last.text) === norm) return;
  if (last && last.role === role && normalizeMsg(last.text).includes(norm)) return;

  const key = role + ':' + norm.slice(0, 60);
  const now = Date.now();
  if (recentMessages.has(key) && now - recentMessages.get(key) < 8000) return;
  recentMessages.set(key, now);
  if (recentMessages.size > 30) {
    for (const [k, t] of recentMessages) {
      if (now - t > 15000) recentMessages.delete(k);
    }
  }

  messages.push({ role, text, badge: badge || null });
  renderChat();
}

function renderChat() {
  const log = document.getElementById('chat-log');
  log.innerHTML = '';
  const recent = messages.slice(-MAX_MESSAGES);
  recent.forEach((m, i) => {
    const el = document.createElement('div');
    el.className = `chat-msg ${m.role}`;
    const age = recent.length - 1 - i;
    if (age >= 4) el.classList.add('older');
    else if (age >= 2) el.classList.add('old');
    if (m.badge) {
      const badge = document.createElement('span');
      badge.className = `msg-badge ${m.badge}`;
      badge.textContent = m.badge === 'ack' ? 'ACK' : m.badge.toUpperCase();
      el.appendChild(badge);
    }
    const display = m.text.length > 160 ? m.text.slice(0, 157) + '\u2026' : m.text;
    el.appendChild(document.createTextNode(display));
    log.appendChild(el);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// MODEL INDICATOR
// ══════════════════════════════════════════════════════════════════════════════
let modelIndicatorTimer = null;

function setModelIndicator(model) {
  const el = document.getElementById('model-indicator');
  clearTimeout(modelIndicatorTimer);
  if (model === 'claude') {
    el.textContent = '\u25C6 CLAUDE \u00B7 API';
    el.className = 'claude';
  } else if (model && model.includes('9b')) {
    el.textContent = '\u25C8 QWEN 9B \u00B7 LOCAL';
    el.className = 'qwen';
  } else if (model && model.includes('4b')) {
    el.textContent = '\u25C8 QWEN 4B \u00B7 LOCAL';
    el.className = 'qwen';
  } else if (model && model.includes('2b')) {
    el.textContent = '\u25C8 QWEN 2B \u00B7 LOCAL';
    el.className = 'qwen';
  } else {
    el.textContent = 'LUMEN';
    el.className = '';
  }
  modelIndicatorTimer = setTimeout(() => {
    el.textContent = 'LUMEN';
    el.className = '';
  }, 6000);
}

// ══════════════════════════════════════════════════════════════════════════════
// INSTANT ACKNOWLEDGMENT PHRASES (Tier 1 — rule-based, no model needed)
// ══════════════════════════════════════════════════════════════════════════════
const INSTANT_PHRASES = [
  [/\b(score|game|match|eagles|phillies|sixers|flyers|union|nfl|nba|nhl|mlb|philly)\b/i,
    ["Checking scores now", "Pulling that up", "On it"]],
  [/\b(stock|market|crypto|bitcoin|btc|eth|nasdaq|dow|sp500|price|ticker)\b/i,
    ["Pulling market data", "Checking the numbers", "On it"]],
  [/\b(news|latest|headline|today|update|breaking)\b/i,
    ["Scanning the latest", "Checking that now", "One moment"]],
  [/\b(weather|forecast|rain|temperature|sunny|cold|hot)\b/i,
    ["Checking the forecast", "Looking that up"]],
  [/\b(think|analyze|plan|strategy|should|recommend|opinion|advice)\b/i,
    ["Let me think through that", "Good question, give me a sec", "Thinking on that"]],
  [/\b(remind|timer|schedule|set a|add a|calendar)\b/i,
    ["Got it", "Consider it done", "On it"]],
  [/\b(who is|what is|what's|when is|where is|how do|how does)\b/i,
    ["One moment", "Let me check", "On it"]],
  [/.*/, ["Give me a moment", "On it", "Let me look into that"]]
];

function getInstantPhrase(transcript) {
  for (const [pattern, phrases] of INSTANT_PHRASES) {
    if (pattern.test(transcript)) {
      return phrases[Math.floor(Math.random() * phrases.length)];
    }
  }
  return "On it";
}

// ══════════════════════════════════════════════════════════════════════════════
// CHAT PIPELINE — SSE streaming with progressive sentence speech
//
// Flow:
//   1. Speak instant phrase immediately (fills model startup latency ~2-5s)
//   2. Simultaneously fire SSE request to /api/chat/stream
//   3. As sentences arrive from the stream, queue them for speech
//   4. First real sentence interrupts/replaces the instant phrase
//   5. Result: user hears ack within 100ms, real content within 3-5s
// ══════════════════════════════════════════════════════════════════════════════

// Track whether we've received real content (to know when to replace instant ack)
let streamReceivedContent = false;
let streamSentenceCount = 0;

// Lock that prevents ANY listening during the entire request-response cycle
let conversationLock = false;

async function handleTranscript(transcript) {
  streamReceivedContent = false;
  streamSentenceCount = 0;
  const t0 = performance.now();

  // LOCK: No mic activity until this entire cycle completes
  conversationLock = true;
  if (listening) stopListening();
  try { recognition.abort(); } catch(e) {}

  // Don't speak an instant phrase — 0.8B responds in ~300ms which is fast enough.
  // Just show processing state. The first real sentence will be the first spoken content.
  setState('processing');

  // Fire SSE request in parallel with the instant phrase
  try {
    const url = `${CFG.serverUrl}/api/chat/stream?message=${encodeURIComponent(transcript)}`;
    const resp = await fetch(url);

    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    let model = null;
    let ackReceived = false;
    let lineBuffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      lineBuffer += decoder.decode(value, { stream: true });

      // Process complete lines
      const lines = lineBuffer.split('\n');
      lineBuffer = lines.pop(); // keep incomplete last line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch(e) { continue; }

        if (data.type === 'classify') {
          console.log(`[Stream] classify: ${data.route} ${data.domain} (${data.reason})`);

        } else if (data.type === 'sentiment') {
          console.log(`[Stream] sentiment: ${data.mood} (${data.compound})`);

        } else if (data.type === 'ack') {
          // Server ack from 2B model — only use if instant phrase already done
          ackReceived = true;
          if (!streamReceivedContent) {
            addMessage('assistant', data.text, 'ack');
            // Don't speak the server ack — instant phrase is already playing
            console.log(`[Stream] ack: "${data.text}" (not spoken, instant phrase active)`);
          }

        } else if (data.type === 'sentence') {
          // A complete sentence from the model — speak it progressively
          streamReceivedContent = true;
          streamSentenceCount++;
          const sentence = data.text;
          fullText += (fullText ? ' ' : '') + sentence;

          console.log(`[Stream] sentence #${data.index}: "${sentence}" (${(performance.now()-t0).toFixed(0)}ms)`);

          // Queue this sentence for speech
          ttsQueue.push(sentence);
          if (!ttsBusy) processTTSQueue();

        } else if (data.type === 'suggestion') {
          // Proactive suggestion from Lumen
          console.log(`[Stream] suggestion: "${data.text}" (${data.category})`);
          showSuggestion(data.text, data.action, data.reason);

        } else if (data.type === 'done') {
          model = data.model;
          if (data.full_text) fullText = data.full_text;
          const spec = data.speculative ? ' [speculative]' : '';
          console.log(`[Stream] done: model=${model} latency=${data.latency_ms}ms guard=${data.guardrail_safe} trace=#${data.trace_id}${spec}`);
        }
      }
    }

    // Show full response in chat log
    if (fullText) {
      const badge = model && model.includes('claude') ? 'claude' : 'qwen';
      addMessage('assistant', fullText, badge);
      setModelIndicator(model || 'qwen');

      // If no sentences were streamed (e.g. very short response), speak the full text
      if (streamSentenceCount === 0) {
        speakText(fullText);
      }
    }

    const elapsed = (performance.now() - t0).toFixed(0);
    console.log(`[Stream] total pipeline: ${elapsed}ms, ${streamSentenceCount} sentences streamed`);

    // Wait for TTS to finish before unlocking
    const waitForSpeech = () => new Promise(resolve => {
      const check = () => {
        if (!ttsBusy && !speaking) resolve();
        else setTimeout(check, 200);
      };
      check();
    });
    await waitForSpeech();

    // UNLOCK after cooldown
    setTimeout(() => {
      conversationLock = false;
      console.log('[Lock] Conversation lock released');
    }, SPEECH_COOLDOWN_MS);

  } catch(err) {
    console.error('[Lumen] Stream failed:', err.message);
    showToast('SERVER UNAVAILABLE', 'red');
    setState('error');

    // Fallback: try Ollama directly with /api/chat (think=false)
    try {
      const resp = await fetch('http://127.0.0.1:11434/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'qwen3.5:4b',
          messages: [
            { role: 'system', content: 'You are Lumen, a concise voice assistant. Answer briefly in 1-3 sentences.' },
            { role: 'user', content: transcript }
          ],
          stream: false,
          think: false,
          options: { temperature: 0.3, num_predict: 512 }
        })
      });
      const data = await resp.json();
      const text = data.message?.content || '';
      if (text) {
        addMessage('assistant', text, 'qwen');
        setModelIndicator('qwen:4b');
        speakText(text);
      }
    } catch(e2) {
      speakText("I'm having trouble connecting. Please check the server.");
      addMessage('assistant', "Connection error. Server and Ollama both unreachable.", null);
    }
    // UNLOCK on error path too
    setTimeout(() => { conversationLock = false; }, SPEECH_COOLDOWN_MS);
    }
  }
}

function sendChat(text) {
  addMessage('user', text);
  handleTranscript(text);
}

// ══════════════════════════════════════════════════════════════════════════════
// SERVER HEALTH CHECK
// ══════════════════════════════════════════════════════════════════════════════
async function checkHealth() {
  try {
    const resp = await fetch(`${CFG.serverUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
    if (resp.ok) {
      const data = await resp.json();
      serverOnline = true;
      setConnStat('ONLINE', 'ok');
      if (currentState === 'error' || currentState === 'offline') {
        setState('idle');
        showToast('SERVER CONNECTED', '');
      }

      // Update Kokoro status
      kokoroAvailable = data.kokoro !== false;
      return;
    }
  } catch(e) {}

  // Server unreachable — check if Ollama is at least up
  try {
    const resp = await fetch('http://127.0.0.1:11434/api/tags', { signal: AbortSignal.timeout(2000) });
    if (resp.ok) {
      serverOnline = false;
      setConnStat('DIRECT', 'warn');
      if (currentState === 'error') setState('idle');
      return;
    }
  } catch(e) {}

  serverOnline = false;
  setConnStat('OFFLINE', 'bad');
}

function setConnStat(label, cls) {
  const el = document.getElementById('conn-stat');
  el.textContent = label;
  el.className = 'stat-value ' + cls;
}

setInterval(checkHealth, 30000);
setTimeout(checkHealth, 800);

// ══════════════════════════════════════════════════════════════════════════════
// DATA FEED STATUS — polls /api/cache/status to show refresh indicators
// ══════════════════════════════════════════════════════════════════════════════
const FEED_STALE_THRESHOLD = {
  finance: 700,   // 11+ min = stale (refresh is 10m)
  sports: 1000,   // 16+ min = stale (refresh is 15m, or 2m during games)
  news: 1000,     // 16+ min = stale
};

function updateFeedStatus(data) {
  for (const [name, info] of Object.entries(data)) {
    if (name === 'game_live') continue;
    const el = document.getElementById(`feed-${name}`);
    if (!el) continue;

    el.className = 'feed-block';
    if (info.state === 'refreshing') {
      el.classList.add('refreshing');
    } else if (info.state === 'error') {
      el.classList.add('error');
    } else if (!info.has_data) {
      // No data yet (startup)
    } else if (info.age_seconds > (FEED_STALE_THRESHOLD[name] || 900)) {
      el.classList.add('stale');
    } else {
      el.classList.add('ready');
    }
  }

  // If a game is live, highlight the sports indicator
  if (data.game_live) {
    const sptLabel = document.querySelector('#feed-sports .feed-label');
    if (sptLabel) sptLabel.textContent = 'SPT LIVE';
  } else {
    const sptLabel = document.querySelector('#feed-sports .feed-label');
    if (sptLabel) sptLabel.textContent = 'SPT';
  }
}

async function pollCacheStatus() {
  try {
    const resp = await fetch(`${CFG.serverUrl}/api/cache/status`, { signal: AbortSignal.timeout(3000) });
    if (resp.ok) {
      const data = await resp.json();
      updateFeedStatus(data);
    }
  } catch(e) {
    // Server down — grey out all feeds
    for (const name of ['finance', 'sports', 'news']) {
      const el = document.getElementById(`feed-${name}`);
      if (el) el.className = 'feed-block';
    }
  }
}

setInterval(pollCacheStatus, 5000);
setTimeout(pollCacheStatus, 2000);

// ══════════════════════════════════════════════════════════════════════════════
// VOICE OUTPUT (TTS) — Kokoro on :5050 with Web Speech fallback
// ══════════════════════════════════════════════════════════════════════════════
let ttsVoice = null;
let kokoroAvailable = true;
let ttsQueue = [];
let ttsBusy = false;
let lastSpokenText = '';
let lastSpokenTime = 0;
let ttsStartTime = 0;

// Compress text for voice output: max 35 words, 3 sentences.
// Research: users disengage after 8-10 seconds / 35 words of spoken output.
// Full text stays in chat log — this only affects what gets spoken.
const MAX_VOICE_WORDS = 35;

function truncateForVoice(text) {
  const sentences = text.match(/[^.!?]+[.!?]*/g)
    ?.map(s => s.trim()).filter(s => s.length > 2) || [text];

  let result = [];
  let wordCount = 0;
  for (const s of sentences) {
    const words = s.trim().split(/\s+/).length;
    if (wordCount + words > MAX_VOICE_WORDS && result.length > 0) break;
    result.push(s);
    wordCount += words;
    if (result.length >= 3) break; // hard cap at 3 sentences
  }
  return result.join(' ');
}

// Inter-sentence pause duration based on ending punctuation
function getPauseMsAfterSentence(sentence) {
  const trimmed = sentence.trim();
  if (trimmed.endsWith('?')) return 500;
  if (trimmed.endsWith('!')) return 350;
  if (trimmed.endsWith(',')) return 200;
  if (trimmed.endsWith(':')) return 300;
  return 420;
}

// Load Web Speech API voices for fallback when Kokoro is unavailable
function loadVoices() {
  const voices = speechSynthesis.getVoices();
  if (!voices.length) return;
  const preferred = CFG.voice.toLowerCase();
  let match = voices.find(v => v.name.toLowerCase().includes(preferred));
  if (!match) match = voices.find(v => v.name.toLowerCase().includes('male'));
  if (!match) match = voices.find(v => v.lang === 'en-GB');
  if (!match) match = voices.find(v => v.lang.startsWith('en'));
  if (!match) match = voices[0];
  ttsVoice = match;
  updateVoiceStatus();
}

function updateVoiceStatus() {
  const el = document.getElementById('voice-stat');
  if (kokoroAvailable) {
    el.textContent = 'KOKORO';
    el.className = 'stat-value ok';
  } else if (ttsVoice) {
    el.textContent = ttsVoice.name.slice(0, 14).toUpperCase();
    el.className = 'stat-value warn';
  } else {
    el.textContent = 'UNAVAIL';
    el.className = 'stat-value bad';
  }
}

speechSynthesis.addEventListener('voiceschanged', loadVoices);
loadVoices();

// Estimate speech duration from text when server doesn't provide it.
// Average English speech: ~150 words/min = 2.5 words/sec = ~400ms/word.
// Kokoro speaks slightly faster, so use ~320ms/word.
function estimateSpeechDuration(text) {
  const words = text.trim().split(/\s+/).length;
  return Math.max(600, words * 320);
}

// Speak a single sentence via Kokoro server
async function speakViaKokoro(sentence) {
  const resp = await fetch(`http://127.0.0.1:${CFG.ttsPort}/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: sentence })
  });
  if (!resp.ok) throw new Error(`TTS server ${resp.status}`);
  const data = await resp.json();
  // Use server duration if available, otherwise estimate from word count
  const durationMs = data.duration_ms || estimateSpeechDuration(sentence);
  await new Promise(r => setTimeout(r, durationMs + 150));
}

// Speak a single sentence via Web Speech API (fallback)
function speakViaWebSpeech(sentence) {
  return new Promise((resolve, reject) => {
    if (!ttsVoice) { reject(new Error('No voice')); return; }
    const utterance = new SpeechSynthesisUtterance(sentence);
    utterance.voice = ttsVoice;
    utterance.rate = CFG.ttsRate;
    utterance.pitch = CFG.ttsPitch;
    utterance.onend = resolve;
    utterance.onerror = reject;
    speechSynthesis.speak(utterance);
  });
}

// Speak a sentence — try Kokoro first, fall back to Web Speech
async function speakSentence(sentence) {
  if (kokoroAvailable) {
    try {
      await speakViaKokoro(sentence);
      return;
    } catch(e) {
      console.warn('Kokoro failed, switching to Web Speech:', e.message);
      kokoroAvailable = false;
      updateVoiceStatus();
    }
  }
  // Web Speech fallback
  try {
    await speakViaWebSpeech(sentence);
  } catch(e) {
    console.warn('Web Speech fallback failed:', e.message);
  }
}

async function processTTSQueue() {
  if (ttsBusy) return;
  ttsBusy = true;
  speaking = true;
  ttsStartTime = Date.now();

  // HARD kill mic before speaking — prevent echo pickup
  if (listening) stopListening();
  try { recognition.abort(); } catch(e) {}

  setState('speaking');
  while (ttsQueue.length > 0) {
    const sentence = ttsQueue.shift();
    trackLumenSpeech(sentence); // record for echo detection
    await speakSentence(sentence);
    if (asleep) break;
    if (ttsQueue.length > 0) {
      const pauseMs = getPauseMsAfterSentence(sentence);
      await new Promise(r => setTimeout(r, pauseMs));
    }
  }
  ttsQueue = [];
  speaking = false;
  lastSpeechEndTime = Date.now(); // start cooldown timer
  setState('idle');
  ttsBusy = false;
  // Don't auto-listen — user must click orb or press spacebar
}

async function speakText(text) {
  if (!text) return;
  const truncated = truncateForVoice(text);
  if (!truncated) return;

  // Dedup: don't speak same thing twice within 10 seconds
  const normalized = truncated.trim().toLowerCase().replace(/\s+/g, ' ');
  const now = Date.now();
  if (normalized === lastSpokenText && now - lastSpokenTime < 10000) return;
  lastSpokenText = normalized;
  lastSpokenTime = now;

  // Hard-kill mic to prevent echo
  if (listening) stopListening();
  try { recognition.abort(); } catch(e) {}

  // Cancel any in-progress speech
  ttsQueue = [];
  speechSynthesis.cancel();
  if (kokoroAvailable) {
    fetch(`http://127.0.0.1:${CFG.ttsPort}/stop`, { method: 'POST' }).catch(() => {});
  }
  ttsStartTime = Date.now();

  // Split into sentences for streaming playback
  const sentences = truncated.match(/[^.!?]+[.!?]*/g)
    ?.map(s => s.trim()).filter(s => s.length > 2) || [truncated];
  ttsQueue = sentences;
  processTTSQueue();
}

// ══════════════════════════════════════════════════════════════════════════════
// ECHO PREVENTION & SMART LISTEN
// Research: 300-500ms cooldown after speech ends before mic activates
// ══════════════════════════════════════════════════════════════════════════════
const SPEECH_COOLDOWN_MS = 800; // wait after TTS finishes before allowing mic
let lastSpeechEndTime = 0;      // when TTS last finished
let recentLumenTexts = [];       // track what Lumen recently said for echo detection

// Record what Lumen says so we can detect echo
function trackLumenSpeech(text) {
  const normalized = text.trim().toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ');
  recentLumenTexts.push({ text: normalized, time: Date.now() });
  // Keep only last 30 seconds of speech
  const cutoff = Date.now() - 30000;
  recentLumenTexts = recentLumenTexts.filter(t => t.time > cutoff);
}

// Check if a transcript looks like echo of Lumen's own speech
function isEcho(transcript) {
  const normalized = transcript.trim().toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ');
  if (normalized.length < 3) return true; // too short, likely noise

  for (const lt of recentLumenTexts) {
    // Check if transcript is a substring of what Lumen said (or vice versa)
    if (lt.text.includes(normalized) || normalized.includes(lt.text)) {
      console.log(`[Echo] Rejected: "${transcript}" matches Lumen speech: "${lt.text}"`);
      return true;
    }
    // Check word overlap — if >60% of words match, it's echo
    const userWords = new Set(normalized.split(' '));
    const lumenWords = new Set(lt.text.split(' '));
    const overlap = [...userWords].filter(w => lumenWords.has(w) && w.length > 2).length;
    const overlapRatio = overlap / Math.max(userWords.size, 1);
    if (overlapRatio > 0.6 && userWords.size > 2) {
      console.log(`[Echo] Rejected: "${transcript}" has ${(overlapRatio*100).toFixed(0)}% word overlap with Lumen speech`);
      return true;
    }
  }
  return false;
}

function shouldAutoListen() {
  const timeSinceSpeech = Date.now() - lastSpeechEndTime;
  if (timeSinceSpeech < SPEECH_COOLDOWN_MS) return false;
  if (ttsBusy)     return false;
  if (speaking)    return false;
  if (isWorking)   return false;
  if (micMuted)    return false;
  if (asleep)      return false;
  return true;
}

// Backchannel system removed — it fired TTS mid-processing, contributing to echo loops.
// Stub functions kept for compatibility with speech recognition event handlers.
function startBackchannelTimer() {}
function clearBackchannelTimer() {}

// ══════════════════════════════════════════════════════════════════════════════
// PROACTIVE SUGGESTIONS
// Shows a subtle notification when Lumen has a proactive suggestion.
// User can accept (click/say yes) or dismiss (click X / ignore).
// ══════════════════════════════════════════════════════════════════════════════
let suggestionTimer = null;

function showSuggestion(text, action, reason) {
  // Don't show if speaking or processing
  if (speaking || ttsBusy || currentState === 'processing') return;

  const el = document.getElementById('toast');
  const display = action ? `${text} ${action}` : text;
  el.textContent = display;
  el.className = 'suggestion visible';
  clearTimeout(suggestionTimer);

  // Auto-dismiss after 15 seconds
  suggestionTimer = setTimeout(() => {
    el.classList.remove('visible');
  }, 15000);

  // Add to chat log as a suggestion
  addMessage('assistant', `\u2728 ${text}${action ? ' ' + action : ''}`, 'suggestion');
  console.log(`[Proactive] Showing: "${display}" (reason: ${reason})`);
}

// ══════════════════════════════════════════════════════════════════════════════
// VOICE INPUT (Speech Recognition)
// ══════════════════════════════════════════════════════════════════════════════
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
  speechSupported = true;
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  recognition.onresult = (evt) => {
    let interim = '', final = '';
    for (let i = evt.resultIndex; i < evt.results.length; i++) {
      const t = evt.results[i][0].transcript;
      if (evt.results[i].isFinal) final += t;
      else interim += t;
    }
    const transcriptEl = document.getElementById('transcript');
    if (interim || final) {
      transcriptEl.textContent = interim || final;
      transcriptEl.classList.add('visible');
    }
    if (final) {
      clearBackchannelTimer();
      transcriptEl.classList.remove('visible');
      const text = final.trim();

      // Echo prevention: reject if it sounds like Lumen's own speech
      if (isEcho(text)) {
        console.log(`[Echo] Dropped transcript: "${text}"`);
        stopListening();
        return;
      }

      // Reject if speech happened during or right after TTS playback
      if (speaking || ttsBusy || (Date.now() - lastSpeechEndTime < SPEECH_COOLDOWN_MS)) {
        console.log(`[Echo] Dropped transcript during/after speech: "${text}"`);
        stopListening();
        return;
      }

      sendChat(text);
      stopListening();
    }
  };

  recognition.onerror = (evt) => {
    console.error('Speech error:', evt.error);
    clearBackchannelTimer();
    stopListening();
    if (evt.error !== 'aborted') showToast('VOICE ERROR: ' + evt.error.toUpperCase(), 'red');
  };

  recognition.onend = () => {
    clearBackchannelTimer();
    if (listening) stopListening();
  };

  // Barge-in: user starts speaking while Lumen is talking
  // ONLY allow barge-in when conversation lock is OFF (prevents self-talk loop)
  recognition.addEventListener('speechstart', () => {
    if (conversationLock) {
      // During active conversation cycle, ignore all speech events — it's echo
      console.log('[Barge-in] Blocked — conversation lock active (likely echo)');
      return;
    }
    if (speaking || ttsBusy) {
      ttsQueue = [];
      ttsBusy = false;
      speaking = false;
      fetch(`http://127.0.0.1:${CFG.ttsPort}/stop`, { method: 'POST' }).catch(() => {});
      setState('listening');
    }
  });

  document.getElementById('voice-stat').textContent = 'READY';
  document.getElementById('voice-stat').className   = 'stat-value ok';
} else {
  document.getElementById('text-input-wrap').classList.add('visible');
  document.getElementById('voice-stat').textContent = 'UNAVAIL';
  document.getElementById('voice-stat').className   = 'stat-value warn';
  showToast('VOICE UNAVAILABLE \u2014 TEXT MODE', 'gold');
}

function startListening() {
  if (!speechSupported || listening || isWorking || micMuted) return;
  // HARD BLOCK: no mic during entire conversation cycle
  if (conversationLock) {
    console.log('[Mic] Blocked — conversation lock active');
    return;
  }
  if (speaking || ttsBusy) return;
  if (Date.now() - lastSpeechEndTime < SPEECH_COOLDOWN_MS) {
    console.log('[Mic] Blocked — post-speech cooldown');
    return;
  }
  listening = true;
  setState('listening');
  document.getElementById('transcript').textContent = '';
  try {
    recognition.start();
    startBackchannelTimer();
  } catch(e) { stopListening(); }
}

function stopListening() {
  listening = false;
  if (currentState === 'listening') setState('idle');
  document.getElementById('transcript').classList.remove('visible');
  try { recognition.abort(); } catch(e) {}
}

// ══════════════════════════════════════════════════════════════════════════════
// ORB CLICK — single tap = listen/stop, double tap = sleep/wake
// ══════════════════════════════════════════════════════════════════════════════
function sleep() {
  asleep = true;
  ttsQueue = [];
  if (listening) stopListening();
  speechSynthesis.cancel();
  fetch(`http://127.0.0.1:${CFG.ttsPort}/stop`, { method: 'POST' }).catch(() => {});
  setState('idle');
  updateOrbState('standby');
  document.getElementById('status-text').textContent = 'STANDBY';
  showToast('LUMEN STANDBY \u2014 TAP TO WAKE', '');
}

function wake() {
  asleep = false;
  updateOrbState('idle');
  setState('idle');
  showToast('LUMEN ONLINE', '');
  if (CFG.autoListen) {
    setTimeout(() => { if (shouldAutoListen() && !listening) startListening(); }, 600);
  }
}

document.getElementById('orb-wrap').addEventListener('click', () => {
  if (orbClickTimer) {
    clearTimeout(orbClickTimer);
    orbClickTimer = null;
    if (asleep) wake(); else sleep();
    return;
  }
  orbClickTimer = setTimeout(() => {
    orbClickTimer = null;
    if (asleep) { wake(); return; }
    if (!speechSupported) return;
    if (listening) stopListening();
    else startListening();
  }, 280);
});

// ══════════════════════════════════════════════════════════════════════════════
// TEXT INPUT FALLBACK
// ══════════════════════════════════════════════════════════════════════════════
function sendFromTextInput() {
  const input = document.getElementById('text-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendChat(text);
}

document.getElementById('text-send-btn').addEventListener('click', sendFromTextInput);
document.getElementById('text-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendFromTextInput();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 't' || e.key === 'T') {
    const wrap = document.getElementById('text-input-wrap');
    wrap.classList.toggle('visible');
    if (wrap.classList.contains('visible')) document.getElementById('text-input').focus();
  }
  if (e.key === 'Escape') {
    document.getElementById('settings-overlay').classList.remove('open');
    document.getElementById('text-input-wrap').classList.remove('visible');
    if (listening) stopListening();
  }
  if (e.key === ' ' && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    if (speechSupported) {
      if (listening) stopListening();
      else startListening();
    }
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS PANEL
// ══════════════════════════════════════════════════════════════════════════════
document.getElementById('gear-btn').addEventListener('click', () => {
  document.getElementById('s-url').value   = CFG.serverUrl;
  document.getElementById('s-tts-port').value = CFG.ttsPort;
  document.getElementById('s-voice').value = CFG.voice;
  document.getElementById('settings-overlay').classList.add('open');
});

document.getElementById('s-cancel').addEventListener('click', () => {
  document.getElementById('settings-overlay').classList.remove('open');
});

document.getElementById('s-save').addEventListener('click', () => {
  CFG.serverUrl = document.getElementById('s-url').value.trim();
  CFG.ttsPort = parseInt(document.getElementById('s-tts-port').value) || 5050;
  CFG.voice = document.getElementById('s-voice').value.trim();
  document.getElementById('settings-overlay').classList.remove('open');
  loadVoices();
  checkHealth();
  showToast('SETTINGS SAVED', '');
});

document.getElementById('settings-overlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('settings-overlay')) {
    document.getElementById('settings-overlay').classList.remove('open');
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// PLASMA ORB CANVAS
// ══════════════════════════════════════════════════════════════════════════════
let orbState = 'idle';

function updateOrbState(state) {
  orbState = state;
}

(function initPlasmaOrb() {
  const canvas = document.getElementById('orb-canvas');
  const ctx = canvas.getContext('2d');
  const W = 320, H = 320;
  const CX = W / 2, CY = H / 2;
  const R = 130;

  const PALETTES = {
    idle: {
      core: [255,255,255], mid: [0,212,255], deep: [0,40,120], edge: [0,5,25],
      blob: 'rgba(0,200,255,', particle: [0,212,255],
      glow: 'rgba(0,180,255,', haze: 'rgba(0,160,255,',
      driftSpeed: 0.4, pulseSpeed: 1.0,
    },
    listening: {
      core: [255,255,200], mid: [255,140,0], deep: [120,40,0], edge: [20,5,0],
      blob: 'rgba(255,120,0,', particle: [255,160,0],
      glow: 'rgba(255,120,0,', haze: 'rgba(255,100,0,',
      driftSpeed: 1.2, pulseSpeed: 2.5,
    },
    processing: {
      core: [255,255,255], mid: [100,200,255], deep: [0,60,140], edge: [0,5,30],
      blob: 'rgba(180,230,255,', particle: [200,240,255],
      glow: 'rgba(80,180,255,', haze: 'rgba(60,160,255,',
      driftSpeed: 2.8, pulseSpeed: 5.0,
    },
    speaking: {
      core: [220,255,255], mid: [0,212,255], deep: [0,30,90], edge: [0,5,20],
      blob: 'rgba(0,220,255,', particle: [0,212,255],
      glow: 'rgba(0,200,255,', haze: 'rgba(0,180,255,',
      driftSpeed: 0.6, pulseSpeed: 1.8,
    },
    working: {
      core: [255,240,255], mid: [168,85,247], deep: [60,10,100], edge: [10,0,20],
      blob: 'rgba(168,85,247,', particle: [200,100,255],
      glow: 'rgba(150,60,240,', haze: 'rgba(120,40,200,',
      driftSpeed: 1.0, pulseSpeed: 2.0,
    },
    error: {
      core: [255,200,200], mid: [255,68,68], deep: [100,10,10], edge: [20,0,0],
      blob: 'rgba(255,80,80,', particle: [255,80,80],
      glow: 'rgba(255,60,60,', haze: 'rgba(200,40,40,',
      driftSpeed: 0.5, pulseSpeed: 1.5,
    },
    offline: {
      core: [150,150,170], mid: [60,70,100], deep: [10,10,25], edge: [5,5,15],
      blob: 'rgba(80,90,120,', particle: [80,90,120],
      glow: 'rgba(60,70,100,', haze: 'rgba(40,50,80,',
      driftSpeed: 0.15, pulseSpeed: 0.5,
    },
    standby: {
      core: [80,90,110], mid: [30,40,60], deep: [5,5,15], edge: [2,2,8],
      blob: 'rgba(40,50,70,', particle: [40,50,70],
      glow: 'rgba(30,40,60,', haze: 'rgba(20,30,50,',
      driftSpeed: 0.08, pulseSpeed: 0.3,
    },
  };

  const blobs = [];
  for (let i = 0; i < 4; i++) {
    blobs.push({
      angle: (Math.PI * 2 / 4) * i + Math.random() * 0.5,
      dist: Math.random() * 0.55 * R,
      r: R * (0.35 + Math.random() * 0.25),
      speed: (Math.random() * 0.3 + 0.15) * (Math.random() < 0.5 ? 1 : -1),
      phase: Math.random() * Math.PI * 2,
    });
  }

  const NUM_PARTICLES = 50;
  const particles = [];
  for (let i = 0; i < NUM_PARTICLES; i++) {
    let px, py;
    do {
      px = (Math.random() * 2 - 1) * R;
      py = (Math.random() * 2 - 1) * R;
    } while (px*px + py*py > R*R);
    particles.push({
      x: px, y: py,
      vx: (Math.random() - 0.5) * 0.6,
      vy: (Math.random() - 0.5) * 0.6,
      r: Math.random() * 2.2 + 0.8,
      a: Math.random() * 0.5 + 0.3,
      phase: Math.random() * Math.PI * 2,
    });
  }

  let curPalette = { ...PALETTES['idle'] };
  let targetPalette = { ...PALETTES['idle'] };
  let lerpT = 1;

  function lerpColor(a, b, t) {
    return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t];
  }
  function lerpNum(a, b, t) { return a + (b-a)*t; }

  let lastState = 'idle';
  let t = 0;
  let scalePulse = 1;
  let scaleVel = 0;

  function draw() {
    requestAnimationFrame(draw);
    t += 0.016;

    const pal = PALETTES[orbState] || PALETTES['idle'];
    if (orbState !== lastState) {
      lastState = orbState;
      targetPalette = pal;
      lerpT = 0;
    }
    if (lerpT < 1) {
      lerpT = Math.min(1, lerpT + 0.04);
      curPalette = {
        core: lerpColor(curPalette.core, targetPalette.core, lerpT),
        mid: lerpColor(curPalette.mid, targetPalette.mid, lerpT),
        deep: lerpColor(curPalette.deep, targetPalette.deep, lerpT),
        edge: lerpColor(curPalette.edge, targetPalette.edge, lerpT),
        blob: lerpT > 0.5 ? targetPalette.blob : curPalette.blob,
        particle: lerpColor(curPalette.particle || [0,212,255], targetPalette.particle, lerpT),
        glow: lerpT > 0.5 ? targetPalette.glow : curPalette.glow,
        haze: lerpT > 0.5 ? targetPalette.haze : curPalette.haze,
        driftSpeed: lerpNum(curPalette.driftSpeed, targetPalette.driftSpeed, lerpT),
        pulseSpeed: lerpNum(curPalette.pulseSpeed, targetPalette.pulseSpeed, lerpT),
      };
    }

    if (orbState === 'speaking') {
      const target = 1 + 0.04 * Math.sin(t * curPalette.pulseSpeed);
      scaleVel += (target - scalePulse) * 0.12;
      scaleVel *= 0.82;
      scalePulse += scaleVel;
    } else if (orbState === 'processing') {
      scalePulse = 1 + 0.02 * Math.sin(t * curPalette.pulseSpeed);
    } else {
      scalePulse += (1.0 - scalePulse) * 0.06;
    }

    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(CX, CY);
    ctx.scale(scalePulse, scalePulse);

    // Outer atmospheric haze
    const hazeR = R + 18;
    const hazeGrad = ctx.createRadialGradient(0, 0, R - 4, 0, 0, hazeR + 14);
    hazeGrad.addColorStop(0, curPalette.haze + '0.18)');
    hazeGrad.addColorStop(0.5, curPalette.haze + '0.08)');
    hazeGrad.addColorStop(1, curPalette.haze + '0)');
    ctx.beginPath();
    ctx.arc(0, 0, hazeR + 14, 0, Math.PI * 2);
    ctx.fillStyle = hazeGrad;
    ctx.fill();

    // Clip to sphere
    ctx.save();
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, Math.PI * 2);
    ctx.clip();

    // Base sphere gradient
    const [cr,cg,cb] = curPalette.core;
    const [mr,mg,mb] = curPalette.mid;
    const [dr,dg,db] = curPalette.deep;
    const [er,eg,eb] = curPalette.edge;

    const sphereGrad = ctx.createRadialGradient(-R*0.25, -R*0.28, R*0.03, 0, 0, R);
    sphereGrad.addColorStop(0,    `rgb(${cr|0},${cg|0},${cb|0})`);
    sphereGrad.addColorStop(0.18, `rgb(${cr|0},${cg|0},${cb|0})`);
    sphereGrad.addColorStop(0.42, `rgb(${mr|0},${mg|0},${mb|0})`);
    sphereGrad.addColorStop(0.72, `rgb(${dr|0},${dg|0},${db|0})`);
    sphereGrad.addColorStop(1,    `rgb(${er|0},${eg|0},${eb|0})`);
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, Math.PI * 2);
    ctx.fillStyle = sphereGrad;
    ctx.fill();

    // Cloud blobs
    const ds = curPalette.driftSpeed;
    for (const b of blobs) {
      b.angle += 0.004 * b.speed * ds;
      const bx = Math.cos(b.angle) * b.dist;
      const by = Math.sin(b.angle) * b.dist * 0.7;
      const pulse = 0.6 + 0.15 * Math.sin(t * 0.8 + b.phase);
      const bAlpha = 0.22 * pulse;
      ctx.save();
      ctx.filter = 'blur(18px)';
      ctx.beginPath();
      ctx.arc(bx, by, b.r, 0, Math.PI * 2);
      ctx.fillStyle = curPalette.blob + bAlpha.toFixed(3) + ')';
      ctx.fill();
      ctx.restore();
    }

    // Particles inside sphere
    const [pr,pg,pb] = curPalette.particle;
    const pSpeed = orbState === 'speaking' ? 1.4 : (orbState === 'processing' ? 2.0 : (orbState === 'listening' ? 1.1 : 0.5));
    for (const p of particles) {
      p.x += p.vx * pSpeed;
      p.y += p.vy * pSpeed;
      const d2 = p.x*p.x + p.y*p.y;
      if (d2 > R*R * 0.9) {
        const ang = Math.atan2(p.y, p.x);
        p.vx -= Math.cos(ang) * 0.04;
        p.vy -= Math.sin(ang) * 0.04;
      }
      const pa = p.a * (0.5 + 0.5 * Math.sin(t * 1.2 + p.phase));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${pr|0},${pg|0},${pb|0},${pa.toFixed(3)})`;
      ctx.fill();
    }

    if (orbState === 'speaking') {
      const breathe = Math.sin(t * curPalette.pulseSpeed) * 0.5 + 0.5;
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.55 * breathe, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${pr|0},${pg|0},${pb|0},${(0.06 * breathe).toFixed(3)})`;
      ctx.fill();
    }

    ctx.restore(); // end clip

    // Specular highlight
    const specGrad = ctx.createRadialGradient(-R*0.3, -R*0.35, 0, -R*0.3, -R*0.35, R*0.35);
    specGrad.addColorStop(0,   'rgba(255,255,255,0.55)');
    specGrad.addColorStop(0.4, 'rgba(255,255,255,0.12)');
    specGrad.addColorStop(1,   'rgba(255,255,255,0)');
    ctx.save();
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, Math.PI * 2);
    ctx.clip();
    ctx.beginPath();
    ctx.ellipse(-R*0.28, -R*0.34, R*0.22, R*0.14, -Math.PI/5, 0, Math.PI*2);
    ctx.fillStyle = specGrad;
    ctx.fill();
    ctx.restore();

    ctx.restore(); // end translate/scale

    // Outer glow
    ctx.save();
    ctx.translate(CX, CY);
    const glowPulse = 0.5 + 0.5 * Math.sin(t * curPalette.pulseSpeed * 0.4);
    ctx.shadowColor = curPalette.glow + (0.7 + 0.3 * glowPulse).toFixed(2) + ')';
    ctx.shadowBlur  = 32 + 20 * glowPulse;
    ctx.beginPath();
    ctx.arc(0, 0, R * scalePulse, 0, Math.PI * 2);
    ctx.strokeStyle = curPalette.glow + '0.5)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();
  }

  draw();
})();
