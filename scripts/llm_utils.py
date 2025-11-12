#!/usr/bin/env python3
"""Shared helpers for calling OpenAI models."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional until runtime
    OpenAI = None  # type: ignore


class OpenAIClientError(RuntimeError):
    pass


def _ensure_client() -> "OpenAI":
    if OpenAI is None:
        raise OpenAIClientError("openai package is not installed. Run `pip install openai>=1.0.0`.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIClientError("Set OPENAI_API_KEY in the environment before running this command.")
    return OpenAI(api_key=api_key)


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text

    chunks: List[str] = []
    for block in getattr(response, "output", []) or []:
        for content in getattr(block, "content", []) or []:
            part_text = getattr(content, "text", None)
            if part_text:
                chunks.append(part_text)
    if chunks:
        return "".join(chunks).strip()
    return ""


def call_openai_api(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    client = _ensure_client()

    if model.lower().startswith("gpt-5"):
        response_kwargs: Dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if top_p is not None:
            response_kwargs["top_p"] = top_p
        if max_output_tokens:
            response_kwargs["max_output_tokens"] = max_output_tokens
        if reasoning_effort:
            response_kwargs["reasoning"] = {"effort": reasoning_effort}
        response = client.responses.create(**response_kwargs)
        if getattr(response, "status", "completed") != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            raise OpenAIClientError(f"Responses API returned incomplete status ({reason}). Consider increasing --max-output-tokens.")
        text = _extract_response_text(response)
        if not text:
            raise OpenAIClientError("Responses API did not return any text output.")
        return text

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise OpenAIClientError("Chat completion returned empty content.")
    return content


__all__ = ["call_openai_api", "OpenAIClientError"]
