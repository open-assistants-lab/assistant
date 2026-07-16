# Desktop App Packaging Plan

## Goal

Package Assistant's Python backend + Flutter frontend into a single installable desktop app:
- **macOS:** `.dmg` file (drag to Applications)
- **Windows:** `.exe` installer

Target audience: solo users who want a zero-configuration desktop experience. No Docker, no `uv run`, no terminal. Download, install, launch.

---

## Current State

| Component | How it runs today |
|-----------|-------------------|
| Backend (FastAPI) | `uv run assistant http` — Python process on `:8080` |
| Frontend (Flutter) | `flutter run` or `flutter build macos` — separate app connecting to `:8080` |
| Storage | SQLite + ChromaDB under `data/users/{user_id}/` |
| Auth | API key for WAN/multi-tenant; localhost bypass for solo |

The gap: a user needs to start the backend in a terminal AND launch the Flutter app. Two processes, two separate install steps. The packaged app makes this one click.

---

## Architecture: Bundled Desktop App

```
┌─────────────────────────────────────────┐
│              Flutter App                  │
│  (UI layer — chat, files, settings)      │
│                                           │
│  ┌─────────────────────────────────────┐ │
│  │        Process Manager              │ │
│  │  - Launch backend on startup       │ │
│  │  - Health-check :8080/health       │ │
│  │  - Terminate backend on quit       │ │
│  │  - Show connection status in UI    │ │
│  └─────────────────────────────────────┘ │
│                   │                       │
│                   ▼                       │
│  ┌─────────────────────────────────────┐ │
│  │       Bundled Backend Binary        │ │
│  │  (PyInstaller — Python + FastAPI +  │ │
│  │   all deps in a single executable)  │ │
│  │  Runs on localhost:8080             │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

**Key design decision:** Flutter owns the lifecycle. It spawns the backend as a child process on startup and kills it on shutdown. The user sees one app window — they never know the backend is a separate process.

---

## Step 1: Bundle Backend with PyInstaller

Convert the Python server into a standalone binary that includes Python, FastAPI, uvicorn, and all pip dependencies.

### `pyinstaller.spec`

```python
# pyinstaller.spec
a = Analysis(
    ['src/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/skills/', 'src/skills'),          # preserve path: code does Path("src/skills")
        ('data/cache/models.json', 'data/cache'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.protocols',
        'chromadb',
        'chromadb.db',
        'chromadb.api',
        'sqlite3',
        'fastapi',
        'pydantic',
        'onnxruntime',
        'hnswlib',
        'overrides',
        'importlib_resources',
        'typing_extensions',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'test', 'unittest'],
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='ea-backend',
    console=False,  # No terminal window on Windows
)
```

### Build command

```bash
pip install pyinstaller
pyinstaller pyinstaller.spec --onefile
# Output: dist/ea-backend (macOS) or dist/ea-backend.exe (Windows)
```

### What gets bundled

| Included | Not bundled (must exist on system) |
|----------|-------------------------------------|
| Python 3.11+ runtime | LLM provider executables |
| FastAPI + uvicorn | |
| SQLite (Python stdlib) | |
| All Python deps (chromadb, pydantic, httpx, etc.) | |
| Skill markdown files (`src/skills/`) | |
| models.dev cache (`data/cache/models.json`) | |

### Verify

```bash
./dist/ea-backend http
# Should start server on :8080 with no Python/pip/uv on the system
```

### Backend freeze adapter

`SkillRegistry._seed_system_skills()` uses `Path("src/skills")` (CWD-relative, line 65 of `src/skills/registry.py`). In a PyInstaller bundle, CWD is unpredictable. Add a one-line guard:

```python
def _seed_system_skills(self) -> None:
    if self._seeded:
        return
    self._seeded = True

    import sys
    if getattr(sys, 'frozen', False):
        system_src = Path(sys._MEIPASS) / "src" / "skills"
    else:
        system_src = Path("src/skills")

    if not system_src.exists():
        return
    # ... rest of seeding ...
```

PyInstaller `datas` preserves `src/skills/ → src/skills` so `sys._MEIPASS/src/skills/` exists. After seeding copies to `~/Assistant/Skills/`, the runtime never reads from `src/skills/` again.

---

## Step 2: Add Process Manager to Flutter

Add a native Dart layer that spawns and monitors the backend process.

### `flutter_app/lib/services/backend_process.dart`

```dart
import 'dart:io';
import 'package:flutter/foundation.dart';

class BackendProcess {
  Process? _process;
  bool _running = false;

  bool get isRunning => _running;

  Future<void> start() async {
    if (_running) return;

    final backendPath = _resolveBackendPath();
    _process = await Process.start(
      backendPath,
      ['http'],
      environment: {
        ...Platform.environment,
        'DEPLOYMENT_DATA_PATH': _resolveDataPath(),
      },
      mode: ProcessStartMode.normal,
    );

    _running = true;

    _process!.stdout.transform(utf8.decoder).listen((data) {
      debugPrint('[backend] $data');
    });
    _process!.stderr.transform(utf8.decoder).listen((data) {
      debugPrint('[backend-err] $data');
    });

    _process!.exitCode.then((code) {
      debugPrint('[backend] exited with code $code');
      _running = false;
    });
  }

