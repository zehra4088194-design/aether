import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/space-grotesk/latin-500.css";
import "@fontsource/space-grotesk/latin-600.css";
import "@fontsource/space-grotesk/latin-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import { api } from "./api";
import type { AetherState, AgentEvent, LicenseStatus, ModelEntry, SubAgent, ToolResult } from "./types";
import "./styles.css";

type ViewName = "command" | "visuals" | "memory" | "productivity" | "skills" | "agents" | "models" | "settings";
type ToastKind = "info" | "success" | "error";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  timestamp: Date;
}

interface PendingConfirmation {
  id: string;
  tool: string;
  description: string;
  arguments: Record<string, unknown>;
}

const app = document.querySelector<HTMLDivElement>("#app")!;
const liveRegion = document.querySelector<HTMLDivElement>("#a11y-live")!;
let state: AetherState;
let activeView: ViewName = "command";
let chatBusy = false;
let visualType = "flowchart";
let visualSource = "";
let messages: ChatMessage[] = [];
let activity: AgentEvent[] = [];
let pendingConfirmations: PendingConfirmation[] = [];
let headlines: Array<{ title: string; link?: string }> = [];
let memoryRecords: Array<Record<string, unknown>> = [];
let notes: Array<Record<string, unknown>> = [];
let tasks: Array<Record<string, unknown>> = [];
let editingModelId: string | null = null;
let currentLicense: LicenseStatus | null = null;
let stateRefreshTimer: number | null = null;
let licenseHeartbeatTimer: number | null = null;
let eventsStarted = false;
let licenseBusy = false;
let onlineHeartbeatBound = false;

const nav: Array<{ id: ViewName; label: string; icon: string }> = [
  { id: "command", label: "Command", icon: "chat" },
  { id: "visuals", label: "Visual intelligence", icon: "visual" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "productivity", label: "Notes and tasks", icon: "tasks" },
  { id: "skills", label: "Skills and plugins", icon: "skills" },
  { id: "agents", label: "Sub-agents", icon: "agents" },
  { id: "models", label: "Models", icon: "models" },
];

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function uid(): string {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
}

function icon(name: string): string {
  const paths: Record<string, string> = {
    chat: '<path d="M4 5.5h16v11H9l-5 3v-14Z"/><path d="M8 10h8M8 13h5"/>',
    visual: '<path d="M4 18 9 7l4 7 2-4 5 8H4Z"/><circle cx="17" cy="6" r="2"/>',
    memory: '<path d="M8 6a4 4 0 0 1 8 0v1a4 4 0 0 1 2 3.5V15a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4v-4.5A4 4 0 0 1 8 7V6Z"/><path d="M9 11h6M9 14h4"/>',
    tasks: '<path d="M9 5h11M9 12h11M9 19h11"/><path d="m3 5 1.5 1.5L7 4m-4 8 1.5 1.5L7 11m-4 8 1.5 1.5L7 18"/>',
    skills: '<path d="M9 3h6v4h4v6h-4v4H9v-4H5V7h4V3Z"/><path d="M9 9h6v2H9z"/>',
    agents: '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5m0 0-6 5m6-5 6 5"/>',
    models: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M4.9 4.9 7 7m10 10 2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19 13.5a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.5-.2-.1a1.7 1.7 0 0 0-1.9.1 1.7 1.7 0 0 0-.8 1.7v.2h-4v-.2a1.7 1.7 0 0 0-.8-1.7 1.7 1.7 0 0 0-1.9-.1l-.2.1-2-3.5.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H4v-4h.5A1.7 1.7 0 0 0 6 7.5a1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3.5.2.1A1.7 1.7 0 0 0 9.7 2a1.7 1.7 0 0 0 .8-1.7V0h4v.3a1.7 1.7 0 0 0 .8 1.7 1.7 1.7 0 0 0 1.9.1l.2-.1 2 3.5-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.5v4h-.5a1.7 1.7 0 0 0-1.5 1Z"/>',
    camera: '<path d="M3 7h4l2-2h6l2 2h4v12H3V7Z"/><circle cx="12" cy="13" r="4"/>',
    screen: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0m-7 7v3"/>',
    volume: '<path d="M4 10v4h4l5 4V6L8 10H4Z"/><path d="M16 9a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12"/>',
    captions: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M10 10a3 3 0 1 0 0 4m8-4a3 3 0 1 0 0 4"/>',
    send: '<path d="m4 4 17 8-17 8 3-8-3-8Z"/><path d="M7 12h14"/>',
    shield: '<path d="M12 2 4 5v6c0 5 3.4 9.2 8 11 4.6-1.8 8-6 8-11V5l-8-3Z"/><path d="m9 12 2 2 4-5"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 8a7 7 0 0 1 12-2l2 6M18 16a7 7 0 0 1-12 2l-2-6"/>',
    diagram: '<path d="M4 4h6v5H4zM14 15h6v5h-6zM14 4h6v5h-6zM4 15h6v5H4z"/><path d="M10 6.5h4M7 9v6m10-6v6m-7 2.5h4"/>',
    warning: '<path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5m0 3v.1"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.skills}</svg>`;
}

function announce(message: string): void {
  liveRegion.textContent = "";
  window.setTimeout(() => { liveRegion.textContent = message; }, 20);
}

function toast(message: string, kind: ToastKind = "info"): void {
  const host = document.querySelector<HTMLDivElement>("#toast-stack");
  if (!host) return;
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  host.append(item);
  announce(message);
  window.setTimeout(() => item.remove(), 4500);
}

function renderLoading(message = "Starting private local services…"): void {
  app.innerHTML = `<div class="loading-screen"><div class="loading-mark"><div class="loading-orb"></div><strong>Aether</strong><span>${escapeHtml(message)}</span></div></div>`;
}

function clearTimers(): void {
  if (stateRefreshTimer !== null) {
    window.clearInterval(stateRefreshTimer);
    stateRefreshTimer = null;
  }
  if (licenseHeartbeatTimer !== null) {
    window.clearInterval(licenseHeartbeatTimer);
    licenseHeartbeatTimer = null;
  }
}

function renderLicenseGate(status: LicenseStatus, message?: string): void {
  const copy = escapeHtml(
    message ||
      status.message ||
      "Connect to the internet once to activate this PC. After that, offline starts stay available until you remove the license in Settings.",
  );
  app.innerHTML = `
    <div class="loading-screen license-gate">
      <div class="license-panel panel-card">
        <div class="dock-card-title">
          <strong>Device activation</strong>
          <span class="micro-status ${status.configured ? "online" : ""}">${status.configured ? "READY" : "NEEDS INTERNET"}</span>
        </div>
        <h1>Aether needs a one-time key check</h1>
        <p>${copy}</p>
        <div class="license-meta">
          <div><span>Device code</span><strong>${escapeHtml(status.device_code)}</strong></div>
          <div><span>License state</span><strong>${status.valid ? "Activated" : "Locked"}</strong></div>
          <div><span>Offline start</span><strong>${status.valid ? "Allowed" : "After activation"}</strong></div>
        </div>
        <form class="form-grid license-form" id="license-form">
          <div class="field full">
            <label for="license-key">License key</label>
            <input id="license-key" type="password" autocomplete="off" inputmode="text" placeholder="Paste your Aether key here" />
          </div>
          <div class="field full">
            <button class="primary-button" id="license-submit" type="submit">Activate this PC</button>
          </div>
        </form>
        <span class="essential-note">If internet is unavailable on first install, connect once so the key can be verified. After that, Aether can start offline until you remove the license in Settings.</span>
      </div>
    </div>`;
  app.insertAdjacentHTML("beforeend", '<div class="toast-stack" id="toast-stack"></div>');
  bindLicenseGate();
}

function bindLicenseGate(): void {
  document.querySelector<HTMLFormElement>("#license-form")?.addEventListener("submit", activateLicense);
  document.querySelector<HTMLInputElement>("#license-key")?.focus();
}

async function activateLicense(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (licenseBusy) return;
  const input = document.querySelector<HTMLInputElement>("#license-key");
  const licenseKey = input?.value.trim() || "";
  if (!licenseKey) {
    toast("Paste your license key first.", "error");
    input?.focus();
    return;
  }
  licenseBusy = true;
  const button = document.querySelector<HTMLButtonElement>("#license-submit");
  if (button) button.disabled = true;
  try {
    currentLicense = await api.activateLicense(licenseKey);
    toast(currentLicense.message || "Activated on this PC.", "success");
    await bootLicensedShell();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    try {
      currentLicense = await api.licenseStatus();
    } catch {
      currentLicense = null;
    }
    if (currentLicense) {
      renderLicenseGate(currentLicense, message);
    } else {
      renderLoading(message);
    }
    toast(message, "error");
  } finally {
    licenseBusy = false;
    if (button) button.disabled = false;
  }
}

async function heartbeatLicense(): Promise<void> {
  if (!currentLicense?.required || !currentLicense.valid) return;
  try {
    currentLicense = await api.heartbeatLicense();
    if (activeView === "settings") renderWorkspace();
  } catch {
    // Offline starts stay allowed after activation; keep the local receipt.
  }
}

function startTimers(): void {
  clearTimers();
  stateRefreshTimer = window.setInterval(refreshState, 3000);
  licenseHeartbeatTimer = window.setInterval(() => {
    void heartbeatLicense();
  }, 15 * 60 * 1000);
}

function activeAgentCount(): number {
  return state.subagents.filter((agent) => agent.state === "queued" || agent.state === "running").length;
}

function renderShell(): void {
  app.innerHTML = `
    <div class="aether-shell tier-${state.tier}">
      <header class="topbar">
        <div class="brand-lockup"><span class="brand-spark">Æ</span><span class="brand-name">Aether</span><span class="brand-sub">· adaptive desktop intelligence</span></div>
        <div class="topbar-status">
          <span class="status-pill" id="tier-pill"><span class="status-dot"></span><strong>${escapeHtml(state.tier)}</strong> tier</span>
          <div class="top-meters">
            <span class="mini-meter">CPU <span class="mini-meter-track"><i class="mini-meter-fill" id="cpu-fill"></i></span><b id="cpu-label">0%</b></span>
            <span class="mini-meter">RAM <span class="mini-meter-track"><i class="mini-meter-fill" id="ram-fill"></i></span><b id="ram-label">0%</b></span>
          </div>
          <span class="cost-pill" id="cost-pill">local · $0.00</span>
          <span class="hotkey-pill">${escapeHtml(state.voice.global_hotkey)}</span>
          <button class="icon-action ${state.voice.speak_responses ? "active" : ""}" data-action="toggle-speech" aria-label="Toggle spoken replies" aria-pressed="${state.voice.speak_responses}" title="Spoken replies">${icon("volume")}</button>
          <button class="icon-action" data-action="captions" aria-label="Captions are always available" title="Captions">${icon("captions")}</button>
        </div>
      </header>
      <nav class="rail" aria-label="Aether workspaces">
        <button class="rail-logo" data-view="command" aria-label="Aether home">Æ</button>
        ${nav.map((item) => `<button class="nav-item ${item.id === activeView ? "active" : ""}" data-view="${item.id}" aria-label="${item.label}" title="${item.label}">${icon(item.icon)}${item.id === "agents" ? `<span class="nav-badge" id="agent-badge">${activeAgentCount()}</span>` : ""}</button>`).join("")}
        <span class="rail-spacer"></span>
        <button class="nav-item" data-view="settings" aria-label="Settings" title="Settings">${icon("settings")}</button>
      </nav>
      <aside class="context-dock" id="context-dock"></aside>
      <section class="workspace" id="workspace" tabindex="-1">
        <div class="workspace-view" id="workspace-view"></div>
        <form class="composer" id="composer">
          <div class="composer-inner">
            <button type="button" class="mic-button" id="mic-button" aria-label="Listen for one voice command">${icon("mic")}</button>
            <label class="sr-only" for="message-input">Message Aether</label>
            <input class="composer-field" id="message-input" autocomplete="off" placeholder="Message Aether — or use the global hotkey…" maxlength="40000" />
            <button class="send-button" id="send-button" aria-label="Send message">${icon("send")}</button>
          </div>
          <div class="composer-hint">Local confirmation guard active · Enter to send · Ctrl+K to focus</div>
        </form>
      </section>
      <aside class="insight-dock" id="insight-dock" aria-label="Aether live intelligence"></aside>
    </div>
    <div id="privacy-host"></div><div id="modal-host"></div><div class="toast-stack" id="toast-stack"></div>`;
  bindShell();
  renderContextDock();
  renderWorkspace();
  renderInsightDock();
  renderStateChrome();
  if (!state.privacy.consent_completed) renderPrivacyConsent();
}

function bindShell(): void {
  document.querySelectorAll<HTMLElement>("[data-view]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.view as ViewName));
  });
  document.querySelector<HTMLFormElement>("#composer")?.addEventListener("submit", sendMessage);
  document.querySelector("#mic-button")?.addEventListener("click", listenForVoice);
  document.querySelector('[data-action="toggle-speech"]')?.addEventListener("click", toggleSpeech);
  document.querySelector('[data-action="captions"]')?.addEventListener("click", () => toast("Captions are always on: every spoken reply also remains visible as text.", "success"));
}

