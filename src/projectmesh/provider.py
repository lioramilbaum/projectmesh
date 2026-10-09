"""Replaceable completion-provider boundary and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from projectmesh.models import ProviderConfig


class ProviderError(RuntimeError):
    """Raised when the configured model provider cannot complete a request."""


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
                "response_format": {"type": "json_object"},
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
