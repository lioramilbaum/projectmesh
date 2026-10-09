# Architecture

## Boundaries and data flow

```text
CLI -> TOML config -> read-only repository snapshot
                              |
                    deterministic orchestrator
                     /                    \
        repository-context analysis   implementation-impact analysis
                     \                    /
                       provider interface
                              |
                    assessment model -> Markdown renderer -> output file
```

- **CLI/config** parse options and validate project/provider settings.
- **Repository reader** reads eligible UTF-8 files from a configured local
  directory, skips generated/VCS directories, symlinks, binary files, and
  oversized files, then freezes those inputs in a snapshot with a stable
  SHA-256. It never writes or executes repository files.
- **Orchestrator** runs the two analysis capabilities sequentially against
  the same snapshot and selected provider. Sequencing and report structure
  are deterministic; model prose itself is not.
- **Spec analysis** evaluates documented repository context and feature fit;
  **code analysis** assesses likely implementation impact. Both use shared
  prompt-response validation. Citation paths must exist in the snapshot and
  quoted text must match exactly; unverifiable claims are demoted to
  unsupported.
- **Provider interface** exposes a minimal completion operation. The MVP
  implementation sends OpenAI-compatible chat-completions requests using
  Python's standard library. Tests inject a local stub.
- **Renderer** renders the typed assessment model and source metadata to
  Markdown. Only this application output is written, and output inside a
  configured source repository is rejected.

## Extension points

- Implement the provider protocol to support another model service without
  changing analysis or report code.
- Add analysis capabilities behind the same typed result shape and
  sequential orchestration contract.
- Extend TOML configuration while keeping validation centralized.
- Add output formats by implementing another renderer for the assessment
  model.

## Read-only and privacy guarantees

The CLI accepts only local project paths and does not invoke Git, clone, or
fetch. Repository access is read-only; symlinks are excluded. The report
output directory is resolved and checked against every project root before
writing. Tests compare fixture repository contents before and after
assessment.

Provider calls are the only network-capable operation. The configured
endpoint receives the prompt and inspected text when an assessment runs.
Credentials are read from the environment and are never written to reports
or logged. Tests replace this boundary with a stub and do not make network
calls.
