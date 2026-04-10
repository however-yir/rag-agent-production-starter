# Shared Component Extraction Blueprint

This document defines a low-risk extraction path for RAG and Agent repositories under the same account.

## Objectives

- Reduce duplicated maintenance cost across RAG and Agent projects.
- Keep project-specific business logic in each repository.
- Move only stable, reusable infrastructure into shared modules.

## Proposed Shared Areas

1. `shared-lib/config`
- environment loading
- typed settings validation
- provider/endpoint presets

2. `shared-lib/retrieval`
- vector store adapters
- embedding client wrappers
- rerank and top-k utilities

3. `shared-lib/agent-runtime`
- tool registration contract
- agent state model
- retry/timeout policy

4. `shared-lib/observability`
- structured logging
- trace/span context helpers
- latency and token metrics hooks

## Template and CLI

- `template/` keeps project scaffold and default config layout.
- `cli/` keeps bootstrap, lint, test, and secret-scan helper commands.

## Migration Strategy

1. Start from utility-level code with no business coupling.
2. Keep old interfaces and add adapters in each project.
3. Migrate one module at a time and validate CI.
4. Remove duplicated implementation only after parity tests pass.

## Candidate Repositories

- graph-rag-agent
- yourrag
- rag-agent-production-starter
- yu-ai-agent (agent runtime subset)
