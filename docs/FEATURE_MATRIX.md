# Aether capability matrix

This matrix compares the supplied Stonic screenshot and the currently advertised Stonic surface on [stonicai.com](https://stonicai.com/) with the implemented Aether application. It is a product parity checklist, not an affiliation claim.

| Stonic-visible surface/capability | Aether implementation | Aether extension |
|---|---|---|
| Main AI chat | Central command workspace with typed chat and streaming activity | One confirmation broker for UI, main agent, and sub-agents |
| Voice assistant | Wake word, global hotkey/manual mic, Whisper STT, selectable Piper/ElevenLabs TTS | Voice preview isolation, offline-first profiles, visible listening/speaking state |
| Memory | Local Chroma memory search/list/edit/delete/import/export and periodic summaries | Tier-based retention caps and explicit storage consent |
| Soul/persona | Editable assistant name, user name, tone, and languages | Persona reaches both the main loop and every sub-agent without weakening safety rules |
| Skills/settings | Live plugin matrix plus settings/privacy/model/voice controls | Resource cost labels, adaptive disabling, manifest verification, accessibility controls |
| Agent Town / multiple agents | Up to four parallel workers, Core satellites, rail badge, swimlane/status observatory | Broken-plan fallback and one shared approval path |
| Live task bubbles/results | WebSocket agent-step/status feed and live activity dock | Provider health, throttling, cost, and confirmations appear on the same event stream |
| Chats/logs/notes/tasks | Chat history in session, activity log, local notes and tasks | Diagnostics export and semantic conversation memory when consented |
| Media link/camera/screen | Separate camera and screen actions with visible active/resting state | Independent consent plus confirmation on every capture; frames remain in memory |
| Today/headlines | User-triggered RSS headline brief | No silent polling; external fetch is confirmed |
| Visual intelligence hub | Flowchart, mind map, sequence, ER, class, Gantt, timeline, and pie generation | Safe Mermaid rendering, lazily loaded so normal chat stays lightweight |
| System Core | Animated adaptive Core with current tier and sub-agent satellites | Real CPU/RAM/battery telemetry and Lite/Balanced/Performance behavior |

## Additional Aether capabilities

- Ollama discovery and editable Anthropic/OpenAI-compatible model registry.
- Circuit breaker/fallback model management with per-session token and cost estimates.
- Scoped coding workspace, syntax checks, Git actions, and tier-aware subprocess/Docker/WSL2 isolation.
- Read-only browser summarization and confirmed multi-step Playwright automation.
- Quote-only trading research with disclaimers; no unattended order endpoint exists.
- Proactive suggestions that never auto-execute actions.
- First-run privacy wall, high-contrast/reduced-motion modes, keyboard navigation, and screen-reader live regions.
- Token-protected random-port loopback API, strict CSP, local diagnostics, signed updater artifacts, MSI, and NSIS packaging.
