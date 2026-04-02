# Architecture

## Goal

The original repository proved that a ReAct agent and a simple RAG flow could live
in the same script. This heavyweight foundation turns that idea into a project that
can grow into a service with clear module boundaries.

## Current Layout

```text
.
├── app
│   ├── agent
│   ├── api
│   ├── core
│   ├── ingestion
│   ├── observability
│   ├── rag
│   ├── storage
│   └── tools
├── deployments
├── docs
├── scripts
├── src
└── tests
```

## Module Responsibilities

- `app/core`: shared settings, logging, and domain models
- `app/auth`: JWT/API-key authentication and RBAC composition
- `app/security`: rate limiting and security helpers
- `app/audit`: audit event orchestration
- `app/queue`: queue backend abstraction (`sqlite` / `redis`)
- `app/tools`: external tools such as Tavily search
- `app/ingestion`: document upload, chunking, and embedding orchestration
- `app/ingestion/worker.py`: background queue consumer for async ingestion jobs
- `app/rag`: vector store adapters and policy-search workflow
- `app/storage`: SQLite repositories for vectors, ingestion jobs, sessions, and chat history
- `app/observability`: route/RAG/latency metrics
- `deployments/observability`: local telemetry stack (OTEL collector, Jaeger, Prometheus, Loki, Grafana)
- `app/agent`: top-level ReAct orchestration
- `app/api`: FastAPI endpoints and schemas
- `src`: backwards-compatible script entrypoint
- `tests`: stdlib unit tests for the stable foundation layer
- `deployments`: container assets for service deployment

## Runtime Modes

- `mock`: deterministic local mode for tests, demos, and offline development
- `live`: LangGraph, OpenAI, Tavily, and Pinecone-backed execution

## Delivered in This Stage

- Security baseline with JWT + API key auth, RBAC permissions, request rate limiting, and audit logs
- Session persistence in SQLite with `sessions` and `chat_messages` tables
- Ingestion queue abstraction with retries, idempotency keys, dead-letter support, and `sqlite/redis` backend selection
- Ingestion module with text/PDF extraction and vectorized chunk persistence
- Request context propagation (`request_id`, `trace_id`, `session_id`, `job_id`) across logs
- Metrics endpoint for route counts, RAG hit rate, latency aggregates, HTTP request telemetry, and Prometheus exposition
- OTEL trace spans around HTTP requests, agent/RAG/tool calls, and ingestion job execution
- OTLP log export hooks for centralized log platforms
- Regression tests for ingestion queue, persistence, and latency guardrails
- Multi-suite dataset-driven regression runner that writes `reports/regression/latest.md` and `latest.json`
- Distributed-style stability validation runner for queue workers and API pressure tests (`reports/stability/latest.*`)
- CI regression quality gate (`.github/workflows/regression.yml`)

## Immediate Next Steps

- Move schema changes to migration tooling (Alembic) with versioned upgrades
- Add token refresh, key rotation, and revocation list support
- Add burn-rate SLO alert rules and runbooks for incident response
- Expand evaluation corpus with semantic scoring and judge-based checks