function navigate(view: ViewName): void {
  activeView = view;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", (item as HTMLElement).dataset.view === view));
  renderWorkspace();
  document.querySelector<HTMLElement>("#workspace")?.focus();
  if (view === "memory") void loadMemories();
  if (view === "productivity") void loadProductivity();
}

function renderContextDock(): void {
  const host = document.querySelector<HTMLDivElement>("#context-dock");
  if (!host) return;
  const cameraAllowed = state.privacy.camera_vision;
  const screenAllowed = state.privacy.screen_vision;
  host.innerHTML = `
    <div class="dock-heading"><span><i></i>Context dock</span><span class="micro-status online">live</span></div>
    <section class="dock-card">
      <div class="dock-card-title"><strong>Media link</strong><span class="micro-status">${cameraAllowed || screenAllowed ? "READY" : "PRIVATE"}</span></div>
      <div class="media-window ${state.camera_active ? "active" : ""}">${state.camera_active ? '<span class="rec-label">● CAMERA ACTIVE</span>' : ""}${icon("camera")}<span class="sr-only">${state.camera_active ? "Camera active" : "Camera resting"}</span></div>
      <div class="dock-actions">
        <button class="soft-button" id="camera-describe" ${cameraAllowed ? "" : "disabled"}>Camera</button>
        <button class="soft-button" id="screen-describe" ${screenAllowed ? "" : "disabled"}>Screen</button>
      </div>
    </section>
    <section class="dock-card">
      <div class="dock-card-title"><strong>Today · signal brief</strong><button class="icon-action" id="headlines-refresh" aria-label="Load headlines">${icon("refresh")}</button></div>
      <div class="headlines-list">${headlines.length ? headlines.slice(0, 5).map((item, index) => `<div class="headline-item"><span class="headline-index">0${index + 1}</span><span>${escapeHtml(item.title)}</span></div>`).join("") : '<div class="empty-mini">Choose refresh to load a public RSS feed. External access will be confirmed.</div>'}</div>
    </section>
    <section class="dock-card">
      <div class="dock-card-title"><strong>Machine pulse</strong><button class="icon-action" id="health-refresh" aria-label="Refresh system health">${icon("refresh")}</button></div>
      <div class="resource-grid">
        <div class="resource-tile"><span>CPU</span><strong>${Math.round(state.resources.cpu_percent)}%</strong></div>
        <div class="resource-tile"><span>RAM</span><strong>${Math.round(state.resources.ram_percent)}%</strong></div>
        <div class="resource-tile"><span>Free</span><strong>${Math.round(state.resources.ram_available_mb / 1024)} GB</strong></div>
        <div class="resource-tile"><span>Power</span><strong>${state.resources.battery_percent == null ? "AC" : `${Math.round(state.resources.battery_percent)}%`}</strong></div>
      </div>
    </section>`;
  host.querySelector("#camera-describe")?.addEventListener("click", () => runContextTool("describe_camera_view", { question: "Describe what is in front of the camera." }));
  host.querySelector("#screen-describe")?.addEventListener("click", () => runContextTool("describe_screen_view", { question: "Describe what is currently visible on the screen." }));
  host.querySelector("#headlines-refresh")?.addEventListener("click", loadHeadlines);
  host.querySelector("#health-refresh")?.addEventListener("click", async () => {
    const result = await safeTool("get_system_health");
    if (result) toast(result.output, result.ok ? "success" : "error");
  });
}

