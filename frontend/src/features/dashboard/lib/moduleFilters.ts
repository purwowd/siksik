/** SATRIA module ids for drill-down to filtered findings. */

export type ModuleId = "forensic" | "gallery" | "whatsapp" | "social" | "email" | "browser" | "notes" | "tiktok";

export const MODULE_FILTER_LABELS: Record<ModuleId, string> = {
  forensic: "Forensik",
  gallery: "Galeri",
  whatsapp: "WhatsApp",
  social: "Media sosial",
  email: "Email",
  browser: "Riwayat browser",
  notes: "Catatan Android",
  tiktok: "TikTok",
};

export const DRILLDOWN_MODULES = new Set<ModuleId>([
  "forensic",
  "gallery",
  "whatsapp",
  "social",
  "email",
  "browser",
  "notes",
]);
