# Agent Connect — Implementation Plan

> Detailed build plan for a standalone open-source library that connects AI agents to user SaaS accounts. One YAML file per service. OAuth, token vault, and tool discovery handled automatically.

**Status:** Ready to build  
**Parent proposal:** `docs/AGENT_CONNECT_PROPOSAL.md`  
**Date:** May 1, 2026

---

## 0. Setup — New Open-Source Package Within EA Repo

Agent Connect is a separate Python package developed inside the EA monorepo (same pattern as HybridDB was extracted). When ready, it gets its own GitHub repo.

```
assistant/
├── packages/
│   └── agent-connect/           # ← New package
│       ├── agent_connect/
│       │   ├── __init__.py      # Public API
│       │   ├── spec.py          # ConnectorSpec Pydantic model
│       │   ├── vault.py         # CredentialVault (encrypted SQLite)
│       │   ├── oauth.py         # OAuth callback endpoints
│       │   ├── backends/
│       │   │   ├── __init__.py
│       │   │   ├── cli.py       # CLIAdapter (user-aware subprocess)
│       │   │   └── mcp.py       # MCPAdapter (vault token → bridge)
│       │   ├── runtime.py       # ConnectorRuntime (YAML → tools)
│       │   ├── bridge.py        # AgentConnectBridge (EA integration)
│       │   ├── meta_tools.py    # connector_list, connector_connect, etc.
│       │   └── health.py        # Connector health checks
│       ├── connectors/          # YAML specs (shipped with the package)
│       │   ├── firecrawl.yaml
│       │   ├── agent-browser.yaml
│       │   ├── google-workspace.yaml
│       │   ├── github.yaml
│       │   └── ...
│       ├── tests/
│       │   ├── test_spec.py
│       │   ├── test_vault.py
│       │   ├── test_oauth.py
│       │   ├── test_cli_adapter.py
│       │   ├── test_mcp_adapter.py
│       │   ├── test_runtime.py
│       │   └── test_integration.py
│       ├── pyproject.toml
│       └── README.md
├── src/
│   └── sdk/
│       ├── runner.py            # ← Modified: wire in AgentConnectBridge
│       └── native_tools.py      # ← Modified: register connector meta-tools
└── docs/
    ├── AGENT_CONNECT_PROPOSAL.md
    └── AGENT_CONNECT_PLAN.md     # ← This file
```

### Decision: License

MIT. Simple, permissive, maximum adoption. Same as HybridDB.

---

## Phase 1 — Foundation (Week 1: Days 1-5)

### Day 1: Project Scaffolding + Spec Model

**Files to create:**

#### `packages/agent-connect/pyproject.toml`

```toml
[project]
name = "agent-connect"
version = "0.1.0"
description = "Connect AI agents to SaaS — one YAML file per service. OAuth, token vault, tool discovery."
requires-python = ">=3.11"
readme = "README.md"
license = {text = "MIT"}
keywords = ["agent", "ai", "saas", "oauth", "connector", "mcp", "tools"]

dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "cryptography>=41.0",
    "httpx>=0.28",
    "fastapi>=0.129",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["agent_connect"]
```

#### `packages/agent-connect/agent_connect/__init__.py`

```python
"""Agent Connect — connect AI agents to SaaS accounts.

One YAML file per service. OAuth, token vault, and tool discovery handled automatically.
"""

__version__ = "0.1.0"

from agent_connect.spec import AuthType, ConnectorSpec, ToolSourceType
from agent_connect.vault import CredentialVault
from agent_connect.runtime import ConnectorRuntime
from agent_connect.bridge import AgentConnectBridge

__all__ = [
    "ConnectorSpec",
    "AuthType",
    "ToolSourceType",
    "CredentialVault",
    "ConnectorRuntime",
    "AgentConnectBridge",
]
```

#### `packages/agent-connect/agent_connect/spec.py`

Complete Pydantic model for the YAML format. This is the contract — every connector YAML validates against this.

