# Architecture

## Boundaries and data flow

```text
CLI -> TOML config -> LangGraph assessment workflow
                              |
            prepare_context: read-only repository snapshot
                              |        (+ optional GitHub evidence)
              +---------------+----------------+
              |                                |
  assess_specification              assess_implementation_impact
  repository-context analysis       implementation-impact analysis
  per-analysis provider             per-analysis provider
  (falls back to [provider])        (falls back to [provider])
              |                                |
              |                  provider interface
              +---------------+----------------+
                              |
            assemble_report -> Markdown renderer -> output file (written by CLI)
```

- **CLI/config** parse options and validate project/provider settings.
- **Repository reader** reads eligible UTF-8 files from a configured local
  directory, skips generated/VCS directories, symlinks, binary files, and
  oversized files, then freezes those inputs in a snapshot with a stable
  SHA-256. It never writes or executes repository files.
- **GitHub reader** (optional, when `github_repo` is set) fetches issues,
  pull requests, and discussions through one read-only GraphQL query, renders
  each item to deterministic text keyed by its URL, and freezes them in a
  GitHub snapshot with a stable SHA-256. Only the repository-context analysis
  receives this snapshot; the implementation-impact analysis does not.
- **Orchestrator** (`orchestrator.py`) runs the assessment as a LangGraph
  `StateGraph` with four nodes: `prepare_context`, `assess_specification`,
  `assess_implementation_impact`, and `assemble_report`. The two analyses fan
  out in parallel after `prepare_context`. Every node works on the same
  snapshot, and each analysis uses its own provider (falls back to
  `[provider]`). Sequencing and report structure are deterministic; model prose
  itself is not. LangGraph and LangSmith are imported only by this module.
- **Spec analysis** evaluates documented repository context and feature fit;
  **code analysis** assesses likely implementation impact. Both use shared
  prompt-response validation. Citation paths must exist in the snapshot and
  quoted text must match exactly; unverifiable claims are demoted to
  unsupported.
- **Provider interface** exposes a minimal completion operation. The MVP
  implementation sends OpenAI-compatible chat-completions requests using
  Python's standard library. A claude-cli adapter runs completions through the
  local Claude Code CLI for users with a Claude Code subscription. Tests inject
  a local stub.
- **Renderer** renders the typed assessment model and source metadata to
  Markdown. The CLI writes only this application output, and output inside a
  configured source repository is rejected.

## Workflow graph

| Node | Reads from state | Writes to state | Calls |
| --- | --- | --- | --- |
| `prepare_context` | context project | `snapshot`, `github`, `completed_stages` | `capture_repository`; `fetch_github_snapshot` only when `github_repo` is set |
| `assess_specification` | `feature_request`, `snapshot`, `github` | `spec_result`, `completed_stages` | `analyze_spec` with the `spec_analysis` provider from context (default `[provider]`) |
| `assess_implementation_impact` | `feature_request`, `snapshot` | `code_result`, `completed_stages` | `analyze_code` with the `code_analysis` provider from context (default `[provider]`); never receives GitHub evidence |
| `assemble_report` | `feature_request`, `snapshot`, `github`, `spec_result`, `code_result`, `generated_at` | `assessment`, `report_markdown`, `completed_stages` | `assemble_assessment`, `render_markdown` |

- **State vs. context.** Plain run data (request text, snapshots, analysis
  results, rendered Markdown) lives in graph state. Provider objects,
  provider configuration, the per-analysis provider mapping, and project
  settings live in the LangGraph runtime context, so credentials and clients
  are never part of state. Large snapshots are passed by reference. The graph
  has no checkpointer, so nothing is serialized or persisted.
- **Fan-out/fan-in edges.** The graph is `START -> prepare_context`, then
  `prepare_context` fans out to `assess_specification` and
  `assess_implementation_impact` in one superstep, which join before
  `assemble_report -> END`. The two analyses run concurrently. The graph has
  no branching, retries, checkpointer, or interrupts. The order of
  `completed_stages` between the two analyses is unspecified. Providers must be
  safe to call from multiple threads. Invalid completions are still corrected
  inside the analysis code, not by the graph.
- **Error behavior.** A node that raises stops the graph. The original
  exception propagates unchanged. For example, a GitHub failure stops before
  any provider call. A failing analysis stops the graph before
  `assemble_report`. Its sibling analysis may already be running, and that
  sibling's provider call may complete (costing tokens).
- **Report writing stays in the CLI.** The graph ends with rendered Markdown
  in state. The CLI still validates the output directory and report path, and
  it writes the file atomically.

## Extension points

- Implement the provider protocol to support another model service without
  changing analysis or report code.
- Add analysis capabilities by following
  [Extending the workflow with further agents](#extending-the-workflow-with-further-agents).
  Each new capability keeps the typed `AnalysisResult` shape and joins the
  fan-out before `assemble_report`.
- Extend TOML configuration while keeping validation centralized.
  `[agents.<capability>]` tables give each analysis its own provider.
- Add output formats by implementing another renderer for the assessment
  model.

## Extending the workflow with further agents

1. Write the capability as a LangGraph-free module that returns
   `AnalysisResult`.
2. Add a state key and an adapter node in `orchestrator.py`. Add the
   capability id to `ANALYSIS_CAPABILITIES` in `models.py` so it can be set
   under `[agents.<capability>]`. The node reads from state, calls
   `ctx.provider_for(<id>)` on the runtime context, and returns its result and
   its stage name in `completed_stages`.
3. Wire its edge from `prepare_context`, and add its stage to
   `ANALYSIS_STAGES` so `assemble_report` waits for it.
4. Update `assemble_assessment` and `render.py`. Today both unpack exactly two
   analyses.
5. Add a transition test in `tests/test_orchestrator.py` that checks the
   fan-out edges, the keys each update writes, that the analyses run
   concurrently, and the stop behavior on error.

## Read-only and privacy guarantees

The CLI accepts only local project paths and does not invoke Git, clone, or
fetch. Repository access is read-only; symlinks are excluded. The report
output directory is resolved and checked against every project root before
writing. Tests compare fixture repository contents before and after
assessment.

The LangGraph workflow runs in-process with no checkpointer and no
persistence. LangSmith tracing is forced off for each run with
`langsmith.tracing_context(enabled=False)`, even when `LANGSMITH_TRACING` or
`LANGCHAIN_TRACING_V2` is set.

Provider calls are the only network-capable operation, apart from one
read-only GitHub GraphQL query per assessment when `github_repo` is set. Each
configured endpoint receives the prompt and inspected text, including fetched
GitHub text, when an assessment runs.
The claude-cli provider runs the local `claude` binary with tools and MCP
disabled from a temporary working directory.
Credentials are read from the environment and are never written to reports
or logged. `GITHUB_TOKEN` is read from the environment only for the GitHub
query, and GitHub error messages never include the token or response bodies.
Tests replace this boundary with a stub and do not make network calls; GitHub
tests stub `projectmesh.github.urlopen`.
