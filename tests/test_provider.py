from __future__ import annotations

import json
import os
import subprocess
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from projectmesh.models import PROVIDER_CLAUDE_CLI, PROVIDER_OPENAI_COMPATIBLE, ProviderConfig
from projectmesh.provider import (
    _RESPONSE_SCHEMA,
    ClaudeCLIProvider,
    OpenAICompatibleProvider,
    ProviderError,
    create_provider,
)


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}
        ).encode()


def test_openai_compatible_provider_uses_configured_endpoint_and_environment_key(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request, timeout: int) -> _Response:
        observed["request"] = request
        observed["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("TEST_PROVIDER_KEY", "test-secret")
    monkeypatch.setattr("projectmesh.provider.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            endpoint="https://provider.test/v1/chat/completions",
            model="test-model",
            api_key_env="TEST_PROVIDER_KEY",
            timeout_seconds=17,
        )
    )

    completion = provider.complete("system", "user")

    request = observed["request"]
    assert request.full_url == "https://provider.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert observed["timeout"] == 17
    payload = json.loads(request.data)
    assert payload["model"] == "test-model"
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    schema_config = response_format["json_schema"]
    assert schema_config["strict"] is True
    schema = schema_config["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["summary"] == {"type": "string"}
    evidence_schema = schema["properties"]["cited_evidence"]["items"]
    assert evidence_schema["additionalProperties"] is False
    assert evidence_schema["required"] == ["claim", "source_path", "quote"]
    inference_schema = schema["properties"]["inferences"]["items"]
    assert inference_schema["additionalProperties"] is False
    assert inference_schema["required"] == ["claim", "basis"]
    assert completion == '{"summary": "ok"}'


def test_schema_format_incompatibility_is_clear_without_leaking_response_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_PROVIDER_KEY", "provider-key-secret")

    def reject_schema_format(*_args, **_kwargs):
        raise HTTPError(
            "https://user-secret:pass-secret@provider.test/"
            "?token=endpoint-token-secret",
            400,
            "Bad Request",
            None,
            BytesIO(
                b'{"error":"response_format json_schema is not supported; '
                b'token=response-token-secret"}'
            ),
        )

    monkeypatch.setattr("projectmesh.provider.urlopen", reject_schema_format)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            endpoint="https://provider.test/v1/chat/completions",
            model="test-model",
            api_key_env="TEST_PROVIDER_KEY",
        )
    )

    with pytest.raises(ProviderError) as error:
        provider.complete("system", "user")

    assert "does not support strict JSON Schema" in str(error.value)
    for secret in (
        "provider-key-secret",
        "user-secret",
        "pass-secret",
        "endpoint-token-secret",
        "response-token-secret",
    ):
        assert secret not in str(error.value)


def test_provider_error_does_not_expose_endpoint_credentials_or_query_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_PROVIDER_KEY", "test-secret")

    def fail_urlopen(*_args, **_kwargs):
        raise URLError(
            "https://error-user-secret:error-pass-secret@provider.invalid/"
            "?token=error-token-secret"
        )

    monkeypatch.setattr("projectmesh.provider.urlopen", fail_urlopen)
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            endpoint=(
                "https://endpoint-user-secret:endpoint-pass-secret@provider.invalid/"
                "?token=endpoint-token-secret"
            ),
            model="test-model",
            api_key_env="TEST_PROVIDER_KEY",
        )
    )

    with pytest.raises(ProviderError) as error:
        provider.complete("system", "user")

    for secret in (
        "error-user-secret",
        "error-pass-secret",
        "error-token-secret",
        "endpoint-user-secret",
        "endpoint-pass-secret",
        "endpoint-token-secret",
    ):
        assert secret not in str(error.value)


def _make_envelope(structured_output=None, result="", is_error=False):
    return json.dumps({
        "is_error": is_error,
        "result": result,
        **({"structured_output": structured_output} if structured_output is not None else {}),
    })


def _claude_cli_config(timeout_seconds: int = 300) -> ProviderConfig:
    return ProviderConfig(
        endpoint="",
        model="haiku",
        api_key_env="",
        timeout_seconds=timeout_seconds,
        type=PROVIDER_CLAUDE_CLI,
    )


def test_claude_cli_provider_passes_correct_argv_and_reads_prompt_from_stdin(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0, _make_envelope(structured_output={"summary": "ok"}), ""
        )

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)
    provider = ClaudeCLIProvider(
        ProviderConfig(
            endpoint="",
            model="haiku",
            api_key_env="",
            timeout_seconds=17,
            type=PROVIDER_CLAUDE_CLI,
        )
    )

    completion = provider.complete("system", "user")

    argv = observed["argv"]
    kwargs = observed["kwargs"]
    assert argv[0] == "claude"
    assert "-p" in argv
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--system-prompt") + 1] == "system"
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == _RESPONSE_SCHEMA
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert kwargs["input"] == "user"
    assert "user" not in argv
    assert kwargs["timeout"] == 17
    assert kwargs["cwd"] != os.getcwd()
    assert completion == json.dumps({"summary": "ok"})


def test_claude_cli_provider_falls_back_to_result_text_when_no_structured_output(
    monkeypatch,
) -> None:
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 0, _make_envelope(result='{"answer": "yes"}'), ""
        )

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    completion = ClaudeCLIProvider(_claude_cli_config()).complete("system", "user")

    assert completion == '{"answer": "yes"}'


def test_claude_cli_provider_raises_on_is_error_envelope(monkeypatch) -> None:
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            _make_envelope(
                is_error=True,
                result="There's an issue with the selected model (haiku).",
            ),
            "",
        )

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    with pytest.raises(ProviderError, match="issue with the selected model"):
        ClaudeCLIProvider(_claude_cli_config()).complete("system", "user")


def test_claude_cli_provider_raises_on_nonzero_exit_without_envelope(
    monkeypatch,
) -> None:
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 1, "", "error: unknown option '--json-schema'\n"
        )

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    with pytest.raises(ProviderError, match="status 1.*unknown option"):
        ClaudeCLIProvider(_claude_cli_config()).complete("system", "user")


def test_claude_cli_provider_raises_on_missing_binary(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    with pytest.raises(ProviderError, match="not found on PATH"):
        ClaudeCLIProvider(_claude_cli_config()).complete("system", "user")


def test_claude_cli_provider_raises_on_timeout(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["claude"], 17)

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    with pytest.raises(ProviderError, match="17 seconds"):
        ClaudeCLIProvider(_claude_cli_config(timeout_seconds=17)).complete(
            "system", "user"
        )


def test_claude_cli_provider_raises_on_empty_completion(monkeypatch) -> None:
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 0, _make_envelope(result="   "), ""
        )

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    with pytest.raises(ProviderError, match="empty completion"):
        ClaudeCLIProvider(_claude_cli_config()).complete("system", "user")


def test_create_provider_selects_implementation_by_type() -> None:
    openai_config = ProviderConfig(
        endpoint="http://x",
        model="m",
        api_key_env="K",
        type=PROVIDER_OPENAI_COMPATIBLE,
    )
    claude_config = ProviderConfig(
        endpoint="",
        model="sonnet",
        api_key_env="",
        type=PROVIDER_CLAUDE_CLI,
    )

    assert isinstance(create_provider(openai_config), OpenAICompatibleProvider)
    assert isinstance(create_provider(claude_config), ClaudeCLIProvider)