```python
"""Connector specification model — the YAML schema.

A connector is a YAML file that describes:
1.  Service identity (name, display name, icon, category)
2.  Authentication (oauth2, api_key, basic, or none)
3.  Tool source (how tools are discovered: cli, mcp, or http)
4.  Tool descriptions (LLM-optimized, per tool)
"""

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AuthType(str, Enum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BASIC = "basic"
    NONE = "none"


class ToolSourceType(str, Enum):
    CLI = "cli"
    MCP = "mcp"
    HTTP = "http"


class OAuth2Config(BaseModel):
    authorize_url: str
    token_url: str
    scopes: list[str]
    extra_params: dict = Field(default_factory=dict)
    pkce: bool = False
    token_env_var: str = ""  # Which env var the CLI reads (e.g. GWS_ACCESS_TOKEN)


class ApiKeyConfig(BaseModel):
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"
    env_var: str  # e.g. FIRECRAWL_API_KEY


class AuthConfig(BaseModel):
    type: AuthType
    oauth2: OAuth2Config | None = None
    api_key: ApiKeyConfig | None = None


class CLIToolSource(BaseModel):
    type: Literal["cli"]
    command: str  # e.g. "gws", "gh", "firecrawl"
    install: str  # e.g. "npm install -g @googleworkspace/cli"
    env_mapping: dict[str, str] = Field(default_factory=dict)
    # Maps vault credential → env var: {"access_token": "GWS_ACCESS_TOKEN"}


class MCPToolSource(BaseModel):
    """MCP server as a tool source.

    The MCP server is started as a child process. The agent_connect MCPAdapter
    injects the OAuth token from the vault into the server's environment so
    the MCP server doesn't need its own auth.
    """

    type: Literal["mcp"]
    server_name: str  # e.g. "dropbox"
    command: str  # e.g. "npx @anthropic/dropbox-mcp"
    env_mapping: dict[str, str] = Field(default_factory=dict)


class HTTPToolEndpoint(BaseModel):
    """A single REST endpoint defined declaratively.

    Deferred to Phase 2 — the peer review correctly flagged that declarative
    REST breaks on pagination, nested resources, and rate limiting.
    """

    name: str
    description: str
    path: str
    method: str = "GET"
    parameters: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class HTTPToolSource(BaseModel):
    type: Literal["http"]
    base_url: str
    tools: list[HTTPToolEndpoint]


ToolSource = CLIToolSource | MCPToolSource | HTTPToolSource


class ToolDescription(BaseModel):
    """LLM-optimized description for a single tool.

    This is NOT API documentation. It's written for the agent — what the tool
    does, when to use it, what each parameter means in plain English.
    """

    name: str
    description: str
    # Optional overrides for parameter descriptions (better than CLI help text)
    parameter_descriptions: dict[str, str] = Field(default_factory=dict)


class ConnectorSpec(BaseModel):
    """Complete connector specification.

    Example YAML:
        name: google-workspace
        display: "Google Workspace"
        icon: google
        category: productivity
        description: "Gmail, Calendar, Drive, and Google Contacts"
        auth:
          type: oauth2
          oauth2:
            authorize_url: https://accounts.google.com/o/oauth2/v2/auth
            token_url: https://oauth2.googleapis.com/token
            scopes:
              - https://www.googleapis.com/auth/gmail.readonly
            extra_params:
              access_type: offline
              prompt: consent
            token_env_var: GWS_ACCESS_TOKEN
        tool_source:
          type: cli
          command: gws
          install: npm install -g @googleworkspace/cli
          env_mapping:
            access_token: GWS_ACCESS_TOKEN
        tool_descriptions:
          - name: gmail_list
            description: "List recent emails from the user's Gmail inbox"
            parameter_descriptions:
              max_results: "Number of emails to return (default 10)"
    """

    name: str
    display: str
    icon: str = "plug"
    category: str = "other"
    version: str = "1.0"
    description: str = ""
    auth: AuthConfig
    tool_source: ToolSource
    tool_descriptions: list[ToolDescription] = Field(default_factory=list)
    # For CLI/MCP backends, the backend auto-discovers tools. The agent
    # applies tool_descriptions on top — overriding names/descriptions from
    # the CLI's own help text with LLM-optimized versions.

    @field_validator("name")
    @classmethod
    def name_must_be_slug(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError(
                f"Connector name '{v}' must be lowercase alphanumeric with hyphens/underscores"
            )
        return v

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ConnectorSpec":
        """Load and validate a connector from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    @classmethod
    def from_yaml_dir(cls, directory: str | Path) -> list["ConnectorSpec"]:
        """Load all connector YAML files from a directory."""
        specs = []
        for yaml_file in sorted(Path(directory).glob("*.yaml")):
            try:
                specs.append(cls.from_yaml(yaml_file))
            except Exception as e:
                # Log and skip broken specs — don't block startup
                import logging

                logging.getLogger("agent_connect").warning(
                    f"Failed to load connector spec {yaml_file}: {e}"
                )
        return specs
```

**Tests:** `packages/agent-connect/tests/test_spec.py`

- Valid YAML parses correctly (happy path)
- Invalid YAML raises ValidationError
- Name validator rejects invalid slugs
- `from_yaml_dir` skips broken files and continues
- All three auth types (oauth2, api_key, none) parse
- Both tool source types (cli, mcp) parse
- `tool_descriptions` are optional

---

### Day 2-3: CredentialVault

#### `packages/agent-connect/agent_connect/vault.py`

Encrypted SQLite credential store. Each agent_connect instance creates one vault at `data/users/{user_id}/agent_connect/vault.db`. Encryption via `cryptography.fernet` with a master key from env var `AGENT_CONNECT_VAULT_KEY`.

```python
"""CredentialVault — encrypted per-user token storage.

Schema:
    CREATE TABLE credentials (
        service_name TEXT PRIMARY KEY,
        auth_type TEXT NOT NULL,       -- 'oauth2', 'api_key', 'basic'
        encrypted_data TEXT NOT NULL,  -- Fernet-encrypted JSON
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE oauth_states (
        state TEXT PRIMARY KEY,        -- Random nonce for OAuth flow
        service_name TEXT NOT NULL,
        user_id TEXT NOT NULL,
        redirect_uri TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL       -- States expire after 10 minutes
    );
"""

import json
import os
import sqlite3
import threading
import secrets
from contextlib import contextmanager
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_or_create_key() -> bytes:
    """Get the vault encryption key from env or generate one.

    In production, AGENT_CONNECT_VAULT_KEY must be set. If not set, generates
    a key that only works for the current process lifetime (test/dev only).
    """
    key_str = os.environ.get("AGENT_CONNECT_VAULT_KEY")
    if key_str:
        return key_str.encode()
    # Dev/test: generate a key. Not persistent across restarts — test only.
    import warnings

    warnings.warn(
        "AGENT_CONNECT_VAULT_KEY not set. Using ephemeral key — "
        "tokens will be lost on restart. Set in production."
    )
    return Fernet.generate_key()


class CredentialVault:
    """Encrypted credential store (SQLite + Fernet).

    Usage:
        vault = CredentialVault("/data/users/alice")
        vault.store_oauth_token("google-workspace", {
            "access_token": "...", "refresh_token": "...", "expires_at": "..."
        })
        token = vault.get_token("google-workspace")
    """

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._db_path = str((self.base_path / "vault.db").resolve())
        self._lock = threading.RLock()
        self._fernet = Fernet(_get_or_create_key())
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Cursor, None, None]:
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.cursor()
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self._connect() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    service_name TEXT PRIMARY KEY,
                    auth_type TEXT NOT NULL,
                    encrypted_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    service_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    redirect_uri TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

    def _encrypt(self, data: dict) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def _decrypt(self, encrypted: str) -> dict:
        return json.loads(self._fernet.decrypt(encrypted.encode()).decode())

    # ── Credential CRUD ────────────────────────────────────

    def store_token(self, service_name: str, auth_type: str, token_data: dict) -> None:
        now = _now_iso()
        encrypted = self._encrypt(token_data)
        with self._connect() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO credentials "
                "(service_name, auth_type, encrypted_data, created_at, updated_at) "
                "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM credentials WHERE service_name = ?), ?), ?)",
                (service_name, auth_type, encrypted, service_name, now, now),
            )

    def get_token(self, service_name: str) -> dict | None:
        with self._connect() as cur:
            cur.execute(
                "SELECT encrypted_data FROM credentials WHERE service_name = ?",
                (service_name,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._decrypt(row["encrypted_data"])

    def delete_token(self, service_name: str) -> bool:
        with self._connect() as cur:
            cur.execute("DELETE FROM credentials WHERE service_name = ?", (service_name,))
            return cur.rowcount > 0

    def list_connected(self) -> list[str]:
        with self._connect() as cur:
            cur.execute("SELECT service_name FROM credentials ORDER BY service_name")
            return [r["service_name"] for r in cur.fetchall()]

    def is_connected(self, service_name: str) -> bool:
        return self.get_token(service_name) is not None

    # ── OAuth State Management ─────────────────────────────

    def create_oauth_state(self, service_name: str, user_id: str, redirect_uri: str = "") -> str:
        state = secrets.token_urlsafe(32)
        now = _now_iso()
        expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        with self._connect() as cur:
            cur.execute(
                "INSERT INTO oauth_states (state, service_name, user_id, redirect_uri, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (state, service_name, user_id, redirect_uri, now, expires),
            )
        return state

    def validate_oauth_state(self, state: str) -> dict | None:
        """Validate an OAuth state parameter. Returns {service_name, user_id} or None."""
        with self._connect() as cur:
            # Clean expired states
            cur.execute("DELETE FROM oauth_states WHERE expires_at < ?", (_now_iso(),))
            cur.execute(
                "SELECT service_name, user_id FROM oauth_states WHERE state = ?",
                (state,),
            )
            row = cur.fetchone()
        if not row:
            return None
        # Delete the state — one-time use
        with self._connect() as cur:
            cur.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        return {"service_name": row["service_name"], "user_id": row["user_id"]}

    def health(self) -> dict:
        """Check vault health."""
        try:
            with self._connect() as cur:
                cur.execute("SELECT COUNT(*) FROM credentials")
                count = cur.fetchone()[0]
            return {"status": "ok", "connected_services": count}
        except Exception as e:
            return {"status": "broken", "error": str(e)}
```

