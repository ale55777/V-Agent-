"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Mic, MicOff, Pause, Play, PhoneOff, RotateCcw, Smartphone } from "lucide-react";
import { Header, useRequireAuth } from "@/components/Header";
import { ConversationPanel } from "@/components/ConversationPanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { StateBadge, VoiceOrb, Waveform } from "@/components/VoiceVisuals";
import { Button } from "@/components/ui/button";
import { useVoiceSession } from "@/hooks/useVoiceSession";
import { formatClock, formatMs, getActiveSession } from "@/lib/api";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[11px] text-slate-500">{label}</dt>
      <dd className="truncate text-sm font-medium tabular-nums text-slate-100" title={value}>{value}</dd>
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

  if (!ready) return null;

  return (
    <div className="flex h-dvh flex-col">
      <Header connected={live ? voice.connected : undefined} onSettings={() => setSettingsOpen(true)} />

      <main className="mx-auto grid min-h-0 w-full max-w-6xl flex-1 gap-4 overflow-y-auto p-4 lg:grid-cols-[1fr_1.05fr] lg:overflow-hidden">
        <section className="glass flex min-h-0 flex-col items-center gap-3 rounded-2xl p-4">
          <div className="flex w-full items-center justify-between">
            <StateBadge state={state} listeningForResume={voice.listeningForResume} />
            <span className="font-mono text-lg tabular-nums text-slate-200" aria-label="Session time">
              {formatClock(total)}
            </span>
          </div>

          <div className="flex flex-1 items-center justify-center">
            <VoiceOrb state={state} micAnalyser={voice.micAnalyser} aiAnalyser={voice.aiAnalyser} />
          </div>

          <div className="flex w-full gap-4">
            <Waveform analyser={voice.micAnalyser} color="#34d399" label="Your microphone" />
            <Waveform analyser={voice.aiAnalyser} color="#60a5fa" label="EchoAI" />
          </div>

          {voice.error && (
            <p role="alert" className="w-full rounded-xl bg-halt/10 p-3 text-sm text-halt">{voice.error}</p>
          )}
          {voice.notice && <p className="w-full rounded-xl bg-think/10 p-3 text-sm text-think">{voice.notice}</p>}

          {voice.blocker && (
            <div className="flex w-full flex-wrap items-center justify-between gap-2 rounded-xl bg-white/5 p-3 text-sm">
              <span className="flex items-center gap-2"><Smartphone size={16} aria-hidden /> {voice.blocker.message}</span>
              {voice.blocker.kind === "conflict" && (
                <Button size="sm" variant="primary" onClick={() => void voice.takeOver()}>Move it here</Button>
              )}
            </div>
          )}

          <div className="flex w-full flex-wrap justify-center gap-2">
            {!live ? (
              <>
                <Button variant="primary" size="lg" onClick={() => void voice.start(continueId ?? undefined)}>
                  <Mic size={18} aria-hidden /> {continueId ? "Continue conversation" : "Start conversation"}
                </Button>
                {unfinished && !continueId && (
                  <Button size="lg" onClick={() => void voice.start(unfinished)}>
                    <RotateCcw size={18} aria-hidden /> Resume last session
                  </Button>
                )}
              </>
            ) : (
              <>
                {paused ? (
                  <Button variant="primary" onClick={voice.resume} disabled={!voice.connected}>
                    <Play size={16} aria-hidden /> Resume
                  </Button>
                ) : (
                  <Button onClick={voice.pause} disabled={!voice.connected}>
                    <Pause size={16} aria-hidden /> Pause
                  </Button>
                )}
                <Button onClick={voice.toggleMute} aria-pressed={voice.muted}>
                  {voice.muted ? <MicOff size={16} aria-hidden /> : <Mic size={16} aria-hidden />}
                  {voice.muted ? "Unmute" : "Mute"}
                </Button>
                <Button variant="danger" onClick={voice.end}>
                  <PhoneOff size={16} aria-hidden /> End conversation
                </Button>
              </>
            )}
          </div>
        </section>

        <section className="flex min-h-[28rem] flex-col gap-4 lg:min-h-0">
          <div className="glass flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl">
            <ConversationPanel messages={voice.messages} partial={voice.partial} reply={voice.reply} />
          </div>
          <dl className="glass grid grid-cols-2 gap-x-4 gap-y-2 rounded-2xl p-4 sm:grid-cols-4">
            <Stat label="External API cost" value="$0.00" />
            <Stat label="Last first-audio" value={formatMs(voice.lastTurn?.first_audio_ms)} />
            <Stat label="Average first-audio" value={formatMs(metrics?.average_latency_ms)} />
            <Stat label="Turns · Interruptions" value={`${metrics?.turn_count ?? 0} · ${metrics?.interruption_count ?? 0}`} />
            <Stat label="Last transcription" value={formatMs(voice.lastTurn?.stt_ms)} />
            <Stat label="AI model" value={voice.models?.llm ?? "—"} />
            <Stat label="Speech recognition" value={voice.models?.stt ?? "—"} />
            <Stat label="Text to speech" value={voice.models?.tts ?? "—"} />
          </dl>
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
