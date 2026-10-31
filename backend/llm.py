
"""
CareCall LLM wrapper.

Supports:
- Groq
- Anthropic Claude

Features:
- JSON response parsing
- Request timeout handling
- Consistent ask_json() interface
- Error handling

Keep DRY_RUN=true and DEMO_MODE=true while testing.
"""

import json
import re
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)

import config


# Reuse worker threads for LLM requests.
_pool = ThreadPoolExecutor(max_workers=4)

# Cache the initialized LLM client.
_client = None


class LLMError(Exception):
    """Base exception for LLM errors."""


class LLMTimeoutError(LLMError):
    """Raised when an LLM request exceeds its timeout."""


class LLMResponseError(LLMError):
    """Raised when the model response cannot be parsed as JSON."""


def _get_client():
    """Create and cache the configured LLM client."""
    global _client

    if _client is not None:
        return _client

    provider = str(
        getattr(config, "LLM_PROVIDER", "groq")
    ).strip().lower()

    if provider == "groq":
        try:
            from groq import Groq
        except ImportError as exc:
            raise LLMError(
                "Groq package is missing. Install it using "
                "'py -m pip install groq'."
            ) from exc

        api_key = getattr(config, "GROQ_API_KEY", None)

        if not api_key:
            raise LLMError("GROQ_API_KEY is not configured.")

        _client = Groq(
            api_key=api_key,
            timeout=15.0,
            max_retries=0,
        )

    elif provider == "claude":
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise LLMError(
                "Anthropic package is missing. Install it using "
                "'py -m pip install anthropic'."
            ) from exc

        api_key = getattr(config, "ANTHROPIC_API_KEY", None)

        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not configured.")

        _client = Anthropic(
            api_key=api_key,
            timeout=15.0,
            max_retries=0,
        )

    else:
        raise LLMError(
            f"Unsupported LLM provider: {provider!r}. "
            "Use 'groq' or 'claude'."
        )

    return _client


def _parse(text):
    """Parse a JSON object or list from the model response."""
    if text is None:
        raise LLMResponseError("The model returned an empty response.")

    text = str(text).strip()

    if not text:
        raise LLMResponseError("The model returned an empty response.")

    # Remove Markdown code fences, if present.
    text = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*```\s*$", "", text)

    # Try parsing the entire response first.
    try:
        result = json.loads(text)

        if isinstance(result, (dict, list)):
            return result

        raise LLMResponseError(
            "The JSON response must be an object or list."
        )

    except json.JSONDecodeError:
        pass

    # Try extracting a JSON object if extra text surrounds it.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if match:
        try:
            result = json.loads(match.group(0))

            if isinstance(result, dict):
                return result

        except json.JSONDecodeError:
            pass

    raise LLMResponseError(
        "Could not parse the model response as JSON."
    )


def _call(system, user, max_tokens=300):
    """Send a request to the configured LLM provider."""
    client = _get_client()

    provider = str(
        getattr(config, "LLM_PROVIDER", "groq")
    ).strip().lower()

    if provider == "groq":
        model = getattr(config, "GROQ_MODEL", None)
    else:
        model = getattr(config, "CLAUDE_MODEL", None)

    if not model:
        raise LLMError(
            f"No model configured for provider {provider!r}."
        )

    system_prompt = (
        str(system).strip()
        + "\n\n"
        "Return exactly one valid JSON object. "
        "Do not include Markdown fences or explanatory text. "
        "Use valid JSON syntax."
    )

    user_prompt = str(user).strip()

    if provider == "groq":
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=max(max_tokens, 512),
            response_format={"type": "json_object"},
            include_reasoning=False,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )

        response_text = response.choices[0].message.content

        if not response_text:
            raise LLMResponseError(
                "Groq returned an empty response."
            )

    elif provider == "claude":
        response = client.messages.create(
            model=model,
            temperature=0,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        )

        response_text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )

        if not response_text:
            raise LLMResponseError(
                "Claude returned an empty response."
            )

    else:
        raise LLMError(
            f"Unsupported LLM provider: {provider!r}."
        )

    return _parse(response_text)


def ask_json(system, user, max_tokens=300, timeout=None):
    """
    Public interface used by CareCall's Planner and Listener.

    Args:
        system: Instructions for the model.
        user: User prompt or task data.
        max_tokens: Requested maximum completion tokens.
        timeout: Optional timeout in seconds.

    Returns:
        A Python dictionary or list.

    Raises:
        LLMTimeoutError: If the request exceeds its timeout.
        LLMResponseError: If the response is not valid JSON.
        LLMError: If the request or configuration fails.
    """
    request_timeout = (
        timeout
        if timeout is not None
        else getattr(config, "LLM_TIMEOUT", 10)
    )

    try:
        request_timeout = float(request_timeout)
    except (TypeError, ValueError) as exc:
        raise LLMError(
            "LLM_TIMEOUT must be a number."
        ) from exc

    if request_timeout <= 0:
        raise LLMError(
            "The LLM timeout must be greater than zero."
        )

    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise LLMError(
            "max_tokens must be a positive integer."
        )

    future = _pool.submit(
        _call,
        system,
        user,
        max_tokens,
    )

    try:
        return future.result(timeout=request_timeout)

    except FutureTimeoutError as exc:
        future.cancel()

        raise LLMTimeoutError(
            f"LLM request exceeded the "
            f"{request_timeout:g}-second timeout."
        ) from exc

    except LLMError:
        raise

    except Exception as exc:
        raise LLMError(
            f"LLM request failed ({type(exc).__name__}): {exc}"
        ) from exc
