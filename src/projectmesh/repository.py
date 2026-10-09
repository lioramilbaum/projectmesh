"""Read-only source snapshot creation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from projectmesh.models import RepositorySnapshot, SourceFile

_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}
_MAX_FILE_BYTES = 128 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_MAX_FILES = 500
_SENSITIVE_NAMES = {
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "application_default_credentials.json",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ecdsa_sk",
    "id_ed25519",
    "id_ed25519_sk",
    "id_rsa",
    "id_rsa_sk",
    "service_account.json",
}
_SECRET_RESOURCE_KINDS = {
    "secret",
    "secretlist",
    "sealedsecret",
    "externalsecret",
    "clusterexternalsecret",
    "secretstore",
    "clustersecretstore",
    "secretproviderclass",
    "pushsecret",
    "clusterpushsecret",
}
_YAML_KIND = re.compile(
    r"^(?P<indent>[ \t]*)(?:-[ \t]+)?kind[ \t]*:[ \t]*"
    r"(?P<quote>['\"]?)(?P<kind>[A-Za-z][A-Za-z0-9]*)(?P=quote)"
    r"[ \t]*(?:#.*)?$"
)
_YAML_DOCUMENT_SEPARATOR = re.compile(r"^[ \t]*---[ \t]*(?:#.*)?$")


def _is_sensitive(path: Path) -> bool:
    lowered = path.name.lower()
    path_parts = {part.lower() for part in path.parts}
    return (
        lowered.startswith(".env")
        or lowered in _SENSITIVE_NAMES
        or (lowered == "config" and ".kube" in path_parts)
        or (lowered == "config.json" and ".docker" in path_parts)
        or (lowered.startswith("client_secret") and lowered.endswith(".json"))
        or lowered.endswith((".pem", ".key", ".p12", ".pfx"))
        or lowered.endswith(".secret")
    )


def _is_sensitive_manifest(path: Path, text: str) -> bool:
    """Recognize known secret-bearing Kubernetes resources in YAML or JSON."""
    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        return False

    def contains_secret_resource(resource: object) -> bool:
        if isinstance(resource, list):
            return any(contains_secret_resource(item) for item in resource)
        if not isinstance(resource, dict):
            return False
        kind = resource.get("kind")
        if isinstance(kind, str) and kind.lower() in _SECRET_RESOURCE_KINDS:
            return True
        items = resource.get("items")
        return (
            isinstance(kind, str)
            and kind.lower() == "list"
            and isinstance(items, list)
            and any(contains_secret_resource(item) for item in items)
        )

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        if suffix == ".json":
            return False
    else:
        return contains_secret_resource(document)

    for document in _YAML_DOCUMENT_SEPARATOR.split(text.lstrip("\ufeff")):
        root_kind: str | None = None
        nested_kinds: list[str] = []
        for line in document.splitlines():
            match = _YAML_KIND.fullmatch(line)
            if match is None:
                continue
            kind = match.group("kind").lower()
            if not match.group("indent") and not line.startswith("-"):
                root_kind = kind
            elif match.group("indent") or line.startswith("-"):
                nested_kinds.append(kind)
        if root_kind in _SECRET_RESOURCE_KINDS:
            return True
        if root_kind == "list" and any(
            kind in _SECRET_RESOURCE_KINDS for kind in nested_kinds
        ):
            return True
    return False


def _read_candidate(candidate: Path, remaining_budget: int) -> bytes | None:
    descriptor = -1
    read_limit = min(_MAX_FILE_BYTES, remaining_budget)
    try:
        initial_stat = candidate.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(initial_stat.st_mode)
            or initial_stat.st_size > read_limit
        ):
            return None

        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(candidate, flags)
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (initial_stat.st_dev, initial_stat.st_ino)
            or opened_stat.st_size != initial_stat.st_size
        ):
            return None

        chunks: list[bytes] = []
        remaining = read_limit + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        final_stat = os.fstat(descriptor)
        if (
            len(content) > read_limit
            or len(content) != opened_stat.st_size
            or final_stat.st_size != opened_stat.st_size
        ):
            return None
        return content
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def capture_repository(root: str | Path) -> RepositorySnapshot:
    """Capture a bounded, deterministic set of text files without modifying it."""
    repo_root = Path(root).resolve(strict=True)
    if not repo_root.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_root}")

    source_files: list[SourceFile] = []
    skipped_paths: list[str] = []
    total_bytes = 0
    candidates: list[Path] = []
    for current_dir, dir_names, file_names in os.walk(repo_root, followlinks=False):
        current_path = Path(current_dir)
        dir_names[:] = sorted(
            dirname
            for dirname in dir_names
            if dirname not in _EXCLUDED_DIRS
            and not (current_path / dirname).is_symlink()
        )
        for filename in sorted(file_names):
            candidate = current_path / filename
            if candidate.is_symlink():
                skipped_paths.append(candidate.relative_to(repo_root).as_posix())
                continue
            candidates.append(candidate)

    for candidate in sorted(
        candidates, key=lambda item: item.relative_to(repo_root).as_posix()
    ):
        relative_path = candidate.relative_to(repo_root).as_posix()
        if _is_sensitive(candidate):
            skipped_paths.append(relative_path)
            continue
        if len(source_files) >= _MAX_FILES or total_bytes >= _MAX_TOTAL_BYTES:
            skipped_paths.append(relative_path)
            continue
        content = _read_candidate(candidate, _MAX_TOTAL_BYTES - total_bytes)
        if content is None:
            skipped_paths.append(relative_path)
            continue
        if total_bytes + len(content) > _MAX_TOTAL_BYTES:
            skipped_paths.append(relative_path)
            continue
        if b"\0" in content:
            skipped_paths.append(relative_path)
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            skipped_paths.append(relative_path)
            continue
        if _is_sensitive_manifest(candidate, text):
            skipped_paths.append(relative_path)
            continue
        source_files.append(SourceFile(path=relative_path, text=text))
        total_bytes += len(content)

    digest = hashlib.sha256()
    for source_file in source_files:
        digest.update(source_file.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_file.text.encode("utf-8"))
        digest.update(b"\0")
    return RepositorySnapshot(
        root=repo_root,
        files=tuple(source_files),
        source_sha=digest.hexdigest(),
        skipped_paths=tuple(sorted(skipped_paths)),
    )
