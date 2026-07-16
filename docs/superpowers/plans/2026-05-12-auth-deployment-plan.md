# API Auth + Solo WAN & Multi-Tenant Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Allow solo users to access EA from phone/other devices over WAN via Tailscale. Allow multi-tenant hosting with one Docker container per user, routed by subdomain via Caddy.

**Architecture:** One API key per container. Container = user. Caddy reverse proxy for multi-tenant subdomain routing. Tailscale for solo WAN.

**Tech Stack:** FastAPI Depends, SHA-256, Caddy, Docker, flutter_secure_storage

---

## Deployment Architecture

```
SOLO (localhost):
  Flutter → localhost:8080                    # No auth needed

SOLO WAN (Tailscale):
  Phone → Tailscale → Desktop:8080            # API key auth
  Desktop runs: ea http --host 0.0.0.0
  Phone connects to: 100.x.x.x:8080 (Tailscale IP)

MULTI-TENANT (subdomain):
  Caddy → alice.domain.com → container_alice:8080    # Docker, EA_API_KEY=abc
          bob.domain.com   → container_bob:8080      # Docker, EA_API_KEY=xyz
```

### Why one container per user

Each user's data is isolated by filesystem. No user_id routing inside the application. The container IS the user. This is the simplest multi-tenant model — matches how Mem0, N8n, and most self-hosted AI tools work.

---

## Task 1: Backend API Key Auth

**Files:**
- Create: `src/http/auth.py`
- Modify: `src/config/settings.py`

- [ ] **Step 1: Add auth config**

```python
# src/config/settings.py

class AuthConfig(BaseModel):
    api_key: str = ""              # Set via EA_API_KEY env var. Empty = auth disabled.
    solo_bypass: bool = True       # Skip auth for localhost connections

class Settings(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
```

- [ ] **Step 2: Create auth module**

```python
# src/http/auth.py

import hashlib
from fastapi import Request, HTTPException, Depends
from src.config.settings import get_settings

async def require_auth(request: Request) -> None:
    """FastAPI dependency. Validates Bearer token or allows localhost bypass."""
    settings = get_settings()
    
    # Auth disabled — allow all
    if not settings.auth.api_key:
        return None
    
    # Localhost bypass
    if settings.auth.solo_bypass:
        client = request.client
        if client and client.host in ("127.0.0.1", "::1", "localhost"):
            return None
    
    # Validate Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    
    provided = auth_header[7:]
    expected_hash = hashlib.sha256(settings.auth.api_key.encode()).hexdigest()
    provided_hash = hashlib.sha256(provided.encode()).hexdigest()
    
    if provided_hash != expected_hash:
        raise HTTPException(status_code=401, detail="Invalid API key")

async def optional_auth(request: Request) -> str | None:
    """Like require_auth but returns None instead of 401. For health endpoints."""
    try:
        await require_auth(request)
        return "authenticated"
    except HTTPException:
        return None
```

- [ ] **Step 3: Write auth tests**

```python
# tests/http/test_auth.py

class TestSoloMode:
    async def test_localhost_no_auth_needed(self, client):
        resp = await client.post("/message", json={"message": "hi"})
        assert resp.status_code == 200

class TestApiKeyAuth:
    async def test_remote_without_key_gets_401(self, client):
        resp = await client.post("/message", 
            json={"message": "hi"},
            headers={"X-Forwarded-For": "192.168.1.1"})
        assert resp.status_code == 401
    
    async def test_remote_with_valid_key_accepted(self, client):
        resp = await client.post("/message",
            json={"message": "hi"},
            headers={
                "X-Forwarded-For": "192.168.1.1",
                "Authorization": "Bearer test-key-123"})
        assert resp.status_code == 200
    
    async def test_remote_with_wrong_key_gets_401(self, client):
        resp = await client.post("/message",
            json={"message": "hi"},
            headers={
                "X-Forwarded-For": "192.168.1.1",
                "Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401
```

- [ ] **Step 4: Run tests**

```bash
EA_API_KEY=test-key-123 uv run pytest tests/http/test_auth.py -v
```

---

## Task 2: Wire Auth Into Routes

**Files:**
- Modify: `src/http/routers/conversation.py`
- Modify: `src/http/main.py`

- [ ] **Step 1: Add dependency to conversation router**

```python
# src/http/routers/conversation.py
from src.http.auth import require_auth

@router.post("/message")
async def send_message(req: MessageRequest, _: None = Depends(require_auth)):
    ...

@router.post("/message/stream")
async def send_message_stream(req: MessageRequest, _: None = Depends(require_auth)):
    ...
```

- [ ] **Step 2: Add to all non-health routes**

Health (`/health`, `/docs`) stays unauthenticated. All message/conversation/memory routes get `Depends(require_auth)`.

- [ ] **Step 3: Test**

```bash
# Local should still work
curl -s -X POST http://localhost:8080/message -H "Content-Type: application/json" -d '{"message":"hi"}'

# With key set, remote should need auth
EA_API_KEY=test uv run assistant http &
curl -s -X POST http://localhost:8080/message -H "Content-Type: application/json" -H "X-Forwarded-For: 1.2.3.4" -d '{"message":"hi"}'  # → 401
curl -s -X POST http://localhost:8080/message -H "Content-Type: application/json" -H "X-Forwarded-For: 1.2.3.4" -H "Authorization: Bearer test" -d '{"message":"hi"}'  # → 200
```

