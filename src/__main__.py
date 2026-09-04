"""Assistant - Main entry point."""

import argparse
import sys
from pathlib import Path as _Path

from dotenv import load_dotenv

load_dotenv(_Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="assistant", description="Assistant")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("http", help="Start HTTP server")
    subparsers.add_parser(
        "desktop-server", help="Run the local desktop sidecar (v0.1)"
    )

    args = parser.parse_args()

    if args.command == "desktop-server":
        from src.http.desktop import desktop_main

        desktop_main()
        return

    if args.command == "http" or args.command is None:
        from src.http.main import run as http_run

        http_run()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