function renderInsightDock(): void {
  const host = document.querySelector<HTMLDivElement>("#insight-dock");
  if (!host) return;
  const positions = [
    ["50%", "2%"], ["6%", "72%"], ["94%", "72%"], ["50%", "98%"],
  ];
  const active = state.subagents.filter((agent) => agent.state === "queued" || agent.state === "running").slice(0, 4);
  const legend = state.subagents.slice(-4);
  host.innerHTML = `
    <section class="core-panel">
      <div class="core-stage" aria-label="Aether Core with ${active.length} active sub-agents">
        <div class="core-path"></div><div class="core-orb"><div class="core-ring"></div><div class="core-ring r2"></div><div class="core-glow"></div></div>
        ${active.map((agent, index) => `<span class="satellite ${agent.state}" style="--sat-x:${positions[index][0]};--sat-y:${positions[index][1]};--agent-color:var(--agent-${index + 1})" title="${escapeHtml(agent.label)} · ${agent.state}"></span>`).join("")}
      </div>
      <div class="core-caption"><span class="core-online">● SYSTEM READY</span> · <strong>${state.tier}</strong> mode</div>
      <div class="satellite-legend">${legend.length ? legend.map((agent, index) => `<div class="legend-row" style="--agent-color:var(--agent-${(index % 4) + 1})"><span class="legend-dot"></span><span>${escapeHtml(agent.label)}</span><em>${agent.state}</em></div>`).join("") : '<div class="empty-mini">Sub-agents appear here when work can run in parallel.</div>'}</div>
    </section>
    <section class="insight-section"><div class="section-eyebrow"><span><i></i>Live activity</span><span>${activity.length}</span></div><div class="activity-list" id="activity-list">${renderActivity()}</div></section>
    <section class="insight-section"><div class="section-eyebrow"><span><i></i>Camera</span><span>${state.camera_active ? "ACTIVE" : "RESTING"}</span></div><div class="camera-mini ${state.camera_active ? "active" : ""}">${state.camera_active ? "● Processing one in-memory frame" : "Camera Vision is resting"}</div></section>`;
}

function renderActivity(): string {
  if (!activity.length) return '<div class="empty-mini">Agent steps will stream here.</div>';
  return activity.slice(-12).reverse().map((event) => {
    const data = event.data || {};
    const kind = String(data.kind || event.type);
    let detail = data.content || data.response || data.tool || data.message;
    if (event.type === "throttle") detail = data.active ? "Resource guard engaged" : "Resource guard released";
    if (event.type === "plugin_state") detail = `${String(data.name || "Plugin")} ${data.enabled ? "enabled" : "disabled"}`;
    if (event.type === "provider_state") detail = `${String(data.state || "provider")} · ${String(data.reason || "state changed")}`;
    const text = String(detail || event.type);
    return `<div class="activity-item"><span class="activity-tag ${escapeHtml(kind)}">${escapeHtml(kind.replaceAll("_", " ").slice(0, 7))}</span><span class="activity-text">${escapeHtml(text.slice(0, 180))}</span></div>`;
  }).join("");
}

function renderWorkspace(): void {
  const host = document.querySelector<HTMLDivElement>("#workspace-view");
  if (!host) return;
  if (activeView === "command") host.innerHTML = renderCommandView();
  if (activeView === "visuals") host.innerHTML = renderVisualView();
  if (activeView === "memory") host.innerHTML = renderMemoryView();
  if (activeView === "productivity") host.innerHTML = renderProductivityView();
  if (activeView === "skills") host.innerHTML = renderSkillsView();
  if (activeView === "agents") host.innerHTML = renderAgentsView();
  if (activeView === "models") host.innerHTML = renderModelsView();
  if (activeView === "settings") host.innerHTML = renderSettingsView();
  bindWorkspace();
  if (activeView === "command") scrollChat();
  if (activeView === "visuals" && visualSource) void renderMermaid();
}

function renderHeader(kicker: string, title: string, subtitle: string, actions = ""): string {
  return `<header class="view-header"><div class="view-title-wrap"><span class="view-kicker">${escapeHtml(kicker)}</span><h1 class="view-title">${escapeHtml(title)}</h1><p class="view-subtitle">${escapeHtml(subtitle)}</p></div>${actions ? `<div class="view-actions">${actions}</div>` : ""}</header>`;
}

function renderCommandView(): string {
  const greeting = state.persona.user_name ? `Ready when you are, ${escapeHtml(state.persona.user_name)}.` : "Ready when you are.";
  return `<div class="chat-stream" id="chat-stream">
    <section class="welcome-panel"><h2>${greeting}</h2><p>Aether can work across files, browser, code, memory, voice, vision and parallel sub-agents. Every sensitive action pauses for your approval.</p><div class="suggestion-row">
      ${["Check my system health", "Summarize a web page", "Create a project diagram", "Show my open tasks"].map((text) => `<button class="suggestion-chip" data-suggestion="${escapeHtml(text)}">${escapeHtml(text)}</button>`).join("")}
    </div></section>
    ${messages.map(renderMessage).join("")}
    ${state.subagents.length ? renderSwimlane() : ""}
  </div>`;
}

function renderMessage(message: ChatMessage): string {
  return `<article class="message ${message.role} ${message.pending ? "pending" : ""}" aria-label="${message.role === "user" ? "You" : "Aether"} message"><div class="message-avatar">${message.role === "user" ? "YOU" : "Æ"}</div><div class="message-content"><div class="message-meta">${message.role === "user" ? "You" : "Aether"} · ${message.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${message.role === "assistant" && state.voice.is_speaking ? " · speaking" : ""}</div><div class="message-bubble">${message.pending ? 'Thinking <span class="thinking-dots"><i></i><i></i><i></i></span>' : escapeHtml(message.content)}</div></div></article>`;
}

function renderSwimlane(): string {
  return `<section class="swimlane-card" aria-label="Sub-agent swimlane"><div class="swimlane-head">${icon("agents")} Sub-agents · one shared live state stream</div>${state.subagents.slice(-4).map((agent, index) => `<div class="agent-lane ${agent.state}" style="--agent-color:var(--agent-${(index % 4) + 1})"><span class="agent-lane-dot"></span><strong>${escapeHtml(agent.label)}</strong><span class="agent-state">${agent.state}</span></div>`).join("")}</section>`;
}

function renderVisualView(): string {
  return `${renderHeader("Visual intelligence hub", "Think spatially", "Create inspectable diagrams locally in the interface instead of receiving a static black-box image.")}
    <div class="visual-shell"><section class="panel-card"><h3>Compose a visual</h3><p>Choose a structure, then describe the system or idea. Aether returns Mermaid source you can inspect and export.</p><div class="visual-types">${["flowchart", "mindmap", "sequence", "er", "class", "gantt", "timeline", "pie"].map((type) => `<button class="visual-type ${visualType === type ? "active" : ""}" data-visual-type="${type}">${type}</button>`).join("")}</div><form id="visual-form" class="form-grid"><div class="field full"><label for="visual-prompt">What should Aether visualize?</label><textarea id="visual-prompt" required placeholder="Map the Aether request flow from privacy gate to sub-agent merge…"></textarea></div><div class="field full"><button class="primary-button">Generate ${escapeHtml(visualType)}</button></div></form></section><section class="visual-canvas" id="visual-canvas">${visualSource ? '<div id="mermaid-output" aria-label="Generated diagram"></div>' : `<div class="visual-placeholder">${icon("diagram")}<h3>Visual canvas standing by</h3><p>Flowcharts, mind maps, sequences, ER models, timelines, classes, Gantt plans and data charts render here.</p></div>`}</section></div>`;
}

