#!/usr/bin/env python3
"""Distributed-style load and stability validation for API and ingestion queue."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.settings import AppSettings
from app.ingestion.service import IngestionService
from app.storage.database import Database
from app.storage.ingestion_job_repository import IngestionJobRepository
from app.storage.vector_repository import VectorRepository


@dataclass(slots=True)
class RequestSample:
    endpoint: str
    method: str
    status_code: int
    latency_ms: float
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 4),
            "ok": self.ok,
            "error": self.error,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stability validation suites.")
    subparsers = parser.add_subparsers(dest="scenario", required=True)

    queue_parser = subparsers.add_parser("queue", help="Distributed queue worker stability test.")
    queue_parser.add_argument("--jobs", type=int, default=240)
    queue_parser.add_argument("--workers", type=int, default=4)
    queue_parser.add_argument("--max-jobs-per-tick", type=int, default=8)
    queue_parser.add_argument("--idle-loops", type=int, default=10)
    queue_parser.add_argument("--idle-sleep", type=float, default=0.2)
    queue_parser.add_argument("--max-retries", type=int, default=2)
    queue_parser.add_argument("--failure-ratio", type=float, default=0.1)
    queue_parser.add_argument("--backend", choices=("sqlite", "redis"), default="sqlite")
    queue_parser.add_argument("--redis-url", default="")
    queue_parser.add_argument("--database-path", default="")
    queue_parser.add_argument("--seed", type=int, default=42)
    queue_parser.add_argument("--max-worker-runtime-seconds", type=int, default=120)
    queue_parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "reports" / "stability"),
    )

    api_parser = subparsers.add_parser("api", help="Concurrent API load test.")
    api_parser.add_argument("--mode", choices=("inprocess", "external"), default="inprocess")
    api_parser.add_argument("--base-urls", default="http://127.0.0.1:8000")
    api_parser.add_argument("--total-requests", type=int, default=300)
    api_parser.add_argument("--concurrency", type=int, default=40)
    api_parser.add_argument("--chat-weight", type=float, default=0.65)
    api_parser.add_argument("--ingestion-weight", type=float, default=0.25)
    api_parser.add_argument("--process-weight", type=float, default=0.10)
    api_parser.add_argument("--username", default="admin")
    api_parser.add_argument("--password", default="admin123")
    api_parser.add_argument("--request-timeout-seconds", type=float, default=10.0)
    api_parser.add_argument("--max-p95-ms", type=float, default=1000.0)
    api_parser.add_argument("--max-error-rate", type=float, default=0.05)
    api_parser.add_argument("--database-path", default="")
    api_parser.add_argument("--seed", type=int, default=7)
    api_parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "reports" / "stability"),
    )

    return parser.parse_args(argv)


def _build_settings_for_validation(
    *,
    database_path: str,
    queue_backend: str = "sqlite",
    redis_url: str = "",
) -> AppSettings:
    return AppSettings(
        use_mock_services=True,
        security_enabled=False,
        database_path=database_path,
        ingestion_queue_backend=queue_backend,
        redis_url=redis_url,
        ingestion_retry_backoff_seconds=0,
        ingestion_retry_max_backoff_seconds=0,
        chunk_size=240,
        chunk_overlap=20,
        retrieval_top_k=4,
    )


def run_queue_validation(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("queue_%Y%m%d_%H%M%S")
    db_path = (
        Path(args.database_path)
        if args.database_path
        else output_dir / f"{run_id}.db"
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    settings = _build_settings_for_validation(
        database_path=str(db_path),
        queue_backend=args.backend,
        redis_url=args.redis_url,
    )
    database = Database(settings)
    repository = IngestionJobRepository(database)
    service = IngestionService(
        settings=settings,
        repository=VectorRepository(database),
        job_repository=repository,
    )

    rng = random.Random(args.seed)
    expected_failures = 0
    for index in range(args.jobs):
        should_fail = rng.random() < max(0.0, min(args.failure_ratio, 1.0))
        if should_fail:
            expected_failures += 1
        service.enqueue_text(
            knowledge_base="stability",
            source_name=f"{run_id}-job-{index:04d}.txt",
            text=(
                "   "
                if should_fail
                else (
                    f"Distributed worker stability doc #{index}. "
                    f"token-{index} confirms deterministic ingestion content."
                )
            ),
            max_retries=args.max_retries,
            trace_id=f"{run_id}-{index}",
        )

    result_queue: mp.Queue[dict[str, object]] = mp.Queue()
    workers: list[mp.Process] = []
    started = time.perf_counter()
    for worker_id in range(args.workers):
        process = mp.Process(
            target=_queue_worker_entry,
            args=(
                worker_id,
                str(db_path),
                args.backend,
                args.redis_url,
                args.max_jobs_per_tick,
                args.idle_loops,
                args.idle_sleep,
                args.max_worker_runtime_seconds,
                result_queue,
            ),
            daemon=True,
        )
        process.start()
        workers.append(process)

    worker_summaries: list[dict[str, object]] = []
    for _ in workers:
        try:
            worker_summaries.append(result_queue.get(timeout=args.max_worker_runtime_seconds + 10))
        except Exception:
            worker_summaries.append({"worker_id": "unknown", "error": "worker summary timeout"})

    for process in workers:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    all_jobs = repository.list_jobs(limit=max(args.jobs * 5, 2000))
    run_jobs = [item for item in all_jobs if item.source_name.startswith(run_id)]
    status_counts: dict[str, int] = {}
    for job in run_jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1

    succeeded = status_counts.get("succeeded", 0)
    failed = status_counts.get("failed", 0)
    queued = status_counts.get("queued", 0)
    processing = status_counts.get("processing", 0)
    duplicate_succeeded_attempts = sum(
        1 for item in run_jobs if item.status == "succeeded" and item.attempt_count > 1
    )
    observed_backend = sorted({item.queue_backend for item in run_jobs})
    worker_errors = [item for item in worker_summaries if item.get("error")]

    checks = [
        {
            "name": "all_jobs_terminal",
            "passed": queued == 0 and processing == 0,
            "detail": f"queued={queued}, processing={processing}",
        },
        {
            "name": "expected_success_count",
            "passed": succeeded == args.jobs - expected_failures,
            "detail": f"expected={args.jobs - expected_failures}, actual={succeeded}",
        },
        {
            "name": "expected_failed_count",
            "passed": failed == expected_failures,
            "detail": f"expected={expected_failures}, actual={failed}",
        },
        {
            "name": "no_duplicate_success_attempts",
            "passed": duplicate_succeeded_attempts == 0,
            "detail": f"duplicate_success_attempts={duplicate_succeeded_attempts}",
        },
        {
            "name": "workers_exit_without_errors",
            "passed": len(worker_errors) == 0,
            "detail": f"worker_errors={len(worker_errors)}",
        },
    ]

    summary: dict[str, Any] = {
        "scenario": "queue",
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_path),
        "config": {
            "jobs": args.jobs,
            "workers": args.workers,
            "backend": args.backend,
            "max_retries": args.max_retries,
            "failure_ratio": args.failure_ratio,
            "max_jobs_per_tick": args.max_jobs_per_tick,
        },
        "result": {
            "duration_ms": round(elapsed_ms, 4),
            "status_counts": status_counts,
            "expected_failures": expected_failures,
            "observed_backends": observed_backend,
            "worker_summaries": worker_summaries,
            "checks": checks,
            "passed": all(item["passed"] for item in checks),
        },
    }
    summary["report_paths"] = _write_report(summary, output_dir=output_dir, prefix="queue")
    return summary


def _queue_worker_entry(
    worker_id: int,
    database_path: str,
    backend: str,
    redis_url: str,
    max_jobs_per_tick: int,
    idle_loops: int,
    idle_sleep: float,
    max_runtime_seconds: int,
    result_queue: mp.Queue[dict[str, object]],
) -> None:
    started = time.perf_counter()
    try:
        settings = _build_settings_for_validation(
            database_path=database_path,
            queue_backend=backend,
            redis_url=redis_url,
        )
        database = Database(settings)
        service = IngestionService(
            settings=settings,
            repository=VectorRepository(database),
            job_repository=IngestionJobRepository(database),
        )

        processed_total = 0
        idle_count = 0
        loop_count = 0
        while idle_count < idle_loops and (time.perf_counter() - started) < max_runtime_seconds:
            processed = service.process_jobs(max_jobs=max_jobs_per_tick)
            loop_count += 1
            if processed == 0:
                idle_count += 1
                time.sleep(max(0.01, idle_sleep))
                continue
            idle_count = 0
            processed_total += processed

        result_queue.put(
            {
                "worker_id": worker_id,
                "processed_total": processed_total,
                "loops": loop_count,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 4),
                "error": "",
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "worker_id": worker_id,
                "processed_total": 0,
                "loops": 0,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 4),
                "error": str(exc),
            }
        )


def run_api_validation(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_run_api_validation_async(args))


async def _run_api_validation_async(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("api_%Y%m%d_%H%M%S")

    if args.mode == "inprocess":
        base_urls = ["http://testserver"]
    else:
        base_urls = [item.strip() for item in args.base_urls.split(",") if item.strip()]
        if not base_urls:
            raise ValueError("When mode=external, at least one base URL is required.")

    samples: list[RequestSample] = []
    rng = random.Random(args.seed)
    headers: dict[str, str] = {}
    app_cleanup = None
    clients: list[httpx.AsyncClient] = []

    try:
        if args.mode == "inprocess":
            client, app_cleanup = await _build_inprocess_client(args, run_id)
            clients = [client]
        else:
            timeout = httpx.Timeout(args.request_timeout_seconds)
            clients = [
                httpx.AsyncClient(base_url=base_url, timeout=timeout)
                for base_url in base_urls
            ]

        login_sample, headers = await _authenticate(
            client=clients[0],
            username=args.username,
            password=args.password,
        )
        samples.append(login_sample)
        if not login_sample.ok:
            summary = _build_api_summary(
                run_id=run_id,
                args=args,
                samples=samples,
                checks=[
                    {
                        "name": "auth_login_success",
                        "passed": False,
                        "detail": login_sample.error or f"status={login_sample.status_code}",
                    }
                ],
            )
            summary["report_paths"] = _write_report(summary, output_dir=output_dir, prefix="api")
            return summary

        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        tasks = []
        started = time.perf_counter()
        for request_index in range(args.total_requests):
            task = asyncio.create_task(
                _run_api_request(
                    request_index=request_index,
                    semaphore=semaphore,
                    clients=clients,
                    headers=headers,
                    rng=rng,
                    chat_weight=args.chat_weight,
                    ingestion_weight=args.ingestion_weight,
                    process_weight=args.process_weight,
                    run_id=run_id,
                )
            )
            tasks.append(task)

        for result in await asyncio.gather(*tasks):
            samples.append(result)
        total_duration_ms = (time.perf_counter() - started) * 1000.0

        # Drain remaining ingestion work once at the end.
        drain_sample = await _request_process_jobs(clients[0], headers=headers, max_jobs=200)
        samples.append(drain_sample)

        checks = _build_api_checks(
            args=args,
            samples=samples,
            total_duration_ms=total_duration_ms,
        )
        summary = _build_api_summary(
            run_id=run_id,
            args=args,
            samples=samples,
            checks=checks,
            total_duration_ms=total_duration_ms,
        )
        summary["report_paths"] = _write_report(summary, output_dir=output_dir, prefix="api")
        return summary
    finally:
        for client in clients:
            await client.aclose()
        if app_cleanup is not None:
            app_cleanup()


async def _run_api_request(
    *,
    request_index: int,
    semaphore: asyncio.Semaphore,
    clients: list[httpx.AsyncClient],
    headers: dict[str, str],
    rng: random.Random,
    chat_weight: float,
    ingestion_weight: float,
    process_weight: float,
    run_id: str,
) -> RequestSample:
    async with semaphore:
        operation = _pick_operation(
            rng=rng,
            chat_weight=chat_weight,
            ingestion_weight=ingestion_weight,
            process_weight=process_weight,
        )
        client = clients[request_index % len(clients)]
        if operation == "chat":
            return await _request_chat(client, headers=headers, rng=rng)
        if operation == "ingestion":
            return await _request_ingestion(
                client,
                headers=headers,
                run_id=run_id,
                request_index=request_index,
            )
        return await _request_process_jobs(client, headers=headers, max_jobs=5)


def _pick_operation(
    *,
    rng: random.Random,
    chat_weight: float,
    ingestion_weight: float,
    process_weight: float,
) -> str:
    total = max(chat_weight + ingestion_weight + process_weight, 1e-9)
    value = rng.random() * total
    if value < chat_weight:
        return "chat"
    if value < chat_weight + ingestion_weight:
        return "ingestion"
    return "process"


async def _authenticate(
    *,
    client: httpx.AsyncClient,
    username: str,
    password: str,
) -> tuple[RequestSample, dict[str, str]]:
    started = time.perf_counter()
    try:
        response = await client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        ok = response.status_code == 200
        if not ok:
            return (
                RequestSample(
                    endpoint="/auth/login",
                    method="POST",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    ok=False,
                    error=response.text[:200],
                ),
                {},
            )
        token = response.json()["access_token"]
        return (
            RequestSample(
                endpoint="/auth/login",
                method="POST",
                status_code=response.status_code,
                latency_ms=latency_ms,
                ok=True,
            ),
            {"Authorization": f"Bearer {token}"},
        )
    except Exception as exc:
        return (
            RequestSample(
                endpoint="/auth/login",
                method="POST",
                status_code=0,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=False,
                error=str(exc),
            ),
            {},
        )


async def _request_chat(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    rng: random.Random,
) -> RequestSample:
    prompts = [
        "How should staff handle a disruptive service animal?",
        "What documentation is required after an incident?",
        "What is the weather in Kanyakumari today?",
    ]
    payload = {
        "query": prompts[rng.randrange(0, len(prompts))],
        "mode": "mock",
        "knowledge_base": "default",
    }
    return await _timed_request(
        client=client,
        method="POST",
        endpoint="/chat",
        headers=headers,
        json_body=payload,
        expected_statuses={200},
    )


async def _request_ingestion(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    run_id: str,
    request_index: int,
) -> RequestSample:
    payload = {
        "knowledge_base": "stability",
        "source_name": f"{run_id}-api-{request_index:04d}.txt",
        "text": (
            "Stability ingestion sample for distributed API pressure validation. "
            f"request_index={request_index}"
        ),
    }
    merged_headers = dict(headers)
    merged_headers["Idempotency-Key"] = f"{run_id}-{request_index}"
    return await _timed_request(
        client=client,
        method="POST",
        endpoint="/ingestion/text",
        headers=merged_headers,
        json_body=payload,
        expected_statuses={202},
    )


async def _request_process_jobs(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    max_jobs: int,
) -> RequestSample:
    return await _timed_request(
        client=client,
        method="POST",
        endpoint=f"/ingestion/jobs/process?max_jobs={max_jobs}",
        headers=headers,
        expected_statuses={200},
    )


async def _timed_request(
    *,
    client: httpx.AsyncClient,
    method: str,
    endpoint: str,
    headers: dict[str, str],
    expected_statuses: set[int],
    json_body: dict[str, object] | None = None,
) -> RequestSample:
    started = time.perf_counter()
    try:
        response = await client.request(
            method=method,
            url=endpoint,
            headers=headers,
            json=json_body,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        ok = response.status_code in expected_statuses
        return RequestSample(
            endpoint=endpoint.split("?")[0],
            method=method,
            status_code=response.status_code,
            latency_ms=latency_ms,
            ok=ok,
            error="" if ok else response.text[:220],
        )
    except Exception as exc:
        return RequestSample(
            endpoint=endpoint.split("?")[0],
            method=method,
            status_code=0,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            ok=False,
            error=str(exc),
        )


async def _build_inprocess_client(
    args: argparse.Namespace,
    run_id: str,
) -> tuple[httpx.AsyncClient, Any]:
    database_path = (
        args.database_path
        if args.database_path
        else str(Path(tempfile.gettempdir()) / f"{run_id}.db")
    )
    keys = {
        "DATABASE_PATH": os.environ.get("DATABASE_PATH"),
        "SECURITY_ENABLED": os.environ.get("SECURITY_ENABLED"),
        "BOOTSTRAP_ADMIN_USERNAME": os.environ.get("BOOTSTRAP_ADMIN_USERNAME"),
        "BOOTSTRAP_ADMIN_PASSWORD": os.environ.get("BOOTSTRAP_ADMIN_PASSWORD"),
        "USE_MOCK_SERVICES": os.environ.get("USE_MOCK_SERVICES"),
        "RATE_LIMIT_PER_MINUTE": os.environ.get("RATE_LIMIT_PER_MINUTE"),
        "INGESTION_EMBEDDED_WORKER_ENABLED": os.environ.get("INGESTION_EMBEDDED_WORKER_ENABLED"),
        "LOG_LEVEL": os.environ.get("LOG_LEVEL"),
        "LOG_JSON": os.environ.get("LOG_JSON"),
        "OPEN_TELEMETRY_ENABLED": os.environ.get("OPEN_TELEMETRY_ENABLED"),
        "OPEN_TELEMETRY_LOGS_ENABLED": os.environ.get("OPEN_TELEMETRY_LOGS_ENABLED"),
    }
    os.environ["DATABASE_PATH"] = database_path
    os.environ["SECURITY_ENABLED"] = "true"
    os.environ["BOOTSTRAP_ADMIN_USERNAME"] = args.username
    os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = args.password
    os.environ["USE_MOCK_SERVICES"] = "true"
    os.environ["RATE_LIMIT_PER_MINUTE"] = "5000"
    os.environ["INGESTION_EMBEDDED_WORKER_ENABLED"] = "false"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["LOG_JSON"] = "false"
    os.environ["OPEN_TELEMETRY_ENABLED"] = "false"
    os.environ["OPEN_TELEMETRY_LOGS_ENABLED"] = "false"

    from app.api import dependencies as api_dependencies
    from app.main import create_app

    _clear_dependency_caches(api_dependencies)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=httpx.Timeout(args.request_timeout_seconds),
    )

    def cleanup() -> None:
        for key, value in keys.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _clear_dependency_caches(api_dependencies)

    return client, cleanup


def _clear_dependency_caches(api_dependencies: object) -> None:
    api_dependencies.get_ingestion_service_singleton.cache_clear()
    api_dependencies.get_rate_limiter.cache_clear()
    api_dependencies.get_auth_service.cache_clear()
    api_dependencies.get_audit_service.cache_clear()
    api_dependencies.get_auth_repository.cache_clear()
    api_dependencies.get_audit_repository.cache_clear()
    api_dependencies.get_ingestion_job_repository.cache_clear()
    api_dependencies.get_session_repository.cache_clear()
    api_dependencies.get_vector_repository.cache_clear()
    api_dependencies.get_database.cache_clear()
    api_dependencies.get_settings.cache_clear()


def _build_api_checks(
    *,
    args: argparse.Namespace,
    samples: list[RequestSample],
    total_duration_ms: float,
) -> list[dict[str, object]]:
    measured = [item for item in samples if item.endpoint != "/auth/login"]
    latencies = [item.latency_ms for item in measured]
    total = max(len(measured), 1)
    failures = sum(1 for item in measured if not item.ok)
    error_rate = failures / total
    p95 = _percentile(latencies, 95.0)
    throughput = (len(measured) / (total_duration_ms / 1000.0)) if total_duration_ms > 0 else 0.0

    return [
        {
            "name": "error_rate",
            "passed": error_rate <= args.max_error_rate,
            "detail": (
                f"expected<={args.max_error_rate:.4f}, actual={error_rate:.4f}, "
                f"failed={failures}/{len(measured)}"
            ),
        },
        {
            "name": "p95_latency",
            "passed": p95 <= args.max_p95_ms,
            "detail": f"expected<={args.max_p95_ms:.2f}ms, actual={p95:.2f}ms",
        },
        {
            "name": "throughput_nonzero",
            "passed": throughput > 0.0,
            "detail": f"throughput_rps={throughput:.4f}",
        },
    ]


def _build_api_summary(
    *,
    run_id: str,
    args: argparse.Namespace,
    samples: list[RequestSample],
    checks: list[dict[str, object]],
    total_duration_ms: float = 0.0,
) -> dict[str, Any]:
    measured = [item for item in samples if item.endpoint != "/auth/login"]
    latencies = [item.latency_ms for item in measured]
    status_counts: dict[str, int] = {}
    endpoint_counts: dict[str, int] = {}
    for item in measured:
        status_counts[str(item.status_code)] = status_counts.get(str(item.status_code), 0) + 1
        endpoint_counts[item.endpoint] = endpoint_counts.get(item.endpoint, 0) + 1

    summary: dict[str, Any] = {
        "scenario": "api",
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "mode": args.mode,
            "base_urls": args.base_urls,
            "total_requests": args.total_requests,
            "concurrency": args.concurrency,
            "chat_weight": args.chat_weight,
            "ingestion_weight": args.ingestion_weight,
            "process_weight": args.process_weight,
            "request_timeout_seconds": args.request_timeout_seconds,
            "max_p95_ms": args.max_p95_ms,
            "max_error_rate": args.max_error_rate,
        },
        "result": {
            "duration_ms": round(total_duration_ms, 4),
            "total_samples": len(samples),
            "measured_samples": len(measured),
            "status_counts": status_counts,
            "endpoint_counts": endpoint_counts,
            "latency_ms": {
                "p50": round(_percentile(latencies, 50.0), 4),
                "p95": round(_percentile(latencies, 95.0), 4),
                "p99": round(_percentile(latencies, 99.0), 4),
                "max": round(max(latencies) if latencies else 0.0, 4),
            },
            "checks": checks,
            "failed_samples": [item.to_dict() for item in measured if not item.ok][:50],
            "passed": all(bool(item["passed"]) for item in checks),
        },
    }
    return summary


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _write_report(
    summary: dict[str, Any],
    *,
    output_dir: Path,
    prefix: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp_key = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{prefix}_{timestamp_key}.json"
    md_path = output_dir / f"{prefix}_{timestamp_key}.md"
    latest_json = output_dir / f"{prefix}_latest.json"
    latest_md = output_dir / f"{prefix}_latest.md"

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2, ensure_ascii=False)
    with md_path.open("w", encoding="utf-8") as file:
        file.write(_render_markdown(summary))

    shutil.copyfile(json_path, latest_json)
    shutil.copyfile(md_path, latest_md)
    shutil.copyfile(json_path, output_dir / "latest.json")
    shutil.copyfile(md_path, output_dir / "latest.md")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "shared_latest_json": str(output_dir / "latest.json"),
        "shared_latest_markdown": str(output_dir / "latest.md"),
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Stability Validation Report",
        "",
        f"- Scenario: `{summary['scenario']}`",
        f"- Run ID: `{summary['run_id']}`",
        f"- Timestamp (UTC): `{summary['timestamp_utc']}`",
        "",
        "## Config",
        "```json",
        json.dumps(summary["config"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Result Snapshot",
        "```json",
        json.dumps(summary["result"], indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scenario == "queue":
        summary = run_queue_validation(args)
    else:
        summary = run_api_validation(args)

    result = summary["result"]
    passed = bool(result.get("passed", False))
    print(f"Scenario: {summary['scenario']}")
    print(f"Passed: {passed}")
    print(f"Report JSON: {summary['report_paths']['json']}")
    print(f"Report MD: {summary['report_paths']['markdown']}")
    checks = result.get("checks", [])
    for item in checks:
        print(f"- {item['name']}: {'PASS' if item['passed'] else 'FAIL'} ({item['detail']})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
