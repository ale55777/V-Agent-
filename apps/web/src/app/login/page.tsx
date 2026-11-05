"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AudioLines } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = mode === "login" ? await api().login(email, password) : await api().register(email, password);
      setToken(result.access_token);
      router.replace("/");
    } catch (cause) {
      const failure = cause as Error;
      setError(failure.message === "Failed to fetch" ? "Cannot reach the EchoAI backend. Is it running?" : failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-dvh place-items-center p-4">
      <form onSubmit={submit} className="glass w-full max-w-sm space-y-4 rounded-2xl p-6">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-accent to-speak text-white">
            <AudioLines size={20} aria-hidden />
          </span>
          <div>
            <h1 className="font-semibold">EchoAI</h1>
            <p className="text-xs text-slate-400">Your voice assistant, running on your own PC</p>
          </div>
        </div>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium text-slate-300">Email</span>
          <input className="field" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium text-slate-300">Password</span>
          <input
            className="field" type="password" required minLength={8}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
          {mode === "register" && <span className="block text-[11px] text-slate-500">At least 8 characters.</span>}
        </label>
        {error && <p role="alert" className="rounded-lg bg-halt/10 p-2 text-sm text-halt">{error}</p>}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy ? "One moment…" : mode === "login" ? "Sign in" : "Create account"}
        </Button>
        <Button
          type="button" variant="ghost" className="w-full"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}
        </Button>
      </form>
    </main>
  );
}
