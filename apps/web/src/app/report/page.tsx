"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { History, Mic, RotateCcw } from "lucide-react";
import type { SessionReport } from "@echoai/shared-types";
import { Header, useRequireAuth } from "@/components/Header";
import { Bubble } from "@/components/ConversationPanel";
import { ExportButtons } from "@/components/ExportButtons";
import { Button } from "@/components/ui/button";
import { api, formatClock, formatDate, formatMs, formatTime } from "@/lib/api";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-white/5 py-1.5 text-sm last:border-0">
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="glass rounded-2xl p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</h2>
      <dl>{children}</dl>
    </section>
  );
}

function ReportContent() {
  const ready = useRequireAuth();
  const id = useSearchParams().get("id");
  const [report, setReport] = useState<SessionReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && id) api().report(id).then(setReport).catch((e) => setError(e.message));
  }, [ready, id]);

  if (!ready) return null;
  const s = report?.session;
  const p = report?.performance;
  const c = report?.cost;

  return (
    <div className="min-h-dvh">
      <Header />
      <main className="mx-auto w-full max-w-4xl space-y-4 p-4">
        {!id && <p className="text-slate-400">No session selected.</p>}
        {error && <p role="alert" className="text-halt">{error}</p>}
        {s && p && c && report && (
          <>
            <div className="glass space-y-3 rounded-2xl p-5">
              <p className="text-xs uppercase tracking-wider text-accent-soft">Conversation report</p>
              <h1 className="text-xl font-semibold">{s.title}</h1>
              <div className="flex flex-wrap gap-2">
                <Link href={`/?session=${s.id}`}><Button size="sm" variant="primary"><RotateCcw size={15} aria-hidden /> Continue conversation</Button></Link>
                <Link href="/"><Button size="sm"><Mic size={15} aria-hidden /> New session</Button></Link>
                <Link href="/history/"><Button size="sm"><History size={15} aria-hidden /> View history</Button></Link>
                <ExportButtons sessionId={s.id} />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <Card title="Session">
                <Row label="Date" value={formatDate(s.start_time)} />
                <Row label="Start" value={formatTime(s.start_time)} />
                <Row label="End" value={s.end_time ? formatTime(s.end_time) : "—"} />
                <Row label="Total" value={formatClock(s.duration_seconds)} />
                <Row label="Active" value={formatClock(s.active_seconds)} />
                <Row label="Paused" value={formatClock(s.paused_seconds)} />
                <Row label="Your messages" value={s.user_messages} />
                <Row label="AI responses" value={s.ai_responses} />
                <Row label="Interruptions" value={s.interruptions} />
              </Card>
              <Card title="Performance (measured)">
                <Row label="Average first audio" value={formatMs(p.average_response_latency_ms)} />
                <Row label="Fastest response" value={formatMs(p.fastest_response_ms)} />
                <Row label="Slowest response" value={formatMs(p.slowest_response_ms)} />
                <Row label="Transcription" value={formatMs(p.average_transcription_latency_ms)} />
                <Row label="AI first token" value={formatMs(p.average_llm_first_token_ms)} />
                <Row label="Cancel after interrupt" value={formatMs(p.average_interruption_latency_ms)} />
                <Row label="You spoke" value={formatClock(p.user_speaking_seconds)} />
                <Row label="AI spoke" value={formatClock(p.ai_speaking_seconds)} />
              </Card>
              <Card title="Cost">
                <Row label="External AI API" value={`$${c.external_api_cost_usd.toFixed(2)}`} />
                <Row label="AI model" value={c.llm_model ?? "—"} />
                <Row label="Speech recognition" value={c.stt_model ?? "—"} />
                <Row label="Text to speech" value={c.tts_model ?? "—"} />
                {c.electricity_estimate && (
                  <Row
                    label={`Electricity (estimated at ${c.electricity_estimate.assumed_watts} W)`}
                    value={`${c.electricity_estimate.cost} ${c.electricity_estimate.currency}`}
                  />
                )}
                <p className="pt-2 text-[11px] leading-snug text-slate-500">{c.note}</p>
              </Card>
            </div>

            <section className="glass space-y-3 rounded-2xl p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Full transcript</h2>
              {report.transcript.length === 0 && <p className="text-sm text-slate-500">No messages were saved.</p>}
              {report.transcript.map((m) => <Bubble key={m.id} message={m} />)}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

export default function ReportPage() {
  return (
    <Suspense>
      <ReportContent />
    </Suspense>
  );
}