**Tests:** `packages/agent-connect/tests/test_vault.py`

- Store + retrieve OAuth token (roundtrip)
- Store + retrieve API key
- Delete token (returns True for existing, False for missing)
- `list_connected()` returns correct list
- `is_connected()` works
- OAuth state create → validate → consumed (one-time)
- Invalid state returns None
- Expired state cleaned up and returns None
- Data is encrypted at rest (raw SQLite reads show ciphertext)
- Ephemeral key warning fires when env var missing
- Multiple services don't interfere

---

### Day 4-5: OAuth Callback Endpoint

#### `packages/agent-connect/agent_connect/oauth.py`

Universal OAuth 2.0 flow. One FastAPI router handles every connector. The connector spec drives the entire flow — no per-service code.

```python
"""Universal OAuth 2.0 endpoints — one router for every connector.

Flow:
    1. GET /auth/login?service=google-workspace&user_id=alice
       → Load spec, build authorize URL, redirect to Google
    2. Google redirects to GET /auth/callback?code=...&state=...
       → Exchange code for tokens, store in vault, return success

The connector spec (ConnectorSpec) drives everything:
    - authorize_url, token_url, scopes, extra_params from the spec
    - client_id, client_secret from deployment config (env vars or config dict)
    - tokens stored in CredentialVault per user
"""

import json
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query

from agent_connect.spec import AuthType, ConnectorSpec
from agent_connect.vault import CredentialVault

# Type for the config provider: returns {client_id, client_secret} per service
ConfigProvider = Callable[[str], dict[str, str]]


def create_oauth_router(
    specs: list[ConnectorSpec],
    vault_factory: Callable[[str], CredentialVault],
    config: ConfigProvider,
    base_url: str = "",
) -> APIRouter:
    """Create a FastAPI router with OAuth endpoints for all given connectors.

    Args:
        specs: List of connector specs (loaded from YAML directory).
        vault_factory: Function that returns a CredentialVault for a user_id.
        config: Function that returns {client_id, client_secret} for a service_name.
        base_url: Public-facing URL of this server (e.g. https://ea.example.com).

    Returns:
        FastAPI APIRouter with /auth/login and /auth/callback routes.
    """
    router = APIRouter(prefix="/auth", tags=["auth"])

    # Build a lookup from service_name → spec for fast auth
    oauth_services = {
        s.name: s
        for s in specs
        if s.auth.type == AuthType.OAUTH2 and s.auth.oauth2 is not None
    }

    def _build_authorize_url(spec: ConnectorSpec, state: str) -> str:
        oauth = spec.auth.oauth2
        assert oauth is not None
        cfg = config(spec.name)
        params = {
            "client_id": cfg["client_id"],
            "redirect_uri": _redirect_uri(base_url, spec.name),
            "response_type": "code",
            "scope": " ".join(oauth.scopes),
            "state": state,
            **oauth.extra_params,
        }
        return f"{oauth.authorize_url}?{urlencode(params)}"

    def _redirect_uri(base: str, service: str) -> str:
        if base:
            return f"{base.rstrip('/')}/auth/callback?service={service}"
        return f"/auth/callback?service={service}"

    @router.get("/login")
    async def oauth_login(
        service: str = Query(..., description="Connector name (e.g. google-workspace)"),
        user_id: str = Query(..., description="User ID (e.g. alice@corp.com)"),
    ):
        """Start OAuth flow for a service. Redirects to the provider's authorize page."""
        spec = oauth_services.get(service)
        if not spec:
            raise HTTPException(400, f"Unknown or non-OAuth service: {service}")

        vault = vault_factory(user_id)
        state = vault.create_oauth_state(service, user_id)

        url = _build_authorize_url(spec, state)
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url, status_code=302)

    @router.get("/callback")
    async def oauth_callback(
        code: str = Query(..., description="Authorization code from provider"),
        state: str = Query(..., description="OAuth state parameter"),
        service: str = Query("", description="Connector name"),
    ):
        """Handle OAuth callback. Exchanges code for tokens and stores in vault."""
        vault = vault_factory("")  # Temporary — we need to look up the user
        state_data = vault.validate_oauth_state(state)
        if not state_data:
            raise HTTPException(400, "Invalid or expired OAuth state")

        service_name = state_data["service_name"]
        user_id = state_data["user_id"]

        spec = oauth_services.get(service_name)
        if not spec:
            raise HTTPException(400, f"Unknown service: {service_name}")

        cfg = config(service_name)
        oauth = spec.auth.oauth2
        assert oauth is not None

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                oauth.token_url,
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _redirect_uri(base_url, service_name),
                },
                headers={"Accept": "application/json"},
            )

            if resp.status_code != 200:
                raise HTTPException(502, f"Token exchange failed: {resp.text}")

            token_data = resp.json()

        # Add metadata
        token_data["_obtained_at"] = datetime.now(UTC).isoformat()
        token_data["_scopes"] = oauth.scopes

        # Store in the user's vault
        user_vault = vault_factory(user_id)
        user_vault.store_token(service_name, "oauth2", token_data)

        return {
            "status": "connected",
            "service": service_name,
            "scopes": oauth.scopes,
        }

    @router.get("/status")
    async def auth_status(user_id: str = Query(...)):
        """List connected services for a user."""
        vault = vault_factory(user_id)
        connected = vault.list_connected()
        return {
            "connected": [
                {"service": s, "has_token": True} for s in connected
            ]
        }

    return router
```

