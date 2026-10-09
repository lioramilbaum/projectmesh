"""Replaceable completion-provider boundary and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from projectmesh.models import ProviderConfig


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "cited_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source_path": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["claim", "source_path", "quote"],
                "additionalProperties": False,
            },
        },
        "inferences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "basis": {"type": "string"},
                },
                "required": ["claim", "basis"],
                "additionalProperties": False,
            },
        },
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "cited_evidence",
        "inferences",
        "unsupported_claims",
        "risks",
        "open_questions",
    ],
    "additionalProperties": False,
}


class ProviderError(RuntimeError):
    """Raised when the configured model provider cannot complete a request."""


def _is_schema_format_incompatibility(error: HTTPError) -> bool:
    """Identify common responses from endpoints that reject JSON Schema mode."""
    if error.code != 400:
        return False
    try:
        response_text = error.read().decode("utf-8", errors="replace").lower()
    except OSError:
        return False
    mentions_schema_mode = (
        "json_schema" in response_text or "response_format" in response_text
    )
    rejects_mode = any(
        phrase in response_text
        for phrase in (
            "not supported",
            "unsupported",
            "does not support",
            "unrecognized",
            "unknown parameter",
            "invalid parameter",
        )
    )
    return mentions_schema_mode and rejects_mode


class CompletionProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant's completion text."""


class OpenAICompatibleProvider:
    """Minimal standard-library client for chat-completions compatible APIs."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise ProviderError(
                f"Environment variable '{self.config.api_key_env}' is not set."
            )
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "projectmesh_assessment",
                        "strict": True,
                        "schema": _RESPONSE_SCHEMA,
                    },
                },
            }
        ).encode("utf-8")
        try:
            request = Request(
                self.config.endpoint,
                data=payload,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                response_data = response.read()
        except HTTPError as exc:
            if _is_schema_format_incompatibility(exc):
                raise ProviderError(
                    "Configured provider does not support strict JSON Schema response format."
                ) from None
            raise ProviderError(f"Provider returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError):
            raise ProviderError(
                "Could not reach the configured provider."
            ) from None
        except ValueError:
            raise ProviderError("Configured provider request is invalid.") from None

        try:
            body = json.loads(response_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Provider returned an invalid JSON response.") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Provider response has no chat completion content.") from exc
        if not isinstance(content, str):
            raise ProviderError("Provider completion content must be a string.")
        return content


def create_provider(config: ProviderConfig) -> CompletionProvider:
    """Construct the configured provider implementation."""
    return OpenAICompatibleProvider(config)
