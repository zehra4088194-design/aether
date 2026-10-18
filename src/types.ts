export type Tier = "lite" | "balanced" | "performance";
export type AgentState = "queued" | "running" | "done" | "error";

export interface LicenseStatus {
  required: boolean;
  configured: boolean;
  activated: boolean;
  valid: boolean;
  device_code: string;
  key_masked: string;
  last_verified_at: string | null;
  online_check: boolean | null;
  message: string;
}

export interface PluginState {
  name: string;
  description: string;
  resource_cost: "light" | "medium" | "heavy";
  enabled: boolean;
  tools: string[];
}

export interface VoiceProfile {
  id: string;
  display_name: string;
  engine: "piper" | "elevenlabs";
  voice_ref: string;
  style: string;
  offline: boolean;
  sample_phrase: string;
}

export interface SubAgent {
  id: string;
  label: string;
  state: AgentState;
  detail: string;
  started_at?: number;
  finished_at?: number;
}

export interface AgentEvent {
  id?: string;
  type: string;
  timestamp?: string;
  data: Record<string, unknown>;
}

export interface ModelEntry {
  id: string;
  display_name: string;
  kind: string;
  model_id: string;
  endpoint_url?: string;
  api_key_env_var?: string;
  is_default: boolean;
  notes: string;
}

export interface AetherState {
  app: { name: string; version: string };
  privacy: Record<string, boolean> & { consent_completed: boolean };
  tier: Tier;
  tier_config: {
    tier: Tier;
    ui_animations_enabled: boolean;
    proactive_mode_default_on: boolean;
    vector_memory_max_items: number;
  };
  resources: {
    cpu_percent: number;
    ram_percent: number;
    ram_available_mb: number;
    battery_percent: number | null;
    plugged_in: boolean | null;
  };
  plugins: PluginState[];
  models: {
    default_id: string | null;
    models: ModelEntry[];
    last_used: string;
    providers: Array<Record<string, unknown>>;
  };
  voice: {
    is_listening: boolean;
    is_speaking: boolean;
    speak_responses: boolean;
    global_hotkey: string;
    wakeword_model_path: string | null;
    elevenlabs_voice_id: string;
    active_id: string | null;
    profiles: VoiceProfile[];
  };
  camera_active: boolean;
  vision: { local_model: string };
  subagents: SubAgent[];
  usage: {
    input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  };
  events: AgentEvent[];
  confirmations: Array<{
    id: string;
    tool: string;
    description: string;
    arguments: Record<string, unknown>;
    expires_in_sec: number;
    created_at: string;
  }>;
  ui: { high_contrast: boolean; reduced_motion: boolean; theme: string };
  persona: {
    assistant_name: string;
    user_name: string;
    style: string;
    languages: string[];
  };
}

export interface ToolResult {
  ok: boolean;
  output: string;
  data?: Record<string, unknown>;
}