**Tests:** `packages/agent-connect/tests/test_oauth.py`

- `GET /auth/login` redirects to correct authorize URL with scopes
- OAuth state stored and included in redirect
- `GET /auth/callback` with valid state + code stores tokens
- Callback with invalid state returns 400
- Callback with expired state returns 400
- Callback for non-existent service returns 400
- `GET /auth/status` returns connected services list
- Service not in specs returns 400 on login
- Multiple users don't leak tokens between vaults

---

## Phase 2 — Adapter Backends (Week 2: Days 6-7)

### Day 6: CLIAdapter

#### `packages/agent-connect/agent_connect/backends/__init__.py`

```python
"""Adapter backends — translate connector specs into agent tools."""

from agent_connect.backends.cli import CLIAdapter
from agent_connect.backends.mcp import MCPAdapter

__all__ = ["CLIAdapter", "MCPAdapter"]
```

#### `packages/agent-connect/agent_connect/backends/cli.py`

Per-user CLI adapter. Reads token from vault → sets env var → runs subprocess → parses JSON output → returns `ToolDefinition`. Unlike the old global singleton `CLIToolAdapter`, this takes `user_id` and uses per-user credentials.

```python
"""CLIAdapter — user-aware subprocess wrapper for CLI-based connectors.

Unlike the old CLIToolAdapter singleton, this:
1.  Reads per-user tokens from CredentialVault
2.  Sets env vars per invocation (no global state)
3.  Discovers tools by parsing CLI --help or a known tool list
4.  Returns namespaced ToolDefinition objects
"""

import json
import os
import shutil
import subprocess
import logging
from typing import Any

from agent_connect.spec import CLIToolSource, ConnectorSpec, ToolDescription
from agent_connect.vault import CredentialVault

logger = logging.getLogger("agent_connect")


class CLIAdapter:
    """Wraps a CLI tool with per-user token injection.

    Usage:
        adapter = CLIAdapter(spec, vault, user_id="alice")
        if not adapter.is_available():
            return f"CLI not installed: {spec.tool_source.install}"

        tools = adapter.discover_tools()

        # Each tool is a ToolDefinition; when called, it spins up a subprocess
        # with the user's token in the environment.
    """

    def __init__(
        self,
        spec: ConnectorSpec,
        vault: CredentialVault,
        user_id: str,
        timeout: int = 60,
    ):
        self.spec = spec
        self.vault = vault
        self.user_id = user_id
        self.timeout = timeout
        self._source: CLIToolSource = spec.tool_source  # type: ignore[assignment]
        self._command = self._source.command

    def is_available(self) -> bool:
        """Check if the CLI is installed and on PATH."""
        return shutil.which(self._command) is not None

    def _build_env(self) -> dict[str, str]:
        """Build environment variables with per-user token from vault."""
        env = os.environ.copy()
        token_data = self.vault.get_token(self.spec.name)
        if not token_data:
            return env

        for cred_key, env_var in self._source.env_mapping.items():
            if cred_key == "access_token":
                env[env_var] = token_data.get("access_token", "")
            elif cred_key == "api_key":
                env[env_var] = token_data.get("api_key", "")
            elif cred_key in token_data:
                env[env_var] = str(token_data[cred_key])

        return env

    def run(self, args: list[str], capture: bool = True) -> tuple[int, str, str]:
        """Run the CLI command with user token. Returns (returncode, stdout, stderr)."""
        env = self._build_env()
        cmd = [self._command] + args
        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=capture,
                text=True,
                timeout=self.timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {self.timeout}s: {' '.join(cmd)}"
        except FileNotFoundError:
            return -1, "", f"Command not found: {self._command}. Install: {self._source.install}"
        except Exception as e:
            return -1, "", f"Subprocess error: {e}"

    def list_commands(self) -> list[str]:
        """Discover available CLI subcommands (e.g. gmail, drive, calendar).

        Attempts:
        1.  CLI --list-commands (if supported)
        2.  CLI help → parse subcommands from output
        3.  Fallback: empty list (connector defines tools explicitly in YAML)
        """
        # Try structured discovery
        rc, stdout, _ = self.run(["--list-commands"])
        if rc == 0:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return [line.strip() for line in stdout.split("\n") if line.strip()]

        # Try parsing help text
        rc, stdout, _ = self.run(["--help"])
        if rc == 0:
            return _parse_subcommands_from_help(stdout)

        return []

    def discover_tools(self, namespace: str) -> list[Any]:
        """Discover tools from the CLI and return ToolDefinition objects.

        Each tool is namespaced: {namespace}__{command}_{subcommand}
        e.g. google-workspace__gmail_list, google-workspace__drive_files_list

        The ToolDescription list in the spec overrides CLI-provided names/descriptions
        with LLM-optimized versions.
        """
        from agent_connect.bridge import build_connector_tool_definition

        commands = self.list_commands()
        descriptions_by_name = {
            td.name: td for td in self.spec.tool_descriptions
        }

        tools = []
        for cmd in commands:
            safe_name = cmd.replace(" ", "_").replace("-", "_")
            tool_name = f"{namespace}__{safe_name}"

            td = descriptions_by_name.get(tool_name, None)
            description = (
                td.description if td
                else f"{self.spec.display}: {cmd}"
            )

            # Build a closure that runs this specific command when the tool is called
            # The closure captures cmd, namespace and the adapter instance
            tools.append(
                build_connector_tool_definition(
                    name=tool_name,
                    description=description,
                    adapter=self,
                    command_args=[cmd.replace(" ", ":")],
                    parameter_descriptions=td.parameter_descriptions if td else {},
                    # CLI tools are destructive unless the spec says otherwise
                    # Default: mark as read_only=False (will trigger interrupt)
                    annotations=None,  # Will be set from spec when we add per-tool annotations
                )
            )

        return tools


def _parse_subcommands_from_help(help_text: str) -> list[str]:
    """Best-effort parse CLI --help output for subcommand names."""
    commands = []
    import re

    for line in help_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Common patterns: "  gmail     List messages" or "  gmail: List messages"
        match = re.match(r"^\s{2,}([a-z][a-z0-9_-]+)\s", line)
        if match:
            commands.append(match.group(1))
    return commands
```

