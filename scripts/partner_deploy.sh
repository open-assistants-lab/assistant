#!/usr/bin/env bash
# Partner onboarding (roadmap T3.7): fresh deployment + partner PROFILE.md
# mount → customized running agent. No pip install path.
#
# Usage:
#   scripts/partner_deploy.sh -t <tenant_name> -p <path/to/PROFILE.md> [-i image] [-k api_key]
#
# Steps: clone-free compose file → mount partner PROFILE.md → compose up →
# POST /profile/reload (K1 bootstrap flow) → health + readiness check.
set -euo pipefail
cd "$(dirname "$0")/.."

TENANT="${TENANT:-partner}"
PROFILE=""
IMAGE="ghcr.io/open-assistants-lab/assistant:latest"
API_KEY="partner-$(date +%s)"

while getopts "t:p:i:k:h" opt; do
  case "$opt" in
    t) TENANT="$OPTARG" ;;
    p) PROFILE="$OPTARG" ;;
    i) IMAGE="$OPTARG" ;;
    k) API_KEY="$OPTARG" ;;
    *) echo "usage: $0 -t <tenant> -p <PROFILE.md path> [-i image] [-k api_key]"; exit 1 ;;
  esac
done
[ -f "${PROFILE:-}" ] || { echo "ERROR: -p <PROFILE.md path> required"; exit 1; }

WORK="docker/partners/${TENANT}"
mkdir -p "$WORK/data" "$WORK/config"

echo "== 1. compose file (published image, no build) =="
cat > "$WORK/docker-compose.yaml" << EOF
services:
  assistant:
    image: ${IMAGE}
    container_name: assistant-partner-${TENANT}
    command: ["uv", "run", "assistant", "http"]
    ports:
      - "127.0.0.1:8090:8080"
    environment:
      API_HOST: "0.0.0.0"
      API_PORT: "8080"
      API_KEY: "${API_KEY}"
      DEPLOYMENT_DATA_ROOT: /app/data
      DEPLOYMENT_DATA_PATH: /app/data
    volumes:
      - ./data:/app/data
      # Partner agent definition (PROFILE.md frontmatter + body) — the K1
      # profile bootstrap reads this at startup and on /profile/reload.
      - ${PROFILE}:/app/profile/PROFILE.md:ro
      # Optional: partner config.yaml (model defaults, MCP, etc.)
      # - ./partner-config.yaml:/app/config.yaml:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
EOF
echo "wrote $WORK/docker-compose.yaml"

echo "== 2. compose up =="
docker compose -f "$WORK/docker-compose.yaml" up -d

echo "== 3. wait for health =="
for i in $(seq 1 30); do
  if curl -sf "http://localhost:8090/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sf "http://localhost:8090/health" || { echo "FAIL: unhealthy"; exit 1; }

echo "== 4. profile reload (K1 bootstrap) =="
curl -s -X POST "http://localhost:8090/profile/reload?user_id=default_user" -H "Authorization: Bearer ${API_KEY}" || echo "(reload call failed — check container logs)"

echo "== 5. round-trip =="
curl -s "http://localhost:8090/health" | head -1
echo "Partner agent for tenant '${TENANT}' deployed (localhost:8090)."
echo "Bring-your-own-auth options: shared-secret API_KEY (as configured above),"
echo "PER_USER_AUTH=true with /auth/keys, or OIDC via /auth/oidc/login."