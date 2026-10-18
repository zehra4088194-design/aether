Copy everything below into Codex as your project prompt. Attach/keep this
entire folder (`aether/` + `aether.config.yaml` + `requirements.txt` +
`demo_end_to_end.py`) in the project directory — do NOT start from a
blank repo, the foundation already exists and works.

---

## CONTEXT: Foundation already built — extend it, don't rewrite it

This project (**Aether**, a Windows AI desktop assistant) already has its
first 5 core modules implemented and working. They live in this exact
folder structure — keep it:

```
aether/
  __init__.py
  system_interface.py          # Module 1 — DONE
  hardware_probe.py            # Module 2 — DONE
  llm_provider.py               # Module 3 — DONE
  agent_loop.py                  # Module 4 — DONE
  model_registry.py              # Module 6 — DONE (bring-your-own-model)
  sub_agent_orchestrator.py       # Module 8 — DONE (parallel task delegation)
  plugins/
    __init__.py
    file_automation_plugin.py   # Module 5 — DONE (reference plugin)
    camera_vision_plugin.py     # Module 7 — DONE (offline camera/screen vision)
    voice_plugin.py             # Module 9 — DONE (wake-word + STT + voice picker)
aether.config.yaml
requirements.txt
demo_end_to_end.py               # proves modules 1-5 wire together
ui_reference_design.html         # visual/structural reference for the real Tauri UI
```

**Before writing any new code:**
1. Read all 5 files in `aether/` top to bottom.
2. Run `python demo_end_to_end.py` to confirm the existing wiring still
   works on your machine (needs `pip install -r requirements.txt` first;
   set `ANTHROPIC_API_KEY` env var, or have Ollama running locally, for
   the LLM call itself to succeed — the plugin/tool-routing works
   either way).
3. Follow the exact same patterns already established:
   - OS-specific code ONLY inside `system_interface.py`, behind the
     `SystemInterface` abstract class. Never import `pywin32`/`winreg`
     etc. anywhere else.
   - Every capability is a subclass of `AetherPlugin` (see
     `agent_loop.py`) implementing `get_tools() -> list[ToolSpec]`,
     following the exact shape of `file_automation_plugin.py`.
   - Any tool that modifies files, sends messages, spends money,
     executes code, or touches an external service MUST set
     `requires_confirmation=True` on its `ToolSpec` — this is enforced
     centrally by `agent_loop.py`, do not bypass it.
   - Read `TierConfig` from `hardware_probe.py` wherever a decision
     depends on machine capability (model size, STT model, memory cap,
     animation on/off, etc.) — do not invent new thresholds elsewhere.
   - LLM calls always go through `ManagedLLM` from `llm_provider.py`,
     never call Anthropic/OpenAI/Ollama APIs directly from a plugin or
     the UI layer.

Full product spec — including 12 NEW improvements beyond the original
brief (battery-aware throttling, privacy consent screen, opt-in
diagnostics, global hotkey, cost estimator, circuit breaker for LLM
failures, sandboxed coding agent container option, plugin signing,
conversation export/import, automated test suite, auto-update, and an
accessibility pass) — is in `improved_master_prompt.md` in this same
folder. Read that file fully before starting; it is the source of truth
for scope. Anything below just tells you the *build order*.

## YOUR TASK: build everything from here to a working, shippable app

Build incrementally, in this order, committing working code at each
step before moving to the next:

### Step 1 — Test the foundation
- Add a `tests/` folder with `pytest` unit tests for `hardware_probe.py`
  (tier classification edge cases: low RAM, no GPU, high-end specs,
  battery-low-on-lite scenario) and `agent_loop.py` (tool routing,
  confirmation gating, unknown-tool handling).