  String _resolveBackendPath() {
    if (Platform.isMacOS) {
      return '${Platform.resolvedExecutable}/../Resources/ea-backend';
    } else if (Platform.isWindows) {
      return '${Platform.resolvedExecutable}/../ea-backend.exe';
    }
    throw UnsupportedError('Unsupported platform');
  }

  String _resolveDataPath() {
    if (Platform.isMacOS) {
      final home = Platform.environment['HOME'] ?? '/tmp';
      return '$home/Library/Application Support/Assistant/data';
    } else if (Platform.isWindows) {
      final appData = Platform.environment['APPDATA'] ?? r'C:\ProgramData';
      return '$appData\\Assistant\\data';
    }
    return 'data';
  }

  Future<void> stop() async {
    if (_process != null) {
      _process!.kill(ProcessSignal.sigterm);
      await _process!.exitCode;
      _running = false;
    }
  }
}
```

### Wire into `main.dart`

```dart
void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final backend = BackendProcess();
  await backend.start();
  await _waitForBackend('http://127.0.0.1:8080/health');

  runApp(
    const InstrumentedApp(
      child: ProviderScope(child: AssistantApp()),
    ),
  );
}

Future<void> _waitForBackend(String url) async {
  final client = HttpClient()
    ..connectionTimeout = const Duration(seconds: 2);
  for (var i = 0; i < 30; i++) {
    try {
      final request = await client.getUrl(Uri.parse(url));
      final response = await request.close();
      if (response.statusCode == 200) {
        client.close();
        return;
      }
    } catch (_) {}
    await Future.delayed(const Duration(seconds: 1));
  }
  client.close();
  // Backend failed — app shows disconnected state, user retries via reconnect
}
```

The backend reads `data_path` from settings via `DEPLOYMENT_DATA_PATH` env var (prefixed to `DEPLOYMENT_` per the pydantic `ConfigDict(env_prefix="DEPLOYMENT_")` at `src/config/settings.py:26`). Flutter sets this when spawning. No new CLI flags or backend code needed.

---

## Step 3: macOS Data Directory Setup

### Path

```
~/Library/Application Support/Assistant/
├── data/
│   └── users/
│       └── default_user/
│           ├── email/emails.db
│           ├── contacts/contacts.db
│           ├── todos/todos.db
│           └── conversation/messages.db
├── cache/
│   └── models.json
└── chroma/
```

Passed via `DEPLOYMENT_DATA_PATH` env var from the Flutter process manager (Step 2).

---

## Step 4: Package for macOS (DMG)

### Requirements
- macOS 12+ (Monterey or later)
- Xcode 15+
- Apple Developer account (for notarization) or ad-hoc signing (manual Gatekeeper bypass)

### Build steps

```bash
# 1. Build backend binary
pyinstaller pyinstaller.spec --onefile

# 2. Copy backend into Flutter macOS bundle
mkdir -p flutter_app/macos/Runner/Resources
cp dist/ea-backend flutter_app/macos/Runner/Resources/

# 3. Build Flutter macOS app
cd flutter_app
flutter build macos --release

# 4. Create DMG
mkdir -p dist_dmg
cp -r build/macos/Build/Products/Release/Executive\ Assistant.app dist_dmg/
ln -s /Applications dist_dmg/Applications
hdiutil create -volname "Assistant" \
  -srcfolder dist_dmg \
  -ov -format UDZO \
  Assistant.dmg
```

### Notarization (for distribution outside App Store)

```bash
# Code sign the .app bundle
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  "dist_dmg/Assistant.app"

# Notarize the DMG
xcrun notarytool submit Assistant.dmg \
  --apple-id "your@email.com" \
  --team-id "TEAMID" \
  --password "@keychain:AC_PASSWORD" \
  --wait

# Staple the notarization ticket
xcrun stapler staple Assistant.dmg
```

**Without notarization:** Users right-click → Open to bypass Gatekeeper on first launch.

### DMG contents

```
Assistant.dmg
├── Assistant.app
│   └── Contents/
│       ├── MacOS/
│       │   └── Assistant    ← Flutter runner
│       └── Resources/
│           └── ea-backend             ← PyInstaller binary
└── Applications (symlink)
```

---

## Step 5: Package for Windows (EXE)

### Option A: NSIS Installer

```bash
# 1. Build backend binary
pyinstaller pyinstaller.spec --onefile

# 2. Copy backend into Flutter Windows bundle
cp dist/ea-backend.exe flutter_app/windows/runner/

