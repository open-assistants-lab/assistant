# Assistant

[![Stars](https://img.shields.io/github/stars/open-assistants-lab/assistant)](https://github.com/open-assistants-lab/assistant)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Build your assistant, not its infrastructure.**

Assistant is a self-hostable engine for assistants that work with your files and tools—not just your questions. Build an application with profiles, custom tools, skills and your own domain rules, using shared agent execution, persistence, APIs and model-provider integrations.

Operate it on your machine or your own server. Builders own application behaviour; deployment teams own identity, credentials, data and operations. A finished native product remains a separate goal on the same engine; macOS product work is currently parked.

Learn more at [openassistants.org](https://openassistants.org) — including [why this stack exists](https://openassistants.org/story).

## For builders and deployment teams

Start with the [builder/deployer conventions and adoption checklist](docs/builder-deployment-guide.md), then the [runtime deployment guide](DEPLOYMENT.md).

The [synthetic Jen reference](examples/jen_reference/README.md) is an executable example of independent instance preparation, fixture approvals, readiness and stopped-copy recovery. It is not a production Jen deployment or proof that eddyave/admi have adopted these conventions. Docker smoke was deferred for this round; configuration validation is not startup proof.

## Engine capabilities

| What | How |
|------|-----|
| **Chat & History** | Session history, streaming responses and configurable memory/search. |
| **Application Content** | Package `PROFILE.md`, custom `TOOL.md` tools and `SKILL.md` skills; retain authored domain rules and ontology. |
| **Web Research** | Search the web, scrape pages, crawl documentation. Ask a question and get an answer with sources. |
| **Files** | Read, write, edit files in your workspace. Version history for every change. |
| **Skills** | Load specialized skill packs for specific tasks — browser automation, code review, debugging, and more. |
| **Subagents** | Create specialized mini-assistants that work on tasks in parallel. |
| **App Builder** | Build simple database apps with structured data and hybrid search. |
| **Browser Automation** | Control a browser to fill forms, take screenshots, test web apps, or automate logins. |
| **MCP Integration** | Connect any Model Context Protocol server to add custom tools. |
| **Native Experiment** | Existing Zig + Native SDK macOS client (`native-sdk-experiment/`); not a finished distributed product or proof of remote-instance interoperability. |

Availability depends on deployment policy, credentials and optional dependencies. The legacy built-in email, contacts and todos families and their APIs have been removed (breaking change). Existing data is retained, not automatically migrated or deleted. Use the generic `app_*` tools for structured data and optional [starter templates](DEPLOYMENT.md#retired-stores-and-optional-app-templates); ConnectKit remains a general integration seam, not a first-party mail replacement.

## Configuration

For local development, put provider credentials in `.env` (never commit them):

```bash
# Pick your provider and add your key
OPENAI_API_KEY=sk-...
# or ANTHROPIC_API_KEY=...
# or OLLAMA_API_KEY=...
```

Choose the model/provider and review `config.yaml`. Deployments must explicitly configure runtime/data paths, access policy and required integrations. Server access credentials (`API_KEY` or per-user tokens) are distinct from model-provider credentials. See the [deployment guide](DEPLOYMENT.md) before exposing a server beyond loopback.

## Data Privacy

You choose where the runtime and storage live (local or your server) and whether inference uses a local or cloud provider. Cloud inference sends selected context to that provider; external tools and configured tracing are separate data flows. **A local runtime does not mean everything stays local.**

User data defaults to `~/Assistant/`; some settings and operational state also live under the configured project data path. Inventory and back up both. Deployment teams choose identity/account provisioning and observability destinations. See [the data-flow and operations conventions](docs/builder-deployment-guide.md#operation-and-data-flows).

## For Developers

```bash
# Install
uv sync --extra dev

# Run a loopback-only development server (default port: 8080)
API_HOST=127.0.0.1 uv run assistant http

# Tests
uv run pytest

# Lint & type check
uv run ruff check src/
uv run mypy src/
```

### Distribution

```bash
# Optional extras install from source via uv (no PyPI path):
uv sync --extra memory-vector      # + ChromaDB + sentence-transformers (semantic memory/embeddings)
uv sync --extra analytics          # + DuckDB analytics mirror
API_HOST=127.0.0.1 assistant http    # local development; configure your model
```

Heavy optional features (vector search, semantic embeddings, analytics) live in
extras so the base install stays light; lazy-import sites log an install hint
when the matching extra is missing.

### Build

The backend is the API server — for local development, run with `API_HOST=127.0.0.1 uv run assistant http`. The unmodified host default is `0.0.0.0`; do not mistake it for loopback-only binding.

The **native desktop app** lives in `native-sdk-experiment/` (Zig + Native SDK):

```bash
cd native-sdk-experiment
native dev                        # run the app (hot reload)
uv run native test                # Zig unit tests (90)
bash tests/frontend_suite.sh --all  # automation suite (51 tests)
```

### Architecture

- **Agent**: Custom SDK `AgentLoop` (ReAct) with tool calling
- **Backend**: Python FastAPI server (REST + SSE + WebSocket)
- **Storage**: SQLite-backed messages and application state; optional hybrid/vector search. Legacy email/contacts/todos stores are retained unread for deliberate operator export.
- **LLM Providers**: OpenAI, Anthropic, Gemini, Ollama (local & cloud)

### Acknowledgments

This project builds on ideas and research from several projects in the AI agent memory and search space:

| Project | Contribution |
|---------|-------------|
| [LangChain](https://github.com/langchain-ai/langchain) & [LangGraph](https://github.com/langchain-ai/langgraph) | Original agent framework. Assistant started on LangChain/LangGraph before migrating to a custom SDK. |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Progressive disclosure pattern for memory retrieval (3-layer workflow: list → load → full). |
| [Claude Code](https://code.claude.com) | Auto-memory and insights system. |
| [ASMR](https://github.com/supermemoryai/supermemory) | Agentic Search and Memory Retrieval — hybrid (keyword + vector + field) search approach. |
| [LongMemEval](https://github.com/xiaowu0162/longmemeval) | Comprehensive benchmark for evaluating long-term interactive memory in chat assistants. |
| [ChromaDB](https://github.com/chroma-core/chroma) | Vector search engine with HNSW indexing. |
| [Firecrawl](https://github.com/mendableai/firecrawl) | Web scraping and search API. |
| [Agent-Browser](https://agent-browser.dev) | Pure Rust CLI for browser automation by Vercel Labs. |
| [Agent Skills](https://agentskills.io) | Open format for giving agents new capabilities via folders of instructions and scripts. |
