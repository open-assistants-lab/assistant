FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy pyproject.toml and uv.lock (needed for build)
COPY pyproject.toml uv.lock ./

# Install dependencies — base + heavy extras (memory-vector: chromadb,
# analytics: duckdb). Tradeoff: heavier image vs full features. To build a
# lean image without semantic memory/analytics, override:
#   docker build --build-arg EXTRAS="" .
# --no-install-project: deps only here; the project itself is installed after
# source is copied (hatchling force-include needs seeds/ present).
ARG EXTRAS="--extra memory-vector --extra analytics"
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS

# Copy source
COPY src/ src/
COPY config.yaml ./
# Seed prompts (summarisation, grader) — resolved relative to repo root
COPY seeds/ seeds/

# Install the project itself (editable, no re-resolve of deps)
RUN uv pip install --no-deps -e .

# Default command — console script is assistant-sdk (renamed in the PyPI
# packaging commit; the old `assistant` script no longer exists).
CMD ["uv", "run", "assistant-sdk", "http"]