# 3. Build Flutter Windows app
cd flutter_app
flutter build windows --release
```

**NSIS script** (`installer.nsi`):
```nsis
OutFile "AssistantSetup.exe"
InstallDir "$PROGRAMFILES\Assistant"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "build\windows\runner\Release\*"
  CreateShortCut "$DESKTOP\Assistant.lnk" "$INSTDIR\executive_assistant.exe"
  CreateShortCut "$SMPROGRAMS\Assistant.lnk" "$INSTDIR\executive_assistant.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\Assistant.lnk"
  Delete "$SMPROGRAMS\Assistant.lnk"
SectionEnd
```

### Option B: MSIX via `flutter build windows --msix`

Requires Microsoft Partner Center account for code signing. Not practical for OSS without signing.

**Recommendation: NSIS** for v1. MSIX can come later if needed for auto-update.

---

## Step 6: Auto-Update

| Platform | Mechanism |
|----------|-----------|
| macOS | Sparkle framework (standard Mac auto-update). Requires hosting an `appcast.xml` feed |
| Windows | Squirrel.Windows or manual "check for updates" button that downloads the latest installer |

**v1:** Skip auto-update. Show current version in settings. Add "Check for Updates" button that opens the GitHub releases page.

---

## Step 7: CI Pipeline

### GitHub Actions matrix

```yaml
jobs:
  build:
    strategy:
      matrix:
        os: [macos-14, windows-2022]
    steps:
      - uses: actions/checkout@v4
      - uses: subosito/flutter-action@v2
        with:
          flutter-version: '3.29'
      - name: Build backend
        run: pip install pyinstaller && pyinstaller pyinstaller.spec
      - name: Build Flutter
        run: flutter build ${{ matrix.os == 'macos-14' && 'macos' || 'windows' }} --release
      - name: Package
        run: ${{ matrix.os == 'macos-14' && 'bash scripts/package-macos.sh' || 'bash scripts/package-windows.sh' }}
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: ea-${{ matrix.os }}
          path: '*.dmg'
```

---

## Execution Order

| Step | Effort | What | Success Criteria |
|------|--------|------|------------------|
| 1. PyInstaller spec | Medium | Create .spec file, test bundled binary on macOS + Windows | `./ea-backend http` starts server, `/health` returns 200 |
| 2. Process manager | Medium | `BackendProcess` class in Flutter, spawn/stop/health-check | Flutter app launches backend on startup, terminates on quit |
| 3. Data dir routing | Small | Pass `DEPLOYMENT_DATA_PATH` env var, use platform-appropriate paths | Data files go to `~/Library/Application Support/` not `cwd` |
| 4. macOS DMG | Medium | `flutter build macos` + copy backend + `hdiutil create` + codesign | Distributable `.dmg` that launches with one click |
| 5. Windows installer | Medium | NSIS script, `flutter build windows`, test on clean Win 11 VM | `.exe` installer that installs to Program Files, creates shortcuts |
| 6. CI pipeline | Medium | GitHub Actions matrix for macOS + Windows builds | Every git tag produces DMG + EXE artifacts |

**Total: ~4-5 days.**

---

## Step 8: Connector CLI Setup

External CLIs (`gws`, `gh`, `stripe`, `m365`) are not bundled — they're too large and platform-specific. Two mechanisms keep this zero-friction:

### On-demand (already works, zero new code)

Agent tries `gws gmail list` → `CLIToolAdapter` detects CLI missing on PATH → returns:
```
"Cannot execute `gws`. Install it with: npm install -g @googleworkspace/cli"
```
Agent calls `shell_execute` to run the install command. Works identically in packaged app vs. source.

### First-run setup wizard (new Flutter screen)

On first launch, a connector setup screen reads ConnectKit YAML specs, checks which CLIs are on PATH, and lets the user install with one click:

```
┌──────────────────────────────────────────┐
│  Set Up Connectors                        │
│                                           │
│  Google Workspace   ✓ gws found           │
│  GitHub (gh)        + Install (4.2 MB)    │
│  Stripe             + Install (6.7 MB)    │
│  Slack              + Install             │
│                                           │
│  [Skip]                [Install Selected]  │
└──────────────────────────────────────────┘
```

Each install button spawns a child process running the `install` command from ConnectKit's `CLIToolSource.install` field. The same `Process.start` pattern from Step 2 applies. The data model exists — only the Flutter screen is new.

Connectors can also be added later from Settings → Connectors.

---

## What This Enables

| Before | After |
|--------|-------|
| `uv run assistant http` + `flutter run` (two terminals) | Double-click app icon (one window) |
| `pip install -e ".[http]"` to install deps | No Python/pip needed — everything bundled |
| Source code clone required | Download DMG/EXE from GitHub releases |
| Data goes to `cwd/data/` | Data goes to proper OS app data directories |

The packaged app is for **solo local mode** — same machine, one user. Multi-tenant deployments still use Docker + Caddy (covered in `DEPLOYMENT.md`). The packaging does not change the server architecture — it just makes the solo case one-click.
