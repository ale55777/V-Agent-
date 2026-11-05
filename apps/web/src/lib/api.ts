import { EchoApi } from "@echoai/api-client";

const TOKEN_KEY = "echoai.token";
const DEVICE_KEY = "echoai.device";
const ACTIVE_KEY = "echoai.activeSession";

function store(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export const getToken = () => store()?.getItem(TOKEN_KEY) ?? null;
export const setToken = (token: string | null) =>
  token ? store()?.setItem(TOKEN_KEY, token) : store()?.removeItem(TOKEN_KEY);

export const getActiveSession = () => store()?.getItem(ACTIVE_KEY) ?? null;
export const setActiveSession = (id: string | null) =>
  id ? store()?.setItem(ACTIVE_KEY, id) : store()?.removeItem(ACTIVE_KEY);

/** A stable id for this browser, used for one-device-at-a-time session ownership. */
export function deviceId(): string {
  let id = store()?.getItem(DEVICE_KEY);
  if (!id) {
    // crypto.randomUUID only exists on secure pages, and LAN http pages are not secure.
    id = `web-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)}`;
    store()?.setItem(DEVICE_KEY, id);
  }
  return id;
}

/**
 * Finds the API from the address this page came from, so no host is hardcoded and LAN
 * devices work. Port 3000 is the web server, whose API is on 8000; anywhere else (such as
 * the backend serving the UI itself over HTTPS) the API is on the same origin.
 */
export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window === "undefined") return "";
  const { protocol, hostname, port, origin } = window.location;
  return port === "3000" ? `${protocol}//${hostname}:8000` : origin;
}

let client: EchoApi | null = null;
export function api(): EchoApi {
  client ??= new EchoApi(apiBase(), getToken);
  return client;
}

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

export async function downloadExport(sessionId: string, format: "txt" | "json" | "pdf") {
  const response = await api().exportFile(sessionId, format);
  const blob = await response.blob();
  const name = response.headers.get("Content-Disposition")?.match(/filename="(.+)"/)?.[1] ?? `conversation.${format}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