function renderMemoryView(): string {
  const memoryPlugin = state.plugins.find((plugin) => plugin.name === "memory");
  return `${renderHeader("Memory vault", "Your context, under your control", `Tier cap: ${state.tier_config.vector_memory_max_items.toLocaleString()} local items. Export and import are portable JSON, never lock-in.`, '<div class="button-row"><button class="soft-button" id="memory-summarize">Summarize</button><button class="soft-button" id="memory-refresh">Refresh</button></div>')}
    <div class="panel-grid"><section class="panel-card"><h3>Memory records</h3><p>${memoryPlugin?.enabled ? "Semantic local memory is enabled." : "Memory is off until you explicitly enable storage consent."}</p><div class="card-list">${memoryRecords.length ? memoryRecords.map((record) => `<article class="data-card"><div class="data-card-main"><strong>${escapeHtml(record.kind || "memory")}</strong><span>${escapeHtml(String(record.text || "").slice(0, 260))}</span></div></article>`).join("") : '<div class="empty-mini">No memory records loaded.</div>'}</div></section><section class="panel-card"><h3>Backup and migrate</h3><p>Choose explicit paths. Import adds records and never deletes existing conversation history.</p><form id="memory-export" class="form-grid"><div class="field full"><label for="memory-export-path">Export JSON path</label><input id="memory-export-path" value="${escapeHtml("~/Documents/aether-memory.json")}" /></div><button class="soft-button">Export JSON</button></form><form id="memory-import" class="form-grid" style="margin-top:12px"><div class="field full"><label for="memory-import-path">Import JSON path</label><input id="memory-import-path" placeholder="C:\\Users\\you\\Documents\\aether-memory.json" /></div><button class="soft-button">Import JSON</button></form></section></div>`;
}

function renderProductivityView(): string {
  return `${renderHeader("Personal workspace", "Notes and tasks", "The Stonic-style chat/logs/notes/tasks surface, backed by local files and the same confirmation guard.", '<button class="soft-button" id="productivity-refresh">Refresh</button>')}
    <div class="panel-grid"><section class="panel-card"><h3>Notes</h3><p>Plain local notes available to Aether when you ask.</p><form id="note-form" class="form-grid"><div class="field full"><label>Title</label><input id="note-title" required /></div><div class="field full"><label>Content</label><textarea id="note-content"></textarea></div><button class="primary-button">Create note</button></form><div class="card-list" style="margin-top:14px">${notes.length ? notes.map((note) => `<article class="data-card"><div class="data-card-main"><strong>${escapeHtml(note.title)}</strong><span>${escapeHtml(note.content)}</span></div></article>`).join("") : '<div class="empty-mini">No notes yet.</div>'}</div></section><section class="panel-card"><h3>Tasks</h3><p>Create reminders and mark work complete.</p><form id="task-form" class="form-grid"><div class="field full"><label>Task</label><input id="task-title" required /></div><div class="field"><label>Due (optional)</label><input id="task-due" type="datetime-local" /></div><div class="field"><label>&nbsp;</label><button class="primary-button">Add task</button></div></form><div class="card-list" style="margin-top:14px">${tasks.length ? tasks.map((task) => `<article class="data-card"><div class="data-card-main"><strong>${task.done ? "✓ " : ""}${escapeHtml(task.title)}</strong><span>${escapeHtml(task.due || "No due time")}</span></div>${task.done ? "" : `<div class="data-card-actions"><button class="soft-button" data-complete-task="${escapeHtml(task.id)}">Done</button></div>`}</article>`).join("") : '<div class="empty-mini">No tasks yet.</div>'}</div></section></div>`;
}

function renderSkillsView(): string {
  return `${renderHeader("Capability matrix", "Skills and plugins", "Each capability declares its cost, tools and live state. Heavy skills can be throttled automatically on constrained hardware.")}
    <div class="panel-grid">${state.plugins.map((plugin) => `<article class="panel-card"><div class="dock-card-title"><div><h3>${escapeHtml(plugin.name.replaceAll("_", " "))}</h3><span class="status-badge ${plugin.enabled ? "on" : ""}">${plugin.enabled ? "● enabled" : "○ disabled"}</span></div><input class="toggle" type="checkbox" data-plugin-toggle="${escapeHtml(plugin.name)}" ${plugin.enabled ? "checked" : ""} aria-label="Toggle ${escapeHtml(plugin.name)}" /></div><p>${escapeHtml(plugin.description)}</p><div class="button-row"><span class="status-badge cost-${plugin.resource_cost}">${plugin.resource_cost} cost</span><span class="status-badge">${plugin.tools.length} tools</span></div></article>`).join("")}</div>`;
}

function renderAgentsView(): string {
  return `${renderHeader("Parallel reasoning", "Sub-agent observatory", "Independent work can run side-by-side; dependent steps remain one task. All workers share one central confirmation broker.")}
    <div class="panel-grid"><section class="panel-card"><h3>Live worker lanes</h3><p>Queued, running, done and error states come from the same stream as the Core satellites and rail badge.</p><div class="card-list">${state.subagents.length ? state.subagents.slice().reverse().map((agent, index) => `<article class="data-card" style="--agent-color:var(--agent-${(index % 4) + 1})"><span class="legend-dot"></span><div class="data-card-main"><strong>${escapeHtml(agent.label)}</strong><span>${escapeHtml(agent.detail || "No extra detail")}</span></div><span class="status-badge ${agent.state === "done" ? "on" : ""}">${agent.state}</span></article>`).join("") : '<div class="empty-mini">No sub-agent run in this session yet.</div>'}</div></section><section class="panel-card"><h3>Safety architecture</h3><p>Planner failures fall back to one normal agent. The pool is capped at four. A sub-agent never sends an out-of-band approval request.</p><div class="setting-row"><div class="setting-copy"><strong>Maximum concurrency</strong><span>Hard capped for predictable resource use</span></div><span class="status-badge on">4 agents</span></div><div class="setting-row"><div class="setting-copy"><strong>Shared confirmation path</strong><span>Every destructive/external tool pauses centrally</span></div><span class="status-badge on">enforced</span></div><div class="setting-row"><div class="setting-copy"><strong>Broken-plan fallback</strong><span>The original request always remains runnable</span></div><span class="status-badge on">safe</span></div></section></div>`;
}

function renderModelsView(): string {
  const editing = state.models.models.find((model) => model.id === editingModelId);
  const kind = editing?.kind || "local_ollama";
  return `${renderHeader("Bring your own model", "Model constellation", "Add local Ollama or any compatible cloud endpoint. API keys go to Credential Manager and never appear in YAML.", '<button class="soft-button" id="ollama-discover">Discover Ollama</button>')}
    <div class="panel-grid"><section class="panel-card"><h3>Configured models</h3><p>Switching the default rebuilds ManagedLLM without changing conversation history.</p><div class="card-list">${state.models.models.length ? state.models.models.map((model) => renderModelCard(model)).join("") : '<div class="empty-mini">No registry entries yet; legacy config fallback is active.</div>'}</div></section><section class="panel-card"><h3>${editing ? "Edit model" : "Add model"}</h3><p>Compatible endpoints should point to the API base before <code>/chat/completions</code>.</p><form id="model-form" class="form-grid"><div class="field"><label>Display name</label><input id="model-name" value="${escapeHtml(editing?.display_name || "")}" required /></div><div class="field"><label>Kind</label><select id="model-kind"><option value="local_ollama" ${kind === "local_ollama" ? "selected" : ""}>Local Ollama</option><option value="cloud_anthropic" ${kind === "cloud_anthropic" ? "selected" : ""}>Anthropic cloud</option><option value="cloud_openai_compatible" ${kind === "cloud_openai_compatible" ? "selected" : ""}>OpenAI-compatible</option></select></div><div class="field full"><label>Model ID</label><input id="model-id" value="${escapeHtml(editing?.model_id || "")}" list="ollama-models" required /><datalist id="ollama-models"></datalist></div><div class="field full"><label>Endpoint URL (compatible/custom local)</label><input id="model-endpoint" value="${escapeHtml(editing?.endpoint_url || "")}" placeholder="http://localhost:11434 or https://api.example.com/v1" /></div><div class="field full"><label>API key (stored in Credential Manager)</label><input id="model-key" type="password" autocomplete="new-password" placeholder="${editing ? "Leave blank to keep the saved key" : ""}" /></div><div class="field"><label><input id="model-default" type="checkbox" ${editing?.is_default ? "checked" : ""} /> Make default</label></div><div class="field button-row"><button class="primary-button">${editing ? "Save changes" : "Add model"}</button>${editing ? '<button class="soft-button" id="cancel-model-edit" type="button">Cancel</button>' : ""}</div></form></section><section class="panel-card"><h3>Local vision model</h3><p>Camera and screen frames always use a dedicated local Ollama vision model through ManagedLLM.</p><form id="vision-model-form" class="form-grid"><div class="field full"><label for="vision-model">Ollama vision model</label><input id="vision-model" list="ollama-models" value="${escapeHtml(state.vision.local_model)}" required /></div><button class="primary-button">Save vision model</button></form></section></div>`;
}

