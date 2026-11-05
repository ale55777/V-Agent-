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
    <header className="glass sticky top-0 z-20 flex flex-wrap items-center gap-x-4 gap-y-2 border-x-0 border-t-0 px-4 py-3 sm:px-6">
      <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-speak text-white">
          <AudioLines size={18} aria-hidden />
        </span>
        EchoAI
      </Link>
      <div className="flex items-center gap-3 text-xs text-slate-400">
        {connected !== undefined && (
          <span className={cn("flex items-center gap-1.5", connected ? "text-listen" : "text-slate-400")}>
            {connected ? <Wifi size={14} aria-hidden /> : <WifiOff size={14} aria-hidden />}
            {connected ? "Connected" : "Not connected"}
          </span>
        )}
        <span className={cn("flex items-center gap-1.5", status.color)} title={health?.models.llm.detail}>
          <Cpu size={14} aria-hidden /> {status.label}
        </span>
      </div>
      <nav className="ml-auto flex items-center gap-1">
        {onSettings && (
          <Button variant="ghost" size="sm" onClick={onSettings}>
            <Settings size={16} aria-hidden /> Settings
          </Button>
        )}
        <Link href="/history/">
          <Button variant="ghost" size="sm">
            <History size={16} aria-hidden /> History
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
