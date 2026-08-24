import type { APIRequestContext } from "@playwright/test";

export function resolveApiBase(_uiBase?: string): string {
  if (process.env.PLAYWRIGHT_API_URL) return process.env.PLAYWRIGHT_API_URL;
  const port = process.env.PLAYWRIGHT_API_PORT || "8012";
  return `http://127.0.0.1:${port}`;
}

export const DEMO = {
  operator: { user: "operator", pass: "Ops@2026" },
  analis: { user: "analis", pass: "Analis@2026" },
  pimpinan: { user: "pimpinan", pass: "Pimpinan@2026" },
  admin: { user: "admin", pass: "Admin@2026" },
} as const;

export type SessionSummary = {
  id: string;
  status: string;
  recommendation?: string;
  progress?: { findings_count?: number };
};

export type Finding = {
  id: string;
  review_status: string;
};

export async function apiLogin(
  request: APIRequestContext,
  baseURL: string,
  username: string,
  password: string,
): Promise<string> {
  const res = await request.post(`${baseURL}/api/v1/auth/login`, {
    data: { username, password },
  });
  if (!res.ok()) {
    throw new Error(`login failed (${username}): ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { token: string };
  return body.token;
}

export async function createSimulatedSession(
  request: APIRequestContext,
  baseURL: string,
  token: string,
  scenario: "tidak_lulus" | "lulus" = "tidak_lulus",
): Promise<string | null> {
  const res = await request.post(`${baseURL}/api/v1/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      device_id: "sim-android-01",
      device_type: "android",
      mode: "quick",
      scenario,
      file_count: 15,
      label: `E2E ${scenario} ${Date.now()}`,
      participant: {
        full_name: "Peserta E2E",
        registration_no: `E2E-${Date.now()}`,
      },
      force_simulated: true,
    },
  });
  if (res.status() === 403) {
    const detail = await res.text();
    throw new Error(`simulator forbidden (403): ${detail}`);
  }
  if (res.status() === 409) {
    const activeId = await findInProgressSession(request, baseURL, token);
    if (activeId) return activeId;
  }
  if (!res.ok()) {
    throw new Error(`create session failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { id: string };
  return body.id;
}

export async function cancelActiveSessions(
  request: APIRequestContext,
  baseURL: string,
  token: string,
): Promise<void> {
  const res = await request.get(`${baseURL}/api/v1/sessions?page=1&page_size=30`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok()) return;
  const body = (await res.json()) as { items: SessionSummary[] };
  for (const session of body.items) {
    if (session.status === "completed" || session.status === "failed" || session.status === "cancelled") {
      continue;
    }
    await request.post(`${baseURL}/api/v1/sessions/${session.id}/cancel`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  }
}

export async function findInProgressSession(
  request: APIRequestContext,
  baseURL: string,
  token: string,
): Promise<string | null> {
  const res = await request.get(`${baseURL}/api/v1/sessions?page=1&page_size=20`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok()) return null;
  const body = (await res.json()) as { items: SessionSummary[] };
  const active = body.items.find(
    (s) => s.status !== "completed" && s.status !== "failed" && s.status !== "cancelled",
  );
  return active?.id ?? null;
}

export async function findSessionWithPendingFindings(
  request: APIRequestContext,
  baseURL: string,
  token: string,
): Promise<string | null> {
  const res = await request.get(`${baseURL}/api/v1/sessions?page=1&page_size=50`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok()) return null;
  const body = (await res.json()) as { items: { id: string; status: string }[] };
  for (const session of body.items) {
    if (session.status !== "completed") continue;
    const findings = await fetchFindings(request, baseURL, token, session.id);
    if (findings.some((f) => f.review_status === "pending")) return session.id;
  }
  return null;
}

export async function waitSessionCompleted(
  request: APIRequestContext,
  baseURL: string,
  token: string,
  sessionId: string,
  timeoutMs = 360_000,
): Promise<SessionSummary> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await request.get(`${baseURL}/api/v1/sessions/${sessionId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok()) throw new Error(`session poll failed: ${res.status()}`);
    const body = (await res.json()) as SessionSummary;
    if (body.status === "completed") return body;
    if (body.status === "failed") throw new Error("session failed");
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`session ${sessionId} did not complete in ${timeoutMs}ms`);
}

export async function fetchFindings(
  request: APIRequestContext,
  baseURL: string,
  token: string,
  sessionId: string,
): Promise<Finding[]> {
  const res = await request.get(
    `${baseURL}/api/v1/sessions/${sessionId}/findings?page=1&page_size=500`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok()) throw new Error(`findings fetch failed: ${res.status()}`);
  const body = (await res.json()) as { items: Finding[] };
  return body.items;
}

export async function reviewFinding(
  request: APIRequestContext,
  baseURL: string,
  token: string,
  findingId: string,
  review_status: "confirmed" | "rejected",
): Promise<void> {
  const res = await request.patch(`${baseURL}/api/v1/findings/${findingId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { review_status },
  });
  if (!res.ok()) throw new Error(`review failed: ${res.status()} ${await res.text()}`);
}

export async function reviewAllPending(
  request: APIRequestContext,
  baseURL: string,
  token: string,
  sessionId: string,
  decision: "confirmed" | "rejected" = "confirmed",
): Promise<void> {
  const items = await fetchFindings(request, baseURL, token, sessionId);
  for (const f of items.filter((i) => i.review_status === "pending")) {
    await reviewFinding(request, baseURL, token, f.id, decision);
  }
}

export async function prepareTidakLulusSession(
  request: APIRequestContext,
  uiBase?: string,
): Promise<{ sessionId: string; adminToken: string; analisToken: string }> {
  const baseURL = resolveApiBase(uiBase);
  const adminToken = await apiLogin(request, baseURL, DEMO.admin.user, DEMO.admin.pass);
  const analisToken = await apiLogin(request, baseURL, DEMO.analis.user, DEMO.analis.pass);

  const existing = await findSessionWithPendingFindings(request, baseURL, analisToken);
  if (existing) {
    return { sessionId: existing, adminToken, analisToken };
  }

  await cancelActiveSessions(request, baseURL, adminToken);

  const sessionId = await createSimulatedSession(request, baseURL, adminToken, "tidak_lulus");
  if (!sessionId) {
    throw new Error(
      "Cannot create simulated session. Run playwright without PLAYWRIGHT_BASE_URL or enable SADT_LAB_DEMO_MODE=1.",
    );
  }

  const session = await waitSessionCompleted(request, baseURL, adminToken, sessionId);
  if ((session.progress?.findings_count ?? 0) === 0) {
    throw new Error("expected findings for tidak_lulus scenario");
  }

  return { sessionId, adminToken, analisToken };
}
