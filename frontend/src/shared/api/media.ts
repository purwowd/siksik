import { authHeaders, BASE, req } from "./http";

export function mediaUrl(sessionId: string, path: string, ticket?: string) {
  const q = new URLSearchParams({ path });
  if (ticket) q.set("ticket", ticket);
  return `${BASE}/sessions/${sessionId}/media?${q}`;
}

export async function fetchMediaBlobUrl(sessionId: string, path: string): Promise<string> {
  const res = await fetch(mediaUrl(sessionId, path), { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(`Media ${res.status}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function fetchMediaText(sessionId: string, path: string): Promise<string> {
  const res = await fetch(mediaUrl(sessionId, path), { headers: authHeaders() });
  if (!res.ok) throw new Error(`Media ${res.status}`);
  return res.text();
}

export async function issueMediaTicket(
  sessionId: string,
  path: string,
): Promise<{ ticket: string; expires_at: string }> {
  return req(`/sessions/${sessionId}/media-ticket`, {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function ticketedMediaUrl(sessionId: string, path: string, ticket: string): string {
  return mediaUrl(sessionId, path, ticket);
}

export function ms(v: number) {
  if (!v) return "—";
  if (v < 1000) return `${v.toFixed(0)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}
