# Aether

Aether is a Windows-first, local-first AI desktop assistant. It combines a Tauri command center with a hardware-aware Python agent runtime, selectable local/cloud models, voice, vision, browser and coding tools, memory, productivity, visual intelligence, and parallel sub-agents.

The app is deliberately deny-by-default: camera, screen, microphone, memory, browsing history, and telemetry all begin off. Agent-requested destructive/state-changing tools, arbitrary code execution, credentialed browser workflows, captures, and trading lookups go through one central confirmation broker.

## What is implemented

- Adaptive Lite, Balanced, and Performance tiers with low-battery throttling.
- Bring-your-own-model registry for Ollama, Anthropic, and OpenAI-compatible endpoints.
- ManagedLLM fallback, circuit breakers, token accounting, and estimated cloud cost.
- Typed chat plus configurable “Hey Aether”/global-hotkey voice activation, offline Whisper STT, Piper voices, and opt-in ElevenLabs.
- One-frame camera and screen vision with separate consent and per-capture confirmation.
- Playwright browser workflows, scoped coding tools, local Chroma memory, notes/tasks, headlines, trading quotes, proactive suggestions, and Mermaid visual generation.
- Up to four visible sub-agents sharing the same plugin registry and confirmation path.
- Accessible Tauri UI with high contrast, reduced motion, keyboard focus, live status, plugin/model/voice controls, diagnostics export, and signed updater support.
- MSI and NSIS packaging scripts with a PyInstaller backend sidecar.

See [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) for the Stonic parity audit and Aether additions.

## Development quick start (Windows)

Prerequisites: Python 3.12, Node.js LTS, Rust stable, and Visual Studio Build Tools with the Desktop C++ workload.

```powershell
python -m pip install -r requirements.txt
npm ci
python -m pytest
npm run build
npm run tauri -- dev
```

For typed-chat-only development, install the smaller `requirements-core.txt`. Start a local model with Ollama or add a cloud/model-compatible endpoint from Models. API keys entered in the UI go to Windows Credential Manager, not YAML.

Useful foundation checks:

```powershell
python -m aether.hardware_probe
python demo_end_to_end.py
```

## First run and privacy

The first launch blocks the workspace behind six independent choices: microphone, camera, screen, browsing history, memory, and diagnostics. All are off initially. Camera and screen remain separate in Settings, and changing consent immediately changes plugin availability. Typed chat and local safety controls do not require telemetry.

User data is stored under `%LOCALAPPDATA%\Aether`. Manual diagnostics ZIPs remain local until the user deliberately shares them.

## Licensing and first activation

If you want one license key to attach to only one PC at a time, use the built-in first-run licensing flow.

- Set `AETHER_LICENSE_REQUIRED=1` in the packaged build.
- Set `AETHER_LICENSE_URL` to your verifier endpoint.
- On first launch, Aether shows an activation screen before the workspace opens.
- If the PC is offline on first install, activation waits until the internet is available.
- After the first successful activation, Aether starts offline on later launches and keeps working until you remove the license in Settings.
- The Settings page includes `Remove license`, which calls the verifier to release the seat before deleting the local receipt.

For a free verifier, the repo includes a Cloudflare Worker + D1 scaffold in `cloudflare/license-worker/`.

- Cloudflare Workers has a free plan, and D1 fits the single-row activation lookup pattern well.
- `schema.sql` creates the license and activation tables.
- `src/index.ts` exposes `/v1/activate`, `/v1/verify`, `/v1/deactivate`, and `/v1/admin/issue`.
- The current Worker is deployed at `https://aether-license.noorhusnain791.workers.dev`; its health check is `/health`. Its D1 database binding is configured in `wrangler.toml`.
- The Worker admin secret is generated locally and stored in the current Windows user's Credential Manager through Python `keyring` under service `Aether Cloudflare` and username `license-admin-key`. It is not in the repository or desktop app. Retrieve it into the current PowerShell session only when issuing a key, then clear the environment variable:

  ```powershell
  $env:AETHER_LICENSE_URL = "https://aether-license.noorhusnain791.workers.dev"
  $env:AETHER_ADMIN_KEY = & "E:\AetherPyEnv\Scripts\python.exe" -c "import keyring; print(keyring.get_password('Aether Cloudflare', 'license-admin-key') or '')"
  if (-not $env:AETHER_ADMIN_KEY) { throw "The Aether Worker admin secret is missing from Windows Credential Manager." }
  .\scripts\issue_license.ps1 -Note "Customer"
  Remove-Item Env:AETHER_ADMIN_KEY
  ```

- No previous Cloudflare account is required. Create/sign in to the new Cloudflare account you want to use, then authenticate Wrangler in a PowerShell terminal:

  ```powershell
  npx wrangler@latest login
  npx wrangler@latest whoami
  ```

  Check that `whoami` shows the intended new account before creating anything.