**Tests:** `packages/agent-connect/tests/test_cli_adapter.py`

- `is_available()` returns True when CLI is on PATH
- `is_available()` returns False when CLI is missing
- `_build_env()` injects access_token into correct env var from vault
- `_build_env()` injects api_key into correct env var from vault
- `_build_env()` returns clean env when vault has no token
- `run()` captures stdout correctly
- `run()` handles timeout gracefully
- `run()` handles command-not-found gracefully
- `discover_tools()` builds correct namespaced names
- `discover_tools()` uses spec descriptions over CLI defaults
- Uses `echo` or `python -c` as test CLI (always available)

---

### Day 7: MCPAdapter

#### `packages/agent-connect/agent_connect/backends/mcp.py`

Thin wrapper around Assistant's existing `MCPToolBridge`. Reads token from vault → passes to MCP server via environment → lets the existing bridge discover tools. This is the lightest adapter — most of the work is already done in `mcp_bridge.py` and `mcp_manager.py`.

```python
"""MCPAdapter — vault token injection for existing MCP tool bridge.

This is a thin wrapper around Assistant's MCPToolBridge. The adapter:
1.  Reads the user's OAuth/API token from CredentialVault
2.  Injects it into the MCP server's environment variables
3.  Delegates tool discovery to MCPToolBridge (already per-user)
4.  Returns namespaced ToolDefinition objects

The MCPToolBridge handles session lifecycle, tool invocation, and
namespacing. This adapter just adds credential injection.
"""

import logging
from typing import Any

from agent_connect.spec import ConnectorSpec, MCPToolSource
from agent_connect.vault import CredentialVault

logger = logging.getLogger("agent_connect")


class MCPAdapter:
    """Wraps an MCP server with per-user token injection.

    Usage:
        adapter = MCPAdapter(spec, vault, user_id="alice")
        tools = adapter.discover_tools(namespace="dropbox")
        # Tools: dropbox__search, dropbox__upload, etc.
    """

    def __init__(
        self,
        spec: ConnectorSpec,
        vault: CredentialVault,
        user_id: str,
    ):
        self.spec = spec
        self.vault = vault
        self.user_id = user_id
        self._source: MCPToolSource = spec.tool_source  # type: ignore[assignment]

    def _build_server_env(self) -> dict[str, str]:
        """Read token from vault, return env dict for the MCP server process."""
        import os

        env = os.environ.copy()
        token_data = self.vault.get_token(self.spec.name)
        if not token_data:
            return env

        for cred_key, env_var in self._source.env_mapping.items():
            if cred_key == "access_token":
                env[env_var] = token_data.get("access_token", "")
            elif cred_key == "api_key":
                env[env_var] = token_data.get("api_key", "")
            elif cred_key in token_data:
                env[env_var] = str(token_data[cred_key])

        return env

    def discover_tools(self, namespace: str) -> list[Any]:
        """Discover tools from the MCP server via the existing bridge.

        This delegates to Assistant's MCPToolBridge. The bridge already:
        - Manages MCP server lifecycle (start, idle-timeout, stop)
        - Discovers tools via MCP protocol
        - Namespaces as mcp__{server}__{tool}
        - Routes tool invocations through session.call_tool()

        We inject the user's token via environment so the MCP server
        doesn't need to handle auth itself.
        """
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        env = self._build_server_env()

        # Register this MCP server as a one-off server in the user's config
        # The existing bridge will start it, discover tools, and route calls
        bridge = MCPToolBridge(user_id=self.user_id)

        # Add this server to the bridge's managed servers
        # (The bridge already supports per-user MCPManager instances)
        if hasattr(bridge, "_manager"):
            bridge._manager.add_server(
                name=f"agent_connect__{self.spec.name}",
                command=self._source.command,
                env=env,
            )

        # Discover and return tools
        # The bridge returns ToolDefinition objects with names like:
        # mcp__agent_connect__dropbox__search
        tools = bridge.get_tool_definitions()
        return [t for t in tools if self.spec.name in t.name]

    def health(self) -> dict:
        """Check if MCP server is reachable."""
        try:
            # The MCPManager tracks server health
            from src.sdk.tools_core.mcp_manager import get_mcp_manager

            manager = get_mcp_manager(self.user_id)
            return {"status": "ok" if manager.is_ready() else "starting"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
```

**Tests:** `packages/agent-connect/tests/test_mcp_adapter.py`

