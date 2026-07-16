from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime

from tests.smoke.models import SmokeReport
from tests.smoke.reporter import write_report
from tests.smoke.server_manager import ServerConfig, managed_server
from tests.smoke.suite_personas import run_persona_suite
from tests.smoke.suite_streaming import run_streaming_suite

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly smoke suite for Assistant"
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OLLAMA_API_KEY", ""),
        help="OllamaCloud API key (default: $OLLAMA_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default="ollama-cloud:deepseek-v4-flash",
        help="Model string for the agent",
    )
    parser.add_argument(
        "--personas",
        default="5",
        help="Personas: number (5) or 'all' (25)",
    )
    parser.add_argument(
        "--streaming",
        default="all",
        help="Streaming categories: 'all' or comma-separated (basic,tool,smalltools,reasoning,accumulation,error,multiturn,integrity)",
    )
    parser.add_argument(
        "--output",
        default="data/smoke",
        help="Output directory for report and logs",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print report to stdout",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        dest="server_port",
        help="Port for the HTTP server (0 = auto-assign)",
    )
    return parser.parse_args()


async def main():
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = ServerConfig(
        port=args.server_port,
        api_key=args.api_key or None,
        model=args.model,
    )

    streaming_cats = (
        None if args.streaming == "all" else [c.strip() for c in args.streaming.split(",")]
    )

    start = datetime.now()

    async with managed_server(config) as base_url:
        logger.info("Server ready at %s", base_url)

        queries_per = 4 if args.personas.lower() == "all" else 25
        persona_suite = await run_persona_suite(
            base_url,
            persona_count=args.personas,
            queries_per_persona=queries_per,
        )
        logger.info(
            "Persona suite: %d/%d passed in %s",
            persona_suite.passed,
            persona_suite.total,
            _fmt_duration(persona_suite.duration_ms),
        )

        streaming_suite = await run_streaming_suite(
            base_url,
            categories=streaming_cats,
        )
        logger.info(
            "Streaming suite: %d/%d passed in %s",
            streaming_suite.passed,
            streaming_suite.total,
            _fmt_duration(streaming_suite.duration_ms),
        )

    total_tests = persona_suite.total + streaming_suite.total
    total_passed = persona_suite.passed + streaming_suite.passed
    total_duration = persona_suite.duration_ms + streaming_suite.duration_ms

    report = SmokeReport(
        timestamp=start.isoformat(),
        suites=[persona_suite, streaming_suite],
        total_tests=total_tests,
        total_passed=total_passed,
        total_duration_ms=total_duration,
        model=args.model,
    )

    write_report(args.output, report, verbose=args.verbose)

    exit_code = 0 if total_passed == total_tests else 1
    raise SystemExit(exit_code)


def _fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


if __name__ == "__main__":
    asyncio.run(main())
