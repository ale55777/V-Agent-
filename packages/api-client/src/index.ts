/** Platform-neutral REST client, used by web and (later) Expo. It only needs `fetch`. */
import type {
  AgentSettings, Health, Message, SessionReport, SessionSummary, Voice,
} from "@echoai/shared-types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export interface AuthResult { access_token: string; user_id: string; email: string }
export interface ModelsInfo {
  llm: { name: string; size: number | null; family: string | null }[];
  llm_error: string | null;
  default_llm: string;
  tts: { active: string | null; engines: string[] };
  groq_available: boolean;
  groq_models: { stt: string; llm: string } | null;
}

export class EchoApi {
  constructor(public baseUrl: string, public getToken: () => string | null) {}

  get wsUrl(): string {
    return this.baseUrl.replace(/^http/, "ws") + "/ws/v1/voice";
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.raw(path, init);
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const token = this.getToken();
    const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
    if (init.body) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${this.baseUrl}/api/v1${path}`, { ...init, headers });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = body?.detail;
      const text = typeof detail === "string" ? detail : detail?.[0]?.msg ?? response.statusText;
      throw new ApiError(response.status, text);
    }
    return response;
  }

  private send<T>(method: string, path: string, body?: unknown) {
    return this.request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  }

  health = () => this.request<Health>("/health");
  register = (email: string, password: string) => this.send<AuthResult>("POST", "/auth/register", { email, password });
  login = (email: string, password: string) => this.send<AuthResult>("POST", "/auth/login", { email, password });
  me = () => this.request<{ id: string; email: string }>("/auth/me");
  models = () => this.request<ModelsInfo>("/models");
  voices = () => this.request<{ voices: Voice[] }>("/voices");
  settings = () => this.request<AgentSettings>("/agent/settings");
  saveSettings = (settings: AgentSettings) => this.send<AgentSettings>("PUT", "/agent/settings", settings);
  createSession = () => this.send<{ id: string }>("POST", "/sessions", {});
  sessions = (q?: string) =>
    this.request<SessionSummary[]>(`/sessions${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  session = (id: string) => this.request<SessionSummary & { messages: Message[] }>(`/sessions/${id}`);
  rename = (id: string, title: string) => this.send("PATCH", `/sessions/${id}`, { title });
  remove = (id: string) => this.send("DELETE", `/sessions/${id}`);
  handoff = (id: string, device_id: string) => this.send("POST", `/sessions/${id}/handoff`, { device_id });
  report = (id: string) => this.request<SessionReport>(`/sessions/${id}/report`);
  exportFile = (id: string, format: "txt" | "json" | "pdf") => this.raw(`/sessions/${id}/export/${format}`);
}