- `_build_server_env()` injects token into env vars
- `_build_server_env()` returns clean env when vault empty
- `health()` returns ok when MCP manager is ready
- `health()` returns error when MCP manager fails
- Token injection doesn't leak across adapter instances

---

## Phase 3 — Runtime + Bridge (Week 3: Days 8-9)

### Day 8: ConnectorRuntime

#### `packages/agent-connect/agent_connect/runtime.py`

The orchestrator. Given a YAML directory and a vault, it:

1. Loads all specs
2. Checks which services are connected (token in vault)
3. For connected services: picks the right adapter (CLI or MCP), discovers tools
4. Returns flat list of ToolDefinition objects (all namespaced)

```python
"""ConnectorRuntime — load specs, check connections, discover tools."""

import logging
from pathlib import Path
from typing import Any

from agent_connect.spec import ConnectorSpec, ToolSourceType
from agent_connect.vault import CredentialVault
from agent_connect.backends.cli import CLIAdapter
from agent_connect.backends.mcp import MCPAdapter

logger = logging.getLogger("agent_connect")


class ConnectorRuntime:
    """Orchestrates: YAML specs → auth check → backend → ToolDefinition[].

    Usage:
        runtime = ConnectorRuntime(
            spec_dir="./connectors",
            vault=CredentialVault("./data/users/alice"),
            user_id="alice",
        )
        tools = runtime.get_tools()
        health = runtime.health()
    """

    def __init__(
        self,
        spec_dir: str | Path,
        vault: CredentialVault,
        user_id: str,
    ):
        self.spec_dir = Path(spec_dir)
        self.vault = vault
        self.user_id = user_id
        self._specs: list[ConnectorSpec] = []
        self._load_specs()

    def _load_specs(self) -> None:
        if self.spec_dir.exists():
            self._specs = ConnectorSpec.from_yaml_dir(self.spec_dir)

    def reload(self) -> None:
        """Reload connector specs from disk (for hot-reload)."""
        self._load_specs()

    def list_available(self) -> list[dict]:
        """Return all known connectors and their connection status."""
        connected = set(self.vault.list_connected())
        return [
            {
                "name": s.name,
                "display": s.display,
                "icon": s.icon,
                "category": s.category,
                "connected": s.name in connected,
                "auth_type": s.auth.type.value,
            }
            for s in self._specs
        ]

    def get_tools(self) -> list[Any]:
        """Discover tools for all connected connectors.

        Returns a flat list of ToolDefinition objects. Unconnected
        connectors produce zero tools (no token = nothing to call).
        """
        tools = []

        for spec in self._specs:
            if not self.vault.is_connected(spec.name):
                continue

            try:
                adapter_tools = self._load_connector(spec)
                tools.extend(adapter_tools)
            except Exception as e:
                logger.warning(
                    f"Failed to load connector '{spec.name}': {e}",
                    exc_info=False,
                )
                continue

        return tools

    def _load_connector(self, spec: ConnectorSpec) -> list[Any]:
        """Load tools for a single connected connector."""
        namespace = spec.name.replace("-", "_")

        if spec.tool_source.type == ToolSourceType.CLI:
            adapter = CLIAdapter(spec, self.vault, self.user_id)
            if not adapter.is_available():
                logger.warning(
                    f"CLI not available for {spec.name}. "
                    f"Install: {spec.tool_source.install}"
                )
                return []
            return adapter.discover_tools(namespace)

        elif spec.tool_source.type == ToolSourceType.MCP:
            adapter = MCPAdapter(spec, self.vault, self.user_id)
            return adapter.discover_tools(namespace)

        elif spec.tool_source.type == ToolSourceType.HTTP:
            # Deferred to Phase 2
            return []

        return []

    def health(self) -> dict:
        """Report health of all connectors.

        Returns:
            {
                "status": "ok" | "partial" | "broken",
                "connectors": {
                    "google-workspace": {"status": "ok", "tools": 12},
                    "dropbox": {"status": "error", "error": "MCP server not reachable"},
                    ...
                }
            }
        """
        result: dict[str, Any] = {"status": "ok", "connectors": {}}
        connected_count = 0
        error_count = 0

        for spec in self._specs:
            if not self.vault.is_connected(spec.name):
                result["connectors"][spec.name] = {"status": "not_connected"}
                continue

            connected_count += 1
            try:
                tools = self._load_connector(spec)
                result["connectors"][spec.name] = {
                    "status": "ok",
                    "tools": len(tools),
                }
            except Exception as e:
                error_count += 1
                result["connectors"][spec.name] = {
                    "status": "error",
                    "error": str(e),
                }

        if error_count > 0:
            result["status"] = "broken" if error_count == connected_count else "partial"

        return result
```

**Tests:** `packages/agent-connect/tests/test_runtime.py`

- No specs loaded → `get_tools()` returns empty list
- Connected service with CLI → tools discovered
- Unconnected service → no tools (skipped silently)
- Broken connector spec → logged, skipped, doesn't crash
- `list_available()` returns metadata for all specs
- `health()` reports per-connector status
- `reload()` picks up new YAML files

---

### Day 9: AgentConnectBridge + Tool Definitions

#### `packages/agent-connect/agent_connect/bridge.py`

The bridge that Assistant's `runner.py` wires into. Same pattern as `MCPToolBridge` — `discover()` + `get_tool_definitions()`.

