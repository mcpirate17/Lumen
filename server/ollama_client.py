"""Async Ollama client for Lumen. Uses /api/chat with think=false for Qwen 3.5 models."""

import httpx
import json
import time
from typing import AsyncIterator
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


@dataclass
class OllamaTrace:
    """Full trace of an Ollama call for observability."""
    model: str = ""
    prompt: str = ""
    system: str = ""
    temperature: float = 0.0
    max_tokens: int = 0
    response: str = ""
    thinking: str = ""
    eval_count: int = 0
    prompt_eval_count: int = 0
    total_duration_ms: int = 0
    load_duration_ms: int = 0
    prompt_eval_duration_ms: int = 0
    eval_duration_ms: int = 0
    tokens_per_second: float = 0.0
    error: str = ""


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        self.last_trace: OllamaTrace | None = None

    async def generate(
        self,
        prompt: str,
        model: str,
        system: str = "",
        temperature: float = 0.4,
        max_tokens: int = 512,
        history: list[dict] | None = None,
    ) -> str:
        """Generate a completion from Ollama using /api/chat with think=false."""
        trace = OllamaTrace(
            model=model, prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # Include recent conversation history for context
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "keep_alive": -1 if "0.8b" in model else "5m",
        }

        try:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

            msg = data.get("message", {})
            trace.response = msg.get("content", "")
            trace.thinking = msg.get("thinking", "")
            trace.eval_count = data.get("eval_count", 0)
            trace.prompt_eval_count = data.get("prompt_eval_count", 0)
            trace.total_duration_ms = int(data.get("total_duration", 0) / 1e6)
            trace.load_duration_ms = int(data.get("load_duration", 0) / 1e6)
            trace.prompt_eval_duration_ms = int(data.get("prompt_eval_duration", 0) / 1e6)
            trace.eval_duration_ms = int(data.get("eval_duration", 0) / 1e6)

            if trace.eval_duration_ms > 0:
                trace.tokens_per_second = trace.eval_count / (trace.eval_duration_ms / 1000)

            self.last_trace = trace
            return trace.response

        except Exception as e:
            trace.error = str(e)
            self.last_trace = trace
            raise

    async def stream_generate(
        self,
        prompt: str,
        model: str,
        system: str = "",
        temperature: float = 0.4,
        max_tokens: int = 512,
        history: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from Ollama using /api/chat."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "keep_alive": -1 if "0.8b" in model else "5m",
        }

        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    if token := msg.get("content"):
                        yield token
                    if chunk.get("done"):
                        return

    async def acknowledge(self, user_message: str, model: str) -> str:
        """Generate a brief backchannel-style acknowledgment (3-5 words)."""
        system = (
            "Respond with ONLY 2-5 words acknowledging the request. "
            "Examples: 'Checking that now.' / 'Looking into it.' / 'One moment.' / 'On it.' "
            "NEVER give an answer. NEVER use more than 5 words."
        )
        return await self.generate(
            prompt=user_message,
            model=model,
            system=system,
            temperature=0.3,
            max_tokens=10,
        )

    async def instant_response(self, user_message: str, model: str,
                                system: str = "", history: list[dict] | None = None) -> str:
        """Get an instant response from the always-on fast model.
        Used as the immediate answer while a bigger model thinks."""
        return await self.generate(
            prompt=user_message,
            model=model,
            system=system,
            temperature=0.3,
            max_tokens=150,
            history=history,
        )

    async def check_relevancy(self, question: str, answer: str, model: str) -> bool:
        """Quick check: does the response actually address the question?"""
        prompt = (
            f"Question: {question}\n"
            f"Answer: {answer}\n\n"
            "Does this answer address the question? Reply with only: yes or no"
        )
        result = await self.generate(
            prompt=prompt,
            model=model,
            temperature=0.0,
            max_tokens=5,
        )
        return "yes" in result.lower()

    async def self_check(self, question: str, answer: str, model: str) -> bool:
        """Ask the model if it's confident in its own answer. Returns True if confident."""
        prompt = (
            f"Question: {question}\n"
            f"Your answer: {answer}\n\n"
            "Are you certain this answer is factually accurate? Reply with only: yes or no"
        )
        result = await self.generate(
            prompt=prompt,
            model=model,
            temperature=0.0,
            max_tokens=10,
        )
        return "yes" in result.lower()

    async def is_healthy(self) -> bool:
        """Check if Ollama is running."""
        try:
            resp = await self._client.get("/api/tags", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()
