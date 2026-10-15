"""Replaceable completion-provider boundary and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from projectmesh.models import PROVIDER_CLAUDE_CLI, ProviderConfig


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "problem_statement": {"type": "string"},
        "goals": {"type": "array", "items": {"type": "string"}},
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
        "existing_capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source_path": {"type": "string"},
                    "quote": {"type": "string"},
                    "capability": {"type": "string"},
                },
                "required": ["claim", "source_path", "quote", "capability"],
                "additionalProperties": False,
            },
        },
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "problem_statement",
        "goals",
        "cited_evidence",
        "existing_capabilities",
        "inferences",
        "unsupported_claims",
        "risks",
        "open_questions",
        "gaps",
    ],
    "additionalProperties": False,
}


class ProviderError(RuntimeError):
    """Raised when the configured model provider cannot complete a request."""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return "no error output"


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


class ClaudeCLIProvider:
    """Runs completions through the local, logged-in Claude Code CLI."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        argv = [
            "claude", "-p",
            "--model", self.config.model,
            "--system-prompt", system_prompt,
            "--output-format", "json",
            "--json-schema", json.dumps(_RESPONSE_SCHEMA),
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        with tempfile.TemporaryDirectory() as workdir:
            try:
                result = subprocess.run(
                    argv,
                    input=user_prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.timeout_seconds,
                    cwd=workdir,
                    check=False,
                )
            except FileNotFoundError:
                raise ProviderError(
                    "Claude CLI executable 'claude' was not found on PATH."
                ) from None
            except OSError:
                raise ProviderError("Could not start the Claude CLI.") from None
            except subprocess.TimeoutExpired:
                raise ProviderError(
                    f"Claude CLI did not finish within {self.config.timeout_seconds} seconds."
                ) from None

        rc = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        try:
            envelope = json.loads(stdout)
            if not isinstance(envelope, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            if rc != 0:
                raise ProviderError(
                    f"Claude CLI exited with status {rc}: {_first_line(stderr)}"
                ) from None
            raise ProviderError("Claude CLI returned an invalid JSON response.") from None

        if envelope.get("is_error") or rc != 0:
            msg = envelope.get("result", "")
            detail = str(msg)[:200] if msg else f"exit status {rc}"
            raise ProviderError(f"Claude CLI reported an error: {detail}")

        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured)

        text = envelope.get("result", "")
        if isinstance(text, str) and text.strip():
            return text

        raise ProviderError("Claude CLI returned an empty completion.")


def create_provider(config: ProviderConfig) -> CompletionProvider:
    """Construct the configured provider implementation."""
    if config.type == PROVIDER_CLAUDE_CLI:
        return ClaudeCLIProvider(config)
    return OpenAICompatibleProvider(config)
