# Synthetic Jen reference (0.1.0)

A local deployment convention example, **not production Jen**. No customer
credentials, Zii, ClickHouse, Telegram, cloud inference, or external actions.
Jen's existing Telegram deployment is untouched. Owner labels are inventory,
not organisation authorization or hostile-tenant isolation.

## Prepare and validate

From the repository root, using its existing Python environment:

```sh
# Supply an operator-reviewed image digest already present in Docker's cache.
# No default digest, login, build or pull is supplied by this example.
IMAGE='your-reviewed-repository@sha256:YOUR_64_LOWERCASE_HEX_DIGEST'
python -m examples.jen_reference.instance prepare \
  --destination /absolute/test-dir/jen-one --instance-id jen-one \
  --owner-label synthetic-owner --engine-image "$IMAGE"
python -m examples.jen_reference.instance validate --instance /absolute/test-dir/jen-one
python /absolute/test-dir/jen-one/data/fixture.py \
  --state /absolute/test-dir/jen-one/data/.fixture/state.sqlite3 init
python -m examples.jen_reference.readiness --instance /absolute/test-dir/jen-one
```

Last command intentionally exits 1: static checks can pass but runtime is
`not_checked`, so `ready=false`. Instances have separate data directories and
random API keys in mode-0600 `.env`. Never print `.env`, resolved Compose config,
HTTP headers, or keys into logs. Preparation never starts anything. Existing
paths (including empty destinations), symlinks and unpinned images are refused.

Synthetic stores are exactly `fixture-alpha` and `fixture-beta`. `read` reads
current state. `pause` accepts only a stable ID, `true|false`, and a positive
expected revision; a stale revision fails and a no-op does not increment it.
Use the engine's real approval path, not direct CLI writes, for normal assistant
operations. The CLI is an operator/fixture interface, not an authorization
boundary; local filesystem owners can change code/config/state.

## Optional offline container smoke (not part of Python acceptance)

No startup was performed automatically. The supplied image must contain
`/app/.venv/bin/assistant`, all assets/dependencies and Python on PATH. The
container runs as UID/GID 1000, not root. On Linux ensure **only the selected
synthetic instance directory** is owned/readable/writable by that account;
review Docker Desktop bind permissions separately. Do not broaden host access
or enable egress to repair startup. The entire private/internal Compose network
has no outbound route. No provider, scheduler, writer, Docker socket or real
Jen mount is added. Specific installed code/profile/tool mounts are read-only;
normal engine state and fixture SQLite remain writable in the instance data.

Set INSTANCE_PORT in the instance `.env` to an unused port (e.g. 18181); set a
second instance to another port (e.g. 18182). Use different explicit project
names. `0` requests an ephemeral host port; find it with the project-scoped
`docker compose port assistant 8080` rather than claiming a fixed address.

```sh
# Run only after confirming the exact image is cached and reviewing permissions.
docker image inspect "$IMAGE" --format '{{.Id}}'
docker compose --project-name jen-reference-one --project-directory /absolute/test-dir/jen-one \
  -f /absolute/test-dir/jen-one/compose.yaml config --quiet
docker compose --project-name jen-reference-one --project-directory /absolute/test-dir/jen-one \
  -f /absolute/test-dir/jen-one/compose.yaml up -d --no-build --pull never
python -m examples.jen_reference.readiness --instance /absolute/test-dir/jen-one \
  --runtime-url http://127.0.0.1:18181
# Repeat preparation/start/readiness with jen-two, port 18182, project jen-reference-two.
# Stop/start ONE project, then repeat its readiness; no LLM/tool execution needed.
docker compose --project-name jen-reference-one --project-directory /absolute/test-dir/jen-one \
  -f /absolute/test-dir/jen-one/compose.yaml stop
docker compose --project-name jen-reference-one --project-directory /absolute/test-dir/jen-one \
  -f /absolute/test-dir/jen-one/compose.yaml start
```

Readiness accepts only literal loopback addresses, never redirects/proxies. It
checks immutable checksums, configuration, real main PROFILE.md path, tools,
fixture state, `/health`, and the enabled **custom** entries in `/tools`. The
current API also lists native catalog entries; that listing is not proof they
are enabled in an AgentLoop. The prepared YAML sets native tools to none.
The runtime API does not attest an image digest: matching metadata/env is only
static pinning, not runtime identity or LLM-quality proof. Do not call readiness
proof of HTTP roles, live integration, full customer isolation or production parity.

## Stopped backup and restore

**Stop all writers first:** engine, scripts, workers, schedulers, test processes.
Close SQLite connections. Do not copy an actively used WAL/journal. This is an
operator-declared stopped copy, not online backup or crash consistency. No
command here discovers/stops another agent's runtime. Package, source instance, snapshot and restore destination must be separate,
non-nested paths; overlap is refused before creating directories or keys.
Snapshot includes only instance data and non-secret metadata, not `.env`; keep required real secrets
in a separately controlled recovery process. Checksum integrity is not a
cryptographic authenticity boundary against an attacker who rewrites metadata.

Save the following executable recipe as `stopped_copy.py` in a local operator
working directory (outside reusable content). The acceptance tests execute this
exact recipe. It has no service/network calls and no generic archive extractor.

