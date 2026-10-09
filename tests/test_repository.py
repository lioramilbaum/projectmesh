from __future__ import annotations

import os
from pathlib import Path

import pytest

from projectmesh import repository
from projectmesh.repository import capture_repository


def test_skips_fifo_without_reading_it(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is not supported on this platform")
    fifo = tmp_path / "source.pipe"
    os.mkfifo(fifo)

    snapshot = capture_repository(tmp_path)

    assert snapshot.files == ()
    assert snapshot.skipped_paths == ("source.pipe",)


def test_bounds_reads_and_skips_file_that_grows_during_capture(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "growing.txt"
    source.write_bytes(b"start")
    original_read = os.read
    requested_sizes: list[int] = []
    changed = False

    def grow_before_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        requested_sizes.append(size)
        if not changed:
            with source.open("ab") as stream:
                stream.write(b"x" * (repository._MAX_FILE_BYTES + 10))
            changed = True
        return original_read(descriptor, size)

    monkeypatch.setattr(repository.os, "read", grow_before_read)

    snapshot = capture_repository(tmp_path)

    assert snapshot.files == ()
    assert snapshot.skipped_paths == ("growing.txt",)
    assert requested_sizes
    assert max(requested_sizes) <= repository._MAX_FILE_BYTES + 1


def test_skips_candidate_already_over_per_file_limit(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"x" * (repository._MAX_FILE_BYTES + 1))

    snapshot = capture_repository(tmp_path)

    assert snapshot.files == ()
    assert snapshot.skipped_paths == ("large.txt",)


def test_skips_dotenv_variants_and_conventional_credential_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    sensitive_paths = (
        ".env",
        ".env.production",
        ".env.development.local",
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_ed25519",
        "server.key",
        "credentials",
        "application_default_credentials.json",
        "client_secret_fixture.json",
        ".aws/credentials",
        ".kube/config",
        ".docker/config.json",
    )
    for relative_path in sensitive_paths:
        candidate = tmp_path / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("never send this secret\n", encoding="utf-8")

    snapshot = capture_repository(tmp_path)

    assert snapshot.file_map() == {"safe.py": "print('safe')\n"}
    assert set(snapshot.skipped_paths) == set(sensitive_paths)


def test_skips_secret_bearing_resource_manifests_but_keeps_other_yaml(
    tmp_path: Path,
) -> None:
    manifests = {
        "secret.yaml": "apiVersion: v1\nkind: Secret\ndata:\n  token: dG9rZW4=\n",
        "secret-list.yml": (
            "apiVersion: v1\nkind: List\nitems:\n"
            "- apiVersion: v1\n  kind: Secret\ndata:\n  token: dG9rZW4=\n"
        ),
        "secret.json": (
            '{"apiVersion":"v1","kind":"Secret",'
            '"data":{"token":"dG9rZW4="}}\n'
        ),
        "json-secret.yaml": (
            '{"apiVersion":"v1","kind":"Secret",'
            '"data":{"token":"dG9rZW4="}}\n'
        ),
        "external-secret.yaml": (
            "apiVersion: external-secrets.io/v1beta1\n"
            "kind: ExternalSecret\nspec:\n  data: []\n"
        ),
    }
    for relative_path, contents in manifests.items():
        (tmp_path / relative_path).write_text(contents, encoding="utf-8")
    safe_manifest = tmp_path / "configmap.yaml"
    safe_manifest.write_text(
        "apiVersion: v1\nkind: ConfigMap\ndata:\n  greeting: hello\n",
        encoding="utf-8",
    )

    snapshot = capture_repository(tmp_path)

    assert snapshot.file_map() == {
        "configmap.yaml": safe_manifest.read_text(encoding="utf-8")
    }
    assert set(snapshot.skipped_paths) == set(manifests)


def test_skips_candidate_over_remaining_total_budget_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(7):
        (tmp_path / f"{index:02}.txt").write_bytes(b"x" * repository._MAX_FILE_BYTES)
    (tmp_path / "07.txt").write_bytes(b"x" * (100 * 1024))
    oversized_candidate = tmp_path / "08.txt"
    oversized_candidate.write_bytes(b"x" * (40 * 1024))
    original_open = os.open
    opened_paths: list[Path] = []

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        opened_paths.append(Path(path))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(repository.os, "open", record_open)

    snapshot = capture_repository(tmp_path)

    assert len(snapshot.files) == 8
    assert snapshot.skipped_paths == ("08.txt",)
    assert oversized_candidate not in opened_paths


def test_candidate_read_is_bounded_by_remaining_budget_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "growing.txt"
    candidate.write_bytes(b"start")
    original_read = os.read
    requested_sizes: list[int] = []
    changed = False

    def grow_before_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        requested_sizes.append(size)
        if not changed:
            with candidate.open("ab") as stream:
                stream.write(b"x" * 100)
            changed = True
        return original_read(descriptor, size)

    monkeypatch.setattr(repository.os, "read", grow_before_read)

    content = repository._read_candidate(candidate, remaining_budget=5)

    assert content is None
    assert requested_sizes == [6]
