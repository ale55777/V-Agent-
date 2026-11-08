"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Activity, ChevronDown, Mic, MicOff, Pause, Play, PhoneOff, RotateCcw, Smartphone } from "lucide-react";
import { Header, useRequireAuth } from "@/components/Header";
import { ConversationPanel } from "@/components/ConversationPanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { LiveCaption, StatusPill, VoiceOrb } from "@/components/VoiceVisuals";
import { Button } from "@/components/ui/button";
import { useVoiceSession } from "@/hooks/useVoiceSession";
import { formatClock, formatMs, getActiveSession } from "@/lib/api";
import { cn } from "@/lib/utils";

/** A round call-style control with its label underneath. */
function CallButton({
  label, onClick, disabled, active, tone = "neutral", big, children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  tone?: "neutral" | "danger" | "primary";
  big?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-1.5">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={active}
        aria-label={label}
        className={cn(
          "grid place-items-center rounded-full border transition-all hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          big ? "h-16 w-16" : "h-14 w-14",
          tone === "danger" && "border-halt/40 bg-halt text-white shadow-[0_8px_30px_-8px_rgba(248,113,113,0.7)] hover:bg-red-400",
          tone === "primary" && "border-accent/50 bg-accent text-white shadow-[0_8px_30px_-8px_rgba(124,108,255,0.8)] hover:bg-accent-soft",
          tone === "neutral" && (active ? "border-white/30 bg-white text-ink-950" : "border-white/10 bg-white/[0.06] text-slate-100 hover:bg-white/[0.12]"),
        )}
      >
        {children}
      </button>
      <span className="text-[11px] text-slate-400">{label}</span>
    </div>
  );
}

function latencyTone(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "text-slate-100";
  return ms < 1500 ? "text-listen" : ms < 3000 ? "text-think" : "text-halt";
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-white/[0.03] px-3 py-2">
      <dt className="truncate text-[11px] text-slate-500">{label}</dt>
      <dd className={cn("truncate text-sm font-semibold tabular-nums", tone ?? "text-slate-100")} title={value}>{value}</dd>
    </div>
  );
}

