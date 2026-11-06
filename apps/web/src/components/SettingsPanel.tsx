"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { ATTENTION_MODES, LANGUAGES, VOICE_COMMANDS } from "@echoai/agent-config";
import type { AgentSettings, ClientMessage, Voice } from "@echoai/shared-types";
import type { ModelsInfo } from "@echoai/api-client";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

interface Props {
  onClose: () => void;
  live: boolean;
  send: (message: ClientMessage) => void;
  onMicrophone: (deviceId: string) => void;
  onVolume: (volume: number) => void;
}

function Field({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-300">{label}</span>
      {children}
      {help && <span className="block text-[11px] leading-snug text-slate-500">{help}</span>}
    </label>
  );
}

export function SettingsPanel({ onClose, live, send, onMicrophone, onVolume }: Props) {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([]);
  const [microphone, setMicrophone] = useState("");
  const [volume, setVolume] = useState(1);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api().settings().then(setSettings).catch((e) => setMessage(e.message));
    api().voices().then((v) => setVoices(v.voices)).catch(() => undefined);
    api().models().then(setModels).catch(() => undefined);
    navigator.mediaDevices?.enumerateDevices().then((devices) =>
      setMicrophones(devices.filter((d) => d.kind === "audioinput")),
    );
  }, []);

  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const update = async (changes: Partial<AgentSettings>) => {
    if (!settings) return;
    const next = { ...settings, ...changes };
    setSettings(next);
    try {
      await api().saveSettings(next);
      setMessage(null);
      if (live) {
        send({
          type: "session.configure",
          language: changes.language,
          voice: changes.selected_voice ?? undefined,
          llm_model: changes.selected_llm ?? undefined,
          personality: changes.personality,
          attention_mode: changes.attention_mode,
          wake_word: changes.wake_word,
          ai_engine: changes.ai_engine,
        });
      }
    } catch (error) {
      setMessage((error as Error).message);
    }
  };

  const defaultEngine = models?.tts.active?.split("/")[0]?.replace("-82m", "");

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/50" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label="Settings"
        className="scroll-thin glass fixed inset-y-0 right-0 z-40 w-full max-w-sm space-y-5 overflow-y-auto bg-ink-900/95 p-5"
      >
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Settings</h2>
          <Button variant="ghost" size="icon" aria-label="Close settings" onClick={onClose}>
            <X size={18} aria-hidden />
          </Button>
        </div>

        {message && <p className="rounded-lg bg-halt/10 p-2 text-xs text-halt">{message}</p>}

        <Field label="Microphone">
          <select
            className="field"
            value={microphone}
            onChange={(e) => {
              setMicrophone(e.target.value);
              onMicrophone(e.target.value);
            }}
          >
            <option value="">System default</option>
            {microphones.map((m, index) => (
              <option key={m.deviceId || index} value={m.deviceId}>
                {m.label || `Microphone ${index + 1}`}
              </option>
            ))}
          </select>
        </Field>

        <Field label={`Volume: ${Math.round(volume * 100)}%`}>
          <input
            type="range" min={0} max={1} step={0.05} value={volume} className="w-full accent-accent"
            onChange={(e) => {
              setVolume(Number(e.target.value));
              onVolume(Number(e.target.value));
            }}
          />
        </Field>

        {settings && (
          <>
            <Field
              label="Voice"
              help={`Default engine on this PC: ${defaultEngine ?? "unknown"}. Kokoro sounds better but may be slower than real time on small CPUs, which causes gaps.`}
            >
              <select
                className="field"
                value={settings.selected_voice ?? ""}
                onChange={(e) => {
                  const voice = voices.find((v) => v.id === e.target.value);
                  void update({
                    selected_voice: e.target.value || null,
                    selected_tts: (voice?.engine as AgentSettings["selected_tts"]) ?? null,
                  });
                }}
              >
                <option value="">Automatic</option>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Language">
              <select
                className="field"
                value={settings.language}
                onChange={(e) => void update({ language: e.target.value as AgentSettings["language"] })}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="AI engine"
              help={
                !models?.groq_available
                  ? "Groq is off: add ECHOAI_GROQ_API_KEY to .env and restart to enable it."
                  : settings.ai_engine === "groq"
                    ? `Your voice and messages are sent to Groq (${models.groq_models?.stt}, ${models.groq_models?.llm}). Voice output stays on this PC. If Groq fails, the local models answer instead.`
                    : "Everything stays on this PC."
              }
            >
              <select
                className="field"
                value={settings.ai_engine ?? "local"}
                onChange={(e) => void update({ ai_engine: e.target.value as AgentSettings["ai_engine"] })}
              >
                <option value="local">Local (private)</option>
                <option value="groq" disabled={!models?.groq_available}>
                  Groq (fast, online)
                </option>
              </select>
            </Field>

            <Field
              label={settings.ai_engine === "groq" ? "Local AI model (fallback)" : "AI model"}
              help={models?.llm_error ? `Ollama: ${models.llm_error}` : "Models installed in Ollama."}
            >
              <select
                className="field"
                value={settings.selected_llm ?? models?.default_llm ?? ""}
                onChange={(e) => void update({ selected_llm: e.target.value })}
              >
                {models?.llm.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="Attention mode"
              help={ATTENTION_MODES.find((m) => m.value === settings.attention_mode)?.help}
            >
              <select
                className="field"
                value={settings.attention_mode}
                onChange={(e) => void update({ attention_mode: e.target.value as AgentSettings["attention_mode"] })}
              >
                {ATTENTION_MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>

            {settings.attention_mode === "wake_word" && (
              <Field label="Wake word">
                <input
                  className="field"
                  defaultValue={settings.wake_word}
                  onBlur={(e) => e.target.value.trim().length >= 2 && void update({ wake_word: e.target.value.trim() })}
                />
              </Field>
            )}

            <Field
              label={`Microphone sensitivity: ${Math.round(settings.vad_sensitivity * 100)}%`}
              help="Lower this if background noise triggers the assistant. Applies from the next session."
            >
              <input
                type="range" min={0} max={1} step={0.05} value={settings.vad_sensitivity} className="w-full accent-accent"
                onChange={(e) => void update({ vad_sensitivity: Number(e.target.value) })}
              />
            </Field>

            <Field label="Personality and speaking style">
              <textarea
                className="field min-h-20"
                maxLength={1000}
                defaultValue={settings.personality}
                placeholder="For example: calm and concise, with a dry sense of humour."
                onBlur={(e) => void update({ personality: e.target.value })}
              />
            </Field>

            <Field label="Delete conversations older than (days)" help="Leave empty to keep everything.">
              <input
                className="field"
                type="number"
                min={1}
                defaultValue={settings.retention_days ?? ""}
                onBlur={(e) => void update({ retention_days: e.target.value ? Number(e.target.value) : null })}
              />
            </Field>
          </>
        )}

        <div className="rounded-xl bg-white/5 p-3 text-xs leading-relaxed text-slate-400">
          <p className="mb-1 font-medium text-slate-300">Voice commands</p>
          {VOICE_COMMANDS.join(" · ")}
        </div>
      </aside>
    </>
  );
}
