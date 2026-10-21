# projectmesh

projectmesh is a small, project-agnostic CLI for assessing a feature request
against one of your existing local repository checkouts. It creates a
traceable Markdown report with two analyses: repository context and feature
fit, followed by likely implementation impact.

The inspected repository is read-only: projectmesh does not clone, fetch,
modify, or create files in it. Reports are written separately. The first
provider is an OpenAI-compatible chat-completions HTTP endpoint or a Claude Code subscription; endpoint and
model are configurable, and the API key is read from an environment variable.
Repository text is sent to that configured endpoint when an assessment runs.
Tests use an in-process stub provider and do not make network calls.

See [MVP_SPEC.md](MVP_SPEC.md) for the CLI/report contract and
[ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries.

## Requirements and setup

- Python 3.11 or newer
- A local checkout of each repository to assess
- An API key for the configured provider (only needed for real assessments)

From the project root, install the package and development dependencies with
uv:

```sh
uv sync --group dev
```

Copy `config/projects.example.toml` to a private/local TOML file and set each
project's `path` to a local checkout. Project paths are resolved relative to
the TOML file; `~` is expanded. The example contains two project entries to
demonstrate multi-project configuration.

```toml
[provider]
endpoint = "https://api.openai.com/v1/chat/completions"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"

[projects.my-service]
name = "My service"
path = "../my-service"

[projects.my-library]
name = "My library"
path = "../my-library"

[assessment]
output_dir = "../projectmesh-assessments"
```

Set the configured environment variable without putting its value in TOML:

```sh
export OPENAI_API_KEY="..."
```

### Using a Claude Code subscription

Set `type = "claude-cli"` in `[provider]` to use the local `claude` binary
instead of an HTTP endpoint. No API key environment variable is needed.

```toml
[provider]
type            = "claude-cli"
model           = "sonnet"
timeout_seconds = 300
```

Requirements:

- `claude` must be on PATH and logged in (`claude --version` should work)
- Repository text is sent to Anthropic through your local Claude Code session
- The CLI runs with tools and MCP disabled from a temporary directory
- Raise `timeout_seconds` if assessments time out on large repositories

### GitHub activity as evidence

Set `github_repo = "owner/repo"` in a `[projects.<key>]` entry to also assess
that repository's issues, pull requests, and discussions. Each item is cited
by its GitHub URL, and the report records the GitHub repository and an
evidence SHA-256 in its traceability and metadata sections.

```toml
[projects.my-service]
name = "My service"
path = "../my-service"
github_repo = "my-org/my-service"
```

Provide a token through the environment, never in TOML:

```sh
export GITHUB_TOKEN="..."
```

- The token needs read access to issues, pull requests, and discussions in
  that repository.
- projectmesh fetches up to 50 most recently updated items per kind and up to
  20 comments per item. Items and text beyond fixed size limits are truncated
  or skipped and listed in the report.
- Fetched GitHub text is sent to the configured provider along with repository
  text when an assessment runs.
- projectmesh only reads from GitHub with one GraphQL query per assessment. It
  never writes to GitHub.
- Projects without `github_repo` make no GitHub requests.

Run an assessment:

```sh
projectmesh assess \
  --config config/projects.toml \
  --project my-service \
  --feature-request "Add a read-only export endpoint for archived records"
```

The report is written to the configured output directory (or `assessments/`
relative to the config file when omitted). `--output-dir` overrides that
location; relative CLI overrides are resolved from the current directory.
The output directory must not be inside any configured reference repository.
The command prints the report path on success.

```sh
projectmesh --help
projectmesh assess --help
```

## Development

```sh
uv sync --group dev
uv run pytest
```

Configuration and assessment tests use temporary repositories and a stub
provider; they never call an HTTP endpoint. The end-to-end CLI test likewise
injects a stub. See the specification for evidence classifications and
acceptance criteria.
