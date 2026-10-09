# projectmesh

projectmesh is a small, project-agnostic CLI for assessing a feature request
against one of your existing local repository checkouts. It creates a
traceable Markdown report with two analyses: repository context and feature
fit, followed by likely implementation impact.

The inspected repository is read-only: projectmesh does not clone, fetch,
modify, or create files in it. Reports are written separately. The first
provider is an OpenAI-compatible chat-completions HTTP endpoint; endpoint and
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
