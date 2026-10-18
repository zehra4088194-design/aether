import { invoke } from "@tauri-apps/api/core";
import type { AetherState, AgentEvent, LicenseStatus, ToolResult } from "./types";

interface BackendInfo {
  base_url: string;
  token: string;
}

type EventListener = (event: AgentEvent) => void;

class AetherApi {
  private baseUrl = "http://127.0.0.1:8765";
  private token = import.meta.env.VITE_AETHER_API_TOKEN || "";
  private socket?: WebSocket;
  private listeners = new Set<EventListener>();
  private reconnectTimer?: number;
  private closed = false;

  async initialize(): Promise<void> {
    let backendInfo: BackendInfo | null = null;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        backendInfo = await invoke<BackendInfo>("backend_info");
        break;
      } catch {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
    }
    if (backendInfo) {
      this.baseUrl = backendInfo.base_url;
      this.token = backendInfo.token;
    } else {
      const localToken = window.localStorage.getItem("aether-dev-token");
      if (localToken) this.token = localToken;
    }

    let lastError: unknown;
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${this.baseUrl}/health`);
        if (response.ok) {
          if (!this.token) {
            throw new Error("Backend is ready, but no API token is available.");
          }
          return;
        }
      } catch (error) {
        lastError = error;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw lastError instanceof Error ? lastError : new Error("Aether backend did not start in time.");
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Aether-Token": this.token,
        ...(init.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `Aether API request failed (${response.status}).`);
    }
    return payload as T;
  }

  state(): Promise<AetherState> {
    return this.request("/v1/state");
  }

  licenseStatus(): Promise<LicenseStatus> {
    return this.request("/v1/license/status");
  }

  activateLicense(licenseKey: string): Promise<LicenseStatus> {
    return this.request("/v1/license/activate", {
      method: "POST",
      body: JSON.stringify({ license_key: licenseKey }),
    });
  }

  heartbeatLicense(): Promise<LicenseStatus> {
    return this.request("/v1/license/heartbeat", { method: "POST" });
  }

  deactivateLicense(): Promise<LicenseStatus> {
    return this.request("/v1/license/deactivate", { method: "POST" });
  }

  startEvents(): void {
    this.closed = false;
    this.connectEvents();
  }

  chat(message: string): Promise<{ response: string }> {
    return this.request("/v1/chat", { method: "POST", body: JSON.stringify({ message }) });
  }

  tool(name: string, arguments_: Record<string, unknown> = {}): Promise<ToolResult> {
    return this.request(`/v1/tools/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ arguments: arguments_ }),
    });
  }

  privacy(choices: Record<string, boolean>): Promise<Record<string, boolean>> {
    return this.request("/v1/privacy", { method: "POST", body: JSON.stringify(choices) });
  }

  confirmation(id: string, approved: boolean): Promise<{ resolved: boolean }> {
    return this.request(`/v1/confirmations/${encodeURIComponent(id)}`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    });
  }

  togglePlugin(name: string, enabled: boolean): Promise<{ name: string; enabled: boolean }> {
    return this.request(`/v1/plugins/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  }

  selectVoice(id: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/voices/${encodeURIComponent(id)}/select`, { method: "POST" });
  }

  previewVoice(id: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/voices/${encodeURIComponent(id)}/preview`, { method: "POST" });
  }

  configureElevenLabs(voiceId: string, apiKey: string | null): Promise<Record<string, unknown>> {
    return this.request("/v1/voices/elevenlabs/configure", {
      method: "PUT",
      body: JSON.stringify({ voice_id: voiceId, api_key: apiKey }),
    });
  }

  addModel(model: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/v1/models", { method: "POST", body: JSON.stringify(model) });
  }

  updateModel(id: string, model: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request(`/v1/models/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(model),
    });
  }

  setDefaultModel(id: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/models/${encodeURIComponent(id)}/default`, { method: "POST" });
  }

  removeModel(id: string): Promise<Record<string, unknown>> {
    return this.request(`/v1/models/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  discoverOllama(): Promise<{ models: Array<Record<string, unknown>> }> {
    return this.request("/v1/models/ollama");
  }

  exportDiagnostics(): Promise<{ path: string; uploaded: boolean }> {
    return this.request("/v1/diagnostics/export", { method: "POST" });
  }

  setting(path: string, value: unknown): Promise<{ path: string; value: unknown }> {
    return this.request(`/v1/settings/${path.replaceAll(".", "/")}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private connectEvents(): void {
    if (this.closed) return;
    this.socket?.close();
    const url = this.baseUrl.replace(/^http/, "ws");
    this.socket = new WebSocket(`${url}/v1/events?token=${encodeURIComponent(this.token)}`);
    this.socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as AgentEvent;
        this.listeners.forEach((listener) => listener(event));
      } catch {
        // Ignore malformed local frames; the next state poll repairs the UI.
      }
    };
    this.socket.onclose = () => {
      if (!this.closed) {
        this.reconnectTimer = window.setTimeout(() => this.connectEvents(), 1500);
      }
    };
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
  }
}

export const api = new AetherApi();
