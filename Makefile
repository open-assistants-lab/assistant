.PHONY: help db-up db-down db-logs assistant cli http test

help:
	@echo "Assistant - Make Commands"
	@echo ""
	@echo "Database:"
	@echo "  make db-up       - Start PostgreSQL"
	@echo "  make db-down     - Stop PostgreSQL"
	@echo "  make db-logs     - View PostgreSQL logs"
	@echo ""
	@echo "Run Commands:"
	@echo "  make assistant cli      - Start CLI"
	@echo "  make assistant http    - Start HTTP server"
	@echo ""
	@echo "Test:"
	@echo "  make test        - Run tests"

db-up:
	cd docker && docker compose up -d

db-down:
	cd docker && docker compose down

db-logs:
	cd docker && docker compose logs -f postgres

assistant cli:
	uv run assistant cli

assistant http:
	uv run assistant http

test:
	uv run pytest