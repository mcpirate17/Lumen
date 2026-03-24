"""Anthropic Claude API client for Lumen. Used sparingly for complex tasks."""

import httpx
import json
from server.config import ClaudeConfig
from server import database as db

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Cost per million tokens (Sonnet 4.6)
INPUT_COST_PER_M = 3.00
OUTPUT_COST_PER_M = 15.00


class ClaudeClient:
    def __init__(self, config: ClaudeConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        reason: str = "escalation",
    ) -> str:
        """Send a request to Claude API. Tracks cost automatically."""
        # Budget check
        monthly_cost = await db.get_claude_monthly_cost()
        if monthly_cost >= self.config.max_monthly_budget:
            return (
                f"I've hit my monthly Claude API budget (${self.config.max_monthly_budget:.2f}). "
                "Let me try answering with my local model instead, or you can adjust "
                "the budget in config/lumen.yaml."
            )

        messages = [{"role": "user", "content": prompt}]

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if temperature != 0.7:
            payload["temperature"] = temperature

        resp = await self._client.post(
            ANTHROPIC_API_URL, json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract response
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block["text"]

        # Track usage
        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        cost = (
            (prompt_tokens / 1_000_000) * INPUT_COST_PER_M
            + (completion_tokens / 1_000_000) * OUTPUT_COST_PER_M
        )

        await db.log_claude_usage(prompt_tokens, completion_tokens, cost, reason)

        return content

    async def is_available(self) -> bool:
        """Check if Claude API key is configured."""
        return bool(self.config.api_key)

    async def close(self):
        await self._client.aclose()