```python
"""AgentConnectBridge — EA integration point.

Mirrors the MCPToolBridge pattern:
    bridge = AgentConnectBridge(user_id="alice")
    await bridge.discover()
    tools = bridge.get_tool_definitions()

Assistant's runner.py injects this between native_tools and AgentLoop creation.
"""

import json as json_lib
import logging
from typing import Any

from agent_connect.runtime import ConnectorRuntime
from agent_connect.vault import CredentialVault
from agent_connect.spec import ConnectorSpec

logger = logging.getLogger("agent_connect")


def _default_vault_path(user_id: str) -> str:
    """Default vault path per user."""
    from src.storage.paths import get_paths

    return str(get_paths(user_id).agent_connect_dir())


def _default_spec_dir() -> str:
    """Default connector spec directory."""
    # Shipped with the package in production
    # Also allow user overrides via env var
    import os

    env = os.environ.get("AGENT_CONNECT_SPEC_DIR")
    if env:
        return env

    # Check if running from EA repo (connectors shipped in packages/)
    import importlib.resources

    try:
        return str(importlib.resources.files("agent_connect") / "connectors")
    except Exception:
        # Fallback: look relative to the package
        from pathlib import Path

        return str(Path(__file__).parent.parent / "connectors")


def build_connector_tool_definition(
    name: str,
    description: str,
    adapter: Any,
    command_args: list[str],
    parameter_descriptions: dict[str, str] | None = None,
    annotations: Any | None = None,
) -> Any:
    """Build an SDK-compatible ToolDefinition for a connector tool.

    This creates a closure that wraps the subprocess call. When the agent
    calls the tool, the closure:
    1.  Formats arguments for the CLI
    2.  Calls adapter.run(command_args + extra_args)
    3.  Parses the output
    4.  Returns a ToolResult

    Args:
        name: Tool name (e.g. google-workspace__gmail_list)
        description: LLM-optimized description
        adapter: CLIAdapter or MCPAdapter instance
        command_args: CLI subcommand arguments (e.g. ["gmail", "messages", "list"])
        parameter_descriptions: Override descriptions for parameters
        annotations: ToolAnnotations (read_only, destructive, etc.)

    Returns:
        ToolDefinition compatible with Assistant's SDK
    """
    from src.sdk.tools import ToolAnnotations, ToolDefinition, ToolResult

    # Default annotations: assume destructive (triggers HITL interrupt)
    tool_annotations = annotations or ToolAnnotations(
        read_only=False,
        destructive=True,
        idempotent=False,
        title=name,
    )

    def _sync_invoke(**kwargs: Any) -> Any:
        """Synchronous tool invocation — wraps the subprocess call."""
        try:
            # Build CLI args from kwargs
            extra_args = []
            for key, val in kwargs.items():
                if val is not None and isinstance(val, (str, int, float, bool)):
                    extra_args.extend([f"--{key.replace('_', '-')}", str(val)])
                elif val is not None:
                    extra_args.extend([f"--{key.replace('_', '-')}", json_lib.dumps(val)])

            all_args = command_args + extra_args
            rc, stdout, stderr = adapter.run(all_args)

            if rc != 0:
                return ToolResult(
                    content=f"Error (code {rc}): {stderr}",
                    is_error=True,
                    audience="assistant",
                )

            # Try to parse JSON output
            try:
                structured = json_lib.loads(stdout)
                return ToolResult(
                    content=stdout[:2000],  # Truncate for LLM
                    structured_content=structured,
                    audience="user",
                )
            except (json_lib.JSONDecodeError, ValueError):
                return ToolResult(
                    content=stdout[:2000],
                    audience="user",
                )

        except Exception as e:
            return ToolResult(
                content=f"Tool error: {e}",
                is_error=True,
                audience="assistant",
            )

    async def _async_invoke(**kwargs: Any) -> Any:
        """Async wrapper — runs sync invoke in thread (subprocess is blocking)."""
        import asyncio

        return await asyncio.to_thread(_sync_invoke, **kwargs)

    # Build parameter descriptions for LLM consumption
    params = {
        k: {"type": "string", "description": desc}
        for k, desc in (parameter_descriptions or {}).items()
    }

    return ToolDefinition(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": params,
        },
        annotations=tool_annotations,
        function=_sync_invoke,
        ainvoke=_async_invoke,
    )


class AgentConnectBridge:
    """Bridge between Agent Connect and Assistant's AgentLoop.

    Mirrors the MCPToolBridge pattern exactly:
        - discover() → loads connector specs, checks vault, discovers tools
        - get_tool_definitions() → returns list[ToolDefinition]
        - health() → reports connector health
    """

    def __init__(
        self,
        user_id: str,
        spec_dir: str | None = None,
        vault_path: str | None = None,
    ):
        self.user_id = user_id
        self._spec_dir = spec_dir or _default_spec_dir()
        self._vault = CredentialVault(vault_path or _default_vault_path(user_id))
        self._runtime = ConnectorRuntime(self._spec_dir, self._vault, user_id)
        self._tools: list[Any] = []

    @property
    def runtime(self) -> ConnectorRuntime:
        return self._runtime

    async def discover(self) -> None:
        """Discover tools for all connected connectors."""
        self._tools = self._runtime.get_tools()

    def get_tool_definitions(self) -> list:
        """Return discovered ToolDefinition objects."""
        return self._tools

    def health(self) -> dict:
        """Report connector health."""
        vault_health = self._vault.health()
        connector_health = self._runtime.health()
        return {
            "vault": vault_health,
            "connectors": connector_health,
            "total_tools": len(self._tools),
        }
```

---

## Phase 4 — EA Integration (Week 3: Days 10-11)

### Day 10: Wire AgentConnectBridge into runner.py

**File to modify:** `src/sdk/runner.py`

After the MCP bridge block (lines 89-96), add the connector bridge block:

```python
# --- NEW: Agent Connect Bridge ---
from agent_connect.bridge import AgentConnectBridge

connector_bridge = AgentConnectBridge(user_id=user_id)
try:
    await connector_bridge.discover()
except Exception:
    logger.warning(
        "agent_connect.failed",
        {"user_id": user_id},
        user_id=user_id,
    )
    conn_tools = []
else:
    conn_tools = connector_bridge.get_tool_definitions()
    logger.info(
        "agent_connect.loaded",
        {"user_id": user_id, "tool_count": len(conn_tools)},
        user_id=user_id,
    )
# --- END NEW ---

# Update the tool concatenation line:
all_tools = tools + mcp_tools + conn_tools
```

And after the loop is created, store the bridge reference:

```python
loop._mcp_bridge = mcp_bridge
loop._connector_bridge = connector_bridge  # ← NEW
```

### Day 11: Add Meta-Tools + First Connector YAMLs

**File to create:** `packages/agent-connect/agent_connect/meta_tools.py`

