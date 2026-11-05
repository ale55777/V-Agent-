"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, FileText, Mic, Pencil, Search, Trash2 } from "lucide-react";
import type { Message, SessionSummary } from "@echoai/shared-types";
import { Header, useRequireAuth } from "@/components/Header";
import { Bubble } from "@/components/ConversationPanel";
import { ExportButtons } from "@/components/ExportButtons";
import { Button } from "@/components/ui/button";
import { api, formatClock, formatDate } from "@/lib/api";

function Detail({ id }: { id: string }) {
  const router = useRouter();
  const [session, setSession] = useState<(SessionSummary & { messages: Message[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api().session(id).then(setSession).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <p className="p-6 text-halt">{error}</p>;
  if (!session) return <p className="p-6 text-slate-400">Loading…</p>;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <Button variant="ghost" size="sm" onClick={() => router.push("/history/")}>
        <ArrowLeft size={16} aria-hidden /> All conversations
      </Button>
      <div className="glass space-y-3 rounded-2xl p-4">
        <h1 className="text-lg font-semibold">{session.title}</h1>
        <div className="flex flex-wrap gap-2">
          <Link href={`/?session=${id}`}><Button variant="primary" size="sm"><Mic size={15} aria-hidden /> Continue</Button></Link>
          <Link href={`/report/?id=${id}`}><Button size="sm"><FileText size={15} aria-hidden /> Report</Button></Link>
          <ExportButtons sessionId={id} />
        </div>
      </div>
      <div className="glass space-y-3 rounded-2xl p-4">
        {session.messages.length === 0 && <p className="text-sm text-slate-500">No messages were saved in this session.</p>}
        {session.messages.map((m) => <Bubble key={m.id} message={m} />)}
      </div>
    </div>
  );
}

function List() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (q?: string) => api().sessions(q).then(setSessions).catch((e) => setError(e.message)),
    [],
  );

  useEffect(() => {
    const timer = setTimeout(() => void load(query.trim() || undefined), 250);
    return () => clearTimeout(timer);
  }, [query, load]);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <h1 className="text-lg font-semibold">Conversation history</h1>
      <div className="relative">
        <Search size={16} className="absolute left-3 top-3 text-slate-500" aria-hidden />
        <input
          className="field pl-9" placeholder="Search titles and transcripts" aria-label="Search conversations"
          value={query} onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {error && <p role="alert" className="text-sm text-halt">{error}</p>}
      {sessions?.length === 0 && (
        <p className="py-10 text-center text-sm text-slate-500">
          {query ? "No conversations match that search." : "No conversations yet."}
        </p>
      )}
      <ul className="space-y-2">
        {sessions?.map((s) => (
          <li key={s.id} className="glass flex flex-wrap items-center gap-2 rounded-xl p-3">
            {renaming === s.id ? (
              <form
                className="min-w-0 flex-1"
                onSubmit={async (event) => {
                  event.preventDefault();
                  const title = new FormData(event.currentTarget).get("title")?.toString().trim();
                  if (title) await api().rename(s.id, title);
                  setRenaming(null);
                  void load(query.trim() || undefined);
                }}
              >
                <input name="title" className="field" defaultValue={s.title} autoFocus aria-label="New title" onBlur={(e) => e.currentTarget.form?.requestSubmit()} />
              </form>
            ) : (
              <button className="min-w-0 flex-1 text-left" onClick={() => router.push(`/history/?id=${s.id}`)}>
                <p className="truncate text-sm font-medium">{s.title}</p>
                <p className="text-xs text-slate-500">
                  {formatDate(s.start_time)} · {formatClock(s.duration)} · {s.message_count} messages
                  {s.status === "active" && <span className="text-listen"> · not ended</span>}
                </p>
              </button>
            )}
            {deleting === s.id ? (
              <span className="flex items-center gap-1 text-xs">
                Delete permanently?
                <Button size="sm" variant="danger" onClick={async () => {
                  await api().remove(s.id);
                  setDeleting(null);
                  void load(query.trim() || undefined);
                }}>Delete</Button>
                <Button size="sm" variant="ghost" onClick={() => setDeleting(null)}>Keep</Button>
              </span>
            ) : (
              <>
                <Button size="icon" variant="ghost" aria-label={`Rename ${s.title}`} onClick={() => setRenaming(s.id)}><Pencil size={15} aria-hidden /></Button>
                <Button size="icon" variant="ghost" aria-label={`Delete ${s.title}`} onClick={() => setDeleting(s.id)}><Trash2 size={15} aria-hidden /></Button>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function HistoryContent() {
  const ready = useRequireAuth();
  const id = useSearchParams().get("id");
  if (!ready) return null;
  return (
    <div className="min-h-dvh">
      <Header />
      {id ? <Detail id={id} /> : <List />}
    </div>
  );
}

export default function HistoryPage() {
  return (
    <Suspense>
      <HistoryContent />
    </Suspense>
  );
}
