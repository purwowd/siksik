import type { Tab } from "@/shared/types";

export type FeaturePageMeta = {
  title: string;
  copy: string;
};

export type FeatureEmptyMeta = {
  title: string;
  body: string;
  hint?: string;
  tone?: "ok" | "warn" | "info";
};

/** Metadata konsisten per tab konsol. */
export const FEATURE_PAGE_META: Record<Tab, FeaturePageMeta> = {
  operator: {
    title: "Penerimaan",
    copy: "Identitas, perangkat, cakupan, lalu jalankan. Satu sesi aktif.",
  },
  findings: {
    title: "Temuan",
    copy: "Konfirmasi atau tolak setiap temuan sebelum pengesahan.",
  },
  gallery: {
    title: "Galeri",
    copy: "Media yang diambil pada sesi aktif.",
  },
  report: {
    title: "Laporan",
    copy: "Ringkasan, PDF, jejak audit. Pimpinan mengesahkan di sini.",
  },
  dashboard: {
    title: "Ikhtisar",
    copy: "Antrean, modul, dan tren sesi yang dipilih.",
  },
};

export const FEATURE_EMPTY_NO_SESSION: Record<Exclude<Tab, "operator">, FeatureEmptyMeta> = {
  findings: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi di atas untuk membuka antrean temuan.",
    hint: "Pilih sesi untuk memulai tinjauan.",
  },
  gallery: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi untuk melihat media yang diambil.",
    hint: "Pilih sesi untuk menelusuri galeri.",
  },
  report: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi untuk melihat laporan dan pengesahan.",
    hint: "Pilih kasus selesai untuk mengesahkan rekomendasi.",
  },
  dashboard: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi untuk melihat ringkasan pemeriksaan.",
    hint: "Gunakan pemilih sesi di atas atau mulai pemeriksaan baru.",
  },
};

export const OPERATOR_TELEMETRY_META: FeaturePageMeta = {
  title: "Status pemeriksaan",
  copy: "Progres pengambilan dan analisa sesi aktif.",
};
