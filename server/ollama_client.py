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
        temperature: float = 0.7,
        top_p: float = 0.8,
        max_tokens: int = 512,
        think: bool = False,
        history: list[dict] | None = None,
    ) -> str:
        """Generate a completion from Ollama using /api/chat."""
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

        # Keep 0.8B and 4B permanently in memory (pre-warmed)
        # 0.8B for instant acks, 4B for most domain queries
        keep_alive = "5m"
        if "0.8b" in model or "4b" in model:
            keep_alive = -1  # permanent

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": think,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": 20,
                "num_predict": max_tokens,
            },
            "keep_alive": keep_alive,
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
        temperature: float = 0.7,
        top_p: float = 0.8,
        max_tokens: int = 512,
        think: bool = False,
        history: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from Ollama using /api/chat."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        keep_alive = "5m"
        if "0.8b" in model or "4b" in model:
            keep_alive = -1

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": think,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": 20,
                "num_predict": max_tokens,
            },
            "keep_alive": keep_alive,
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

    async def self_consistency_check(self, prompt: str, model: str,
                                       system: str = "", n: int = 3) -> tuple[str, bool]:
        """Generate N responses and return the majority answer + consistency flag.

        Research: self-consistency voting catches hallucinations in small models.
        At 500 tok/s, 3 short responses from 0.8B takes <1s total.

        Returns: (majority_answer, is_consistent)
        """
        import asyncio
        tasks = [
            self.generate(prompt=prompt, model=model, system=system,
                          temperature=0.7, max_tokens=150)
            for _ in range(n)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errors
        valid = [r.strip() for r in responses if isinstance(r, str) and r.strip()]
        if not valid:
            return ("", False)
        if len(valid) == 1:
            return (valid[0], True)

        # Check consistency: if all responses start with the same key fact (first sentence)
        first_sentences = []
        for r in valid:
            sent = r.split('.')[0].strip() if '.' in r else r[:100].strip()
            first_sentences.append(sent.lower())

        # Simple majority: most common first sentence
        from collections import Counter
        counts = Counter(first_sentences)
        most_common, count = counts.most_common(1)[0]
        is_consistent = count >= (n // 2 + 1)  # majority agrees

        # Return the full response that matches the majority
        for r in valid:
            first = r.split('.')[0].strip().lower() if '.' in r else r[:100].strip().lower()
            if first == most_common:
                return (r, is_consistent)

        return (valid[0], is_consistent)

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
