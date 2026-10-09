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
  network fetch or clone is performed.
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

[assessment]
output_dir = "../assessments"
```

`provider.endpoint`, `provider.model`, and `provider.api_key_env` are
required. `timeout_seconds` defaults to 60. At least one project is
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
- `## Summary`
- `## Repository context and feature fit`
- `## Implementation impact`
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

## Non-goals

- Cloning, fetching, or otherwise acquiring repositories.
- Writing to, patching, or executing code from a reference repository.
- Autonomous implementation, planning-agent workflows, or code changes.
- A web interface, database, background jobs, or parallel agent orchestration.
- Multiple provider protocols, model-side tool calling, or guaranteed
  correctness of model-generated conclusions.