function renderModelCard(model: ModelEntry): string {
  return `<article class="data-card"><div class="data-card-main"><strong>${escapeHtml(model.display_name)} ${model.id === state.models.default_id ? '<span class="status-badge on">default</span>' : ""}</strong><span>${escapeHtml(model.kind)} · ${escapeHtml(model.model_id)}</span></div><div class="data-card-actions">${model.id === state.models.default_id ? "" : `<button class="soft-button" data-default-model="${escapeHtml(model.id)}">Use</button>`}<button class="soft-button" data-edit-model="${escapeHtml(model.id)}">Edit</button><button class="soft-button" data-remove-model="${escapeHtml(model.id)}">Remove</button></div></article>`;
}

function privacySettingRows(): string {
  const rows = [
    ["voice_recording", "Voice recording", "Hotkey/button capture asks first; an optional wake-word model is processed locally."],
    ["camera_vision", "Camera Vision", "Separate consent; each frame also requires confirmation."],
    ["screen_vision", "Screen Vision", "Separate consent; every capture requires confirmation."],
    ["browsing_history", "Browsing history", "Allows contextual browser history access when implemented."],
    ["memory_storage", "Memory storage", "Keeps local semantic memories and conversations."],
    ["telemetry", "Anonymous diagnostics", "Off by default; no silent uploads, ever."],
  ];
  return rows.map(([key, title, copy]) => `<div class="setting-row"><div class="setting-copy"><strong>${title}</strong><span>${copy}</span></div><input class="toggle privacy-setting" data-privacy-key="${key}" type="checkbox" ${state.privacy[key] ? "checked" : ""} aria-label="${title}" /></div>`).join("");
}

function renderSettingsView(): string {
  const voices = state.voice.profiles;
  const main = `${renderHeader("Control surface", "Settings, Soul and privacy", "Tune Aether's personality, voice, accessibility, performance and data permissions without editing files.")}
    <div class="panel-grid"><section class="panel-card"><h3>Soul · persona</h3><p>This controls tone and address, not hidden behavior or safety policy.</p><form id="persona-form" class="form-grid"><div class="field"><label>Assistant name</label><input id="persona-assistant" value="${escapeHtml(state.persona.assistant_name)}" /></div><div class="field"><label>Your preferred name</label><input id="persona-user" value="${escapeHtml(state.persona.user_name)}" /></div><div class="field full"><label>Communication style</label><input id="persona-style" value="${escapeHtml(state.persona.style)}" /></div><div class="field full"><label>Languages (comma separated)</label><input id="persona-languages" value="${escapeHtml(state.persona.languages.join(", "))}" /></div><button class="primary-button">Save persona</button></form></section><section class="panel-card"><h3>Privacy controls</h3><p>Camera and screen remain independent. These switches update plugin availability immediately.</p><div id="privacy-settings">${privacySettingRows()}</div></section><section class="panel-card"><h3>Voice studio</h3><p>Preview and select curated offline voices; ElevenLabs stays cloud/opt-in.</p><div class="card-list">${voices.map((voice) => `<article class="data-card"><div class="message-avatar" style="border-radius:50%">${escapeHtml(voice.display_name[0])}</div><div class="data-card-main"><strong>${escapeHtml(voice.display_name)} ${voice.offline ? '<span class="status-badge on">offline</span>' : '<span class="status-badge">cloud</span>'}</strong><span>${escapeHtml(voice.style)}</span></div><div class="data-card-actions"><button class="soft-button" data-preview-voice="${voice.id}">Preview</button>${voice.id === state.voice.active_id ? '<span class="status-badge on">selected</span>' : `<button class="soft-button" data-select-voice="${voice.id}">Select</button>`}</div></article>`).join("")}</div><form id="voice-trigger-form" class="form-grid" style="margin-top:14px"><div class="field"><label>Global hotkey</label><input id="voice-hotkey" value="${escapeHtml(state.voice.global_hotkey)}" /></div><div class="field"><label>Hey Aether model path</label><input id="wakeword-model" value="${escapeHtml(state.voice.wakeword_model_path || "")}" placeholder="C:\\Models\\hey_aether.onnx" /></div><button class="soft-button">Save voice triggers</button></form><form id="elevenlabs-form" class="form-grid" style="margin-top:14px"><div class="field"><label>ElevenLabs voice ID</label><input id="elevenlabs-voice-id" value="${escapeHtml(state.voice.elevenlabs_voice_id)}" /></div><div class="field"><label>API key (Credential Manager)</label><input id="elevenlabs-key" type="password" autocomplete="new-password" placeholder="Leave blank to keep saved key" /></div><button class="soft-button">Save cloud voice</button></form></section><section class="panel-card"><h3>Accessibility and performance</h3><p>Keyboard navigation, screen-reader labels and focus rings are always enabled.</p><div class="setting-row"><div class="setting-copy"><strong>High contrast</strong><span>Increase text and boundary contrast.</span></div><input class="toggle" id="high-contrast" type="checkbox" ${state.ui.high_contrast ? "checked" : ""} /></div><div class="setting-row"><div class="setting-copy"><strong>Reduce motion</strong><span>Static Core and minimal transitions.</span></div><input class="toggle" id="reduce-motion" type="checkbox" ${state.ui.reduced_motion ? "checked" : ""} /></div><div class="setting-row"><div class="setting-copy"><strong>Performance tier</strong><span>Auto uses hardware and low-battery throttling.</span></div><select id="tier-override"><option value="">Auto</option><option value="lite" ${state.tier === "lite" ? "selected" : ""}>Lite</option><option value="balanced" ${state.tier === "balanced" ? "selected" : ""}>Balanced</option><option value="performance" ${state.tier === "performance" ? "selected" : ""}>Performance</option></select></div><div class="setting-row"><div class="setting-copy"><strong>Diagnostics bundle</strong><span>Generated locally and never uploaded automatically.</span></div><button class="soft-button" id="export-diagnostics">Export</button></div><div class="setting-row"><div class="setting-copy"><strong>App updates</strong><span>Check manually; installation always asks first.</span></div><button class="soft-button" id="check-updates">Check</button></div></section></div>`;
  const licenseCard = `<section class="panel-card"><h3>Device activation</h3><p>${escapeHtml(currentLicense?.message || "This copy is attached to the current PC.")}</p><div class="setting-row"><div class="setting-copy"><strong>${escapeHtml(currentLicense?.key_masked || "Activated")}</strong><span>Device ${escapeHtml(currentLicense?.device_code || "local")} · offline startup allowed</span></div><button class="danger-button" id="remove-license">Remove license</button></div><span class="essential-note">Removing requires internet so the key can be released for another PC.</span></section>`;
  return main.replace(/<\/div>$/, `${licenseCard}</div>`);
}

