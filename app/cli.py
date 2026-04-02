"""CLI entrypoint for local demos."""

from __future__ import annotations

import argparse
import json

from app.agent.service import ReActAgentService
from app.core.settings import AppSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the RAG + ReAct demo queries.")
    parser.add_argument("query", nargs="?", help="A single query to run.")
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        default=None,
        help="Override the service mode for this run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit responses as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = AppSettings.from_env()
    if args.mode == "mock":
        settings.use_mock_services = True
    elif args.mode == "live":
        settings.use_mock_services = False

    service = ReActAgentService(settings)
    queries = [args.query] if args.query else list(settings.default_queries)

    for query in queries:
        response = service.answer(query)
        if args.json:
            print(json.dumps(response.to_dict(), indent=2))
            continue

        print("=" * 80)
        print(f"Query: {query}")
        print(f"Route: {response.route}")
        print(f"Answer: {response.answer}")
        if response.tool_calls:
            print("Tools:")
            for tool_call in response.tool_calls:
                print(f"- {tool_call.name}: {tool_call.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