```python
"""Agent Connect meta-tools — connector_list, connector_connect, connector_health.

These are SDK-native @tool functions that the agent can call to manage connections.
"""

from src.sdk.tools import ToolAnnotations, ToolResult, tool


@tool(annotations=ToolAnnotations(read_only=True, idempotent=True, title="List Connectors"))
def connector_list(user_id: str = "") -> str:
    """List all available SaaS connectors and their connection status."""
    ...
```

Four meta-tools:

| Tool | What it does | Read-only? |
|------|-------------|------------|
| `connector_list` | List available connectors + connection status | ✅ |
| `connector_connect` | Start OAuth flow for a connector (returns the authorize URL) | ❌ (interrupt required) |
| `connector_disconnect` | Remove stored credentials for a service | ❌ (destructive) |
| `connector_health` | Report health of all connectors | ✅ |

Register these in `native_tools.py` (same pattern as `mcp_list`, `mcp_reload`, `mcp_tools`).

**Files to create:** First 3 connector YAML specs

#### `packages/agent-connect/connectors/firecrawl.yaml`

```yaml
name: firecrawl
display: "Firecrawl"
icon: globe
category: web
description: "Web scraping, crawling, and search"
auth:
  type: api_key
  api_key:
    env_var: FIRECRAWL_API_KEY
tool_source:
  type: cli
  command: firecrawl
  install: npm install -g firecrawl@latest
  env_mapping:
    api_key: FIRECRAWL_API_KEY
tool_descriptions:
  - name: firecrawl__scrape_url
    description: "Scrape and extract clean markdown content from a URL"
  - name: firecrawl__search_web
    description: "Search the web and return results with full page content"
```

#### `packages/agent-connect/connectors/google-workspace.yaml`

Full GWS spec (Gmail + Calendar + Drive + Contacts).

#### `packages/agent-connect/connectors/github.yaml`

Full GitHub spec via `gh` CLI.

---

## Phase 5 — Flutter + Catalog (Week 4: Day 12)

### Day 12: Connector Catalog API + Flutter Widget

**Backend:** Add to Assistant's HTTP router:

```python
# src/http/routers/connectors.py  (new file)
@router.get("/connectors")
async def list_connectors(user_id: str = Query(...)):
    bridge = AgentConnectBridge(user_id=user_id)
    await bridge.discover()
    return {
        "available": bridge.runtime.list_available(),
        "health": bridge.health(),
    }
```

**Flutter:** A reusable `ConnectorCard` widget:

```
┌─────────────────────────────────┐
│ 🔵 Google Workspace    Connected│  ← Green dot + "Configure" button
│ Gmail, Calendar, Drive          │
└─────────────────────────────────┘
┌─────────────────────────────────┐
│ ⚫ GitHub             Connect   │  ← Grey dot + "Connect" button
│ Issues, PRs, repos              │     (triggers /auth/login?service=github)
└─────────────────────────────────┘
```

Uses `url_launcher` to open the OAuth authorize URL in the browser. The callback redirect handles the rest.

---

## Phase 6 — Polish & Ship (Week 4: Days 13-14)

- README with quick start
- CONTRIBUTING guide
- GitHub Actions CI (pytest on 3.11/3.12/3.13)
- Ruff linting
- `py.typed` marker
- Publish to PyPI

---

## Test Plan

| Test type | What | Count |
|-----------|------|-------|
| Unit (spec) | YAML parsing, validation, error handling | 6 |
| Unit (vault) | Encrypt/decrypt, CRUD, OAuth states, expiry | 10 |
| Unit (oauth) | Login redirect, callback, token storage, error handling | 8 |
| Unit (cli adapter) | Env injection, run, discover, namespace | 8 |
| Unit (mcp adapter) | Env injection, delegation, health | 4 |
| Unit (runtime) | Load, filter, aggregate, health, reload | 6 |
| Unit (bridge) | Discover, get_tool_definitions, health | 4 |
| Unit (meta tools) | List, connect URL generation, disconnect | 4 |
| Integration | Full flow: YAML → vault → OAuth → tools → agent calls tool | 3 |
| **Total** | | **~53** |

---

## What's NOT in v0.1 (by design)

| Item | When | Rationale |
|------|------|-----------|
| HTTPAdapter backend | Phase 2 | Peer review flagged: declarative REST breaks on pagination, rate limiting, nested resources |
| Enterprise admin vault seeding | v2.0 | Solo/self-serve only. Enterprise needs admin UI for bulk credential provisioning |
| Rate limiting / degraded mode | v1.0 | Connector health reports errors; agent loop handles timeouts. Full rate-limit-aware mode is a v1.0 polish feature |
| Scoped tools (read-only Gmail, no Drive delete) | v1.0 | Annotations exist on ToolDefinition. Per-tool annotation overrides in spec are a v1.0 feature |
| Webhook listening / event triggers | v2.0 | Separate subsystem — event-driven triggers belong to Phase 18-22 of EA roadmap |
| MCP server auto-install | v1.0 | v0.1 requires user/CLI to pre-install. Future: `connector_install` meta-tool |

---

## Summary

| Phase | Days | What |
|-------|------|------|
| 0 | 0 | Scaffolding: `packages/agent-connect/` package structure |
| 1 | 1-5 | Foundation: spec model (Day 1), CredentialVault (Days 2-3), OAuth router (Days 4-5) |
| 2 | 6-7 | Adapters: CLIAdapter (Day 6), MCPAdapter (Day 7) |
| 3 | 8-9 | Orchestration: ConnectorRuntime (Day 8), AgentConnectBridge (Day 9) |
| 4 | 10-11 | EA integration: runner.py wiring (Day 10), meta-tools + 3 YAMLs (Day 11) |
| 5 | 12 | Flutter connector widget + catalog API |
| 6 | 13-14 | Polish: README, CI, lint, py.typed |

**Total: ~14 days (3 weeks)** to v0.1.0 with 3 working connectors + Flutter Connect UI.

**Post-v0.1.0:** Each new connector = one YAML file + one OAuth app registration. 10 minutes of YAML, 15 minutes of dev console one-time setup.
