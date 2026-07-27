# Assistant — Deployment Guide

## Solo Mode (Default)

**Use case:** One user, one machine, localhost only. Zero configuration.

```bash
uv run assistant http
```

- Auth: Disabled (localhost-only, no API key needed)
- Data path: `data/users/default_user/`
- Client connects to: `http://localhost:8080`

---

## Solo WAN (Multi-Device via Tailscale)

**Use case:** Same user, desktop + phone/laptop, access EA from anywhere without port forwarding.

### Setup

1. **Install Tailscale** on desktop and phone ([tailscale.com](https://tailscale.com/download))

2. **On desktop:**
```bash
tailscale up
export EA_API_KEY=$(openssl rand -hex 32)
echo "Your API key: $EA_API_KEY"  # Save this!
uv run assistant http --host 0.0.0.0
```

3. **On phone/client:**
- Settings → Connection → Host: `<desktop-tailscale-ip>:8080`
- Enter the API key from step 2
- Connect

**How it works:**
- Tailscale creates an encrypted mesh network between your devices
- Each device gets a `100.x.x.x` IP (the Tailscale IP)
- Phone connects to `100.x.x.x:8080` → Tailscale routes to your desktop
- `EA_API_KEY` protects the connection from unauthorized access
- Localhost requests (desktop itself) still bypass auth

**Security:** Tailscale's WireGuard encryption + Assistant's API key auth. No port forwarding, no DNS, no firewall changes.

---

## Multi-Tenant (Docker + Caddy + Subdomain)

**Use case:** Host EA for multiple users. Each user gets their own container, accessed via `user.domain.com`.

### Architecture

```
                          ┌─────────────────┐
bob.myea.com ───────────►│                  │──► bob:8080    (bob's container)
alice.myea.com ─────────►│  Caddy (proxy)   │──► alice:8080  (alice's container)
                          │  :80, :443       │
                          └─────────────────┘
```

### Setup

1. **Set up DNS:** Point a wildcard `*.myea.com` to your server's IP.

2. **Generate API keys:**
```bash
openssl rand -hex 32  # → alice's key
openssl rand -hex 32  # → bob's key
```

3. **Create `Caddyfile`:**
```caddy
*.myea.com {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }

    @alice host alice.myea.com
    handle @alice {
        reverse_proxy alice:8080
    }

    @bob host bob.myea.com
    handle @bob {
        reverse_proxy bob:8080
    }
}
```

4. **Create `docker-compose.yml`:**
```yaml
services:
  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
    environment:
      - CLOUDFLARE_API_TOKEN=${CF_TOKEN}

  alice:
    build: .
    environment:
      - EA_API_KEY=${ALICE_KEY}
    volumes:
      - alice_data:/app/data

  bob:
    build: .
    environment:
      - EA_API_KEY=${BOB_KEY}
    volumes:
      - bob_data:/app/data

volumes:
  alice_data:
  bob_data:
```

5. **Start:**
```bash
ALICE_KEY=abc123 BOB_KEY=xyz789 CF_TOKEN=... docker compose up -d
```

6. **Each user connects to their subdomain** (e.g., `alice.myea.com`) with their API key.

**Adding users:** Add a new service block + Caddy entry. Each user gets isolated data via Docker volumes.

---

## Comparison

| Mode | Auth | Access | Setup |
|---|---|---|---|
| **Solo** | None | localhost only | `uv run assistant http` |
| **Solo WAN** | API key + Tailscale | Anywhere, encrypted mesh | Tailscale + API key |
| **Multi-Tenant** | API key per user | Subdomain + Caddy + Docker | Docker Compose + DNS |
