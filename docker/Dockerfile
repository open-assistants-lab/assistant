FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy pyproject.toml and uv.lock (needed for build)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ src/
COPY config.yaml ./
# Seed prompts (summarisation, grader) — resolved relative to repo root
COPY seeds/ seeds/

# Default command
CMD ["uv", "run", "assistant", "http"]
