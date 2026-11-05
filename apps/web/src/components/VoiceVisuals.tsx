"use client";

import { useEffect, useRef } from "react";
import { Ear, Loader2, Mic, Pause, Volume2, WifiOff } from "lucide-react";
import type { SessionState } from "@echoai/shared-types";

const ORB_COLORS: Partial<Record<SessionState, [string, string]>> = {
  LISTENING: ["#34d399", "#7c6cff"],
  PROCESSING: ["#fbbf24", "#7c6cff"],
  SPEAKING: ["#60a5fa", "#a79dff"],
  INTERRUPTED: ["#f87171", "#7c6cff"],
  PAUSED: ["#64748b", "#334155"],
  DISCONNECTED: ["#475569", "#1e2238"],
  CONNECTING: ["#7c6cff", "#334155"],
};

function peakOf(analyser: AnalyserNode | null, data: Uint8Array<ArrayBuffer>): number {
  if (!analyser) return 0;
  analyser.getByteTimeDomainData(data);
  let peak = 0;
  for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
  return peak / 128;
}

/** The orb reacts to real audio levels, written straight to the DOM to avoid re-rendering at 60 fps. */
export function VoiceOrb({
  state, micAnalyser, aiAnalyser,
}: { state: SessionState; micAnalyser: () => AnalyserNode | null; aiAnalyser: () => AnalyserNode | null }) {
  const orb = useRef<HTMLDivElement>(null);
  const halo = useRef<HTMLDivElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const data = new Uint8Array(new ArrayBuffer(512));
    let frame = 0;
    let level = 0;
    const draw = () => {
      const current = stateRef.current;
      const source = current === "SPEAKING" ? aiAnalyser() : current === "PAUSED" ? null : micAnalyser();
      const target = source ? peakOf(source, data) : 0;
      level += (target - level) * 0.25;
      if (orb.current) orb.current.style.transform = `scale(${1 + level * 0.28})`;
      if (halo.current) {
        halo.current.style.transform = `scale(${1.1 + level * 0.8})`;
        halo.current.style.opacity = String(0.25 + level * 0.6);
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [micAnalyser, aiAnalyser]);

  const [from, to] = ORB_COLORS[state] ?? ["#7c6cff", "#334155"];
  return (
    <div className="relative grid h-52 w-52 place-items-center sm:h-64 sm:w-64" aria-hidden>
      <div
        ref={halo}
        className="absolute inset-6 rounded-full blur-2xl transition-colors duration-500"
        style={{ background: `radial-gradient(circle, ${from}, transparent 70%)` }}
      />
      {state === "PROCESSING" && (
        <div className="absolute inset-3 animate-spinslow rounded-full border-2 border-transparent border-t-think/70" />
      )}
      <div
        ref={orb}
        className="h-36 w-36 rounded-full shadow-2xl sm:h-44 sm:w-44"
        style={{
          background: `radial-gradient(circle at 32% 28%, #ffffffcc, ${from} 34%, ${to} 88%)`,
          transition: "background 400ms",
        }}
      />
    </div>
  );
}

/** A scrolling waveform drawn from a live AnalyserNode. */
export function Waveform({ analyser, color, label }: { analyser: () => AnalyserNode | null; color: string; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return;
    const data = new Uint8Array(new ArrayBuffer(512));
    let frame = 0;
    const draw = () => {
      const { width, height } = element;
      context.clearRect(0, 0, width, height);
      const node = analyser();
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.beginPath();
      if (node) {
        node.getByteTimeDomainData(data);
        for (let i = 0; i < data.length; i++) {
          const x = (i / (data.length - 1)) * width;
          const y = (data[i] / 255) * height;
          if (i) context.lineTo(x, y);
          else context.moveTo(x, y);
        }
      } else {
        context.moveTo(0, height / 2);
        context.lineTo(width, height / 2);
      }
      context.stroke();
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [analyser, color]);

  return (
    <div className="min-w-0 flex-1">
      <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <canvas ref={canvas} width={320} height={44} className="h-11 w-full" aria-hidden />
    </div>
  );
}

export function StateBadge({ state, listeningForResume }: { state: SessionState; listeningForResume: boolean }) {
  const views: Partial<Record<SessionState, { icon: React.ReactNode; text: string; color: string }>> = {
    IDLE: { icon: <Mic size={15} />, text: "Ready when you are", color: "text-slate-300" },
    CONNECTING: { icon: <Loader2 size={15} className="animate-spin" />, text: "Connecting", color: "text-accent-soft" },
    LISTENING: { icon: <Ear size={15} />, text: "Listening", color: "text-listen" },
    PROCESSING: { icon: <Loader2 size={15} className="animate-spin" />, text: "Thinking", color: "text-think" },
    SPEAKING: { icon: <Volume2 size={15} />, text: "Speaking. Talk over me any time.", color: "text-speak" },
    INTERRUPTED: { icon: <Ear size={15} />, text: "Interrupted", color: "text-halt" },
    PAUSED: {
      icon: <Pause size={15} />,
      text: listeningForResume ? "Paused. Say “continue” to resume." : "Paused. Microphone is ignored.",
      color: "text-slate-300",
    },
    DISCONNECTED: { icon: <WifiOff size={15} />, text: "Disconnected. Reconnecting…", color: "text-halt" },
    ENDED: { icon: <Mic size={15} />, text: "Conversation ended", color: "text-slate-300" },
  };
  const view = views[state] ?? views.IDLE!;
  return (
    <p role="status" aria-live="polite" className={`flex items-center gap-2 text-sm font-medium ${view.color}`}>
      {view.icon} {view.text}
    </p>
  );
}