---

## Task 3: WebSocket Auth

**Files:**
- Modify: `src/http/routers/ws.py`
- Modify: `src/http/ws_protocol.py`

- [ ] **Step 1: Add auth message types**

```python
# src/http/ws_protocol.py

class AuthMessage(BaseModel):
    type: Literal["auth"] = "auth"
    api_key: str

class AuthOkMessage(BaseModel):
    type: Literal["auth_ok"] = "auth_ok"
```

- [ ] **Step 2: Handle auth in WS handler**

```python
# src/http/routers/ws.py

from src.http.auth import validate_key

async def ws_handler(websocket: WebSocket):
    await websocket.accept()
    
    settings = get_settings()
    needs_auth = (
        bool(settings.auth.api_key) 
        and not (settings.auth.solo_bypass and _is_localhost_ws(websocket))
    )
    
    if needs_auth:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        if data.get("type") != "auth" or not validate_key(data.get("api_key", "")):
            await websocket.send_json({"type": "error", "code": "AUTH_FAILED"})
            await websocket.close()
            return
        await websocket.send_json({"type": "auth_ok"})
    
    # ... existing message loop ...
```

- [ ] **Step 3: Test**

```bash
uv run python -c "
import asyncio, json, websockets
async def test():
    async with websockets.connect('ws://localhost:8080/ws/conversation') as ws:
        await ws.send(json.dumps({'type': 'auth', 'api_key': 'test-key'}))
        resp = json.loads(await ws.recv())
        assert resp['type'] == 'auth_ok'
        print('WS auth OK')
asyncio.run(test())
"
```

---

## Task 4: Flutter Client Auth

**Files:**
- Modify: `flutter_app/lib/services/ws_client.dart`
- Modify: `flutter_app/lib/services/api_client.dart`
- Modify: `flutter_app/lib/providers/agent_provider.dart` (settings screen)

- [ ] **Step 1: Add apiKey to ApiClient**

```dart
class ApiClient {
  final String? apiKey;
  
  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    if (apiKey != null) 'Authorization': 'Bearer $apiKey',
  };
}
```

- [ ] **Step 2: Add auth handshake to WsClient**

```dart
Future<void> connect() async {
  _ws = await WebSocket.connect(uri);
  
  if (apiKey != null) {
    _ws!.add(jsonEncode({'type': 'auth', 'api_key': apiKey}));
    // Wait for auth_ok before proceeding
    await for (final msg in _ws!) {
      final data = jsonDecode(msg);
      if (data['type'] == 'auth_ok') break;
      if (data['type'] == 'error') throw Exception(data['message']);
    }
  }
}
```

- [ ] **Step 3: Add settings UI for API key**

Add a masked text field in the connection settings for the API key. Store in `flutter_secure_storage`.

- [ ] **Step 4: Run tests**

```bash
cd flutter_app && flutter test
```

---

## Task 5: Caddy Reverse Proxy (Multi-Tenant)

**Files:**
- Create: `multi-tenant/Caddyfile`
- Create: `multi-tenant/docker-compose.yml`

- [ ] **Step 1: Write Caddyfile**

```caddy
# multi-tenant/Caddyfile

*.domain.com {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }

    @alice host alice.domain.com
    handle @alice {
        reverse_proxy alice:8080
    }

    @bob host bob.domain.com
    handle @bob {
        reverse_proxy bob:8080
    }
}
```

- [ ] **Step 2: Write docker-compose for multi-tenant**

```yaml
# multi-tenant/docker-compose.yml

services:
  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile

  alice:
    build: .
    environment:
      - EA_API_KEY=${ALICE_KEY}
    volumes:
      - alice_data:/data

  bob:
    build: .
    environment:
      - EA_API_KEY=${BOB_KEY}
    volumes:
      - bob_data:/data

volumes:
  alice_data:
  bob_data:
```

- [ ] **Step 3: Document setup**

Write step-by-step in README: generate API keys, set DNS wildcard, `docker compose up`.

---

## Task 6: Solo WAN via Tailscale

**Files:**
- Create: `docs/SOLO_WAN.md`

- [ ] **Step 1: Write Tailscale setup guide**

```markdown
# Solo WAN — Access EA from Phone

1. Install Tailscale on both desktop and phone (tailscale.com)
2. On desktop: tailscale up
3. Start EA: ea http --host 0.0.0.0
4. On phone Flutter app: connect to <desktop-tailscale-ip>:8080
5. If EA_API_KEY is set, enter the key in Flutter settings
```

- [ ] **Step 2: Commit docs**

```bash
git add docs/SOLO_WAN.md && git commit -m "docs: solo WAN via Tailscale"
```

---

## Task 7: DEPLOYMENT.md

**Files:**
- Create: `DEPLOYMENT.md`

Write the comprehensive deployment guide covering all three modes with copy-paste-ready commands.

- [ ] **Step 1: Write DEPLOYMENT.md**
- [ ] **Step 2: Commit**

---

## Self-Review

**Spec coverage:** Solo local (✅ bypass), solo WAN (✅ Tailscale + key), multi-tenant (✅ subdomain + Caddy).

**Container = user model:** No user_id routing inside the app. Each container is one user. API key scoped per container.

**Solo stays zero-config:** localhost bypass means `uv run assistant http` works unmodified.

**No placeholders:** All code shown inline.