function bindWorkspace(): void {
  document.querySelectorAll<HTMLButtonElement>("[data-suggestion]").forEach((button) => button.addEventListener("click", () => {
    const input = document.querySelector<HTMLInputElement>("#message-input"); if (input) { input.value = button.dataset.suggestion || ""; input.focus(); }
  }));
  document.querySelectorAll<HTMLButtonElement>("[data-visual-type]").forEach((button) => button.addEventListener("click", () => { visualType = button.dataset.visualType || "flowchart"; renderWorkspace(); }));
  document.querySelector("#visual-form")?.addEventListener("submit", generateVisual);
  document.querySelector("#memory-refresh")?.addEventListener("click", loadMemories);
  document.querySelector("#memory-summarize")?.addEventListener("click", summarizeMemories);
  document.querySelector("#memory-export")?.addEventListener("submit", exportMemory);
  document.querySelector("#memory-import")?.addEventListener("submit", importMemory);
  document.querySelector("#productivity-refresh")?.addEventListener("click", loadProductivity);
  document.querySelector("#note-form")?.addEventListener("submit", createNote);
  document.querySelector("#task-form")?.addEventListener("submit", createTask);
  document.querySelectorAll<HTMLButtonElement>("[data-complete-task]").forEach((button) => button.addEventListener("click", () => completeTask(button.dataset.completeTask || "")));
  document.querySelectorAll<HTMLInputElement>("[data-plugin-toggle]").forEach((toggle) => toggle.addEventListener("change", () => togglePlugin(toggle)));
  document.querySelector("#model-form")?.addEventListener("submit", saveModel);
  document.querySelector("#vision-model-form")?.addEventListener("submit", saveVisionModel);
  document.querySelector("#cancel-model-edit")?.addEventListener("click", () => { editingModelId = null; renderWorkspace(); });
  document.querySelector("#ollama-discover")?.addEventListener("click", discoverOllama);
  document.querySelectorAll<HTMLButtonElement>("[data-default-model]").forEach((button) => button.addEventListener("click", () => setDefaultModel(button.dataset.defaultModel || "")));
  document.querySelectorAll<HTMLButtonElement>("[data-edit-model]").forEach((button) => button.addEventListener("click", () => { editingModelId = button.dataset.editModel || null; renderWorkspace(); }));
  document.querySelectorAll<HTMLButtonElement>("[data-remove-model]").forEach((button) => button.addEventListener("click", () => removeModel(button.dataset.removeModel || "")));
  document.querySelector("#persona-form")?.addEventListener("submit", savePersona);
  document.querySelectorAll<HTMLInputElement>(".privacy-setting").forEach((toggle) => toggle.addEventListener("change", savePrivacySettings));
  document.querySelectorAll<HTMLButtonElement>("[data-preview-voice]").forEach((button) => button.addEventListener("click", () => previewVoice(button.dataset.previewVoice || "")));
  document.querySelectorAll<HTMLButtonElement>("[data-select-voice]").forEach((button) => button.addEventListener("click", () => selectVoice(button.dataset.selectVoice || "")));
  document.querySelector("#voice-trigger-form")?.addEventListener("submit", saveVoiceTriggers);
  document.querySelector("#elevenlabs-form")?.addEventListener("submit", saveElevenLabs);
  document.querySelector<HTMLInputElement>("#high-contrast")?.addEventListener("change", changeAccessibility);
  document.querySelector<HTMLInputElement>("#reduce-motion")?.addEventListener("change", changeAccessibility);
  document.querySelector<HTMLSelectElement>("#tier-override")?.addEventListener("change", changeTier);
  document.querySelector("#export-diagnostics")?.addEventListener("click", exportDiagnostics);
  document.querySelector("#check-updates")?.addEventListener("click", checkUpdates);
  document.querySelector("#remove-license")?.addEventListener("click", removeLicense);
}

async function bootLicensedShell(): Promise<void> {
  if (!eventsStarted) {
    api.startEvents();
    api.subscribe(handleEvent);
    eventsStarted = true;
  }
  state = await api.state();
  activity = state.events || [];
  pendingConfirmations = [...(state.confirmations || [])];
  messages = [{ id: uid(), role: "assistant", content: "Aether is online. Tell me what outcome you want, and I’ll show every tool step that gets us there.", timestamp: new Date() }];
  renderShell();
  renderStateChrome();
  startTimers();
  if (!onlineHeartbeatBound) {
    onlineHeartbeatBound = true;
    window.addEventListener("online", () => {
      void heartbeatLicense();
    });
  }
}

async function removeLicense(): Promise<void> {
  if (licenseBusy) return;
  licenseBusy = true;
  const button = document.querySelector<HTMLButtonElement>("#remove-license");
  if (button) button.disabled = true;
  try {
    const status = await api.deactivateLicense();
    currentLicense = status;
    clearTimers();
    eventsStarted = false;
    activity = [];
    pendingConfirmations = [];
    messages = [];
    api.close();
    renderLicenseGate(status, status.message || "License removed. This key can now be activated on another PC.");
    toast(status.message || "License removed.", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : String(error), "error");
  } finally {
    licenseBusy = false;
    if (button) button.disabled = false;
  }
}

async function sendMessage(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (chatBusy || !state.privacy.consent_completed) return;
  const input = document.querySelector<HTMLInputElement>("#message-input");
  const message = input?.value.trim() || "";
  if (!message) return;
  input!.value = "";
  chatBusy = true;
  messages.push({ id: uid(), role: "user", content: message, timestamp: new Date() });
  const pendingId = uid();
  messages.push({ id: pendingId, role: "assistant", content: "", pending: true, timestamp: new Date() });
  renderWorkspace();
  updateComposer();
  try {
    const result = await api.chat(message);
    messages = messages.filter((item) => item.id !== pendingId);
    messages.push({ id: uid(), role: "assistant", content: result.response || "Done.", timestamp: new Date() });
    announce("Aether replied.");
  } catch (error) {
    messages = messages.filter((item) => item.id !== pendingId);
    messages.push({ id: uid(), role: "assistant", content: error instanceof Error ? error.message : "The request failed.", timestamp: new Date() });
  } finally {
    chatBusy = false;
    renderWorkspace();
    updateComposer();
    input?.focus();
  }
}

async function listenForVoice(): Promise<void> {
  if (!state.privacy.voice_recording) { toast("Enable Voice recording in Privacy first.", "error"); navigate("settings"); return; }
  const result = await safeTool("listen_for_voice_command", { seconds: 6 });
  if (result?.ok) {
    const input = document.querySelector<HTMLInputElement>("#message-input");
    if (input) { input.value = result.output; input.focus(); }
  }
}

async function toggleSpeech(): Promise<void> {
  const next = !state.voice.speak_responses;
  try { await api.setting("voice.speak_responses", next); state.voice.speak_responses = next; renderStateChrome(); toast(`Spoken replies ${next ? "enabled" : "disabled"}.`, "success"); } catch (error) { toast(String(error), "error"); }
}

async function safeTool(name: string, arguments_: Record<string, unknown> = {}): Promise<ToolResult | null> {
  try { return await api.tool(name, arguments_); } catch (error) { toast(error instanceof Error ? error.message : String(error), "error"); return null; }
}

async function runContextTool(name: string, arguments_: Record<string, unknown>): Promise<void> {
  const result = await safeTool(name, arguments_);
  if (!result) return;
  messages.push({ id: uid(), role: "assistant", content: result.output, timestamp: new Date() });
  navigate("command");
}

async function loadHeadlines(): Promise<void> {
  const result = await safeTool("get_headlines", { limit: 7 });
  const items = result?.data?.headlines;
  if (Array.isArray(items)) headlines = items as Array<{ title: string; link?: string }>;
  renderContextDock();
}

async function generateVisual(event: Event): Promise<void> {
  event.preventDefault();
  const prompt = document.querySelector<HTMLTextAreaElement>("#visual-prompt")?.value.trim();
  if (!prompt) return;
  const result = await safeTool("create_visual_diagram", { prompt, kind: visualType });
  if (result?.ok) { visualSource = String(result.data?.source || result.output); renderWorkspace(); }
}

async function renderMermaid(): Promise<void> {
  const host = document.querySelector<HTMLDivElement>("#mermaid-output");
  if (!host || !visualSource) return;
  try {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict", themeVariables: { primaryColor: "#1a2230", primaryTextColor: "#eef4f8", primaryBorderColor: "#4fd1c5", lineColor: "#9b7ede", secondaryColor: "#202b3a", tertiaryColor: "#151b24" } });
    const rendered = await mermaid.render(`aether-visual-${Date.now()}`, visualSource);
    host.innerHTML = rendered.svg;
  } catch (error) { host.textContent = `Diagram syntax error: ${error instanceof Error ? error.message : error}`; }
}

async function loadMemories(): Promise<void> {
  const result = await safeTool("list_memories", { limit: 100 });
  memoryRecords = Array.isArray(result?.data?.records) ? result!.data!.records as Array<Record<string, unknown>> : [];
  if (activeView === "memory") renderWorkspace();
}

async function summarizeMemories(): Promise<void> {
  const result = await safeTool("summarize_memories");
  if (result?.ok) { toast(result.output, "success"); await loadMemories(); }
}

async function exportMemory(event: Event): Promise<void> {
  event.preventDefault();
  const path = document.querySelector<HTMLInputElement>("#memory-export-path")?.value;
  const result = path ? await safeTool("export_memories_json", { path }) : null;
  if (result) toast(result.output, result.ok ? "success" : "error");
}

async function importMemory(event: Event): Promise<void> {
  event.preventDefault();
  const path = document.querySelector<HTMLInputElement>("#memory-import-path")?.value;
  const result = path ? await safeTool("import_memories_json", { path }) : null;
  if (result) { toast(result.output, result.ok ? "success" : "error"); await loadMemories(); }
}

