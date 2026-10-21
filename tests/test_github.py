from __future__ import annotations

import json
from typing import Self
from urllib.error import HTTPError, URLError

import pytest

from projectmesh import github
from projectmesh.github import GITHUB_GRAPHQL_URL, GitHubError, fetch_github_snapshot

ISSUE_URL = "https://github.com/acme/widgets/issues/12"
PR_URL = "https://github.com/acme/widgets/pull/45"
DISCUSSION_URL = "https://github.com/acme/widgets/discussions/3"


def _comment(body: str) -> dict:
    return {"body": body, "createdAt": "2024-01-01T00:00:00Z"}


def _graphql_response() -> dict:
    issue = {
        "number": 12,
        "title": "Fix login bug",
        "body": "Users can't log in.",
        "state": "OPEN",
        "url": ISSUE_URL,
        "labels": {"nodes": [{"name": "bug"}, {"name": "ux"}]},
        "comments": {
            "nodes": [
                _comment("Reproduced on mobile."),
                _comment("Fixed in #45."),
            ]
        },
    }
    pull_request = {
        "number": 45,
        "title": "Fix authentication",
        "body": "Fixes #12.",
        "state": "MERGED",
        "url": PR_URL,
        "labels": {"nodes": [{"name": "fix"}]},
        "comments": {"nodes": [_comment("LGTM.")]},
    }
    discussion = {
        "number": 3,
        "title": "Auth redesign",
        "body": "Should we redesign auth?",
        "closed": False,
        "url": DISCUSSION_URL,
        "labels": {"nodes": []},
        "comments": {"nodes": []},
    }
    return {
        "data": {
            "repository": {
                "issues": {"nodes": [issue]},
                "pullRequests": {"nodes": [pull_request]},
                "discussions": {"nodes": [discussion]},
            }
        }
    }


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _stub_github(monkeypatch, response, requests: list | None = None) -> None:
    """Replace urlopen with a stub that returns or raises `response`."""

    def fake_urlopen(request, timeout=None):
        if requests is not None:
            requests.append(request)
        if isinstance(response, BaseException):
            raise response
        body = response if isinstance(response, bytes) else json.dumps(response)
        return _FakeResponse(body.encode("utf-8"))

    monkeypatch.setattr(github, "urlopen", fake_urlopen)


def test_fetch_sends_read_only_graphql_query_with_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-gh-token")
    requests: list = []
    _stub_github(monkeypatch, _graphql_response(), requests)

    snapshot = fetch_github_snapshot("acme/widgets")

    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == GITHUB_GRAPHQL_URL
    assert request.get_header("Authorization") == "Bearer test-gh-token"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["query"].startswith("query")
    assert "mutation" not in payload["query"]
    assert payload["variables"] == {"owner": "acme", "name": "widgets"}

    assert [item.path for item in snapshot.items] == [
        ISSUE_URL,
        PR_URL,
        DISCUSSION_URL,
    ]
    issue_text = snapshot.items[0].text
    assert "Fix login bug" in issue_text
    assert "Users can't log in." in issue_text
    assert "Labels: bug, ux" in issue_text
    assert "Reproduced on mobile." in issue_text
    assert "Fixed in #45." in issue_text
    assert "State: MERGED" in snapshot.items[1].text
    assert "State: OPEN" in snapshot.items[2].text


def test_missing_token_raises_before_network(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fail_urlopen(request, timeout=None):
        raise AssertionError("network must not be used without a token")

    monkeypatch.setattr(github, "urlopen", fail_urlopen)

    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        fetch_github_snapshot("acme/widgets")


@pytest.mark.parametrize(
    "response",
    [
        HTTPError(GITHUB_GRAPHQL_URL, 401, "Unauthorized", {}, None),
        HTTPError(GITHUB_GRAPHQL_URL, 500, "Server Error", {}, None),
        URLError("name resolution failed"),
        {
            "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}],
            "data": {"repository": None},
        },
    ],
)
def test_http_and_network_errors_do_not_leak_token(monkeypatch, response) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-gh-token")
    _stub_github(monkeypatch, response)

    with pytest.raises(GitHubError) as excinfo:
        fetch_github_snapshot("acme/widgets")

    assert "test-gh-token" not in str(excinfo.value)


def test_snapshot_sha_is_independent_of_api_order(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-gh-token")
    lower_issue_url = "https://github.com/acme/widgets/issues/5"
    lower_issue = {
        "number": 5,
        "title": "Typo in docs",
        "body": "Missing word.",
        "state": "OPEN",
        "url": lower_issue_url,
        "labels": {"nodes": []},
        "comments": {"nodes": []},
    }
    issue_12 = _graphql_response()["data"]["repository"]["issues"]["nodes"][0]

    ascending_response = _graphql_response()
    ascending_response["data"]["repository"]["issues"]["nodes"] = [
        lower_issue,
        issue_12,
    ]
    _stub_github(monkeypatch, ascending_response)
    ascending = fetch_github_snapshot("acme/widgets")

    descending_response = _graphql_response()
    descending_response["data"]["repository"]["issues"]["nodes"] = [
        issue_12,
        lower_issue,
    ]
    _stub_github(monkeypatch, descending_response)
    descending = fetch_github_snapshot("acme/widgets")

    expected_paths = [lower_issue_url, ISSUE_URL, PR_URL, DISCUSSION_URL]
    assert [item.path for item in ascending.items] == expected_paths
    assert [item.path for item in descending.items] == expected_paths
    assert descending.source_sha == ascending.source_sha


def test_oversized_items_are_truncated_and_budget_skips_are_recorded(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-gh-token")
    monkeypatch.setattr(github, "_MAX_ITEM_CHARS", 50)
    monkeypatch.setattr(github, "_MAX_TOTAL_CHARS", 100)
    _stub_github(monkeypatch, _graphql_response())

    snapshot = fetch_github_snapshot("acme/widgets")

    assert any("[truncated]" in item.text for item in snapshot.items)
    # Each item is cut to 50 chars plus "\n[truncated]" (62 total). Only the
    # first fits in the 100-char budget; the other two are skipped.
    assert len(snapshot.items) == 1
    assert snapshot.items[0].path == ISSUE_URL
    assert set(snapshot.skipped_urls) == {PR_URL, DISCUSSION_URL}