- Create a new D1 database. Run the create command outside the worker folder so Wrangler does not load its config before the new database ID is known:

  ```powershell
  Push-Location $env:TEMP
  npx wrangler@latest d1 create aether-license-db
  Pop-Location
  ```

  Copy the returned `database_id` into `cloudflare/license-worker/wrangler.toml`, replacing `REPLACE_WITH_NEW_CLOUDFLARE_D1_DATABASE_ID`. The config contains no old account or database identifiers.
- From `cloudflare/license-worker/`, initialize the new database and deploy:

  ```powershell
  npx wrangler@latest d1 execute aether-license-db --remote --file .\schema.sql
  npx wrangler@latest secret put AETHER_ADMIN_KEY
  npx wrangler@latest deploy
  ```

  When prompted, enter a long, randomly generated admin secret and save it in a password manager. Do not commit it or put it in the app config. The deploy output provides the new `https://aether-license.<your-workers-subdomain>.workers.dev` URL. Check it with `Invoke-RestMethod https://<your-worker-url>/health`.
- Set the worker URL only for release builds that should require activation:

  ```powershell
  $env:AETHER_LICENSE_URL = "https://<your-worker-url>"
  $env:AETHER_LICENSE_REQUIRED = "1"
  ```

  Then run the desktop build in the same PowerShell session. Licensing is off by default for ordinary development builds; the activation feature remains available when release builds opt in.
- Issue customer keys using `scripts/issue_license.ps1` with the new worker URL and admin secret. `schema.sql` stores only SHA-256 key hashes; keep the raw admin secret and customer keys private.

## Models, voice, and optional assets

- Models can be added, edited, removed, discovered from Ollama, or made default without losing chat history.
- Piper voice models and Playwright Chromium are fetched by the desktop build script and bundled as application resources.
- Before redistributing a release, review each downloaded [Piper voice model card and license](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md); model licensing can vary by voice.
- A custom openWakeWord “Hey Aether” model can be set with `voice.wakeword_model_path`; the global hotkey remains available if the model/runtime is absent.
- Cloud voice and model credentials are resolved only through `SystemInterface`/Credential Manager.

## Build installers

The complete build downloads optional browser/voice assets, creates the Python sidecar, generates Windows icons, and produces MSI/NSIS bundles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_desktop.ps1 -Python "C:\Path\To\python.exe"
```

For a faster repeat build after dependencies/assets already exist:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_desktop.ps1 -SkipDependencies -SkipOptionalAssets
```

For a UI-only repeat build, add `-SkipBackend`; the script first verifies that a previously built sidecar exists.

Low-space system drives can redirect transient Rust/PyInstaller output to another drive:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_desktop.ps1 -Python "D:\AetherBuildEnv\Scripts\python.exe" -BuildRoot "D:\AetherBuild" -CargoTargetDir "D:\AetherTarget"
```

By default, outputs are written under `src-tauri\target\release\bundle`; when `-CargoTargetDir` is supplied they are written beneath that target directory instead. The updater is configured for this repository's GitHub Releases. Publish the signed `latest.json` and updater bundles there to enable in-app updates. The updater public key is committed; its private key is ignored under `.aether-data` and its password is stored in Windows Credential Manager. On a release machine, set `TAURI_SIGNING_PRIVATE_KEY_PATH` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

## Architecture guardrails

- `aether/system_interface.py` is the only OS-specific boundary. Porting to another OS means implementing that interface, then changing `get_system_interface()`—plugins should stay unchanged.
- Every plugin subclasses `AetherPlugin` and declares `ToolSpec` metadata, including confirmation requirements and resource cost.
- Direct UI tool execution and agent/sub-agent execution both use `AgentLoop.execute_tool`, so no capability bypasses confirmation.
- Hardware choices come from `TierConfig`; plugins do not invent their own machine thresholds.
- All inference flows through `ManagedLLM`; plugins never call an LLM provider directly.
- Third-party plugins can be checked against a SHA-256 manifest with `aether/plugin_security.py`; unverified plugins require an explicit warning/allow decision.

## Tests and CI

```powershell
python -m pytest -q
npm run build
cargo check --manifest-path src-tauri\Cargo.toml
```

CI runs Python safety/runtime tests, the TypeScript production build, and a Windows Rust/Tauri check. The test suite covers tier detection and low-battery behavior, confirmation enforcement, secret-safe config/model persistence, LLM fallback/circuit breaking, API authentication, and voice-preview isolation.

## Repository map

- `aether/runtime.py` — one composition root for config, hardware, models, plugins, agents, and events.
- `aether/api.py` — token-protected loopback FastAPI/WebSocket bridge.
- `aether/plugins/` — capability implementations.
- `src/` — accessible TypeScript command-center UI.
- `src-tauri/` — Windows shell, sidecar lifecycle, bundling, CSP, and updater.
- `scripts/` — reproducible sidecar and desktop builds.
- `tests/` — backend safety and integration tests.
# aether
