import type { AuthSession } from "@/shared/api/client";
import { can } from "@/shared/api/client";
import type { Tab } from "@/shared/types";

export const ACTIVE = new Set([
  "pending",
  "detecting",
  "preparing_agent",
  "awaiting_access",
  "acquiring",
  "selecting",
  "awaiting_review",
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
  {
    id: "acquire",
    label: "Pengambilan Data",
    match: [
      "pending",
      "detecting",
      "preparing_agent",
      "awaiting_access",
      "acquiring",
      "selecting",
      "awaiting_review",
    ],
  },
  { id: "content", label: "Analisis Konten", match: ["indexing"] },
  { id: "cross", label: "Lintas Sumber", match: ["analyzing"] },
  { id: "integrity", label: "Penilaian Integritas", match: ["completed"] },
  { id: "final", label: "Hasil Akhir", match: ["completed"] },
] as const;

export const TAB_PERMS: Record<Tab, string> = {
  operator: "sessions:start",
  dashboard: "dashboard",
  findings: "findings:read",
  gallery: "findings:read",
  report: "report:read",
};

/** Urutan nav: Temuan → Galeri → Laporan → Dasbor. */
export const TAB_DEFS: { id: Tab; label: string }[] = [
  { id: "operator", label: "Pengambilan Data" },
  { id: "findings", label: "Temuan" },
  { id: "gallery", label: "Galeri" },
  { id: "report", label: "Hasil Akhir" },
  { id: "dashboard", label: "Dasbor Petugas" },
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
  { user: "admin", pass: "Admin@2026", role: "Administrator" },
];
