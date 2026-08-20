# Native Sdk Experiment

A native-rendered Native SDK app: the view lives in `src/app.native`
(declarative markup) and the logic in `src/main.zig` (`Model`, `Msg`,
`update`). No WebView, no npm, no build files — the `native` CLI owns
the build.

## Commands

```sh
native dev     # build and run the app with hot reload
native test    # run the app's test suite
native build   # produce a ReleaseFast binary in zig-out/bin/
native check   # validate src/*.native markup and app.zon
```

## Hot reload

`src/app.native` is watched while `native dev` runs: edit it and the
window updates within ~2s without losing model state. Parse failures
keep the last good view.

## Owning the build

Need custom build logic? `native eject` writes a build.zig and
build.zig.zon into the app — from then on the `native` verbs drive
your files through `zig build` and never regenerate them.

## Features

- **Chat** — send/receive with block-structured streaming, multi-chat sidebar with search, unread dots, model picker (mid-stream switching), theme toggle, reduced motion.
- **Settings** — three sections: **Models** (catalog + role toggle), **General** (rubric, appearance, about), **Tools**.
- **Tools section** — searchable built-in tool list with per-tool enable/disable (scope PATCH), and **Connections**: ConnectKit SaaS catalog (400+ services) with status, disconnect, api_key credential form, and OAuth2 browser-authorize flow with 2s catalog polling, timeout, and cancel.
- **Sidebar** — New chat, search, chat list, Settings + theme toggle. (Tools/Skills/Subagents rows are gone; the Tools page lives in Settings.)

## Backend contract

The app talks to the FastAPI backend at `http://127.0.0.1:8080` with `user_id=native_sdk_chat`, `workspace_id=personal`. Start it from the repo root with `uv run assistant http`. Key endpoints: `/tools`, `/connectors/catalog`, `/connectors/connect`, `/connectors/disconnect`, `/auth/login` + `/auth/callback` (ConnectKit OAuth), `/settings`, `/settings/model-catalog`.

## Testing

```sh
uv run native test                 # Zig unit tests (90)
bash tests/frontend_suite.sh --all # automation suite (51 tests) — starts its own backend + app
bash tests/frontend_suite.sh --tools --settings --connectform   # focused modes
```

See `docs/frontend-tests.md` for the test catalog and `docs/settings-panel-spec.md` for the panel design.
