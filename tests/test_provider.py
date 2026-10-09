from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from projectmesh.models import ProviderConfig
from projectmesh.provider import OpenAICompatibleProvider, ProviderError


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
    assert json.loads(request.data)["model"] == "test-model"
    assert completion == '{"summary": "ok"}'


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
