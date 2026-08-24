import type { SessionSummary } from "@/shared/api/client";

export const TERMINAL = new Set(["completed", "failed", "cancelled"]);
export const QUICK_VIDEO_CAP = 80;
export const SESSION_STORAGE_KEY = "sadt_active_session_id";

export type ReviewSummary = {
  pending: number;
  confirmed: number;
  rejected: number;
  total: number;
};

/** Sesi lab/demo — prioritas saat bootstrap konsol. */
export function isLabDemoSession(s: SessionSummary): boolean {
  const label = (s.label || "").trim().toLowerCase();
  return label.startsWith("dummy");
}

/** Hindari sesi E2E/tes otomatis yang tersisa di localStorage. */
function isStaleTestSession(s: SessionSummary): boolean {
  const label = (s.label || "").trim();
  return label.startsWith("E2E ") || s.status === "cancelled" || s.status === "failed";
}

/** Pilih sesi awal: URL → localStorage (jika masih valid) → dummy terbanyak temuan. */
export function pickBootstrapSession(
  items: SessionSummary[],
  preferId: string | null,
): SessionSummary | undefined {
  if (!items.length) return undefined;

  const fromPrefer = preferId ? items.find((s) => s.id === preferId) : undefined;
  if (fromPrefer && !isStaleTestSession(fromPrefer)) return fromPrefer;

  const completed = items.filter((s) => s.status === "completed");
  const rankByFindings = (list: SessionSummary[]) =>
    [...list].sort(
      (a, b) => (b.progress?.findings_count ?? 0) - (a.progress?.findings_count ?? 0),
    );

  const demo = rankByFindings(completed.filter(isLabDemoSession));
  if (demo[0]) return demo[0];

  const best = rankByFindings(completed);
  if (best[0]) return best[0];

  return fromPrefer ?? items.find((s) => s.recommendation) ?? items[0];
}
