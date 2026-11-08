"use client";

import { useEffect, useRef, useState } from "react";
import { Mic } from "lucide-react";
import type { SessionState } from "@echoai/shared-types";
import type { LiveReply } from "@/hooks/useVoiceSession";
import { cn } from "@/lib/utils";

const ORB_COLORS: Partial<Record<SessionState, [string, string]>> = {
  IDLE: ["#8b7dff", "#1e2238"],
  ENDED: ["#8b7dff", "#1e2238"],
  LISTENING: ["#34d399", "#1f5f6b"],
  PROCESSING: ["#fbbf24", "#6b3fa0"],
  SPEAKING: ["#60a5fa", "#5b4bd6"],
  INTERRUPTED: ["#f87171", "#5b2a4a"],
  PAUSED: ["#64748b", "#1e2238"],
  DISCONNECTED: ["#475569", "#141728"],
  CONNECTING: ["#a79dff", "#2c3150"],
};

const VISUAL_INTERVAL_MS = 80;

function peakOf(analyser: AnalyserNode | null, data: Uint8Array<ArrayBuffer>): number {
  if (!analyser) return 0;
  analyser.getByteTimeDomainData(data);
  let peak = 0;
  for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
  return peak / 128;
}

/**
 * The orb reacts to real audio: whoever is talking drives it (the agent while speaking, you
 * otherwise). Only a CSS variable is written, at about 12 fps, never a React re-render.
 * When idle it is the start button.
 */
export function VoiceOrb({
  state, micAnalyser, aiAnalyser, onStart,
}: {
  state: SessionState;
  micAnalyser: () => AnalyserNode | null;
  aiAnalyser: () => AnalyserNode | null;
  onStart?: () => void;
}) {
  const orb = useRef<HTMLDivElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const data = new Uint8Array(new ArrayBuffer(512));
    let level = 0;
    // A timer at about 12 fps, not requestAnimationFrame: drawing at 60 fps was measured using
    // over 2 CPU threads on an integrated GPU, slowing speech recognition by more than 10x.
    const timer = setInterval(() => {
      if (document.hidden) return;
      const current = stateRef.current;
      const source = current === "SPEAKING" ? aiAnalyser() : current === "PAUSED" ? null : micAnalyser();
      const target = source ? Math.min(1, peakOf(source, data) * 1.6) : 0;
      const next = level + (target - level) * 0.5;
      if (Math.abs(next - level) < 0.004) return;
      level = next;
      orb.current?.style.setProperty("--level", level.toFixed(3));
    }, VISUAL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [micAnalyser, aiAnalyser]);

  const [c1, c2] = ORB_COLORS[state] ?? ORB_COLORS.IDLE!;
  const idle = state === "IDLE" || state === "ENDED";
  const busy = state === "PROCESSING" || state === "CONNECTING";
  const style = { "--c1": c1, "--c2": c2 } as React.CSSProperties;

  const body = (
    <div
      ref={orb}
      className="orb h-60 w-60 sm:h-72 sm:w-72"
      data-idle={idle}
      data-busy={busy}
      style={style}
    >
      <div className="orb-aura" />
      <div className="orb-spin" />
      <div className="orb-ring" style={{ "--k": 0.22 } as React.CSSProperties} />
      <div className="orb-ring" style={{ "--k": 0.4, width: "86%", height: "86%", opacity: 0.18 } as React.CSSProperties} />
      <div className="orb-ring" style={{ "--k": 0.6, width: "98%", height: "98%", opacity: 0.1 } as React.CSSProperties} />
      <div className="orb-core grid place-items-center">
        {idle && <Mic size={34} className="text-white/90 drop-shadow" aria-hidden />}
      </div>
    </div>
  );

  if (idle && onStart) {
    return (
      <button
        type="button"
        onClick={onStart}
        aria-label="Start conversation"
        className="group rounded-full transition-transform hover:scale-[1.03] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent active:scale-95"
      >
        {body}
      </button>
    );
  }
  return <div aria-hidden>{body}</div>;
}

