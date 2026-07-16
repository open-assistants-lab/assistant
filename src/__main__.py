"""Assistant - Main entry point."""

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(prog="assistant", description="Assistant")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("http", help="Start HTTP server")

    args = parser.parse_args()

    if args.command == "http" or args.command is None:
        from src.http.main import run as http_run

        http_run()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
