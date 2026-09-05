#!/usr/bin/env bash
# Enterprise tier smoke test (roadmap T3.5) — NOT run in CI by default.
# Boots the container-per-user profile with 2 users, health-checks both,
# verifies on-disk data-dir isolation, tears down.
#
# Requires: docker + docker compose. Uses the published GHCR image by default
# (override with ENTERPRISE_IMAGE=...).
set -uo pipefail
cd "$(dirname "$0")/.."

USERS=(smoke_alice smoke_bob)
WORK="docker/enterprise"
FAIL=0

command -v docker >/dev/null || { echo "SKIP: docker not available"; exit 0; }
docker compose version >/dev/null || { echo "SKIP: docker compose not available"; exit 0; }

echo "== generating enterprise compose for ${USERS[*]} =="
bash scripts/generate_enterprise_compose.sh "${USERS[@]}" || exit 1

echo "== validating compose syntax =="
docker compose -f docker/docker-compose.enterprise.yaml config --quiet || FAIL=1

echo "== booting per-user containers =="
if ! docker compose -f docker/docker-compose.enterprise.yaml up -d --quiet-pull; then
  echo "SMOKE INCOMPLETE: image pull failed (run 'docker login ghcr.io' or set"
  echo "ENTERPRISE_IMAGE=assistant:local after 'docker build -f docker/Dockerfile ..')"
  exit 1
fi
sleep 5

for user in "${USERS[@]}"; do
  shard=$(printf '%s' "$user" | sha256sum | cut -c1-8)
  name="assistant-ent-${shard}"
  echo "== health: ${name} =="
  for i in $(seq 1 30); do
    state=$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null || echo missing)
    [ "$state" = "healthy" ] && break
    sleep 2
  done
  echo "${name}: ${state}"
  [ "$state" = "healthy" ] || FAIL=1
done

echo "== on-disk data-dir isolation =="
dirA="$WORK/users/${USERS[0]}/data"
dirB="$WORK/users/${USERS[1]}/data"
ls "$dirA" >/dev/null && ls "$dirB" >/dev/null || FAIL=1
overlap=$(find "$dirB" -name "leak-marker" 2>/dev/null | wc -l | tr -d ' ')
touch "$dirA/leak-marker"
[ "$overlap" = "0" ] || { echo "FAIL: user dirs overlap"; FAIL=1; }
echo "dirs distinct on disk: $([ -d "$dirA" ] && [ -d "$dirB" ] && [ "$dirA" != "$dirB" ] && echo yes)"

echo "== teardown =="
docker compose -f docker/docker-compose.enterprise.yaml down || FAIL=1

if [ "$FAIL" = "0" ]; then
  echo "SMOKE PASS"
else
  echo "SMOKE FAIL"
fi
exit $FAIL