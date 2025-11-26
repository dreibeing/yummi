from __future__ import annotations

import logging
import json
from typing import Any, Dict, Callable, Optional

import httpx
from fastapi import HTTPException, status
from openai import OpenAI

from ..config import get_settings

logger = logging.getLogger(__name__)


def call_openai_responses(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    top_p: float | None = None,
    reasoning_effort: str | None = None,
    stream: bool = False,
    on_stream_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """Call the OpenAI Responses API and return the combined text output."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI not configured",
        )
    client = OpenAI(api_key=settings.openai_api_key)
    response_payload: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_output_tokens": max_output_tokens,
    }
    if top_p is not None:
        response_payload["top_p"] = top_p
    if reasoning_effort:
        response_payload["reasoning"] = {"effort": reasoning_effort}
    if stream:
        return _call_openai_streaming(response_payload, settings, on_stream_delta)

    responses_client = getattr(client, "responses", None)
    if responses_client and hasattr(responses_client, "create"):
        response = responses_client.create(**response_payload)
        if getattr(response, "status", "completed") != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            logger.error("OpenAI Responses API returned incomplete status: %s", reason)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Recommendation model did not complete successfully",
            )
        text = _extract_response_text(response)
        if not text:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Recommendation model returned empty output",
            )
        return text

    logger.warning("OpenAI client missing Responses API; falling back to HTTP call")
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/responses",
            json=response_payload,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.openai_request_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        logger.error("HTTP timeout calling OpenAI Responses API after %ss", settings.openai_request_timeout_seconds)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timed out while waiting for the OpenAI model. Please retry.",
        ) from exc
    except httpx.HTTPError as exc:  # pragma: no cover - network failure path
        logger.error("HTTP error calling OpenAI Responses API: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach OpenAI",
        ) from exc

    if resp.status_code >= 400:
        logger.error("OpenAI Responses REST API returned %s: %s", resp.status_code, resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recommendation model call failed",
        )

    payload = resp.json()
    text = _extract_response_text(payload)
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recommendation model returned empty output",
        )
    return text


def _call_openai_streaming(
    response_payload: Dict[str, Any],
    settings,
    on_stream_delta: Optional[Callable[[str], None]],
) -> str:
    payload = dict(response_payload)
    payload["stream"] = True
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    chunks: list[str] = []
    try:
        with httpx.stream(
            "POST",
            "https://api.openai.com/v1/responses",
            json=payload,
            headers=headers,
            timeout=settings.openai_request_timeout_seconds,
        ) as resp:
            if resp.status_code >= 400:
                logger.error("OpenAI Responses REST API returned %s during stream: %s", resp.status_code, resp.text)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Recommendation model call failed",
                )
            for raw_line in resp.iter_lines():
                if raw_line is None:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="ignore")
                else:
                    line = raw_line
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith(":"):
                    continue
                if not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    logger.debug("Malformed streaming event: %s", data)
                    continue
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    delta_text = delta if isinstance(delta, str) else ""
                    if delta_text:
                        chunks.append(delta_text)
                        if on_stream_delta:
                            on_stream_delta(delta_text)
                elif event_type == "response.error":
                    message = (event.get("error") or {}).get("message", "unknown error")
                    logger.error("OpenAI streaming error: %s", message)
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Recommendation model call failed",
                    )
    except httpx.TimeoutException as exc:
        logger.error("HTTP timeout calling OpenAI Responses API after %ss (stream)", settings.openai_request_timeout_seconds)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timed out while waiting for the OpenAI model. Please retry.",
        ) from exc
    except httpx.HTTPError as exc:  # pragma: no cover
        logger.error("HTTP error during OpenAI streaming request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach OpenAI",
        ) from exc
    return "".join(chunks)


def _extract_response_text(response: Any) -> str:
    chunks: list[str] = []
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    for block in output or []:
        block_content = getattr(block, "content", None)
        if block_content is None and isinstance(block, dict):
            block_content = block.get("content")
        for content in block_content or []:
            part_text = getattr(content, "text", None)
            if part_text is None and isinstance(content, dict):
                part_text = content.get("text")
            if part_text:
                chunks.append(part_text)
    return "".join(chunks).strip()