```python
import hashlib
import json
import shutil
from pathlib import Path
import yaml
from examples.jen_reference.instance import (
    CONTENT, ROOT_CONTENT, PACKAGE_VERSION, InstancePaths,
    prepare_instance, validate_instance, reject_overlapping_paths,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject_symlinks(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_path")
    if any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("symlink_content")


def data_checksums(data):
    return {str(p.relative_to(data)): digest(p) for p in data.rglob("*") if p.is_file()}


def snapshot_stopped(source, snapshot):
    # Caller has stopped all writers/closed connections; never automatic.
    reject_symlinks(snapshot)
    reject_overlapping_paths(source.root, snapshot)
    if validate_instance(source):
        raise ValueError("invalid_source")
    reject_symlinks(source.data)
    if snapshot.exists():
        raise ValueError("snapshot_exists")
    snapshot.mkdir(parents=True)
    shutil.copytree(source.data, snapshot / "data")
    (snapshot / "source.json").write_text(source.metadata.read_text())
    (snapshot / "checksums.json").write_text(json.dumps(data_checksums(snapshot / "data"), sort_keys=True))


def restore_stopped(package, snapshot, destination, *, instance_id, owner_label, engine_image):
    reject_symlinks(package)
    reject_symlinks(snapshot)
    reject_symlinks(destination)
    reject_overlapping_paths(package, snapshot, destination)
    meta = json.loads((snapshot / "source.json").read_text())
    if meta["package_version"] != PACKAGE_VERSION or meta["engine_image"] != engine_image:
        raise ValueError("version_or_image_mismatch")
    if data_checksums(snapshot / "data") != json.loads((snapshot / "checksums.json").read_text()):
        raise ValueError("snapshot_checksum_mismatch")
    expected = {"data/" + f: digest(package / f) for f in CONTENT}
    expected.update({f: digest(package / f) for f in ROOT_CONTENT})
    if meta["content_hashes"] != expected:
        raise ValueError("package_mismatch")
    paths = prepare_instance(package, destination, instance_id=instance_id,
                             owner_label=owner_label, engine_image=engine_image)
    paths.data.rename(destination / "data.initial")
    try:
        shutil.copytree(snapshot / "data", paths.data)
        if validate_instance(paths):
            raise ValueError("restored_content_mismatch")
        # Explicit recovery gate: preserve pending records but refuse writers.
        config_path = destination / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["governance"]["permissions"]["tools"]["fixture_store_pause"] = "deny"
        config["scheduling"]["subagent_enabled"] = False
        config_path.write_text(yaml.safe_dump(config))
        with paths.env_file.open("a") as f:
            f.write('GOVERNANCE_PERMISSIONS={"tools":{"fixture_store_read":"allow","fixture_store_pause":"deny"}}\n')
        fresh_meta = json.loads(paths.metadata.read_text())
        fresh_meta["content_hashes"]["config.yaml"] = digest(config_path)
        fresh_meta["recovery_mode"] = "writes_denied"
        paths.metadata.write_text(json.dumps(fresh_meta, indent=2))
        if validate_instance(paths):
            raise ValueError("recovery_configuration_invalid")
    except Exception:
        # Keep snapshot, copied data, and known-fresh data.initial for manual recovery.
        raise ValueError("restore_incomplete_manual_recovery") from None
    shutil.rmtree(destination / "data.initial")  # Only our known-fresh redundant copy.
    return paths
```

With that recipe saved, run from the repo root (set PYTHONPATH to that local
operator directory plus the repository, or save the script in the working
repo root without committing it). Choose all paths explicitly:

```sh
python -c 'from pathlib import Path; from stopped_copy import snapshot_stopped; from examples.jen_reference.instance import InstancePaths; snapshot_stopped(InstancePaths.at(Path("/absolute/test-dir/jen-one")), Path("/absolute/test-dir/snapshot-one"))'
# Supply the same reviewed digest in this explicit restore call.
python -c 'from pathlib import Path; from stopped_copy import restore_stopped; restore_stopped(Path("examples/jen_reference"), Path("/absolute/test-dir/snapshot-one"), Path("/absolute/test-dir/jen-restored"), instance_id="jen-restored", owner_label="reviewed-fixture-owner", engine_image="your-reviewed-repository@sha256:YOUR_64_LOWERCASE_HEX_DIGEST")'
python -m examples.jen_reference.instance validate --instance /absolute/test-dir/jen-restored
```

Do **not** start restored services automatically or replay approvals. Review
access, pending actions, changed bindings, schedules and new key first. Recovery
keeps fixture_store_pause denied in YAML **and** instance env. Independent write
re-enablement requires deliberate review and a fresh proposal; local owners can
bypass this gate by editing files or running fixture.py directly. Approvals may
be definition-bound to old installation paths. Updating image/package requires
separate revalidation: this recipe refuses mismatches. Source, snapshot and
restored state remain separate copies. On failure keep them all for manual
recovery; never rerun into the same destination.

Tests preserve fixture revisions, a real pending approval and a saved JSON
transcript artifact. They do **not** establish full engine MessageStore/history
or production recovery fidelity. Code rollback, database restore and reversing
external actions are three different things.

Teardown is project-specific `docker compose ... down` using the **same selected
project name/directory/file**. Do not use `down -v`, global prune or broad rm.
Keep snapshots until review; remove only individually verified synthetic paths.

## Real Jen mappings / deferred gates

The main profile and two TOOL.md files demonstrate package-vs-instance wiring;
the local SQLite revision guard demonstrates deterministic target/state checks.
Real Jen still needs its Zii service authentication, Telegram whitelist/roles,
control-plane Postgres, manifest/approval binding, domain workers and preservation
rules mapped and independently tested. This does not port 33 tools, retire its
bridge, prove fleet sync or replace operational authority with prompts. eddyave,
admi, multi-organisation access and native public UX each require separate gates.

Python acceptance: `python -m pytest tests/reference_deployments -q`. Docker
validation, two-instance startup/readiness, restart and teardown must be reported
separately as executed or skipped; a skipped smoke is never a passed deployment.
