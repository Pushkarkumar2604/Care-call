"""
One tiny wrapper around the LLM. Every agent calls ask_json().

- Works with Groq (LLM_PROVIDER=groq) or Claude (LLM_PROVIDER=claude).
- Returns a dict parsed from JSON, or raises an exception.
- Has a hard time limit so a slow LLM never freezes the phone call.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import config


_pool = ThreadPoolExecutor(max_workers=8)
_client = None


def _get_client():
    """Create the LLM client once and reuse it."""
    global _client

    if _client is None:
        if config.LLM_PROVIDER == "claude":
            import anthropic

            _client = anthropic.Anthropic(
                api_key=config.ANTHROPIC_API_KEY,
                timeout=10,
                max_retries=0,
            )
        else:
            from groq import Groq

            _client = Groq(
                api_key=config.GROQ_API_KEY,
                timeout=10,
                max_retries=0,
            )

    return _client


def _parse(text: str) -> dict:
    """Parse a JSON object, including one wrapped in Markdown code fences."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("LLM returned an empty response.")

    text = text.strip()

    # First, try parsing the entire response as JSON.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("LLM response must be a JSON object.")
    except json.JSONDecodeError:
        pass

    # Remove Markdown code fences, if present.
    cleaned = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
        raise ValueError("LLM response must be a JSON object.")
    except json.JSONDecodeError:
        pass

    # Find a JSON object inside additional surrounding text.
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)

    if not match:
        raise ValueError(
            f"LLM did not return valid JSON. Response starts with: {text[:120]}"
        )

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM returned malformed JSON. Response starts with: {text[:120]}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object.")

    return data


def _call(system: str, user: str, max_tokens: int) -> dict:
    """Make one request to the configured LLM provider."""
    client = _get_client()

    if config.LLM_PROVIDER == "claude":
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            temperature=0,
            system=(
                system
                + "\nReply with one valid JSON object only. "
                "Do not include Markdown or any other text."
            ),
            messages=[
                {
                    "role": "user",
                    "content": user,
                }
            ],
        )

        # Collect text blocks safely.
        response_text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )

        return _parse(response_text)

    response = client.chat.completions.create(
        model=config.GROQ_MODEL,
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    system
                    + "\nReply with one valid JSON object only. "
                    "Do not include Markdown or any other text."
                ),
            },
            {
                "role": "user",
                "content": user,
            },
        ],
    )

    response_text = response.choices[0].message.content

    return _parse(response_text)


def ask_json(
    system: str,
    user: str,
    max_tokens: int = 150,
    timeout: float | None = None,
) -> dict:
    """
    Run the LLM with a deadline.

    Raises an exception if configuration, the provider request, parsing,
    or the timeout fails. Calling agents can then use their fallback logic.
    """
    if config.LLM_PROVIDER == "claude":
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY missing")
        if not config.CLAUDE_MODEL:
            raise RuntimeError("CLAUDE_MODEL missing")
    else:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY missing")
        if not config.GROQ_MODEL:
            raise RuntimeError("GROQ_MODEL missing")

    request_timeout = timeout or config.LLM_TIMEOUT

    future = _pool.submit(_call, system, user, max_tokens)

    try:
        return future.result(timeout=request_timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(
            f"LLM request exceeded the {request_timeout}-second deadline."
        ) from exc