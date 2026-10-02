# Agent-First Vault — Design Document

> The design thinking, research, and final architecture for the Assistant's credential vault.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Platform Research: How OSes Store Credentials](#2-platform-research-how-oses-store-credentials)
3. [How Existing Password Managers Inject Credentials](#3-how-existing-password-managers-inject-credentials)
4. [The Critical Insight: What Needs the Vault?](#4-the-critical-insight-what-needs-the-vault)
5. [Architecture: Middleware Injection](#5-architecture-middleware-injection)
6. [Platform Strategy](#6-platform-strategy)
7. [Vault Schema](#7-vault-schema)
8. [Tool Integration Patterns](#8-tool-integration-patterns)
9. [Approval Modes](#9-approval-modes)
10. [Implementation Plan](#10-implementation-plan)

---

## 1. Problem Statement

The Assistant agent runs autonomously — it calls `email_connect` at 3am, queries databases, and accesses APIs. It needs credentials to do this, but:

- **No existing vault has per-service/per-category approval for agent auto-injection.** This is the gap.
- Current email tools store passwords in **plaintext in SQLite** (`email_db.py`).
- The existing `vault_path` in `src/storage/user_storage.py` is just a path — no implementation.
- A human password manager (1Password, Bitwarden) assumes a human clicks "allow." An agent needs programmatic approval.

### Requirements (Confirmed via Q&A)

- Store all types: passwords, API keys, credit cards, login credentials, OAuth tokens, SSH keys
- Auto-inject into tools (agent-first)
- Agent-only access, user chooses approval mode
- Encrypted at rest (recommended)
- Per-service approval (not per-tool)
- Access logging

---

## 2. Platform Research: How OSes Store Credentials

### macOS — Apple Keychain

Apple's Keychain is the gold standard for credential management. Key architectural details:

**Storage**: Single SQLite database, encrypted with **two AES-256-GCM keys per item**:
- **Metadata key** — encrypts all attributes except the secret value. Cached in the Application Processor for fast searches. Protected by the Secure Enclave.
- **Secret key** — encrypts the actual secret (`kSecValueData`). **Always requires a round trip through the Secure Enclave** to decrypt.

This two-key design is brilliant: you can search your keychain instantly (metadata is fast to decrypt) without ever touching the secret key. The secret is only decrypted when specifically requested.

**Daemon**: `securityd` — a single daemon that enforces access control. It checks keychain-access-groups, application-identifier, and application-group entitlements to decide which processes can access which items.

**Protection Classes** (this is the key innovation for our vault):

| Class | When Accessible | Use Case |
|-------|----------------|----------|
| `WhenUnlocked` | Device unlocked only | Safari passwords, bookmarks |
| `AfterFirstUnlock` | After first unlock (stays accessible when locked) | Mail accounts, Wi-Fi, VPN, LDAP |
| `Always` | Always | Find My token, voicemail |
| `WhenPasscodeSetThisDeviceOnly` | Device unlocked + passcode set | Highest security, never syncs, no backup |

This is a **per-category approval model** — Apple doesn't ask "may this app access this password?" Instead, items are assigned to protection classes, and the **class** determines the unlock requirement. This is the model we should adopt.

**Access Control Lists (ACLs)**: Individual items can require biometrics (Face ID, Touch ID) or passcode before decryption. ACLs are evaluated **inside the Secure Enclave** — the secret key is only released if constraints are met. Enrollment change detection: if new fingerprints/faces are added since the item was stored, access is denied.

**Syncing (iCloud Keychain)**: Each device generates a syncing identity (P-384 asymmetric keys). Devices form a **circle of trust** — new devices join via pairing (approved by existing device) or recovery (SMS + iCloud Security Code via SRP protocol, HSM-verified). Items are encrypted end-to-end. Only items explicitly marked `kSecAttrSynchronizable` sync.

**Escrow/Recovery**: HSM clusters guard escrow records. Recovery requires iCloud auth + SMS + iCloud Security Code. Code verified via Secure Remote Password protocol (never sent to Apple). **10 failed attempts → escrow record destroyed** (brute-force protection). HSM firmware access cards have been **physically destroyed**.

**Key lessons for our vault:**
1. Two-key encryption (metadata searchable, secrets only decrypted on demand)
2. Protection classes per item type (not per-app approval)
3. Per-item ACLs evaluated before secret release
4. Access group concept for sharing between processes

### Windows — Credential Manager + DPAPI

Windows takes a different approach: DPAPI (Data Protection API) provides the **primitive layer**, and Credential Manager is a **consumer** of it.

**DPAPI** has just two functions: `CryptProtectData()` and `CryptUnprotectData()`. Applications never see keys.

**How it works**:
1. Application calls `CryptProtectData(plaintext)` → gets an encrypted blob back
2. Blob contains `guidMasterKey` (which Master Key encrypted it), `pbSalt`, and `pbData` (ciphertext)
3. To decrypt: DPAPI locates the Master Key by GUID, decrypts it using the user's password-derived key, then uses the recovered symmetric key + salt to decrypt the blob
4. Optional `pOptionalEntropy` parameter for app-specific hardening

**Protection scopes**:
- `CurrentUser` — encrypted with user's Master Key (derived from user's password)
- `LocalMachine` — encrypted with machine's Master Key (derived from `DPAPI_SYSTEM` LSA secret)

**Master Key derivation**: Four authorities:
| Authority | Key Location | Protected By |
|-----------|-------------|-------------|
| Local Machine | `%WINDIR%\System32\Microsoft\Protect\S-1-5-18\` | `DPAPI_SYSTEM` LSA secret |
| System Users (SYSTEM/LocalService/NetworkService) | Same path, `\User\` subfolder | `DPAPI_SYSTEM` |
| Local Users | `%USERPROFILE%\AppData\Roaming\Microsoft\Protect\<SID>\` | User's password |
| Domain Users | Same path | User's AD password **plus** domain backup key |

**Critical weakness**: Domain backup key is RSA-2048, generated **once at domain creation**, never rotated. Compromise of this key → all domain users' Master Keys → all their Credential Manager secrets.

**Credential Manager** stores credentials as DPAPI-encrypted blobs under `%USERPROFILE%\AppData\`. Applications access credentials via Win32 API — they never handle the DPAPI keys.

**Key lessons for our vault:**
- Simple API surface (protect/unprotect) is powerful
- Two scopes (user vs machine) map to our solo vs multi-user deployments
- The domain backup key weakness shows why we should NOT use a single master key for all users

### Linux — Fragmented, No Single Solution

Linux has **no standard credential store**. There are multiple competing implementations:

**Kernel keyring** (`keyctl` syscall):
- In-memory only, no persistence across reboots
- Three scopes: thread-specific, process-specific, session-specific
- Useful for session caching, not for long-term storage

**Secret Service specification** (`org.freedesktop.secrets`):
- A D-Bus API specification (not an implementation) created by GNOME and KDE
- Items are stored in collections with attributes (searchable metadata)
- The spec defines: secret (the password), item (secret + attributes), collection (set of items)

**Implementations**:
| Implementation | Status | D-Bus |
|---------------|--------|-------|
| GNOME Keyring (`gnome-keyring-daemon`) | Most common, works | ✅ Full Secret Service |
| KeePassXC | Works as provider | ✅ Full Secret Service |
| KWallet | **Broken** — abandoned `ksecretsservice` | ❌ No Secret Service |

**Critical vulnerability**: D-Bus messages can be intercepted by **any process on the session bus** — secrets leak in transit. This is a known, unpatched issue.

**Headless Linux**: Often no Secret Service daemon at all — `org.freedesktop.secrets` simply not available. `python-keyring` falls back to plaintext or fails.

**API access**: Python's `keyring` library + `secret-tool` CLI provide access. Example:
```python
import keyring
keyring.set_password("system", "username", "password")
keyring.get_password("system", "username")  # → "password"
```

**Key lessons for our vault:**
- Cannot rely on OS keyring on Linux (fragmented, broken, insecure D-Bus)
- Must have our own encrypted storage as primary, OS keyring as optional
- Headless Linux is the most common deployment for agents → must work without GUI

---

## 3. How Existing Password Managers Inject Credentials

### 1Password — 5 Injection Methods

| Method | How It Works | Scope |
|--------|-------------|-------|
| **`op run`** | Wraps a command, injects secrets as env vars from secret references (`op://vault/item/field`) | Process-scoped |
| **`op read`** | Read a specific secret by URI | Single value |
| **`op inject`** | Template a config file with `op://` references, resolve at runtime | Config files |
| **Shell plugins** | Alias a CLI tool (`aws`, `gh`) so the plugin intercepts and injects credentials | Per-tool |
| **Auto-Type** | Simulates keyboard input into any focused UI field | Desktop GUI apps |

**Shell plugins** are the closest analog to what we need — they wrap CLI tools and inject credentials transparently. But they're tool-specific (each tool needs a custom plugin).

### 1Password Secret Reference Syntax

```bash
op://<vault-name>/<item-name>/<section-name>/<field-name>
```

Example:
```bash
export AWS_SECRET_ACCESS_KEY="op://Personal/AWS/credentials/secret_access_key"
op run -- aws s3 ls
```

The `op run` command:
1. Scans environment variables for `op://` references
2. Resolves each reference by calling the 1Password API
3. Injects the resolved values as env vars for the subprocess only
4. The subprocess sees plain env vars, never the `op://` syntax

**Key insight**: The process never sees the `op://` URI — by the time it runs, env vars contain real values. This is exactly what our VaultMiddleware should do: resolve secrets before the tool executes, so the tool only sees the real value.

### Bitwarden — Similar Patterns

- `bw get password <item>` — single secret retrieval
- `bw serve` — HTTP API on localhost
- Browser extension — DOM injection for web

---

## 4. The Critical Insight: What Needs the Vault?

This thinking evolved through the conversation. Initially, I considered the vault as a universal credential provider for everything. But examining real tool types revealed that **most modern CLIs don't need vault injection at all**:

### Credential Types and Vault Necessity

| Credential Type | Example Tools | Vault Needed? | Why |
|----------------|---------------|---------------|-----|
| **Password/username** | `email_connect` (IMAP), `clickhouse_query`, SSH | ✅ Yes | Plaintext credentials the tool can't store safely |
| **API key** | `shell_execute "curl -H 'X-API-Key: ...'"`, firecrawl | ✅ Yes | Static secret that tools receive as parameter |
| **Service account keys** | `gws auth --service-account`, GCP JSON keys | ✅ Yes | Static JSON key files that need secure storage |
| **Credit cards** | Future payment tools | ✅ Yes | Sensitive data that should never be in chat |
| **SSH key passphrases** | `shell_execute ssh` | ✅ Yes | Passphrase needed on each connection |
| **OAuth2 (CLI-managed)** | `gws`, `m365`, `gh` | ❌ No | CLI stores and refreshes tokens internally |
| **OAuth2 (MCP-managed)** | `mcp__github`, `mcp__linear` | ❌ No | MCP server handles its own auth |
| **OAuth2 (browser flow)** | `gws auth login` | ❌ No | Human interactive step, no vault involvement |

### The Google Workspace CLI Example

`gws` uses OAuth2. The flow is:
1. `gws auth login` → browser opens → user grants → tokens stored locally by `gws`
2. `gws gmail send` → `gws` checks token → refreshes if expired → makes API call

**The vault is irrelevant for `gws` during normal operation.** `gws` manages its own entire token lifecycle: refresh, expiry, storage. The token files live in `~/.config/google-workspace/`.

The only `gws` edge case: **service accounts and headless/CI credentials** — static JSON key files or refresh tokens that don't require a browser. These the vault should store, because they're effectively API keys.

### Revised Vault Scope

```
Vault stores:                 Vault does NOT store:
├── Passwords                 ├── OAuth2 tokens (CLI manages)
├── API keys                  ├── Browser session cookies
├── Service account keys      ├── Certificate private keys (OS keychain better)
├── Credit cards              └── OAuth2 refresh flows (CLI manages)
├── SSH key passphrases
└── Database credentials

Vault injects via:           Vault does NOT inject via:
├── Middleware (SDK tools)    └── OAuth2 token refresh
├── Env vars (CLI tools)
└── Startup config (MCP client)
```

This is dramatically simpler than the initial vision. The vault is for **legacy/infrastructure credentials**: IMAP passwords, database passwords, API keys, SSH keys, credit cards. Modern OAuth2 CLIs handle themselves.

---

## 5. Architecture: Middleware Injection

### The Core Flow

```
LLM generates:    email_connect(account="gmail")        ← no password
                   │
                   ▼
VaultMiddleware:  intercept, check approval, inject       ← secret here
                   │
                   ▼
Tool executes:    email_connect(account="gmail", password="s3cret")
                   │
                   ▼
Tool result:     "Connected to gmail"                    ← no password
                   │
                   ▼
LLM receives:     tool_result("Connected to gmail")      ← LLM never sees secret
```

**The LLM only ever sees two things**: (1) its own tool call without the password, and (2) the tool result with no password. The credential exists only in the VaultMiddleware → tool execution path, which is **never written back into the message history**.

### Why Middleware (Not `vault.resolve()` Inside Tools)

Initially, I considered having each tool call `vault.resolve("gmail_password")` internally. But middleware injection is strictly better:

| Aspect | `vault.resolve()` in tool | VaultMiddleware |
|--------|--------------------------|-----------------|
| Secret in LLM context | Tool call might include `"password=vault://gmail"` placeholder | Tool call omits password entirely |
| Approval logic | Scattered across every tool | Single enforcement point |
| Tool complexity | Every tool imports vault | Tools stay simple, no vault imports |
| Audit trail | Each tool logs independently | Central logging in middleware |
| Consistency | Each tool author decides pattern | One pattern, all tools |

### Code: VaultMiddleware

```python
class VaultMiddleware(Middleware):
    """Intercepts tool calls, injects credentials from vault, scrubs results."""

    async def on_tool_call(self, call: ToolCall, context: Context) -> ToolCall:
        # 1. Find parameters that are vault-substitutable
        vault_params = self._get_vault_params(call)

        for param_name, vault_ref in vault_params.items():
            # 2. Check approval mode
            approval = await self.vault.check_approval(
                item=vault_ref.item,
                tool=call.name,
                mode=context.session.approval_mode
            )

            if approval == Approval.EVERY_TIME:
                raise Interrupt(
                    f"Allow {call.name} to access '{vault_ref.item}'?"
                )

            # 3. Inject secret into tool call (never reaches LLM)
            secret = await self.vault.get(vault_ref.item, vault_ref.field)
            call.args[param_name] = secret

        return call

    async def on_tool_result(self, result: ToolResult, context: Context) -> ToolResult:
        # 4. Scrub secrets from tool results
        result.content = self.vault.scrub(result.content)
        return result
```

### How Tools Declare Vault Parameters

Tools use `ToolAnnotations` (already in the SDK) to mark which parameters are vault-substitutable:

```python
@email_connect_tool
def email_connect(
    account: str,
    password: str = ""    # empty default = "inject from vault"
) -> str:
    """Connect to email. Password auto-injected from vault if omitted."""
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
```

The convention:
- **Empty string default** (`password=""`) = "this is vault-substitutable"
- **No default** = "this parameter is required, user must provide"
- VaultMiddleware knows to inject when a vault-substitutable param is empty

### Tool Annotations for Vault

```python
ToolAnnotations(
    title="email_connect",
    vault_params={
        "password": "email_{account}_password"  # template: vault item name
    }
)
```

This tells VaultMiddleware: "when `password` is empty, look up `email_{account}_password` in the vault, substituting the `account` parameter value."

---

## 6. Platform Strategy

### Two-Layer Architecture

```
┌─────────────────────────────────────────┐
│  SQLCipher Vault (vault.db)             │
│  ┌───────────┐ ┌──────────┐ ┌────────┐ │
│  │ items     │ │ approvals │ │  logs  │ │
│  └───────────┘ └──────────┘ └────────┘ │
│  Encrypted with master key              │
└────────────┬────────────────────────────┘
             │ master key stored in:
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
 OS Keychain      User Password
 (preferred)      (fallback on
  macOS/Win       headless Linux)
```

1. **Vault is SQLCipher** — works on all platforms, no daemon dependency
2. **Master key stored in OS keychain** when available — leverages platform security (Secure Enclave, DPAPI, Secret Service)
3. **On headless Linux** — master key derived from user password (PBKDF2), typed once per session
4. **No plaintext secrets on disk** — even if `vault.db` is copied, it's encrypted

### Per-Platform Behavior

| Platform | Master Key Storage | Unlock Mechanism |
|----------|-------------------|-----------------|
| **macOS** | Apple Keychain (`security add-generic-password`) | Keychain unlocks at login |
| **Windows** | DPAPI (`CryptProtectData`) with `CurrentUser` scope | Unlocked at login |
| **Linux (GUI)** | Secret Service (`secret-tool store`) via `libsecret` | Unlocked at session start |
| **Linux (headless)** | PBKDF2 derivation from user password | Typed once per session |

### Fallback Chain

```python
async def unlock_vault(user_id: str) -> Vault:
    # 1. Try OS keychain
    if os_keychain_available():
        master_key = os_keychain.get(f"ea-vault-{user_id}")
        if master_key:
            return Vault.decrypt(vault_db, master_key)

    # 2. Try session cache (already unlocked this session)
    if session_cache.has(f"vault-{user_id}"):
        return session_cache.get(f"vault-{user_id}")

    # 3. Prompt for password (headless Linux, first run)
    password = await prompt_user("Enter vault password: ")
    master_key = pbkdf2(password, salt=vault_db.salt)
    vault = Vault.decrypt(vault_db, master_key)
    session_cache.set(f"vault-{user_id}", vault)
    return vault
```

### Python Library for OS Keychain Access

| Platform | Library | Install |
|----------|---------|---------|
| macOS | `keyring` (uses Security.framework) | `pip install keyring` |
| Windows | `keyring` (uses DPAPI via `pywin32`) | `pip install keyring pywin32` |
| Linux | `keyring` (uses `libsecret`/Secret Service) | `pip install keyrange SecretStorage` |
| All | `keyring` is the universal interface | Already a dependency |

```python
import keyring

# Store master key in OS keychain
keyring.set_password("assistant", f"vault-{user_id}", master_key_hex)

# Retrieve master key from OS keychain
master_key_hex = keyring.get_password("assistant", f"vault-{user_id}")
```

---

## 7. Vault Schema

### Apple Keychain-Inspired Two-Key Design

Following Apple's two-key architecture (metadata key + secret key), we split each vault item into searchable metadata and encrypted secrets:

```sql
-- Searchable metadata (encrypted but cached for fast lookup)
-- Decrypted once per session, kept in memory
CREATE TABLE vault_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,       -- "gmail", "clickhouse_prod"
    category    TEXT NOT NULL,              -- "password", "api_key", "credit_card",
                                            -- "ssh_key", "oauth_token", "service_account"
    metadata    TEXT NOT NULL,              -- JSON: host, port, user, database (searchable)
                                            -- NOT encrypted — fast to search
    secret_blob BLOB NOT NULL,             -- AES-256-GCM encrypted secret value
    secret_iv   BLOB NOT NULL,              -- IV for secret decryption
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE(name)
);

-- Per-item approval configuration
-- Like Apple's protection classes but for our agent
CREATE TABLE vault_approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES vault_items(id) ON DELETE CASCADE,
    tool_name   TEXT NOT NULL,              -- "email_connect", "shell_execute", or "*" for all
    mode        TEXT NOT NULL CHECK(mode IN ('always_allow', 'per_session', 'every_time')),
    granted_at  INTEGER,                    -- when user approved (NULL = not yet approved)
    UNIQUE(item_id, tool_name)
);

-- Access log — every vault.resolve() call
CREATE TABLE vault_access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES vault_items(id),
    tool_name   TEXT NOT NULL,              -- which tool requested it
    approved    BOOLEAN NOT NULL,
    mode_used   TEXT NOT NULL,              -- always_allow / per_session / every_time
    timestamp   INTEGER NOT NULL
);

-- Default approval modes by item category
-- Like Apple's protection classes: different types get different default restrictions
CREATE TABLE vault_category_defaults (
    category    TEXT PRIMARY KEY,           -- "password", "api_key", "credit_card", etc.
    default_mode TEXT NOT NULL DEFAULT 'per_session',  -- default approval mode
    auto_lock_minutes INTEGER DEFAULT 0    -- 0 = never auto-lock, >0 = re-lock after N minutes
);
```

### Default Category Approval Modes

Following Apple's protection class model — different item types get different default restrictions:

| Category | Default Mode | Rationale | Auto-Lock |
|----------|-------------|-----------|-----------|
| `password` | `per_session` | Approve once per session | 0 (session-scoped) |
| `api_key` | `per_session` | Approve once per session | 0 |
| `credit_card` | `every_time` | High-value, always confirm | 0 |
| `ssh_key` | `per_session` | Approve once per session | 0 |
| `service_account` | `always_allow` | Long-lived keys, trust established | 0 |
| `oauth_token` | `always_allow` | Already approved via OAuth flow | 0 |

### Encryption Details

```python
import sqlcipher3  # or pysqlcipher3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import os

class Vault:
    def __init__(self, db_path: Path, master_key: bytes):
        # SQLCipher handles db-level encryption
        self.db = sqlcipher3.connect(str(db_path))
        self.db.execute(f"PRAGMA key = \"{master_key.hex()}\"")
        # Per-item secret encryption uses AES-256-GCM
        self._item_key = AESGCM(master_key)

    def store(self, name: str, category: str, metadata: dict, secret: str):
        iv = os.urandom(12)
        secret_blob = self._item_key.encrypt(iv, secret.encode(), name.encode())
        self.db.execute(
            "INSERT INTO vault_items (name, category, metadata, secret_blob, secret_iv, ...) VALUES (?, ?, ?, ?, ?, ...)",
            (name, category, json.dumps(metadata), secret_blob, iv, ...)
        )

    def get(self, name: str, field: str = "secret") -> str:
        row = self.db.execute(
            "SELECT secret_blob, secret_iv FROM vault_items WHERE name = ?", (name,)
        ).fetchone()
        return self._item_key.decrypt(row.secret_iv, row.secret_blob, name.encode()).decode()
```

Why two encryption layers:
- **SQLCipher**: Encrypts the entire database file at rest. Protects against file-level theft.
- **AES-256-GCM per item**: Protects against in-memory SQL injection or db-level compromise. Even if the SQLCipher key is extracted, individual secrets need their own decryption.

**This is overkill for v1.** Start with SQLCipher-only (whole-db encryption). Add per-item AES-GCM in v2 if there's demand for field-level protection.

---

## 8. Tool Integration Patterns

### Pattern 1: SDK Tools (Primary)

```python
@email_connect_tool
def email_connect(account: str, password: str = "") -> str:
    """Connect to email account. Password auto-injected from vault if omitted."""
    ...

@clickhouse_query_tool
def clickhouse_query(query: str, host: str = "", user: str = "", password: str = "") -> str:
    """Run a ClickHouse query. Credentials auto-injected from vault if omitted."""
    ...
```

Convention: vault-substitutable parameters default to empty string. When empty, VaultMiddleware fills them.

**Connection caching** (important for databases):
```python
_conn_cache: dict[str, Connection] = {}

def clickhouse_query(query: str, host: str = "", user: str = "", password: str = "") -> str:
    key = f"{host}:{user}"
    if key not in _conn_cache:
        conn = clickhouse_driver.connect(host=host, user=user, password=password)
        _conn_cache[key] = conn
    return _conn_cache[key].execute(query)
```

Vault involved once (first call). After that the cached connection is reused. If the connection drops and needs re-auth, the middleware catches the error and re-injects.

### Pattern 2: CLI Tools via shell_execute

```python
# Agent calls:
shell_execute("clickhouse-client -q 'SELECT count() FROM events'")

# VaultMiddleware sees "clickhouse-client" command pattern
# Injects CLICKHOUSE_PASSWORD via subprocess-scoped env var
# Command string stays clean — no password in it
```

**Env var scoping**:
```python
class VaultMiddleware:
    async def on_tool_call(self, call, context):
        if call.name == "shell_execute":
            env_overrides = await self._resolve_cli_env(call.args["command"])
            call.args["env_overrides"] = env_overrides  # subprocess-scoped only

# In shell_execute tool:
result = subprocess.run(cmd, env={**os.environ, **env_overrides})
# env_overrides is process-scoped, never written to shell history
# not visible in /proc/PID/environ after process exits
```

### Pattern 3: MCP Client Startup

```python
# User configures once:
# mcp_add server clickhouse --env CLICKHOUSE_PASSWORD=vault://clickhouse/password

# When MCP client starts the clickhouse-mcp-server subprocess:
# 1. Vault resolves vault://clickhouse/password → actual password
# 2. Sets as env var for the subprocess only
# 3. After this, zero vault involvement per query
```

The vault only participates at **MCP server startup time**, not per-call. The MCP server handles its own connection lifecycle after that.

### Pattern 4: Escape Hatch — `vault.resolve()` Inside Tools

Some tools construct connections internally without passing credentials through parameters:

```python
async def email_connect(account: str):
    conn = imaplib.IMAP4_SSL(host)
    pw = await vault.resolve(f"{account}:password")  # direct call, not in LLM context
    conn.login(user, pw)
```

This is fine — `vault.resolve()` is an in-process function call, not a message. It doesn't put the secret in the LLM context. But it should be the **exception**, not the default. Middleware injection is preferred because it's centralized and auditable.

---

## 9. Approval Modes

### Per-Item, Per-Category (Not Per-Tool)

Following Apple's protection class model:

| Mode | Behavior | Stored In | UI Equivalent |
|------|----------|-----------|---------------|
| `always_allow` | Agent can access without prompting | `vault_approvals` table | Face ID → "Always Allow" |
| `per_session` | Prompt once per session, then cache | Session state | Face ID → "Allow Until Tomorrow" |
| `every_time` | Prompt on every access | `vault_approvals` table | Face ID → "Always Require" |

### How Approval Works in Practice

```
Agent:  email_connect(account="gmail")
  │
  ▼
VaultMiddleware: check approval for "gmail" + "email_connect"
  │
  ├── mode=always_allow → inject password, continue
  │
  ├── mode=per_session →
  │     ├── session cache hit → inject password, continue
  │     └── session cache miss → raise Interrupt("Allow email_connect to access Gmail?")
  │           ├── user approves → cache in session, inject password, continue
  │           └── user denies → return error "Vault access denied"
  │
  └── mode=every_time → raise Interrupt("Allow email_connect to access Gmail?")
        ├── user approves → inject password, continue
        └── user denies → return error "Vault access denied"
```

The Interrupt mechanism already exists in the SDK (`sdk/loop.py`). The vault just uses the same tool-interruption pattern for approval prompts.

### Default Approval by Category

When a user stores a new credential, the vault assigns a default approval mode based on category:

```python
CATEGORY_DEFAULTS = {
    "password":         ApprovalMode.PER_SESSION,
    "api_key":          ApprovalMode.PER_SESSION,
    "credit_card":      ApprovalMode.EVERY_TIME,
    "ssh_key":          ApprovalMode.PER_SESSION,
    "service_account":  ApprovalMode.ALWAYS_ALLOW,
    "oauth_token":      ApprovalMode.ALWAYS_ALLOW,
}
```

Users can override per-item: `vault configure gmail --mode every_time`

---

## 10. Implementation Plan

### Phase 8.5: Core Vault

| Step | Task | Depends On |
|------|------|-----------|
| 1 | Create `src/sdk/vault/` module | — |
| 2 | Implement `VaultDB` class (SQLCipher, schema migration) | Step 1 |
| 3 | Implement `Vault` class (store/get/resolve/scrub) | Step 2 |
| 4 | Implement OS keychain integration via `keyring` library | Step 3 |
| 5 | Implement `VaultMiddleware` (on_tool_call, on_tool_result) | Step 3 |
| 6 | Implement approval modes (always_allow, per_session, every_time) | Step 5 |
| 7 | Implement access logging | Step 5 |
| 8 | Add vault tools: `vault_put`, `vault_get`, `vault_list`, `vault_delete`, `vault_configure` | Step 3 |
| 9 | Add `password=""` optional params to `email_connect`, `email_send` | Step 5 |
| 10 | Write tests (vault encryption, middleware injection, approval, scrubbing) | Steps 2-8 |
| 11 | Update `DataPaths` to include `vault_path()` | Step 1 |

### Phase 8.6: CLI + MCP Integration

| Step | Task | Depends On |
|------|------|-----------|
| 12 | Add `env_overrides` parameter to `shell_execute` | Phase 8.5 |
| 13 | Add `env_overrides` parameter to `CLIToolAdapter` | Step 12 |
| 14 | Implement CLI pattern detection in VaultMiddleware | Step 13 |
| 15 | Add vault URI resolution for MCP client startup | Phase 8.5 |
| 16 | Tests for CLI env injection, MCP startup | Steps 12-15 |

### Phase 8.7: Advanced Features (Future)

| Step | Task | Notes |
|------|------|-------|
| 17 | Per-item AES-GCM encryption (on top of SQLCipher) | v2 — overkill for v1 |
| 18 | Vault sharing (multi-user) | Depends on Phase 8 app sharing |
| 19 | SSH agent integration | Store SSH key passphrases in vault |
| 20 | Browser extension for web form fill | Separate project |

### Dependencies

```toml
[dependencies]
pysqlcipher3 = ">=1.2"   # SQLCipher for vault storage
keyring = ">=25.0"       # OS keychain access (all platforms)
```

### File Structure

```
src/sdk/vault/
├── __init__.py           # Public API: Vault, VaultMiddleware, VaultDB
├── db.py                 # VaultDB class (SQLCipher, schema, migrations)
├── vault.py              # Vault class (store, get, resolve, scrub, approval)
├── middleware.py          # VaultMiddleware (intercepts tool calls)
├── os_keychain.py        # OS keychain integration (keyring library)
├── encryption.py         # AES-256-GCM per-item encryption (v2)
└── tools.py              # vault_put, vault_get, vault_list, vault_delete, vault_configure
```

---

## Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Storage engine** | SQLCipher | Cross-platform, encrypted at rest, single-file, no daemon |
| **Master key storage** | OS keychain (preferred), password (fallback) | Leverages platform security (Secure Enclave, DPAPI) |
| **Injection mechanism** | VaultMiddleware (not in-tool `vault.resolve()`) | Centralized, auditable, LLM never sees secrets |
| **Per-item vs per-tool approval** | Per-item, per-category (Apple protection classes) | Different sensitivity levels, not all-or-nothing |
| **Default approval modes** | Category-based defaults | Passwords = per_session, credit cards = every_time, API keys = always_allow |
| **MCP scope** | Client only (credentials at server startup) | We're an MCP client, not a server |
| **OAuth2 CLIs** | Not vault-managed (gws, m365, gh handle their own auth) | These CLIs have their own token lifecycle |
| **CLI credential injection** | Subprocess-scoped env vars | Never in command string, not visible in ps/history |
| **Connection caching** | In-tool connection cache | Vault only involved on first connection, not per-call |
| **Scrubbing** | Middleware scrubs tool results | Prevents accidental credential leakage in error messages |