"""Markdown assessment renderer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from projectmesh.models import Assessment


_SENSITIVE_QUERY_PARTS = {
    "accesskey",
    "accesstoken",
    "apikey",
    "auth",
    "authtoken",
    "bearer",
    "clientsecret",
    "credential",
    "credentials",
    "jwt",
    "key",
    "password",
    "passwd",
    "passphrase",
    "privatekey",
    "secret",
    "secretkey",
    "sig",
    "signature",
    "token",
}


def _bullets(items: list[str] | tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "None reported."


def _escape_inline_code(value: str) -> str:
    return value.replace("`", "\\`")


def _sanitize_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
        query = urlencode(
            [
                (
                    name,
                    "REDACTED" if _has_sensitive_query_name(name) else value,
                )
                for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parsed.scheme, safe_netloc, parsed.path, query, ""))
    except ValueError:
        return "[configured endpoint]"


def _has_sensitive_query_name(name: str) -> bool:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    parts = "".join(
        character.lower() if character.isalnum() else " " for character in name
    )
    return bool(_SENSITIVE_QUERY_PARTS.intersection(parts.split()))


def render_markdown(
    assessment: Assessment, generated_at: datetime | None = None
) -> str:
    """Render the required assessment sections and traceability metadata."""
    generated_at = generated_at or datetime.now(timezone.utc)
    repository_analysis, impact_analysis = assessment.analyses
    evidence = [
        f"- **Cited evidence** ({item.capability}): {item.claim}\n"
        f"  - Source: `{item.source_path}`\n"
        f"  - Quote: `{_escape_inline_code(item.quote)}`"
        for analysis in assessment.analyses
        for item in analysis.cited_evidence
    ]
    inferences = [
        f"- **Inference** ({item.capability}): {item.claim}\n"
        f"  - Basis: {item.basis}"
        for analysis in assessment.analyses
        for item in analysis.inferences
    ]
    unsupported = [
        f"- **Unsupported claim** ({analysis.title}): {claim}"
        for analysis in assessment.analyses
        for claim in analysis.unsupported_claims
    ]
    risks = [
        f"- **Risk** ({analysis.title}): {risk}"
        for analysis in assessment.analyses
        for risk in analysis.risks
    ]
    questions = [
        f"- **Open question** ({analysis.title}): {question}"
        for analysis in assessment.analyses
        for question in analysis.open_questions
    ]
    skipped = (
        "\n\nFiles skipped by snapshot limits or file-type rules:\n"
        + _bullets([f"`{path}`" for path in assessment.skipped_paths])
        if assessment.skipped_paths
        else ""
    )
    return f"""# Feature assessment: {assessment.feature_request}

## Request

{assessment.feature_request}

## Summary

{repository_analysis.summary}

## Repository context and feature fit

{repository_analysis.summary}

## Implementation impact

{impact_analysis.summary}

## Cited evidence

{chr(10).join(evidence) if evidence else "None reported."}

## Inferences

{chr(10).join(inferences) if inferences else "None reported."}

## Unsupported claims

{chr(10).join(unsupported) if unsupported else "None reported."}

## Risks and open questions

{chr(10).join(risks) if risks else "None reported."}

{chr(10).join(questions) if questions else "None reported."}

## Traceability

- Source SHA-256: `{assessment.source_sha}`
- Inspected source files:
{_bullets([f"`{path}`" for path in assessment.source_paths])}
{skipped}

## Metadata

- Project key: `{assessment.project.key}`
- Project name: {assessment.project.name}
- Model: `{assessment.model}`
- Prompt version: `{assessment.prompt_version}`
- Source SHA-256: `{assessment.source_sha}`
- Provider endpoint: `{_sanitize_endpoint(assessment.provider_endpoint)}`
- Generated at: `{generated_at.astimezone(timezone.utc).isoformat()}`
"""
