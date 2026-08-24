import { useEffect, useRef } from "react";
import type { SessionSummary } from "@/shared/api/client";
import { loadAuth } from "@/shared/api/client";

const STREAM_BASE = "/api/v1";

/** SSE session stream with Authorization header; falls back to null on error. */
export function useSessionStream(
  sessionId: string | null | undefined,
  active: boolean,
  onUpdate: (s: SessionSummary) => void,
  enabled = true,
) {
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled || !sessionId || !active) {
      abortRef.current?.abort();
      abortRef.current = null;
      return;
    }

    const auth = loadAuth();
    const controller = new AbortController();
    abortRef.current = controller;
    let cancelled = false;

    async function run() {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (auth?.token) headers.Authorization = `Bearer ${auth.token}`;

      try {
        const res = await fetch(`${STREAM_BASE}/sessions/${sessionId}/stream`, {
          headers,
          signal: controller.signal,
        });
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() || "";
          for (const chunk of parts) {
            const line = chunk.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            try {
              const data = JSON.parse(line.slice(6)) as SessionSummary;
              if (!cancelled) onUpdate(data);
            } catch {
              /* ignore malformed */
            }
          }
        }
      } catch {
        /* fallback: App polling continues */
      }
    }

    void run();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [sessionId, active, enabled, onUpdate]);
}
