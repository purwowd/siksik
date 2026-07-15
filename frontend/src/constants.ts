import type { AuthSession } from "./api";
import { can } from "./api";
import type { Tab } from "./types";

export const ACTIVE = new Set([
  "pending",
  "detecting",
  "acquiring",
  "indexing",
  "analyzing",
]);

export const REC_LULUS = "LULUS";
export const REC_TIDAK_LULUS = "TIDAK LULUS";
export const REC_MENUNGGU_REVIEW = "MENUNGGU REVIEW";

export function isOpenRecommendation(rec?: string | null): boolean {
  return (
    rec === REC_LULUS || rec === REC_TIDAK_LULUS || rec === REC_MENUNGGU_REVIEW
  );
}

export function isThreatRecommendation(rec?: string | null): boolean {
  return rec === REC_TIDAK_LULUS || rec === REC_MENUNGGU_REVIEW;
}

export const PIPELINE = [
  { id: "detect", label: "Deteksi", match: ["pending", "detecting"] },
  { id: "acquire", label: "Akuisisi", match: ["acquiring"] },
  { id: "index", label: "Indeks", match: ["indexing"] },
  { id: "analyze", label: "Analisa", match: ["analyzing"] },
  { id: "report", label: "Temuan", match: ["completed"] },
] as const;

export const TAB_PERMS: Record<Tab, string> = {
  operator: "sessions:start",
  dashboard: "dashboard",
  findings: "findings:read",
  report: "report:read",
};

/** Urutan nav: Temuan sebelum Dasbor. */
export const TAB_DEFS: { id: Tab; label: string }[] = [
  { id: "operator", label: "Operator" },
  { id: "findings", label: "Temuan" },
  { id: "report", label: "Laporan" },
  { id: "dashboard", label: "Dasbor" },
];

export function preferredLandingTab(
  auth: AuthSession | null,
  allowed: { id: Tab }[],
): Tab | null {
  if (!auth || allowed.length === 0) return null;
  const ids = new Set(allowed.map((t) => t.id));
  if (can(auth, "findings:review") && !can(auth, "sessions:start") && ids.has("findings")) {
    return "findings";
  }
  if (can(auth, "report:authorize") && !can(auth, "sessions:start") && ids.has("report")) {
    return "report";
  }
  return allowed[0].id;
}

/** Lab demo usernames only — passwords filled on click for PoC convenience. */
export const DEMO_ACCOUNTS = [
  { user: "operator", pass: "Ops@2026", role: "Operator" },
  { user: "analis", pass: "Analis@2026", role: "Analis" },
  { user: "pimpinan", pass: "Pimpinan@2026", role: "Pimpinan" },
  { user: "admin", pass: "Admin@2026", role: "Admin" },
];
