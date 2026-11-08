"use client";

import { useEffect, useRef } from "react";
import { AudioLines, MessageSquareText, User } from "lucide-react";
import type { Message } from "@echoai/shared-types";
import type { LiveReply } from "@/hooks/useVoiceSession";
import { formatTime } from "@/lib/api";
import { cn } from "@/lib/utils";

function Avatar({ user }: { user: boolean }) {
  return (
    <span
      className={cn(
        "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full",
        user ? "bg-accent/25 text-accent-soft" : "bg-gradient-to-br from-accent to-speak text-white",
      )}
      aria-hidden
    >
      {user ? <User size={14} /> : <AudioLines size={14} />}
    </span>
  );
}

function Row({
  user, children, meta,
}: { user: boolean; children: React.ReactNode; meta: React.ReactNode }) {
  return (
    <div className={cn("flex animate-rise gap-2.5", user ? "flex-row-reverse" : "flex-row")}>
      <Avatar user={user} />
      <div className={cn("flex min-w-0 max-w-[85%] flex-col", user ? "items-end" : "items-start")}>
        {children}
        <p className="mt-1 px-1 text-[11px] text-slate-500">{meta}</p>
      </div>
    </div>
  );
}

const bubbleClass = (user: boolean) =>
  cn(
    "rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
    user
      ? "rounded-tr-md bg-gradient-to-br from-accent/40 to-accent/20 text-slate-50"
      : "rounded-tl-md border border-white/[0.06] bg-white/[0.05] text-slate-100",
  );

export function Bubble({ message }: { message: Pick<Message, "speaker" | "content" | "timestamp" | "interrupted"> }) {
  const user = message.speaker === "user";
  return (
    <Row
      user={user}
      meta={
        <>
          {user ? "You" : "Jarvis"} · {formatTime(message.timestamp)}
          {message.interrupted && <span className="ml-1 text-halt/80">· interrupted</span>}
        </>
      }
    >
      <div className={bubbleClass(user)}>
        {message.content}
        {message.interrupted && <span className="text-slate-500"> —</span>}
      </div>
    </Row>
  );
}

export function ConversationPanel({
  messages, partial, reply,
}: { messages: Message[]; partial: string; reply: LiveReply | null }) {
  const end = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);

  // Auto-scroll, unless the user has scrolled up to read something earlier.
  useEffect(() => {
    const box = container.current;
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight < 180) {
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, partial, reply]);

  const spoken = reply ? reply.generated.slice(0, reply.spokenChars) : "";
  const pending = reply ? reply.generated.slice(reply.spokenChars) : "";
  const empty = messages.length === 0 && !partial && !reply;

  return (
    <>
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-3">
        <MessageSquareText size={16} className="text-accent-soft" aria-hidden />
        <h2 className="text-sm font-medium">Conversation</h2>
        {messages.length > 0 && (
          <span className="ml-auto rounded-full bg-white/[0.06] px-2 py-0.5 text-[11px] tabular-nums text-slate-400">
            {messages.length} {messages.length === 1 ? "message" : "messages"}
          </span>
        )}
      </div>
      <div ref={container} className="scroll-thin flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
        {empty && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-10 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.05] text-accent-soft">
              <MessageSquareText size={22} aria-hidden />
            </span>
            <p className="text-sm text-slate-300">No messages yet</p>
            <p className="max-w-xs text-xs text-slate-500">
              Everything you and Jarvis say shows up here as you talk, and is saved to your history.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <Bubble key={message.id} message={message} />
        ))}
        {reply && (reply.generated || reply.spokenChars > 0) && (
          <Row user={false} meta={<span className="text-speak">Jarvis · {reply.spokenChars > 0 ? "speaking" : "getting ready to speak"}</span>}>
            <div className={bubbleClass(false)}>
              <span className="text-slate-100">{spoken}</span>
              <span className="text-slate-500">{pending}</span>
            </div>
          </Row>
        )}
        {partial && (
          <Row user meta={<span className="text-listen">You · transcribing</span>}>
            <div className="rounded-2xl rounded-tr-md border border-dashed border-accent/40 px-3.5 py-2 text-sm italic text-slate-300">
              {partial}
            </div>
          </Row>
        )}
        <div ref={end} />
      </div>
    </>
  );
}
