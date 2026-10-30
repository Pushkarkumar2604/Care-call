"""One tiny wrapper around the LLM. Every agent calls ask_json().

- Works with Groq (LLM_PROVIDER=groq) or Claude (LLM_PROVIDER=claude).
- Always returns a dict parsed from strict JSON, or raises an exception.
- Has a hard time limit so a slow LLM never freezes the phone call.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

import config

_pool = ThreadPoolExecutor(max_workers=8)
_client = None


def _get_client():
    global _client
    if _client is None:
        if config.LLM_PROVIDER == "claude":
            import anthropic

            _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=10, max_retries=0)
        else:
            from groq import Groq

            _client = Groq(api_key=config.GROQ_API_KEY, timeout=10, max_retries=0)
    return _client


def _parse(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"LLM did not return JSON: {text[:120]}")
        return json.loads(m.group(0))


def _call(system: str, user: str, max_tokens: int) -> dict:
    client = _get_client()
    if config.LLM_PROVIDER == "claude":
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            temperature=0,
            system=system + " Reply with one JSON object only, no other text.",
            messages=[{"role": "user", "content": user}],
        )
        return _parse(resp.content[0].text)
    resp = client.chat.completions.create(
        model=config.GROQ_MODEL,
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + " Reply with one JSON object only."},
            {"role": "user", "content": user},
        ],
    )
    return _parse(resp.choices[0].message.content)


def ask_json(system: str, user: str, max_tokens: int = 150, timeout: float | None = None) -> dict:
    """Run the LLM with a hard deadline. Raises on timeout or error (callers fall back)."""
    if config.LLM_PROVIDER == "claude" and not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    if config.LLM_PROVIDER != "claude" and not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    future = _pool.submit(_call, system, user, max_tokens)
    return future.result(timeout=timeout or config.LLM_TIMEOUT)
