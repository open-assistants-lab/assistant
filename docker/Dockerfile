FROM python:3.13-slim

WORKDIR /app

# OS utilities for TOOL.md command-based custom tools (bridge HTTP calls,
# JSON post-processing, timezone data for zoneinfo). uv installed below.
# libstdc++6 is needed by the Node runtime copied in below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq tzdata ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Node + the firecrawl CLI.
#
# The property-research agent's ONLY web access is `shell_execute` running the
# firecrawl CLI — see skills/property-research/briefs/*.md. That tool executes
# INSIDE this container, so a firecrawl install on the host is not visible to it:
# the binary and its API key must exist here. Pinned to 1.15.2 to match the host
# and the acceptance gate, so a captured page is reproducible across machines.
#
# Node is copied from the official image rather than installed from Debian
# repos: firecrawl-cli needs a modern Node, and the Debian nodejs in
# bookworm/trixie is too old. `npm install -g` needs the real npm on PATH, which
# the copy does not create, hence the two symlinks.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g firecrawl-cli@1.15.2 \
    && firecrawl --version

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

# Default command — console script is assistant (
# packaging commit; the old `assistant` script no longer exists).
CMD ["uv", "run", "assistant", "http"]