async function loadProductivity(): Promise<void> {
  const [noteResult, taskResult] = await Promise.all([safeTool("list_notes"), safeTool("list_tasks")]);
  notes = Array.isArray(noteResult?.data?.notes) ? noteResult!.data!.notes as Array<Record<string, unknown>> : [];
  tasks = Array.isArray(taskResult?.data?.tasks) ? taskResult!.data!.tasks as Array<Record<string, unknown>> : [];
  if (activeView === "productivity") renderWorkspace();
}

async function createNote(event: Event): Promise<void> {
  event.preventDefault();
  const title = document.querySelector<HTMLInputElement>("#note-title")?.value || "";
  const content = document.querySelector<HTMLTextAreaElement>("#note-content")?.value || "";
  const result = await safeTool("create_note", { title, content });
  if (result?.ok) await loadProductivity();
}

async function createTask(event: Event): Promise<void> {
  event.preventDefault();
  const title = document.querySelector<HTMLInputElement>("#task-title")?.value || "";
  const due = document.querySelector<HTMLInputElement>("#task-due")?.value || null;
  const result = await safeTool("create_task", { title, due });
  if (result?.ok) await loadProductivity();
}

async function completeTask(id: string): Promise<void> {
  const result = await safeTool("complete_task", { id });
  if (result?.ok) await loadProductivity();
}

async function togglePlugin(toggle: HTMLInputElement): Promise<void> {
  const name = toggle.dataset.pluginToggle || "";
  try { await api.togglePlugin(name, toggle.checked); state = await api.state(); renderWorkspace(); renderStateChrome(); } catch (error) { toggle.checked = !toggle.checked; toast(error instanceof Error ? error.message : String(error), "error"); }
}

async function saveModel(event: Event): Promise<void> {
  event.preventDefault();
  const payload = {
    display_name: document.querySelector<HTMLInputElement>("#model-name")?.value,
    kind: document.querySelector<HTMLSelectElement>("#model-kind")?.value,
    model_id: document.querySelector<HTMLInputElement>("#model-id")?.value,
    endpoint_url: document.querySelector<HTMLInputElement>("#model-endpoint")?.value || null,
    api_key: document.querySelector<HTMLInputElement>("#model-key")?.value || null,
    is_default: document.querySelector<HTMLInputElement>("#model-default")?.checked || false,
  };
  try {
    if (editingModelId) await api.updateModel(editingModelId, payload); else await api.addModel(payload);
    const action = editingModelId ? "updated" : "added";
    editingModelId = null;
    state = await api.state(); renderWorkspace(); toast(`Model ${action} and runtime rebuilt.`, "success");
  } catch (error) { toast(error instanceof Error ? error.message : String(error), "error"); }
}

async function discoverOllama(): Promise<void> {
  try {
    const result = await api.discoverOllama();
    const list = document.querySelector<HTMLDataListElement>("#ollama-models");
    if (list) list.innerHTML = result.models.map((item) => `<option value="${escapeHtml(item.name)}"></option>`).join("");
    toast(`Found ${result.models.length} local Ollama model(s).`, "success");
  } catch (error) { toast(error instanceof Error ? error.message : String(error), "error"); }
}

async function saveVisionModel(event: Event): Promise<void> {
  event.preventDefault();
  const model = document.querySelector<HTMLInputElement>("#vision-model")?.value.trim();
  if (!model) return;
  try {
    await api.setting("vision.local_model", model);
    state = await api.state(); renderWorkspace(); toast("Local vision model updated.", "success");
  } catch (error) { toast(String(error), "error"); }
}

async function setDefaultModel(id: string): Promise<void> {
  try { await api.setDefaultModel(id); state = await api.state(); renderWorkspace(); toast("Default model switched.", "success"); } catch (error) { toast(String(error), "error"); }
}

async function removeModel(id: string): Promise<void> {
  try { await api.removeModel(id); if (editingModelId === id) editingModelId = null; state = await api.state(); renderWorkspace(); toast("Model entry removed; history was preserved.", "success"); } catch (error) { toast(String(error), "error"); }
}

async function savePersona(event: Event): Promise<void> {
  event.preventDefault();
  const values: Array<[string, unknown]> = [
    ["persona.assistant_name", document.querySelector<HTMLInputElement>("#persona-assistant")?.value || "Aether"],
    ["persona.user_name", document.querySelector<HTMLInputElement>("#persona-user")?.value || ""],
    ["persona.style", document.querySelector<HTMLInputElement>("#persona-style")?.value || "warm, concise, transparent"],
    ["persona.languages", (document.querySelector<HTMLInputElement>("#persona-languages")?.value || "English").split(",").map((item) => item.trim()).filter(Boolean)],
  ];
  try { await Promise.all(values.map(([path, value]) => api.setting(path, value))); state = await api.state(); toast("Aether persona updated.", "success"); } catch (error) { toast(String(error), "error"); }
}

async function savePrivacySettings(): Promise<void> {
  const choices: Record<string, boolean> = {};
  document.querySelectorAll<HTMLInputElement>(".privacy-setting").forEach((toggle) => { choices[toggle.dataset.privacyKey || ""] = toggle.checked; });
  try { await api.privacy(choices); state = await api.state(); renderStateChrome(); renderWorkspace(); toast("Privacy choices saved.", "success"); } catch (error) { toast(String(error), "error"); }
}

async function previewVoice(id: string): Promise<void> { try { await api.previewVoice(id); toast("Voice preview started.", "success"); } catch (error) { toast(String(error), "error"); } }
async function selectVoice(id: string): Promise<void> { try { await api.selectVoice(id); state = await api.state(); renderWorkspace(); toast("Voice selected.", "success"); } catch (error) { toast(String(error), "error"); } }

async function saveVoiceTriggers(event: Event): Promise<void> {
  event.preventDefault();
  const hotkey = document.querySelector<HTMLInputElement>("#voice-hotkey")?.value.trim() || "ctrl+alt+space";
  const modelPath = document.querySelector<HTMLInputElement>("#wakeword-model")?.value.trim() || null;
  try {
    await api.setting("voice.global_hotkey", hotkey);
    await api.setting("voice.wakeword_model_path", modelPath);
    state = await api.state(); renderWorkspace(); toast("Voice triggers updated.", "success");
  } catch (error) { toast(String(error), "error"); }
}

async function saveElevenLabs(event: Event): Promise<void> {
  event.preventDefault();
  const voiceId = document.querySelector<HTMLInputElement>("#elevenlabs-voice-id")?.value.trim() || "";
  const apiKey = document.querySelector<HTMLInputElement>("#elevenlabs-key")?.value || null;
  if (!voiceId) { toast("Enter an ElevenLabs voice ID.", "error"); return; }
  try {
    await api.configureElevenLabs(voiceId, apiKey);
    state = await api.state(); renderWorkspace(); toast("Cloud voice stored securely.", "success");
  } catch (error) { toast(String(error), "error"); }
}

async function changeAccessibility(): Promise<void> {
  const high = document.querySelector<HTMLInputElement>("#high-contrast")?.checked || false;
  const reduced = document.querySelector<HTMLInputElement>("#reduce-motion")?.checked || false;
  document.body.classList.toggle("high-contrast", high);
  document.body.classList.toggle("reduced-motion", reduced);
  await Promise.all([api.setting("ui.high_contrast", high), api.setting("ui.reduced_motion", reduced)]).catch((error) => toast(String(error), "error"));
  state.ui.high_contrast = high; state.ui.reduced_motion = reduced;
}

async function changeTier(): Promise<void> {
  const value = document.querySelector<HTMLSelectElement>("#tier-override")?.value || null;
  try { await api.setting("performance.tier_override", value); state = await api.state(); renderStateChrome(); toast(`Tier set to ${value || "auto"}.`, "success"); } catch (error) { toast(String(error), "error"); }
}

async function exportDiagnostics(): Promise<void> { try { const result = await api.exportDiagnostics(); toast(`Diagnostics saved locally: ${result.path}`, "success"); } catch (error) { toast(String(error), "error"); } }

async function checkUpdates(): Promise<void> {
  try {
    const { check } = await import("@tauri-apps/plugin-updater");
    const update = await check();
    if (!update) { toast("Aether is up to date.", "success"); return; }
    const approved = window.confirm(`Aether ${update.version} is available. Download and install it now?`);
    if (approved) await update.downloadAndInstall();
  } catch (error) { toast(`Update check unavailable: ${error instanceof Error ? error.message : error}`, "error"); }
}

