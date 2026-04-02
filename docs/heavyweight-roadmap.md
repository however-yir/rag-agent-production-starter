# Heavyweight Roadmap

## Phase 1

- Modularize the single-file demo into service layers
- Add API and CLI entrypoints
- Standardize configuration, dependencies, and local bootstrap
- Add tests and deployment scaffolding

## Phase 2

- Add ingestion jobs for policy documents
- Add persistent chat memory and session store
- Add retrieval evaluation and prompt regression suites
- Add structured tracing and tool-call observability

## Phase 3

- Introduce multi-agent routing
- Add document upload APIs and asynchronous ingestion (delivered with retry queue + worker)
- Add auth, rate limiting, and audit logs (delivered in baseline form)
- Support local-model fallback with Ollama or vLLM

## Phase 4

- Move queue, auth, and audit schema evolution to migration-based workflow
- Add refresh/revocation and API-key rotation controls
- Wire tracing/logging/metrics into dashboards and alerting pipelines (baseline delivered via `deployments/observability`)
- Expand evaluation datasets and hard quality gates in CI/CD (expanded datasets delivered; semantic judge scoring remains)
- Add distributed load/stability gates before release
