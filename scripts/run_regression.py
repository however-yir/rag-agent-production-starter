#!/usr/bin/env python3
"""Run dataset-driven regression checks and emit a report."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent.service import ReActAgentService
from app.core.settings import AppSettings
from app.ingestion.service import IngestionService
from app.rag.service import PolicySearchService
from app.storage.database import Database
from app.storage.ingestion_job_repository import IngestionJobRepository
from app.storage.vector_repository import VectorRepository


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(slots=True)
class CaseResult:
    dataset: str
    case_id: str
    query: str
    knowledge_base: str
    route: str
    latency_ms: float
    passed: bool
    checks: list[CheckResult]
    answer_preview: str
    evidence_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "case_id": self.case_id,
            "query": self.query,
            "knowledge_base": self.knowledge_base,
            "route": self.route,
            "latency_ms": round(self.latency_ms, 4),
            "passed": self.passed,
            "checks": [item.to_dict() for item in self.checks],
            "answer_preview": self.answer_preview,
            "evidence_count": self.evidence_count,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run evaluation datasets and generate report.")
    parser.add_argument(
        "--dataset",
        default=str(REPO_ROOT / "evaluation" / "datasets"),
        help="Path to one dataset JSON file or a directory containing JSON datasets.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "reports" / "regression"),
        help="Directory where report files will be written.",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        default="mock",
        help="Execution mode for the agent.",
    )
    parser.add_argument(
        "--database-path",
        default=None,
        help="Optional sqlite path to use for this regression run.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.9,
        help="Minimum required pass rate for quality gate.",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit with status 1 when any case fails or pass rate is below threshold.",
    )
    return parser.parse_args(argv)


def load_dataset_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if "documents" not in payload or "cases" not in payload:
        raise ValueError(f"Dataset must include both 'documents' and 'cases': {path}")
    payload.setdefault("name", path.stem)
    payload["_path"] = str(path)
    return payload


def load_datasets(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return [load_dataset_file(path)]
    if not path.exists():
        raise ValueError(f"Dataset path does not exist: {path}")
    files = sorted(item for item in path.glob("*.json") if item.is_file())
    if not files:
        raise ValueError(f"No dataset JSON files found in: {path}")
    return [load_dataset_file(item) for item in files]


def build_settings(mode: str, database_path: str) -> AppSettings:
    settings = AppSettings.from_env()
    settings.database_path = database_path
    settings.use_mock_services = mode == "mock"
    settings.ingestion_retry_backoff_seconds = 0
    settings.ingestion_retry_max_backoff_seconds = 0
    settings.security_enabled = False
    return settings


def run_regression(
    *,
    dataset_path: Path,
    output_dir: Path,
    mode: str,
    database_path: str | None,
) -> dict[str, Any]:
    datasets = load_datasets(dataset_path)
    run_timestamp = datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp_key = run_timestamp.strftime("%Y%m%d_%H%M%S")
    db_path = (
        Path(database_path)
        if database_path
        else output_dir / f"regression_runtime_{timestamp_key}.db"
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    settings = build_settings(mode=mode, database_path=str(db_path))
    database = Database(settings)
    vector_repository = VectorRepository(database)
    job_repository = IngestionJobRepository(database)
    ingestion_service = IngestionService(
        settings=settings,
        repository=vector_repository,
        job_repository=job_repository,
    )
    policy_service = PolicySearchService(settings=settings, vector_repository=vector_repository)
    agent_service = ReActAgentService(settings=settings, policy_service=policy_service)

    ingested_keys: set[tuple[str, str]] = set()
    ingestion_jobs: list[dict[str, object]] = []
    for dataset in datasets:
        for document in dataset["documents"]:
            knowledge_base = str(document.get("knowledge_base", "default"))
            source_name = str(document.get("source_name", "unknown.txt"))
            dedupe_key = (knowledge_base, source_name)
            if dedupe_key in ingested_keys:
                continue
            ingested_keys.add(dedupe_key)
            job = ingestion_service.enqueue_text(
                knowledge_base=knowledge_base,
                source_name=source_name,
                text=str(document.get("text", "")),
                source_type="text",
                metadata={"dataset": str(dataset.get("name", "unnamed"))},
            )
            ingestion_jobs.append(job.to_dict())

    ingestion_service.run_until_idle(max_iterations=200, jobs_per_iteration=50)
    final_jobs = ingestion_service.list_jobs(limit=max(len(ingestion_jobs), 1) + 20)
    final_jobs_map = {job.id: job for job in final_jobs}
    failed_jobs = [
        final_jobs_map[str(job["id"])]
        for job in ingestion_jobs
        if str(job["id"]) in final_jobs_map and final_jobs_map[str(job["id"])].status == "failed"
    ]

    case_results: list[CaseResult] = []
    per_dataset_totals: dict[str, dict[str, float]] = {}
    for dataset in datasets:
        dataset_name = str(dataset.get("name", "unnamed"))
        for case in dataset["cases"]:
            result = evaluate_case(
                dataset_name=dataset_name,
                case=case,
                agent_service=agent_service,
            )
            case_results.append(result)
            bucket = per_dataset_totals.setdefault(
                dataset_name,
                {"cases": 0.0, "passed": 0.0, "failed": 0.0},
            )
            bucket["cases"] += 1
            if result.passed:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1

    for dataset_name, item in per_dataset_totals.items():
        total = item["cases"] or 1.0
        item["pass_rate"] = round(item["passed"] / total, 4)

    total_cases = len(case_results)
    passed_cases = sum(1 for item in case_results if item.passed)
    failed_cases = total_cases - passed_cases
    pass_rate = (passed_cases / total_cases) if total_cases else 0.0

    summary: dict[str, Any] = {
        "datasets": [str(item.get("name", "unnamed")) for item in datasets],
        "dataset_count": len(datasets),
        "timestamp_utc": run_timestamp.isoformat(),
        "mode": mode,
        "database_path": str(db_path),
        "ingestion": {
            "queued_documents": len(ingestion_jobs),
            "failed_jobs": [job.to_dict() for job in failed_jobs],
        },
        "totals": {
            "cases": total_cases,
            "passed": passed_cases,
            "failed": failed_cases,
            "pass_rate": round(pass_rate, 4),
        },
        "per_dataset_totals": per_dataset_totals,
        "cases": [item.to_dict() for item in case_results],
    }

    report_paths = write_reports(summary=summary, output_dir=output_dir)
    summary["report_paths"] = report_paths
    return summary


def evaluate_case(
    *,
    dataset_name: str,
    case: dict[str, Any],
    agent_service: ReActAgentService,
) -> CaseResult:
    case_id = str(case.get("id", "unknown_case"))
    query = str(case.get("query", ""))
    knowledge_base = str(case.get("knowledge_base", "default"))

    started = time.perf_counter()
    response = agent_service.answer(query, knowledge_base=knowledge_base)
    latency_ms = (time.perf_counter() - started) * 1000.0

    checks: list[CheckResult] = []

    expected_route = case.get("expected_route")
    if expected_route is not None:
        matches = response.route == str(expected_route)
        checks.append(
            CheckResult(
                name="route",
                passed=matches,
                detail=f"expected={expected_route}, actual={response.route}",
            )
        )

    min_evidence = case.get("min_evidence")
    if min_evidence is not None:
        evidence_count = len(response.evidence)
        passed = evidence_count >= int(min_evidence)
        checks.append(
            CheckResult(
                name="evidence",
                passed=passed,
                detail=f"expected>={min_evidence}, actual={evidence_count}",
            )
        )

    max_latency_ms = case.get("max_latency_ms")
    if max_latency_ms is not None:
        passed = latency_ms <= float(max_latency_ms)
        checks.append(
            CheckResult(
                name="latency",
                passed=passed,
                detail=f"expected<={max_latency_ms}, actual={latency_ms:.2f}",
            )
        )

    answer_text = response.answer.lower()
    evidence_text = "\n".join(item.content for item in response.evidence).lower()

    expected_keywords = case.get("expected_keywords", [])
    for keyword in expected_keywords:
        token = str(keyword).lower()
        passed = token in answer_text or token in evidence_text
        checks.append(
            CheckResult(
                name=f"keyword:{keyword}",
                passed=passed,
                detail=f"keyword_present={passed}",
            )
        )

    expected_absent_keywords = case.get("expected_absent_keywords", [])
    for keyword in expected_absent_keywords:
        token = str(keyword).lower()
        passed = token not in answer_text
        checks.append(
            CheckResult(
                name=f"absent_keyword:{keyword}",
                passed=passed,
                detail=f"keyword_absent={passed}",
            )
        )

    passed = all(item.passed for item in checks) if checks else True
    answer_preview = response.answer.replace("\n", " ").strip()
    if len(answer_preview) > 160:
        answer_preview = answer_preview[:157] + "..."

    return CaseResult(
        dataset=dataset_name,
        case_id=case_id,
        query=query,
        knowledge_base=knowledge_base,
        route=response.route,
        latency_ms=latency_ms,
        passed=passed,
        checks=checks,
        answer_preview=answer_preview,
        evidence_count=len(response.evidence),
    )


def write_reports(summary: dict[str, Any], output_dir: Path) -> dict[str, str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"regression_{timestamp}.json"
    md_path = output_dir / f"regression_{timestamp}.md"
    latest_json = output_dir / "latest.json"
    latest_md = output_dir / "latest.md"

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2, ensure_ascii=False)
    with md_path.open("w", encoding="utf-8") as file:
        file.write(render_markdown_report(summary))

    shutil.copyfile(json_path, latest_json)
    shutil.copyfile(md_path, latest_md)
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    lines = [
        "# Regression Report",
        "",
        f"- Datasets: `{', '.join(summary['datasets'])}`",
        f"- Timestamp (UTC): `{summary['timestamp_utc']}`",
        f"- Mode: `{summary['mode']}`",
        f"- Cases: `{totals['cases']}`",
        f"- Passed: `{totals['passed']}`",
        f"- Failed: `{totals['failed']}`",
        f"- Pass rate: `{totals['pass_rate']}`",
        "",
        "## Dataset Summary",
        "",
        "| Dataset | Cases | Passed | Failed | Pass rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for dataset_name, item in summary["per_dataset_totals"].items():
        lines.append(
            f"| {dataset_name} | {int(item['cases'])} | {int(item['passed'])} | "
            f"{int(item['failed'])} | {item['pass_rate']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Dataset | Case | Status | Route | Latency(ms) | Evidence |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for case in summary["cases"]:
        status_text = "PASS" if case["passed"] else "FAIL"
        lines.append(
            f"| {case['dataset']} | {case['case_id']} | {status_text} | {case['route']} | "
            f"{case['latency_ms']:.2f} | {case['evidence_count']} |"
        )

    lines.extend(["", "## Failed Checks", ""])
    failures = [case for case in summary["cases"] if not case["passed"]]
    if not failures:
        lines.append("No failed checks.")
    else:
        for case in failures:
            lines.append(f"### {case['dataset']} / {case['case_id']}")
            lines.append(f"- Query: `{case['query']}`")
            for check in case["checks"]:
                if not check["passed"]:
                    lines.append(f"- {check['name']}: {check['detail']}")
            lines.append("")

    lines.extend(["## Ingestion", ""])
    failed_jobs = summary["ingestion"]["failed_jobs"]
    lines.append(f"- Queued documents: `{summary['ingestion']['queued_documents']}`")
    lines.append(f"- Failed ingestion jobs: `{len(failed_jobs)}`")
    if failed_jobs:
        for job in failed_jobs:
            lines.append(
                f"- Job `{job['id']}` source=`{job['source_name']}` error=`{job['last_error']}`"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_regression(
        dataset_path=Path(args.dataset),
        output_dir=Path(args.output_dir),
        mode=args.mode,
        database_path=args.database_path,
    )

    report_paths = summary["report_paths"]
    pass_rate = float(summary["totals"]["pass_rate"])
    below_threshold = pass_rate < float(args.min_pass_rate)
    failed_cases = int(summary["totals"]["failed"])

    print(f"Regression report JSON: {report_paths['json']}")
    print(f"Regression report Markdown: {report_paths['markdown']}")
    print(
        "Summary: "
        f"datasets={summary['dataset_count']}, "
        f"passed={summary['totals']['passed']}, "
        f"failed={failed_cases}, "
        f"pass_rate={pass_rate}"
    )
    if below_threshold:
        print(
            "Quality gate failed: "
            f"pass_rate={pass_rate} < min_pass_rate={args.min_pass_rate}"
        )
    if args.fail_on_errors and (failed_cases > 0 or below_threshold):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
