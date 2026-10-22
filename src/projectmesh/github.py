"""Read-only GitHub evidence collection through the GraphQL API."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from projectmesh.models import GitHubSnapshot, SourceFile

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
TOKEN_ENV = "GITHUB_TOKEN"
_MAX_ITEMS_PER_KIND = 50
_MAX_COMMENTS = 20
_MAX_ITEM_CHARS = 32 * 1024
_MAX_TOTAL_CHARS = 256 * 1024
_TIMEOUT_SECONDS = 30
_KIND_ORDER = {"issue": 0, "pull_request": 1, "discussion": 2}
_KIND_LABELS = {
    "issue": "Issue",
    "pull_request": "Pull request",
    "discussion": "Discussion",
}


class GitHubError(RuntimeError):
    """Raised when GitHub evidence cannot be fetched."""


_QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title body state url
        labels(first: 20) { nodes { name } }
        comments(last: 20) { nodes { body createdAt } }
      }
    }
    pullRequests(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title body state url
        labels(first: 20) { nodes { name } }
        comments(last: 20) { nodes { body createdAt } }
      }
    }
    discussions(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title body closed url
        labels(first: 20) { nodes { name } }
        comments(last: 20) { nodes { body createdAt } }
      }
    }
  }
}"""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _nodes(connection: Any) -> list[dict[str, Any]]:
    if not isinstance(connection, dict):
        return []
    nodes = connection.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _render_item(kind: str, node: dict[str, Any]) -> SourceFile | None:
    url = node.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    if kind == "discussion":
        state = "CLOSED" if node.get("closed") else "OPEN"
    else:
        state = _text(node.get("state")) or "UNKNOWN"
    labels = [_text(label.get("name")) for label in _nodes(node.get("labels"))]
    body = _text(node.get("body")) or "(empty)"
    lines = [
        f"Type: {_KIND_LABELS[kind]}",
        f"Number: {node.get('number', '')}",
        f"Title: {_text(node.get('title'))}",
        f"State: {state}",
        f"Labels: {', '.join(labels) if labels else 'none'}",
        f"URL: {url}",
        "",
        "Body:",
        body,
    ]
    for index, comment in enumerate(_nodes(node.get("comments"))[:_MAX_COMMENTS], 1):
        lines.extend(
            [
                "",
                f"Comment {index} ({_text(comment.get('createdAt'))}):",
                _text(comment.get("body")),
            ]
        )
    text = "\n".join(lines)
    if len(text) > _MAX_ITEM_CHARS:
        text = text[:_MAX_ITEM_CHARS] + "\n[truncated]"
    return SourceFile(path=url, text=text)


def _build_snapshot(
    repo: str,
    issues: list[dict[str, Any]],
    prs: list[dict[str, Any]],
    discussions: list[dict[str, Any]],
) -> GitHubSnapshot:
    rendered: list[tuple[int, int, SourceFile]] = []
    for kind, nodes in (
        ("issue", issues),
        ("pull_request", prs),
        ("discussion", discussions),
    ):
        for node in nodes:
            item = _render_item(kind, node)
            if item is None:
                continue
            number = node.get("number")
            rendered.append(
                (_KIND_ORDER[kind], number if isinstance(number, int) else 0, item)
            )
    rendered.sort(key=lambda entry: (entry[0], entry[1], entry[2].path))

    items: list[SourceFile] = []
    skipped_urls: list[str] = []
    total_chars = 0
    for _, _, item in rendered:
        if total_chars + len(item.text) > _MAX_TOTAL_CHARS:
            skipped_urls.append(item.path)
            continue
        items.append(item)
        total_chars += len(item.text)

    digest = hashlib.sha256()
    for item in items:
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.text.encode("utf-8"))
        digest.update(b"\0")
    return GitHubSnapshot(
        repo=repo,
        items=tuple(items),
        source_sha=digest.hexdigest(),
        skipped_urls=tuple(skipped_urls),
    )


def fetch_github_snapshot(repo: str) -> GitHubSnapshot:
    """Fetch recent issues, pull requests, and discussions with a read-only query."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise GitHubError(
            "Environment variable 'GITHUB_TOKEN' is not set; "
            "it is required when a project sets github_repo."
        )
    owner, name = repo.split("/", 1)
    payload = json.dumps(
        {"query": _QUERY, "variables": {"owner": owner, "name": name}}
    ).encode("utf-8")
    request = Request(
        GITHUB_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "projectmesh",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            response_data = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise GitHubError(
                f"GitHub rejected the request (HTTP {exc.code}); "
                "check GITHUB_TOKEN permissions."
            ) from None
        raise GitHubError(f"GitHub returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise GitHubError("Could not reach GitHub.") from None

    try:
        body = json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GitHubError("GitHub returned an invalid JSON response.") from None
    if not isinstance(body, dict):
        raise GitHubError("GitHub returned an invalid JSON response.")

    data = body.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    if body.get("errors") or not isinstance(repository, dict):
        raise GitHubError(
            f"GitHub repository '{repo}' was not found or is not accessible."
        )
    return _build_snapshot(
        repo,
        _nodes(repository.get("issues")),
        _nodes(repository.get("pullRequests")),
        _nodes(repository.get("discussions")),
    )
