"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AudioLines, Cpu, History, LogOut, Settings, Wifi, WifiOff } from "lucide-react";
import type { Health } from "@echoai/shared-types";
import { Button } from "@/components/ui/button";
import { api, setToken } from "@/lib/api";
import { cn } from "@/lib/utils";

function modelStatus(health: Health | null): { label: string; color: string } {
  if (!health) return { label: "Backend offline", color: "text-halt" };
  if (health.loading) return { label: "Models loading", color: "text-think" };
  const failed = Object.entries(health.models).filter(([, v]) => v.state === "failed").map(([k]) => k.toUpperCase());
  if (failed.length) return { label: `${failed.join(", ")} unavailable`, color: "text-halt" };
  return { label: "Models ready", color: "text-listen" };
}

export function Header({ connected, onSettings }: { connected?: boolean; onSettings?: () => void }) {
  const router = useRouter();
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    const check = () => api().health().then((h) => alive && setHealth(h)).catch(() => alive && setHealth(null));
    check();
    const timer = setInterval(check, 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const status = modelStatus(health);

  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/[0.06] bg-ink-950/80 px-4 py-3 sm:px-6">
      <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-speak text-white shadow-[0_6px_20px_-6px_rgba(124,108,255,0.8)]">
          <AudioLines size={18} aria-hidden />
        </span>
        <span className="text-[15px]">
          <span className="text-gradient">Jarvis</span>
        </span>
      </Link>
      <div className="flex items-center gap-2 text-[11px]">
        {connected !== undefined && (
          <span
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1",
              connected ? "border-listen/25 bg-listen/10 text-listen" : "border-white/10 bg-white/5 text-slate-400",
            )}
          >
            {connected ? <Wifi size={12} aria-hidden /> : <WifiOff size={12} aria-hidden />}
            <span className="hidden sm:inline">{connected ? "Connected" : "Not connected"}</span>
          </span>
        )}
        <span
          className={cn("flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1", status.color)}
          title={health?.models.llm.detail}
        >
          <Cpu size={12} aria-hidden /> {status.label}
        </span>
      </div>
      <nav className="ml-auto flex items-center gap-1">
        {onSettings && (
          <Button variant="ghost" size="sm" onClick={onSettings} aria-label="Settings">
            <Settings size={16} aria-hidden /> <span className="hidden sm:inline">Settings</span>
          </Button>
        )}
        <Link href="/history/" aria-label="History">
          <Button variant="ghost" size="sm" tabIndex={-1}>
            <History size={16} aria-hidden /> <span className="hidden sm:inline">History</span>
          </Button>
        </Link>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Sign out"
          onClick={() => {
            setToken(null);
            router.replace("/login/");
          }}
        >
          <LogOut size={16} aria-hidden />
        </Button>
      </nav>
    </header>
  );
}

/** Sends the visitor to sign-in when there is no valid token. */
export function useRequireAuth() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    api().me().then(() => setReady(true)).catch(() => {
      setToken(null);
      router.replace("/login/");
    });
  }, [router]);
  return ready;
}