function renderPrivacyConsent(): void {
  const host = document.querySelector<HTMLDivElement>("#privacy-host");
  if (!host) return;
  const options = [
    ["voice_recording", "mic", "Voice recording", "Wake word, hotkey and manual mic input. Audio stays local with offline STT."],
    ["camera_vision", "camera", "Camera Vision", "One in-memory frame per separately confirmed request. Never saved by default."],
    ["screen_vision", "screen", "Screen Vision", "Separate from camera. Every screen capture requires confirmation."],
    ["browsing_history", "visual", "Browsing history", "Optional contextual access. Normal browser automation does not require history."],
    ["memory_storage", "memory", "Memory storage", "Local vector memory and conversation backups under your control."],
    ["telemetry", "tasks", "Anonymous diagnostics", "Off by default. Bundles are generated manually; no silent upload."],
  ];
  host.innerHTML = `<div class="privacy-overlay" role="dialog" aria-modal="true" aria-labelledby="privacy-title"><section class="privacy-card"><header class="privacy-head"><span class="privacy-brand">${icon("shield")} Privacy before intelligence</span><h1 id="privacy-title">You decide what Aether can perceive.</h1><p>Every optional category starts off. Camera and screen are deliberately separate. You can change these choices later, and essential typed chat never needs telemetry.</p></header><form id="privacy-form"><div class="privacy-grid">${options.map(([key, iconName, title, copy]) => `<label class="privacy-option"><span class="privacy-icon">${icon(iconName)}</span><span class="privacy-copy"><strong>${title}</strong><span>${copy}</span></span><input class="toggle" type="checkbox" name="${key}" /></label>`).join("")}</div><footer class="privacy-foot"><span class="essential-note"><b>● Essential only</b> · typed chat and local safety controls</span><button class="primary-button">Continue with these choices</button></footer></form></section></div>`;
  host.querySelector<HTMLFormElement>("#privacy-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const choices = Object.fromEntries(options.map(([key]) => [key, form.has(key)]));
    try { await api.privacy(choices); state = await api.state(); host.innerHTML = ""; renderStateChrome(); renderContextDock(); renderWorkspace(); toast("Privacy choices saved locally.", "success"); document.querySelector<HTMLInputElement>("#message-input")?.focus(); } catch (error) { toast(String(error), "error"); }
  });
  (host.querySelector("input") as HTMLInputElement | null)?.focus();
}

function renderConfirmation(): void {
  const host = document.querySelector<HTMLDivElement>("#modal-host");
  if (!host) return;
  const confirmation = pendingConfirmations[0];
  if (!confirmation) { host.innerHTML = ""; return; }
  host.innerHTML = `<div class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><section class="modal-card"><div class="modal-icon">${icon("warning")}</div><h2 id="confirm-title">Confirmation needed</h2><p><strong>${escapeHtml(confirmation.tool)}</strong> — ${escapeHtml(confirmation.description)}</p><pre class="argument-preview">${escapeHtml(JSON.stringify(confirmation.arguments, null, 2))}</pre><div class="modal-actions"><button class="soft-button" id="confirm-decline">Decline</button><button class="danger-button" id="confirm-approve">Approve once</button></div></section></div>`;
  const resolve = async (approved: boolean) => {
    try { await api.confirmation(confirmation.id, approved); } catch (error) { toast(String(error), "error"); }
    pendingConfirmations = pendingConfirmations.filter((item) => item.id !== confirmation.id);
    renderConfirmation();
  };
  host.querySelector("#confirm-decline")?.addEventListener("click", () => resolve(false));
  host.querySelector("#confirm-approve")?.addEventListener("click", () => resolve(true));
  (host.querySelector("#confirm-decline") as HTMLButtonElement | null)?.focus();
}

function handleEvent(event: AgentEvent): void {
  if (event.type === "snapshot") {
    state = event.data as unknown as AetherState;
    pendingConfirmations = [...(state.confirmations || [])];
    renderStateChrome(); renderConfirmation();
    return;
  }
  activity.push(event);
  if (activity.length > 120) activity = activity.slice(-120);
  if (event.type === "subagent_status") {
    const updated = event.data as unknown as SubAgent;
    const index = state.subagents.findIndex((agent) => agent.id === updated.id);
    if (index >= 0) state.subagents[index] = updated; else state.subagents.push(updated);
    if (activeView === "command" || activeView === "agents") renderWorkspace();
  }
  if (event.type === "confirmation_required") {
    pendingConfirmations.push(event.data as unknown as PendingConfirmation);
    renderConfirmation(); announce(`Confirmation required for ${String(event.data.tool)}.`);
  }
  if (event.type === "provider_state") toast(`Model provider: ${String(event.data.state)}. ${String(event.data.reason || "")}`, "error");
  if (event.type === "tier_changed") toast(`Performance adapted: ${String(event.data.previous)} → ${String(event.data.current)}.`, "info");
  if (event.type === "memory_summary_due") toast("Memory maintenance is due. Open Memory and choose Summarize when ready.", "info");
  if (event.type === "voice_trigger") {
    announce(`Voice activated by ${String(event.data.source || "trigger")}.`);
    void listenForVoice();
  }
  renderInsightDock();
  renderStateChrome();
}

function renderStateChrome(): void {
  const shell = document.querySelector(".aether-shell");
  if (!shell) return;
  shell.className = `aether-shell tier-${state.tier}`;
  const setText = (selector: string, value: string) => { const element = document.querySelector(selector); if (element) element.textContent = value; };
  const setWidth = (selector: string, value: number) => { const element = document.querySelector<HTMLElement>(selector); if (element) element.style.width = `${Math.min(Math.max(value, 0), 100)}%`; };
  setWidth("#cpu-fill", state.resources.cpu_percent); setWidth("#ram-fill", state.resources.ram_percent);
  setText("#cpu-label", `${Math.round(state.resources.cpu_percent)}%`); setText("#ram-label", `${Math.round(state.resources.ram_percent)}%`);
  setText("#agent-badge", String(activeAgentCount()));
  const tier = document.querySelector("#tier-pill strong"); if (tier) tier.textContent = state.tier;
  const cost = document.querySelector("#cost-pill"); if (cost) cost.textContent = state.models.last_used.includes("Local") || !state.models.last_used ? `local · $${state.usage.estimated_cost_usd.toFixed(3)}` : `session ≈ $${state.usage.estimated_cost_usd.toFixed(3)}`;
  const speech = document.querySelector<HTMLButtonElement>('[data-action="toggle-speech"]');
  speech?.classList.toggle("active", state.voice.speak_responses);
  speech?.setAttribute("aria-pressed", String(state.voice.speak_responses));
  document.body.classList.toggle("high-contrast", state.ui.high_contrast);
  document.body.classList.toggle("reduced-motion", state.ui.reduced_motion || !state.tier_config.ui_animations_enabled);
  updateComposer();
  renderContextDock();
}

function updateComposer(): void {
  const send = document.querySelector<HTMLButtonElement>("#send-button"); if (send) send.disabled = chatBusy;
  const mic = document.querySelector("#mic-button"); mic?.classList.toggle("listening", state.voice.is_listening);
}

function scrollChat(): void {
  window.requestAnimationFrame(() => { const view = document.querySelector("#workspace-view"); if (view) view.scrollTop = view.scrollHeight; });
}

async function refreshState(): Promise<void> {
  try { state = await api.state(); renderStateChrome(); renderInsightDock(); if (activeView === "agents") renderWorkspace(); } catch { /* WebSocket reconnect/status toast handles transient restarts. */ }
}

async function boot(): Promise<void> {
  renderLoading();
  try {
    await api.initialize();
    currentLicense = await api.licenseStatus();
    if (currentLicense.required && !currentLicense.valid) {
      renderLicenseGate(currentLicense);
      return;
    }
    await bootLicensedShell();
  } catch (error) {
    renderLoading(`Backend unavailable: ${error instanceof Error ? error.message : error}`);
  }
}
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); document.querySelector<HTMLInputElement>("#message-input")?.focus(); }
  if (event.key === "Escape" && pendingConfirmations.length) document.querySelector<HTMLButtonElement>("#confirm-decline")?.click();
});
window.addEventListener("beforeunload", () => {
  clearTimers();
  api.close();
});
void boot();