const STATUS: Partial<Record<SessionState, { text: string; dot: string; tone: string }>> = {
  IDLE: { text: "Ready", dot: "bg-accent-soft", tone: "text-slate-200" },
  CONNECTING: { text: "Connecting", dot: "bg-accent-soft animate-pulsedot", tone: "text-accent-soft" },
  LISTENING: { text: "Listening", dot: "bg-listen animate-pulsedot", tone: "text-listen" },
  PROCESSING: { text: "Thinking", dot: "bg-think animate-pulsedot", tone: "text-think" },
  SPEAKING: { text: "Speaking", dot: "bg-speak", tone: "text-speak" },
  INTERRUPTED: { text: "Go ahead, I'm listening", dot: "bg-halt", tone: "text-halt" },
  PAUSED: { text: "Paused", dot: "bg-slate-400", tone: "text-slate-300" },
  DISCONNECTED: { text: "Reconnecting", dot: "bg-halt animate-pulsedot", tone: "text-halt" },
  ENDED: { text: "Conversation ended", dot: "bg-slate-400", tone: "text-slate-300" },
};

export function StatusPill({ state }: { state: SessionState }) {
  const view = STATUS[state] ?? STATUS.IDLE!;
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn("flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium", view.tone)}
    >
      <span className={cn("h-2 w-2 rounded-full", view.dot)} aria-hidden />
      {view.text}
    </p>
  );
}

const SUGGESTIONS = [
  "“Tell me a fun fact about space.”",
  "“What should I cook for dinner tonight?”",
  "“Explain black holes like I'm ten.”",
  "“Give me a quick motivation for today.”",
  "“Quiz me on world capitals.”",
  "“Tell me a short story about a robot.”",
];

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 5 ? "Up late?" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

/** The big line under the orb: what Jarvis is saying, what it heard from you, or a hint. */
export function LiveCaption({
  state, partial, reply, listeningForResume,
}: { state: SessionState; partial: string; reply: LiveReply | null; listeningForResume: boolean }) {
  const [tip, setTip] = useState(0);
  const [hello, setHello] = useState("");
  useEffect(() => setHello(greeting()), []);
  useEffect(() => {
    const timer = setInterval(() => setTip((t) => (t + 1) % SUGGESTIONS.length), 4500);
    return () => clearInterval(timer);
  }, []);

  let main: React.ReactNode;
  let sub: React.ReactNode = null;

  if (state === "IDLE" || state === "ENDED") {
    main = <span className="text-gradient">{hello || "Hello"}</span>;
    sub = (
      <>
        Tap the orb and just talk. Try{" "}
        <span key={tip} className="inline-block animate-fade text-slate-300">{SUGGESTIONS[tip]}</span>
      </>
    );
  } else if (state === "PAUSED") {
    main = "Taking a break";
    sub = listeningForResume ? "Say “continue” or press Resume when you're ready." : "The microphone is ignored until you press Resume.";
  } else if (state === "SPEAKING" && reply) {
    // Show the tail of the reply: the words already spoken bright, the upcoming ones dim.
    const spoken = reply.generated.slice(0, reply.spokenChars);
    const start = Math.max(0, spoken.length - 150);
    const shownSpoken = (start > 0 ? "…" : "") + spoken.slice(start);
    const upcoming = reply.generated.slice(reply.spokenChars, reply.spokenChars + 60);
    main = (
      <>
        <span className="text-slate-50">{shownSpoken}</span>
        <span className="text-slate-500">{upcoming}</span>
      </>
    );
    sub = "Talk over me any time to interrupt.";
  } else if (partial) {
    main = <span className="caret text-slate-100">{partial}</span>;
    sub = "Hearing you…";
  } else if (state === "PROCESSING") {
    main = <span className="text-slate-300">Thinking…</span>;
  } else if (state === "LISTENING" || state === "INTERRUPTED") {
    main = <span className="text-slate-300">I'm listening</span>;
    sub = "Speak naturally. Say “pause”, “repeat that” or “end conversation” any time.";
  } else if (state === "CONNECTING") {
    main = <span className="text-slate-300">Connecting…</span>;
  } else {
    main = <span className="text-slate-400">Connection lost. Trying again…</span>;
  }

  return (
    <div className="flex min-h-[6.5rem] w-full max-w-xl flex-col items-center justify-start gap-2 px-2 text-center">
      <p className="line-clamp-3 text-lg font-medium leading-snug sm:text-xl">{main}</p>
      {sub && <p className="text-xs text-slate-500 sm:text-sm">{sub}</p>}
    </div>
  );
}
