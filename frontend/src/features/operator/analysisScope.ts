import type { AnalysisScope } from "@/shared/api/types/common";

export type { AnalysisScope };

export const DEVICE_SOURCE_OPTIONS = [
  { id: "gallery", label: "Galeri & media", hint: "Foto, video, screenshot" },
  { id: "documents", label: "Dokumen", hint: "PDF, unduhan, berkas" },
  { id: "contacts", label: "Kontak", hint: "Buku telepon" },
  { id: "sms", label: "SMS", hint: "Pesan masuk/keluar" },
  { id: "notifications", label: "Notifikasi", hint: "Notifikasi aplikasi" },
  { id: "recovery", label: "Recovery cache/sampah", hint: "Lebih lama; jejak hapus di HP" },
  { id: "email", label: "Email / Gmail", hint: "Kotak masuk yang diotorisasi" },
  { id: "browser", label: "Riwayat browser", hint: "Chrome / browser di HP" },
  { id: "whatsapp", label: "WhatsApp", hint: "Cadangan / data percakapan" },
] as const;

export const SOCIAL_TARGET_OPTIONS = [
  { id: "instagram", label: "Instagram" },
  { id: "facebook", label: "Facebook" },
  { id: "x", label: "X / Twitter" },
] as const;

export const ANALYSIS_SCOPE_OPTIONS = [
  {
    id: "device" as const,
    label: "HP saja",
    hint: "Galeri, kontak, SMS, recovery — tanpa crawl sosmed.",
  },
  {
    id: "social" as const,
    label: "Sosmed",
    hint: "Hanya crawl akun yang dicentang. Inventori HP dilewati.",
  },
  {
    id: "combined" as const,
    label: "Gabungan",
    hint: "HP + sosmed. Default Satria: paling lengkap.",
  },
];

export type DeviceSourceId = (typeof DEVICE_SOURCE_OPTIONS)[number]["id"];
export type SocialTargetId = (typeof SOCIAL_TARGET_OPTIONS)[number]["id"];

export const ALL_DEVICE_SOURCES: DeviceSourceId[] = DEVICE_SOURCE_OPTIONS.map((item) => item.id);
export const ALL_SOCIAL_TARGETS: SocialTargetId[] = SOCIAL_TARGET_OPTIONS.map((item) => item.id);

/** Legacy clients / Satria default: device + social. */
export const DEFAULT_ANALYSIS_SCOPE: AnalysisScope = "combined";

export const ANALYSIS_SCOPE_LABEL: Record<AnalysisScope, string> = {
  device: "HP saja",
  social: "Sosmed",
  combined: "Gabungan",
};

export function planForScope(scope: AnalysisScope): {
  deviceSources: DeviceSourceId[];
  socialTargets: SocialTargetId[];
} {
  if (scope === "device") {
    return { deviceSources: [...ALL_DEVICE_SOURCES], socialTargets: [] };
  }
  if (scope === "social") {
    return { deviceSources: [], socialTargets: [...ALL_SOCIAL_TARGETS] };
  }
  return {
    deviceSources: [...ALL_DEVICE_SOURCES],
    socialTargets: [...ALL_SOCIAL_TARGETS],
  };
}

export function analysisPlanReady(
  scope: AnalysisScope,
  deviceSources: readonly string[],
  socialTargets: readonly string[],
): boolean {
  if (scope === "device") return deviceSources.length > 0;
  if (scope === "social") return socialTargets.length > 0;
  return deviceSources.length > 0 && socialTargets.length > 0;
}

export function toggleChecked<T extends string>(list: readonly T[], id: T): T[] {
  return list.includes(id) ? list.filter((item) => item !== id) : [...list, id];
}
