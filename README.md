# Assistant

[![Download DMG](https://img.shields.io/badge/download-macOS-brightgreen?logo=apple)](https://github.com/your-org/assistant/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Your personal AI assistant that runs on your machine. Chat, email, tasks, research, files — all through a desktop app, powered by your choice of LLM (OpenAI, Anthropic, Ollama, Gemini).

<!-- TODO: Add screenshot of Flutter chat interface -->

## Download

- **[macOS DMG (Apple Silicon)](https://github.com/your-org/assistant/releases/latest)** — download, open, and you're set.
- **Windows / Linux** — coming soon.

## Features

| What | How |
|------|-----|
| **Chat** | Conversational AI with memory across sessions. It remembers who you are, your preferences, and your work. |
| **Email** | Connect Gmail, Outlook, iCloud, or any IMAP account. Read, search, send, reply — hands-free. |
| **Tasks & Contacts** | Add todos, manage contacts. The assistant can extract them from conversation automatically. |
| **Web Research** | Search the web, scrape pages, crawl documentation. Ask a question and get an answer with sources. |
| **Files** | Read, write, edit files in your workspace. Version history for every change. |
| **Skills** | Load specialized skill packs for specific tasks — browser automation, code review, debugging, and more. |
| **Subagents** | Create specialized mini-assistants that work on tasks in parallel. |
| **App Builder** | Build simple database apps with structured data and hybrid search. |
| **Browser Automation** | Control a browser to fill forms, take screenshots, test web apps, or automate logins. |
| **MCP Integration** | Connect any Model Context Protocol server to add custom tools. |

## Configuration

Your API keys go in `.env` — no config files to edit:

```bash
# Pick your provider and add your key
OPENAI_API_KEY=sk-...
# or ANTHROPIC_API_KEY=...
# or OLLAMA_API_KEY=...
```

Everything else (model, memory, sync intervals) is pre-configured with sensible defaults. Change them in `config.yaml` if you want, but you don't need to.

## Data Privacy

Everything runs locally on your machine. Your data lives at `~/Assistant/` — no cloud, no telemetry, no accounts. You control the model, the keys, and the data.

## For Developers

```bash
# Install
uv sync --extra dev

# Run the server
uv run assistant http

# Tests
uv run pytest

# Lint & type check
uv run ruff check src/
uv run mypy src/
```

### Build the DMG

```bash
./flutter_app/build_macos.sh
```

### Architecture

- **Agent**: Custom SDK `AgentLoop` (ReAct) with tool calling
- **Frontend**: Flutter desktop app (thin client, WebSocket to backend)
- **Backend**: Python FastAPI server, embedded in the `.app` bundle via PyInstaller
- **Storage**: SQLite for messages, contacts, todos, email. ChromaDB for vector search.
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
