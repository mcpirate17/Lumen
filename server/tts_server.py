"""Kokoro TTS Server for Lumen.

High-performance text-to-speech server using kokoro_onnx (preferred) or
kokoro legacy as fallback. Plays audio server-side via sounddevice.

Run standalone:
    python -m server.tts_server

Or import and mount into an existing FastAPI app:
    from server.tts_server import create_tts_app
    app.mount("/tts", create_tts_app())
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

log = logging.getLogger("lumen.tts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000
DEFAULT_VOICE = "bm_george"
DEFAULT_MAX_CACHE = 50
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050

WARMUP_PHRASES = [
    "On it",
    "Working on that now",
    "One moment",
    "Let me check",
    "Checking scores now",
    "Pulling market data",
    "Give me a moment",
    "Good question, give me a sec",
]

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    speed: float = 1.0


class CacheWarmRequest(BaseModel):
    phrases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LRU audio cache
# ---------------------------------------------------------------------------


class AudioCache:
    """Thread-safe LRU cache for pre-synthesized numpy audio arrays."""

    __slots__ = ("_cache", "_maxsize", "_lock")

    def __init__(self, maxsize: int = DEFAULT_MAX_CACHE) -> None:
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> np.ndarray | None:
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    async def put(self, key: str, audio: np.ndarray) -> None:
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return
            self._cache[key] = audio
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Audio player — callback-based sounddevice OutputStream kept open
# ---------------------------------------------------------------------------


class AudioPlayer:
    """Manages a persistent sounddevice OutputStream for low-latency playback."""

    __slots__ = ("_stream", "_buffer", "_pos", "_playing", "_lock", "_sd")

    def __init__(self) -> None:
        self._stream = None
        self._buffer: np.ndarray | None = None
        self._pos: int = 0
        self._playing: bool = False
        self._lock = asyncio.Lock()
        self._sd = None

    def _ensure_sounddevice(self) -> Any:
        if self._sd is None:
            import sounddevice as sd
            self._sd = sd
        return self._sd

    def start_stream(self) -> None:
        """Open the persistent output stream. Called once at startup."""
        sd = self._ensure_sounddevice()
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=1024,
        )
        self._stream.start()
        log.info("Audio output stream opened (%d Hz, mono, float32)", SAMPLE_RATE)

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time_info: Any, status: Any
    ) -> None:
        """sounddevice callback — fills output buffer from queued audio."""
        if status:
            log.warning("Audio stream status: %s", status)

        if self._buffer is None or not self._playing:
            outdata[:] = 0
            return

        remaining = len(self._buffer) - self._pos
        if remaining <= 0:
            outdata[:] = 0
            self._playing = False
            return

        n = min(frames, remaining)
        outdata[:n, 0] = self._buffer[self._pos : self._pos + n]
        if n < frames:
            outdata[n:] = 0
            self._playing = False
        self._pos += n

    async def play(self, audio: np.ndarray) -> float:
        """Queue audio for playback. Returns duration in milliseconds."""
        async with self._lock:
            # Ensure float32
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            # Flatten to 1-D mono
            if audio.ndim > 1:
                audio = audio.flatten()

            self._buffer = audio
            self._pos = 0
            self._playing = True

            duration_ms = (len(audio) / SAMPLE_RATE) * 1000.0
            return duration_ms

    async def stop(self) -> None:
        """Immediately stop playback."""
        async with self._lock:
            self._playing = False
            self._buffer = None
            self._pos = 0

    @property
    def is_playing(self) -> bool:
        return self._playing

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


# ---------------------------------------------------------------------------
# TTS Engine abstraction
# ---------------------------------------------------------------------------


class TTSEngine:
    """Wraps kokoro_onnx (preferred) or kokoro legacy for synthesis."""

    __slots__ = ("_engine", "_engine_name", "_model")

    def __init__(self) -> None:
        self._engine: str = "none"
        self._engine_name: str = "none"
        self._model: Any = None

    def load(self) -> bool:
        """Try to load kokoro_onnx, fall back to kokoro legacy. Returns True on success."""
        # Try kokoro_onnx first
        if self._try_load_onnx():
            return True
        # Fall back to legacy kokoro
        if self._try_load_legacy():
            return True
        return False

    def _try_load_onnx(self) -> bool:
        try:
            from kokoro_onnx import Kokoro

            self._model = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
            self._engine = "kokoro-onnx"
            self._engine_name = "kokoro-onnx"
            log.info("Loaded kokoro_onnx engine")
            return True
        except Exception as exc:
            log.info("kokoro_onnx not available: %s", exc)
            return False

    def _try_load_legacy(self) -> bool:
        try:
            from kokoro import KPipeline  # type: ignore[import-untyped]

            self._model = KPipeline(lang_code="b")
            self._engine = "kokoro-legacy"
            self._engine_name = "kokoro-legacy"
            log.info("Loaded kokoro legacy engine (KPipeline)")
            return True
        except Exception as exc:
            log.info("kokoro legacy not available: %s", exc)
            return False

    @property
    def engine_name(self) -> str:
        return self._engine_name

    @property
    def available(self) -> bool:
        return self._model is not None

    async def synthesize(
        self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
    ) -> np.ndarray:
        """Synthesize text to a numpy float32 audio array."""
        if self._engine == "kokoro-onnx":
            return await self._synthesize_onnx(text, voice, speed)
        elif self._engine == "kokoro-legacy":
            return await self._synthesize_legacy(text, voice)
        else:
            raise RuntimeError("No TTS engine loaded")

    async def _synthesize_onnx(
        self, text: str, voice: str, speed: float
    ) -> np.ndarray:
        loop = asyncio.get_event_loop()
        audio, _sr = await loop.run_in_executor(
            None, lambda: self._model.create(text, voice=voice, speed=speed, lang="en-us")
        )
        return audio.astype(np.float32) if audio.dtype != np.float32 else audio

    async def _synthesize_legacy(self, text: str, voice: str) -> np.ndarray:
        loop = asyncio.get_event_loop()

        def _run():
            chunks = []
            for _gs, _ps, audio_chunk in self._model(text, voice=voice):
                if audio_chunk is not None:
                    chunks.append(audio_chunk)
            if not chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(chunks).astype(np.float32)

        return await loop.run_in_executor(None, _run)


# ---------------------------------------------------------------------------
# Application state container
# ---------------------------------------------------------------------------


class TTSState:
    """Holds all server state — engine, player, cache."""

    __slots__ = ("engine", "player", "cache")

    def __init__(self, max_cache: int = DEFAULT_MAX_CACHE) -> None:
        self.engine = TTSEngine()
        self.player = AudioPlayer()
        self.cache = AudioCache(maxsize=max_cache)


# ---------------------------------------------------------------------------
# Core operations (no god functions)
# ---------------------------------------------------------------------------


async def _synthesize(state: TTSState, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
    """Synthesize text, using cache if available."""
    cache_key = f"{voice}:{speed}:{text}"
    cached = await state.cache.get(cache_key)
    if cached is not None:
        log.debug("Cache hit: %s", text[:40])
        return cached

    audio = await state.engine.synthesize(text, voice=voice, speed=speed)
    await state.cache.put(cache_key, audio)
    return audio


async def _play_audio(state: TTSState, audio: np.ndarray) -> float:
    """Play audio via the persistent stream. Returns duration in ms."""
    return await state.player.play(audio)


async def _cache_phrase(state: TTSState, phrase: str, voice: str = DEFAULT_VOICE) -> None:
    """Pre-synthesize and cache a single phrase."""
    try:
        cache_key = f"{voice}:1.0:{phrase}"
        if await state.cache.get(cache_key) is not None:
            return
        audio = await state.engine.synthesize(phrase, voice=voice)
        await state.cache.put(cache_key, audio)
    except Exception as exc:
        log.warning("Failed to cache phrase '%s': %s", phrase, exc)


async def _warm_cache(state: TTSState, phrases: list[str], voice: str = DEFAULT_VOICE) -> int:
    """Pre-cache a list of phrases. Returns count cached."""
    count = 0
    for phrase in phrases:
        await _cache_phrase(state, phrase, voice)
        count += 1
    log.info("Warmed cache with %d phrases", count)
    return count


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_tts_app(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    voice: str = DEFAULT_VOICE,
    max_cache: int = DEFAULT_MAX_CACHE,
) -> FastAPI:
    """Create and return the TTS FastAPI application."""

    state = TTSState(max_cache=max_cache)

    app = FastAPI(title="Lumen TTS Server", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store state on app for access in routes
    app.state.tts = state
    app.state.default_voice = voice

    @app.on_event("startup")
    async def _startup() -> None:
        loaded = state.engine.load()
        if loaded:
            try:
                state.player.start_stream()
            except Exception as exc:
                log.error("Failed to open audio stream: %s", exc)
            # Pre-cache warmup phrases in background
            asyncio.create_task(_warm_cache(state, WARMUP_PHRASES, voice))
        else:
            log.error(
                "No TTS engine available. Server will return 503 on /tts. "
                "Ensure kokoro_onnx (kokoro-v1.0.onnx + voices-v1.0.bin) or "
                "the kokoro package is installed."
            )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        state.player.close()

    # -- Routes --

    @app.get("/ping")
    async def ping() -> dict:
        if not state.engine.available:
            return {"ok": False, "reason": "model not found"}
        return {"ok": True, "engine": state.engine.engine_name}

    @app.post("/tts")
    async def tts(req: TTSRequest) -> dict:
        if not state.engine.available:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "TTS engine not available"},
            )

        text = req.text.strip()
        if not text:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "empty text"},
            )

        used_voice = req.voice or voice
        log.info("TTS: %s", text[:80])

        t0 = time.monotonic()
        try:
            audio = await _synthesize(state, text, used_voice, req.speed)
            duration_ms = await _play_audio(state, audio)
            synth_ms = (time.monotonic() - t0) * 1000.0
            log.info(
                "Synthesized %.0fms audio in %.0fms (cache: %d entries)",
                duration_ms, synth_ms, state.cache.size,
            )
            return {"ok": True, "duration_ms": round(duration_ms, 1)}
        except Exception as exc:
            log.error("TTS error: %s", exc, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": str(exc)},
            )

    @app.post("/stop")
    async def stop() -> dict:
        await state.player.stop()
        log.info("Playback stopped")
        return {"ok": True}

    @app.post("/tts/stream")
    async def tts_stream(req: TTSRequest) -> dict:
        """Streaming endpoint — currently aliases to /tts for future expansion."""
        return await tts(req)

    @app.post("/cache/warm")
    async def cache_warm(req: CacheWarmRequest) -> dict:
        if not state.engine.available:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "TTS engine not available"},
            )
        phrases = req.phrases if req.phrases else WARMUP_PHRASES
        count = await _warm_cache(state, phrases, voice)
        return {"ok": True, "cached": count}

    return app


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the TTS server standalone."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Try to load config from Lumen config
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    voice = DEFAULT_VOICE

    try:
        from server.config import load_config

        cfg = load_config()
        host = cfg.tts.host
        port = cfg.tts.port
        voice = cfg.tts.voice
        log.info("Loaded TTS config from lumen.yaml: %s:%d voice=%s", host, port, voice)
    except Exception:
        log.info("Using default TTS config: %s:%d voice=%s", host, port, voice)

    app = create_tts_app(host=host, port=port, voice=voice)

    log.info("Starting Lumen TTS server on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
