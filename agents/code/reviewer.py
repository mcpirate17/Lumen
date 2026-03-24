"""Code review via Claude API.

Budget-capped. Only triggered for explicit code review requests.
"""

import logging

log = logging.getLogger("lumen.code.reviewer")


async def review_code(code: str, claude_client, context: str = "") -> str:
    """Review code using Claude.

    Returns review comments focusing on bugs, security, and clarity.
    """
    if not await claude_client.is_available():
        return "Claude API is not available. Code review requires Claude."

    prompt = (
        f"Review this code for bugs, security issues, and improvements:\n\n"
        f"```\n{code}\n```\n"
    )
    if context:
        prompt += f"\nContext: {context}\n"

    prompt += (
        "\nFocus on:\n"
        "1. Bugs or logic errors\n"
        "2. Security vulnerabilities\n"
        "3. Performance issues\n"
        "4. One concrete improvement suggestion\n\n"
        "Be concise. Skip obvious things."
    )

    system = (
        "Expert code reviewer. Focus on bugs, security, and clarity. "
        "Be concise and actionable. Skip praise and obvious observations."
    )

    try:
        return await claude_client.generate(
            prompt=prompt,
            system=system,
            reason="code_review",
        )
    except Exception as e:
        log.error("Code review failed: %s", e)
        return f"Failed to review code: {e}"