export function VoiceApp() {
  const ready = useRequireAuth();
  const router = useRouter();
  const params = useSearchParams();
  const continueId = params.get("session");
  const onEnded = useCallback((id: string) => router.push(`/report/?id=${id}`), [router]);
  const voice = useVoiceSession(onEnded);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [unfinished, setUnfinished] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const { state, metrics, timerBase } = voice;
  const live = !["IDLE", "ENDED"].includes(state);
  const paused = state === "PAUSED";

  useEffect(() => {
    if (!continueId) setUnfinished(getActiveSession());
  }, [continueId]);

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setTick((t) => t + 1), 500);
    return () => clearInterval(timer);
  }, [live]);

  const running = live && timerBase.at > 0 && voice.connected;
  const elapsed = running ? (performance.now() - timerBase.at) / 1000 : 0;
  const total = timerBase.total + elapsed;
  const start = () => void voice.start(continueId ?? undefined);

  if (!ready) return null;

  return (
    <div className="flex h-dvh flex-col">
      <Header connected={live ? voice.connected : undefined} onSettings={() => setSettingsOpen(true)} />

      <main className="mx-auto grid min-h-0 w-full max-w-6xl flex-1 gap-4 overflow-y-auto p-3 sm:p-4 lg:grid-cols-[1.1fr_1fr] lg:overflow-hidden">
        {/* ---- the call ---- */}
        <section className="glass relative flex min-h-[34rem] flex-col items-center gap-2 overflow-hidden rounded-3xl p-4 sm:p-6 lg:min-h-0">
          <div className="flex w-full items-center justify-between">
            <StatusPill state={state} />
            <span
              className={cn(
                "rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 font-mono text-xs tabular-nums",
                running ? "text-slate-200" : "text-slate-500",
              )}
              aria-label="Session time"
            >
              {formatClock(total)}
            </span>
          </div>

          <div className="flex flex-1 flex-col items-center justify-center gap-5 py-2">
            <VoiceOrb
              state={state}
              micAnalyser={voice.micAnalyser}
              aiAnalyser={voice.aiAnalyser}
              onStart={start}
            />
            <LiveCaption
              state={state}
              partial={voice.partial}
              reply={voice.reply}
              listeningForResume={voice.listeningForResume}
            />
          </div>

          {voice.error && (
            <p role="alert" className="w-full animate-rise rounded-xl border border-halt/20 bg-halt/10 p-3 text-sm text-halt">{voice.error}</p>
          )}
          {voice.notice && (
            <p className="w-full animate-rise rounded-xl border border-think/20 bg-think/10 p-3 text-sm text-think">{voice.notice}</p>
          )}
          {voice.blocker && (
            <div className="flex w-full flex-wrap items-center justify-between gap-2 rounded-xl bg-white/5 p-3 text-sm">
              <span className="flex items-center gap-2"><Smartphone size={16} aria-hidden /> {voice.blocker.message}</span>
              {voice.blocker.kind === "conflict" && (
                <Button size="sm" variant="primary" onClick={() => void voice.takeOver()}>Move it here</Button>
              )}
            </div>
          )}

          <div className="flex w-full flex-wrap items-end justify-center gap-5 pt-2 sm:gap-7">
            {!live ? (
              <>
                <Button variant="primary" size="lg" className="rounded-full px-7 shadow-[0_10px_40px_-10px_rgba(124,108,255,0.9)]" onClick={start}>
                  <Mic size={18} aria-hidden /> {continueId ? "Continue conversation" : "Start talking"}
                </Button>
                {unfinished && !continueId && (
                  <Button size="lg" className="rounded-full" onClick={() => void voice.start(unfinished)}>
                    <RotateCcw size={18} aria-hidden /> Resume last
                  </Button>
                )}
              </>
            ) : (
              <>
                <CallButton label={voice.muted ? "Unmute" : "Mute"} onClick={voice.toggleMute} active={voice.muted}>
                  {voice.muted ? <MicOff size={22} aria-hidden /> : <Mic size={22} aria-hidden />}
                </CallButton>
                {paused ? (
                  <CallButton label="Resume" tone="primary" big onClick={voice.resume} disabled={!voice.connected}>
                    <Play size={26} aria-hidden />
                  </CallButton>
                ) : (
                  <CallButton label="Pause" big onClick={voice.pause} disabled={!voice.connected}>
                    <Pause size={26} aria-hidden />
                  </CallButton>
                )}
                <CallButton label="End" tone="danger" onClick={voice.end}>
                  <PhoneOff size={22} aria-hidden />
                </CallButton>
              </>
            )}
          </div>
        </section>

        {/* ---- transcript and numbers ---- */}
        <section className="flex min-h-[28rem] flex-col gap-4 lg:min-h-0">
          <div className="glass flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl">
            <ConversationPanel messages={voice.messages} partial={voice.partial} reply={voice.reply} />
          </div>

          <details className="glass group rounded-3xl">
            <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 text-sm [&::-webkit-details-marker]:hidden">
              <Activity size={16} className="text-accent-soft" aria-hidden />
              <span className="font-medium">Performance</span>
              <span className="ml-auto flex items-center gap-3 text-xs text-slate-400">
                <span>
                  Response <b className={cn("font-semibold tabular-nums", latencyTone(voice.lastTurn?.first_audio_ms))}>{formatMs(voice.lastTurn?.first_audio_ms)}</b>
                </span>
                <span className="hidden sm:inline">
                  Turns <b className="font-semibold tabular-nums text-slate-200">{metrics?.turn_count ?? 0}</b>
                </span>
                <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
              </span>
            </summary>
            <dl className="grid grid-cols-2 gap-2 px-4 pb-4 sm:grid-cols-4">
              <Stat label="Last response" value={formatMs(voice.lastTurn?.first_audio_ms)} tone={latencyTone(voice.lastTurn?.first_audio_ms)} />
              <Stat label="Average response" value={formatMs(metrics?.average_latency_ms)} tone={latencyTone(metrics?.average_latency_ms)} />
              <Stat label="Turns · Interruptions" value={`${metrics?.turn_count ?? 0} · ${metrics?.interruption_count ?? 0}`} />
              <Stat label="Last transcription" value={formatMs(voice.lastTurn?.stt_ms)} />
              <Stat label="AI model" value={voice.models?.llm ?? "—"} />
              <Stat label="Speech recognition" value={voice.models?.stt ?? "—"} />
              <Stat label="Voice" value={voice.models?.tts ?? "—"} />
              <Stat label="External API cost" value="$0.00" />
            </dl>
          </details>
        </section>
      </main>

      {settingsOpen && (
        <SettingsPanel
          live={voice.connected}
          send={voice.send}
          onClose={() => setSettingsOpen(false)}
          onMicrophone={(id) => void voice.selectMicrophone(id)}
          onVolume={voice.setVolume}
        />
      )}
    </div>
  );
}
