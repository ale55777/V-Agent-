"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { downloadExport } from "@/lib/api";

export function ExportButtons({ sessionId }: { sessionId: string }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      {(["txt", "json", "pdf"] as const).map((format) => (
        <Button key={format} size="sm" onClick={() => downloadExport(sessionId, format).catch((e) => setError(e.message))}>
          <Download size={15} aria-hidden /> {format.toUpperCase()}
        </Button>
      ))}
      {error && <span role="alert" className="text-xs text-halt">{error}</span>}
    </>
  );
}
