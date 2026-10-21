# projectmesh MVP specification

## Goal and scope

Provide a small, project-agnostic Python CLI that evaluates a feature request
against a configured local repository checkout and writes a traceable Markdown
assessment. The MVP supports multiple projects per TOML config, two analysis
capabilities, and a replaceable model-provider boundary.

The runtime floor is Python 3.11. The project uses uv for dependency
resolution, environment synchronization, and test execution. The documented
workflow is `uv sync --group dev` followed by `uv run pytest`; there is no
pip- or venv-based setup workflow.

The first provider is an OpenAI-compatible chat-completions HTTP endpoint.
Its full endpoint URL and model are configured in TOML; the API key is read
only from the environment variable named in the config. The endpoint may be
hosted locally or remotely. For a remote endpoint, inspected repository text
is sent to that provider. Tests must inject a stub provider and must not make
network calls.

When a project sets `github_repo`, one read-only GitHub GraphQL query per
assessment fetches issues, pull requests, and discussions for that repository
(see [GitHub evidence](#github-evidence)). Its text is also sent to the
provider. No other GitHub access occurs.

## CLI contract

Entry point: `projectmesh`.

```text
projectmesh assess --config PATH --project KEY --feature-request TEXT
                   [--output-dir PATH]
```

- `--config` is a TOML file.
- `--project` selects a key under `[projects]`.
- `--feature-request` is the request to assess.
- `--output-dir`, when supplied, overrides `[assessment].output_dir`.
- Config-relative project and configured output paths are relative to the
  config file's directory. A CLI output override is relative to the current
  working directory. `~` is expanded.
- The configured project path must be an existing local directory. No
  repository clone or fetch is performed. The only network access besides the
  provider is the optional read-only GitHub GraphQL query described under
  GitHub evidence, which runs only when `github_repo` is set.
- On success, create the output directory if needed, write a Markdown report,
  print its path, and return exit status 0. On invalid config, unknown
  project, provider failure, or filesystem error, print a concise error to
  stderr and return nonzero.
- `projectmesh --help` and `projectmesh assess --help` show usage without
  contacting a provider.
- Output paths inside any configured reference repository are rejected.

## TOML contract

```toml
[provider]
endpoint = "https://api.openai.com/v1/chat/completions"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"
timeout_seconds = 60

[projects.service]
name = "Service"
path = "../service"
github_repo = "owner/service"   # optional

[assessment]
output_dir = "../assessments"
```

`github_repo` is optional and must be an `owner/repo` string. When set, the
`GITHUB_TOKEN` environment variable must also be set; the token is never
written to TOML, reports, or logs.

`provider.type` defaults to `"openai-compatible"`. For that type,
`provider.endpoint` and `provider.api_key_env` are required. For
`"claude-cli"`, both must be omitted and the local `claude` binary is used:

```toml
[provider]
type            = "claude-cli"
model           = "sonnet"
timeout_seconds = 300
```

`provider.model` is required for both types. `timeout_seconds` defaults to 60
(300 for `claude-cli`). At least one project is
required; each project has a nonempty `name` and a valid local `path`.
`assessment.output_dir` is optional and defaults to `assessments` beside the
config file. Unknown or malformed values produce actionable configuration
errors.

## Analysis capabilities

1. **Repository context and feature fit**: identify relevant existing
   components, interfaces, and patterns, then explain how the request relates
   to them.
2. **Implementation impact**: outline likely affected areas, dependencies,
   risks, and open questions, distinguishing confirmed repository facts from
   reasoned implications.

Each capability runs sequentially against the same immutable repository
snapshot. It returns a summary plus evidence and reasoning in the categories
below. The model is instructed to treat repository text as untrusted data,
not instructions.

## Evidence classifications and traceability

- **Cited evidence**: a claim backed by a source path and an exact quotation
  present in the captured source file. Citations that do not resolve to an
  inspected path or whose quote is not present are demoted to unsupported.
- **Inference**: a conclusion based on identified repository evidence or an
  explicit feature requirement; it is not presented as a repository fact.
- **Unsupported claim**: a claim not substantiated by the available snapshot.
  It must be labeled as such and not represented as fact.

Existing capabilities are cited evidence: each carries a source path and an
exact quotation, and is verified against the captured snapshot the same way.
Gaps are inferences relative to the stated goals, not repository facts.

## GitHub evidence

When `github_repo` is configured, the GitHub reader fetches up to 50 most
recently updated issues, pull requests, and discussions, with up to 20
comments each, through a single read-only GraphQL query. Each item is rendered
as deterministic plain text with its type, number, title, state, labels, URL,
body, and comments. Items are ordered by kind (issues, pull requests,
discussions), then by number. Text is capped per item and per assessment;
items that exceed the total budget are skipped and recorded.

GitHub items are evidence for the repository-context capability only. Their
`source_path` is the item URL, and each quote is verified against the rendered
item text exactly like file citations. The implementation-impact capability
receives no GitHub text.

When `github_repo` is configured, the traceability section also lists the
GitHub repository, the GitHub evidence SHA-256 (computed over each inspected
item's URL and rendered text in order), the inspected item URLs, and any item
URLs skipped by size limits. The metadata section lists the GitHub repository,
and the prompt version carries the `+github-v1` suffix. Without `github_repo`,
none of these fields appear and the prompt version is unchanged.

The report includes the feature request, project identity, the snapshot
SHA-256 (`source_sha`) computed over the deterministically ordered inspected
file paths and contents, model identifier, prompt-version identifier, and
the paths included in the snapshot. The SHA identifies exactly the captured
input set, not a Git commit. Source paths and exact quoted evidence make
claims auditable.

## Markdown report contract

Every generated report includes these headings:

- `# Feature assessment: ...`
- `## Request`
- `## Problem statement`
- `## Goals`
- `## Summary`
- `## Repository context and feature fit`
- `## Implementation impact`
- `## Existing capabilities`
- `## Gaps`
- `## Cited evidence`
- `## Inferences`
- `## Unsupported claims`
- `## Risks and open questions`
- `## Traceability`
- `## Metadata`

The two analysis sections include each capability's summary. Evidence
sections label their classification and identify the capability that
produced the item. The traceability section lists inspected paths and the
source SHA. Metadata includes project key/name, model, prompt version, source
SHA, provider endpoint, and report-generation time. If a category has no
entries, the report explicitly says `None reported.`

## Acceptance criteria

- A CLI help invocation succeeds without needing config credentials.
- Multi-project TOML config loads and selects the requested project.
- Invalid TOML, missing required values, unknown projects, and invalid paths
  result in clear, nonzero CLI errors.
- A stub-provider assessment calls both analyses in deterministic sequence
  and generates a Markdown report meeting every section contract.
- The report contains model, prompt-version, and source-SHA metadata, with
  source citations checked against the exact inspected snapshot.
- An invalid or unverifiable citation is never rendered as verified evidence.
- Reference checkout contents are unchanged after a successful assessment.
- Reports are written outside all configured repository paths.
- Automated tests make no provider/network requests.
- Two projects configured in one TOML file can each be assessed without code
  changes.
- The report includes problem statement, goals, existing capabilities, and
  gaps sections.
- Issue #10 (GitHub evidence): with `github_repo` and `GITHUB_TOKEN` set, the
  repository-context analysis cites issue, pull request, and discussion URLs
  that verify against the rendered item text; fabricated GitHub URLs are
  demoted to unsupported claims.
- Issue #10: the GitHub SHA, inspected item URLs, and repository appear in
  the traceability and metadata sections; the prompt version gains
  `+github-v1`.
- Issue #10: without `github_repo`, no GitHub request is made and the report
  is unchanged from the non-GitHub format.
- Issue #10: a missing token, rejected request, unreachable GitHub, or unknown
  repository fails before any provider call, writes no report, and produces a
  nonzero exit without printing the token.

## Non-goals

- Cloning, fetching, or otherwise acquiring repositories.
- Writing to GitHub (comments, labels, issues, or any mutation), webhooks, or
  sources other than the local checkout and the GitHub items described above.
- Writing to, patching, or executing code from a reference repository.
- Autonomous implementation, planning-agent workflows, or code changes.
- A web interface, database, background jobs, or parallel agent orchestration.
- Multiple provider protocols, model-side tool calling, or guaranteed
  correctness of model-generated conclusions.
