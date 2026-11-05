"use client";

import { useEffect, useRef } from "react";
import type { Message } from "@echoai/shared-types";
import type { LiveReply } from "@/hooks/useVoiceSession";
import { formatTime } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Bubble({ message }: { message: Pick<Message, "speaker" | "content" | "timestamp" | "interrupted"> }) {
  const user = message.speaker === "user";
  return (
    <div className={cn("flex animate-rise flex-col", user ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[88%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
          user ? "rounded-br-md bg-accent/25 text-slate-50" : "rounded-bl-md bg-white/5 text-slate-100",
        )}
      >
        {message.content}
        {message.interrupted && <span className="text-slate-500"> —</span>}
      </div>
      <p className="mt-1 px-1 text-[11px] text-slate-500">
        {user ? "You" : "EchoAI"} · {formatTime(message.timestamp)}
        {message.interrupted && <span className="ml-1 text-halt/80">· interrupted</span>}
      </p>
    </div>
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

  return (
    <div ref={container} className="scroll-thin flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
      {messages.length === 0 && !partial && !reply && (
        <p className="pt-10 text-center text-sm text-slate-500">Your conversation will appear here as you talk.</p>
      )}
      {messages.map((message) => (
        <Bubble key={message.id} message={message} />
      ))}
      {reply && (reply.generated || reply.spokenChars > 0) && (
        <div className="flex flex-col items-start">
          <div className="max-w-[88%] rounded-2xl rounded-bl-md bg-white/5 px-3.5 py-2 text-sm leading-relaxed">
            <span className="text-slate-100">{spoken}</span>
            <span className="text-slate-500">{pending}</span>
          </div>
          <p className="mt-1 px-1 text-[11px] text-speak">
            EchoAI · {reply.spokenChars > 0 ? "speaking" : "preparing speech"} (dim text has not been spoken yet)
          </p>
        </div>
      )}
      {partial && (
        <div className="flex flex-col items-end">
          <div className="max-w-[88%] rounded-2xl rounded-br-md border border-dashed border-accent/40 px-3.5 py-2 text-sm italic text-slate-300">
            {partial}
          </div>
          <p className="mt-1 px-1 text-[11px] text-listen">You · transcribing</p>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
