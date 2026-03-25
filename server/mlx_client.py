"""MLX inference client for Lumen (Apple Silicon optimized).

Research: MLX is 21-87% faster than llama.cpp and ~50% faster than Ollama
on Apple Silicon for LLM inference.

Current Ollama baseline (Mac Mini):
  - Qwen3.5-0.8B: ~52 tok/s
  - Qwen3.5-4B: ~22 tok/s

Expected MLX performance (from arxiv 2601.19139):
  - Qwen3-0.6B: ~525 tok/s (10x faster)
  - Qwen3-4B: ~159 tok/s (7x faster)
  - Qwen3-8B: ~93 tok/s (4x faster)

Setup required:
  1. huggingface-cli login (Qwen3.5 models are gated)
  2. Download MLX models: mlx_lm.convert --hf-path Qwen/Qwen3.5-4B -q
  3. Or use pre-converted: Qwen/Qwen3.5-4B-mlx (if available)

Usage:
  client = MLXClient()
  await client.load("Qwen/Qwen3.5-4B-4bit")
  result = await client.generate("Hello", max_tokens=100)
"""

import logging
import time
from typing import AsyncIterator

log = logging.getLogger("lumen.mlx")

_model = None
_tokenizer = None
_model_name = ""


async def load(model_path: str):
    """Load an MLX model. Call once at startup."""
    global _model, _tokenizer, _model_name
    try:
        from mlx_lm import load as mlx_load
        t0 = time.monotonic()
        _model, _tokenizer = mlx_load(model_path)
        ms = int((time.monotonic() - t0) * 1000)
        _model_name = model_path
        log.info("[MLX] Loaded %s in %dms", model_path, ms)
    except Exception as e:
        log.error("[MLX] Failed to load %s: %s", model_path, e)
        raise


async def generate(
    prompt: str,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    top_p: float = 0.8,
) -> str:
    """Generate text using the loaded MLX model."""
    if _model is None:
        raise RuntimeError("MLX model not loaded. Call load() first.")

    from mlx_lm import generate as mlx_generate

    # Build chat-style prompt
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Apply chat template
    full_prompt = _tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    t0 = time.monotonic()
    result = mlx_generate(
        _model, _tokenizer,
        prompt=full_prompt,
        max_tokens=max_tokens,
        temp=temperature,
        top_p=top_p,
        verbose=False,
    )
    ms = int((time.monotonic() - t0) * 1000)
    tokens = len(_tokenizer.encode(result))
    tps = tokens / (ms / 1000) if ms > 0 else 0
    log.info("[MLX] Generated %d tokens in %dms (%.1f tok/s)", tokens, ms, tps)

    return result


def is_available() -> bool:
    """Check if MLX model is loaded and ready."""
    return _model is not None


def get_info() -> dict:
    """Get info about the loaded model."""
    return {
        "backend": "mlx",
        "model": _model_name,
        "loaded": _model is not None,
    }
