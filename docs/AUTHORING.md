# Authoring Guide — configure agents without touching engine code

**Audience:** admins and kit authors who deployed via Docker. Prerequisite:
[`docker/DEPLOYMENT.md`](docker/DEPLOYMENT.md) (deployment + `.env` setup).

The thesis: **verticals are content, not code.** Everything an author needs lives in
three artifact types — `PROFILE.md` (agent configuration), skills (`SKILL.md`), and
rubrics/eval sets (quality bars). This guide documents each surface as it actually
behaves today, with verification steps.

---

## 1. PROFILE.md — the agent's configuration

One `PROFILE.md` **per user**, at `data/Users/{user_id}/PROFILE.md`
(the default user's lives at the data root). It configures that user's agent:
model, persona, skills. **Never touches engine code.**

### Frontmatter fields (verified against the `AgentProfile` schema)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | str (1–64 chars, required) | — | Agent identifier |
| `description` | str | `""` | What this agent is for |
| `model` | str | `""` | Default model as `provider:model` (e.g. `anthropic:claude-sonnet-4`); validated against the registry **and provider-key availability** at bootstrap |
| `tools` | list[str] | `[]` | Requested tools — **advisory only, see precedence** |
| `skills` | list[str] | `[]` | Skills this agent uses (validated against the skills registry) |

Body (everything after the frontmatter) = the **persona / system prompt**.

```markdown
---
name: alice-assistant
description: Operations assistant for weekly reporting
model: anthropic:claude-sonnet-4
tools: [web_search]
skills: [writing, research]
---
You are Alice's operations assistant. Be concise; flag anything financial for review.
```

### Precedence (who wins)

```
per-request override (`"model": ...` + `provider_keys` in the API call)
   ↓ if the request doesn't specify a model
PROFILE.md (`model:`, persona, limits)      ← THE primary agent configuration
   ↓ only if no profile exists
deployment config (config.yaml agent.model / env keys)
```

- **Capabilities always outrank `profile.tools`** — governance over convenience
  (a profile requesting a disabled tool is ignored, not an error)
- Profile persona replaces the deployment's generic system prompt
- **No profile = unchanged behavior** (deployment default applies)
- **No model anywhere** → fail-fast at first use: *"No model configured. Set
  agent.model in config.yaml, add `model:` to the user's PROFILE.md, or pass
  model/provider_keys per request."*

### Lifecycle

```bash
# write the profile, then apply without downtime:
curl -X POST "http://localhost:8080/profile/reload?user_id=alice"
# invalid PROFILE.md -> 400, running loops untouched
# valid -> cached loop reset + active sessions detached (no stale turns)
```

**How to verify:** `POST /profile/reload` returns 200 with the new model/persona in
the body; `GET /models?user_id=…` reflects the profile model in the catalog view.

---

## 2. Skills — `SKILL.md` files

Skills are the second content type: reusable procedures the agent loads on demand.

- **Location**: the per-user skills dir (`data/Users/{user_id}/Skills/`); seeded
  skills are copied there on first run (seed-hash refresh only overwrites
  **unmodified** seeds — author edits are never overwritten)
- **Format**: one directory per skill containing `SKILL.md` (frontmatter: `name`,
  `description`, optional metadata; body = the procedure)
- **Discovery**: the registry scans the user's skills dir; the skill catalog is
  injected into the system prompt (`disable_model_invocation: true` keeps a skill
  out of the model-visible catalog — useful for bot-invoked-only skills)
- Loading is warnings-not-errors: a bad `name` falls back to the directory name with
  a diagnostic; over-long descriptions still load; only a missing description skips

**Authoring loop:** create the directory + `SKILL.md` → it appears in the catalog
(next request) → the agent loads it via the skills middleware.

**How to verify:** `GET /skills?user_id=…` lists the skill; the `<available_skills>`
block in the system prompt includes its name/description.

---

## 3. Rubrics & verification — the quality bar

Responses can be auto-graded by a separate **grader loop** before delivery.
**The admin owns the grader; authors do not.** The worker serves the user; the
grader serves the deployment — same party controlling both would be self-grading.

Admin knobs (`.env` or `config.yaml verification:` — env wins):

| Env | Field | Default |
|---|---|---|
| `VERIFICATION_ENABLED` | `enabled` | `false` |
| `VERIFICATION_DEFAULT_RUBRIC` | `default_rubric` | `""` |
| `VERIFICATION_GRADER_MODEL` | `grader_model` (empty = agent model) | `""` |
| `VERIFICATION_GRADER_SYSTEM_PROMPT` | `grader_system_prompt` | `""` |
| `VERIFICATION_GRADER_TOOLS` | `grader_tools` | `[]` (deliberately none) |
| `VERIFICATION_MAX_ITERATIONS` | `max_iterations` | `3` |
| `VERIFICATION_MODE` | `mode` (`off`/`on`/`auto`) | `off` |

**Per-run rubric** — an author/client may pass a rubric for one request:

```json
{"message": "draft the update", "user_id": "alice",
 "verification": {"rubric": "Accurate, cites sources, <=200 words", "mode": "on"}}
```

**Audit story:** the grading prompt is **hash-pinned** per user
(`grader_prompt_hash`, sha256) when verification is on — you can always prove which
prompt an output was graded against. Kits may ship rubrics (admin-curated at kit
install) — that is the sanctioned per-vertical variation, not user-defined ad-hoc bars.

**How to verify:** `GET /models` + a request with `verification.rubric` → the
response's `verification` block reports status/iterations; audit rows record the run.

---

## 4. Eval sets — the kit quality loop

Kits carry **eval sets** so their rubrics are measured, not vibes. The intended loop:

1. Kit author writes eval queries (persona-style interactions + expected qualities)
2. Eval sets run through the existing harness: `tests/evaluation/evaluate.py`
   (25 personas, `generate_test_queries`, per-persona success metrics via HTTP)
3. Acceptance threshold gates kit promotion — plan default **≥70% of drafted
   skills accepted without human edits** (configurable per kit)

**Honest status:** the harness exists and runs against a live server; kit eval sets
are **not yet wired** into it (documented-as-aspirational). Today the loop is:
author writes queries → run `tests/evaluation/evaluate.py` manually → review queue.
Wiring kit eval sets into the harness is a planned Phase 1 task (P1-T8).

**How to verify (today):** `uv run python tests/evaluation/evaluate.py --help` —
personas × queries × success metrics.

---

## 5. Model catalog — registry-driven

The model registry is powered by **models.dev** (fetched, cached ~5-min TTL,
offline fallback): **4172+ models across 110+ providers**. `provider:model` strings
are validated against it — a typo fails at bootstrap, not at first call.

- **Per-user model picker**: users set a default model via the Settings API /
  native app (stored as their `default_model`; wins over deployment config)
- **Provider keys** resolve per-request (`provider_keys`) → env
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) → saved per-user settings
- **How to verify:** `GET /models?user_id=…` — the catalog the picker renders

---

## 6. Capabilities — governance over profile

Every tool/skill/subagent has an enable state **per user** (scopes). Capabilities
are the governance layer: they decide what an agent *may* use, regardless of what
its PROFILE.md or a kit requests.

- Unconfigured items default to **enabled** (`scope=all`)
- `data/Users/{user_id}/capabilities.yaml` (+ migrated `item_scopes`) holds the
  user's enable state; managed via the Settings → Tools UI or the capabilities API
- Precedence: **capabilities > profile.tools** — an author requesting a disabled
  tool gets a silent no-op, not an error

**How to verify:** `PATCH /tools/{name} {"scope":"none"}` → the tool disappears from
that user's catalog; `GET /tools?user_id=…` reflects it.

---

## The author's workflow, end to end

1. Deploy once (`docker/DEPLOYMENT.md`)
2. For each user: `PROFILE.md` (§1) + optional skills (§2) + `PROFILE.md` reload
3. Admin: verification knobs (§3), kit rubrics, capabilities per user (§6)
4. Quality: eval set per kit, run through the harness (§4) before promoting
5. **Zero engine code touched.** If a change needs Python, it's an engine request —
   not an authoring task.