- Add `pytest.ini` and a basic `.github/workflows/ci.yml` (even if CI
  isn't actually wired to a remote yet — keep the project CI-ready).

### Step 2 — Remaining starter plugins
Using `file_automation_plugin.py` as your template, build:
- `browser_automation_plugin.py` — Playwright-based. Two tools minimum:
  a read-only "summarize this page" tool (no confirmation needed) and
  an autonomous multi-step "do this on this site" tool
  (`requires_confirmation=True`). Stored-login credentials must go
  through `SystemInterface.get_credential`/`store_credential` — never
  handle raw passwords yourself.
- `voice_plugin.py` (already built, Module 9) — wire it in: hook wake-word
  detection to `openWakeWord` and bind the NEW global-hotkey fallback
  (default `Ctrl+Alt+Space`) as an alternative trigger. Register it with
  the `PluginRegistry` and connect `VoicePlugin.is_listening` /
  `is_speaking` to the UI's mic states described in Step 4. Persist the
  user's chosen `VoiceProfile` id under a `voice:` key in
  `aether.config.yaml`, same pattern as `model_registry.py`.
- `coding_agent_plugin.py` — sandboxed subprocess execution by default;
  on Performance tier, offer the NEW optional Docker/WSL2 container
  sandbox if available. tree-sitter for syntax-aware edits. Git tools
  (commit/branch/diff/push) all `requires_confirmation=True` for
  anything that could rewrite history or push.
- `memory_plugin.py` — Chroma or LanceDB vector store, cap size from
  `TierConfig.vector_memory_max_items`, periodic summarization job, and
  the NEW export/import-as-JSON tools.
- Wire `model_registry.py` (already built, Module 6) into the settings
  layer: persist `ModelEntry` records under the `models:` key in
  `aether.config.yaml`, resolve API keys via
  `SystemInterface.get_credential`/`store_credential` (never plaintext),
  and use `ModelRegistry.instantiate()` to build the active
  `ManagedLLM` at startup and whenever the user adds/edits/switches a
  model in Settings.
- Register `camera_vision_plugin.py` (already built, Module 7) with the
  `PluginRegistry`, but keep it disabled until the user explicitly turns
  on "Camera Vision" in Settings > Privacy. Bind the UI's camera-active
  indicator to `CameraVisionPlugin.is_camera_active`.
- Wire `sub_agent_orchestrator.py` (already built, Module 8) in as the
  entry point that receives every user message *instead of* calling
  `AgentLoop` directly — `SubAgentOrchestrator.run()` transparently
  falls back to a single normal agent loop when a request isn't
  splittable, so nothing else about the request/response flow changes.
  Wire `SubAgentOrchestrator`'s `on_status` callback to push
  `SubAgentStatus` updates over the same WebSocket channel as
  `on_step`, so the UI can render the swimlane card + orbiting
  satellites described in Step 4 below in real time.

### Step 3 — Proactive engine + trading plugin (both optional/off-by-default)
- `proactive_engine_plugin.py` — pattern detection over memory/activity
  log, emits suggestions only (never auto-acts), respects
  `TierConfig.proactive_mode_default_on`.
- `trading_plugin.py` — Alpha Vantage or Yahoo Finance free tier, clearly
  labeled "informational only, not financial advice" in every response
  that uses it, no order execution.

### Step 4 — Tauri UI shell
- **Use `ui_reference_design.html` in this folder as the design source
  of truth** — the palette, type system (Space Grotesk / Inter /
  JetBrains Mono), layout (left icon rail, top bar, main chat with
  tool-call and confirmation cards, right panel with the "Aether Core"
  orb + live activity log + camera preview slot), and the tier-aware
  motion on the Aether Core are all specified there. Open it in a
  browser to see it live before building. Do not default to a generic
  dark-mode template — match this system.
- The mockup also includes the **Sub-Agents** rail icon (badge = count
  of currently active sub-agents), an inline **swimlane card** in chat
  (one colored status chip per sub-agent: queued/running/done, driven
  by `SubAgentOrchestrator`'s `on_status` callback), and **orbiting
  satellite nodes** around the Aether Core, one per active sub-agent,
  colored from the `--agent-1..4` CSS tokens with a text legend below.
  Wire all three to the same live status stream — they should always
  agree with each other at any given moment.
- It also includes a **voice-picker card** (Settings > Voice tab in the
  real app) listing each `VoiceProfile` from `voice_plugin.py` with its
  style description and a preview button, plus a **listening** state on
  the mic button and a **speaking** indicator next to Aether's replies
  — bind these directly to `VoicePlugin.is_listening` / `is_speaking`.
- Connect to the Python backend via local REST/WebSocket API.
- Screens: main chat/command view (voice + typed), live activity log
  (every `AgentStep` from `agent_loop.py`'s `on_step` callback rendered
  in real time), settings panel (tier override, plugin toggles,
  **Models tab wired to `model_registry.py`**, **Privacy tab with the
  Camera Vision toggle from `camera_vision_plugin.py`**, voice
  settings), memory viewer/editor, and a resource graph showing current
  tier + live CPU/RAM.
- Implement the NEW first-run privacy consent screen (per-category
  toggles, all off by default except what's essential — camera and
  screen vision must be their own separate toggles, not bundled)
  before any other screen is reachable.
- Implement the NEW cost/token estimator display when cloud mode is active.
- Implement the NEW accessibility pass: full keyboard nav, screen-reader
  labels, high-contrast theme option, visible focus rings (see the
  mockup's focus-state CSS as a starting point).
- Disable/simplify the Aether Core animation and other motion
  automatically when `TierConfig.ui_animations_enabled` is False, per
  the mockup's `prefers-reduced-motion` handling.

### Step 5 — Packaging, updates, and docs
- Tauri bundler config for a Windows installer (.msi).
- Wire Tauri's updater (or a simple GitHub-releases-check fallback) —
  must always prompt before installing, never silent/forced.
- Update the README with: setup instructions, minimum specs per tier,
  a note on the `SystemInterface` layer for future macOS/Linux porting,
  and instructions for enabling/disabling each plugin.

## GROUND RULES (do not violate)
- No plugin may perform a destructive/external action without going
  through the `requires_confirmation` + `confirm_callback` path already
  built into `agent_loop.py`.
- No API keys or secrets ever hardcoded or bundled — always read from
  `aether.config.yaml` / env vars / Windows Credential Manager.
- 100%-local mode (Ollama + offline STT/TTS + local vector store) must
  remain fully functional with zero network calls when the user chooses
  it.
- Keep OS-specific code confined to `system_interface.py`. If you find
  yourself importing `pywin32` anywhere else, stop and add the method to
  `SystemInterface` instead.

Work step by step; after each step, run the existing tests plus any new
ones you add, and keep `demo_end_to_end.py` working throughout.
