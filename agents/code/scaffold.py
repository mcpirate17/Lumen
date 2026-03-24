"""Project scaffolding via Claude API.

This is the one agent that intentionally uses Claude API.
Budget-capped: tracks usage, warns at 80% of monthly limit.
"""

import logging

log = logging.getLogger("lumen.code.scaffold")


async def scaffold_project(description: str, claude_client, language: str = "python") -> str:
    """Generate a project scaffold from a description using Claude.

    Returns the scaffold as text (file structure + starter code).
    """
    if not await claude_client.is_available():
        return "Claude API is not available. Code scaffolding requires Claude."

    prompt = (
        f"Create a minimal project scaffold for the following:\n\n"
        f"Description: {description}\n"
        f"Language: {language}\n\n"
        "Include:\n"
        "1. Directory structure\n"
        "2. Key files with starter code\n"
        "3. Requirements/dependencies\n"
        "4. Brief README content\n\n"
        "Keep it minimal and practical. No boilerplate comments."
    )

    system = (
        "You are an expert software architect. Generate clean, minimal project scaffolds. "
        "Use modern best practices. Keep it simple — no over-engineering."
    )

    try:
        return await claude_client.generate(
            prompt=prompt,
            system=system,
            reason="code_scaffold",
        )
    except Exception as e:
        log.error("Scaffold generation failed: %s", e)
        return f"Failed to generate scaffold: {e}"
