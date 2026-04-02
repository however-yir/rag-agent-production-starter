# Operations Hardening Playbook

## Scope

This playbook covers three production-hardening tracks:

1. Distributed queue stability validation.
2. Tracing and centralized log platform integration.
3. Expanded regression/evaluation datasets.

## 1) Distributed Queue Stability

Run multi-process worker validation against a single queue/database:

```bash
python scripts/run_stability_validation.py queue \
  --jobs 240 \
  --workers 4 \
  --backend sqlite \
  --failure-ratio 0.1 \
  --max-retries 2
```

Or use Redis queue backend:

```bash
python scripts/run_stability_validation.py queue \
  --jobs 240 \
  --workers 4 \
  --backend redis \
  --redis-url redis://localhost:6379/0
```

Expected pass criteria:

- No `queued` / `processing` jobs left.
- Succeeded/failed counts match expected injected workload.
- No succeeded jobs with `attempt_count > 1`.
- No worker process runtime errors.

## 2) Tracing and Log Platform Integration

Start the full observability stack:

```bash
docker compose -f deployments/observability/docker-compose.yml up --build -d
```

Endpoints:

- API: `http://localhost:8000`
- Grafana: `http://localhost:3000` (admin/admin)
- Prometheus: `http://localhost:9090`
- Jaeger: `http://localhost:16686`
- Loki: `http://localhost:3100`

Telemetry linkage:

- App/worker export traces via OTLP to collector.
- App/worker export logs via OTLP logs to collector.
- Collector forwards traces to Jaeger and logs to Loki.
- Prometheus scrapes `/metrics/prometheus`.

## 3) Expanded Evaluation Suites

Run the dataset directory gate:

```bash
python scripts/run_regression.py --mode mock --min-pass-rate 0.9 --fail-on-errors
```

Expanded suites now include:

- `routing_suite_v2`
- `rag_single_hop_v2`
- `rag_multi_hop_v2`
- `hallucination_guard_v2`
- `cross_kb_matrix_v1`

Regression output:

- `reports/regression/latest.json`
- `reports/regression/latest.md`

Stability output:

- `reports/stability/latest.json`
- `reports/stability/latest.md`
