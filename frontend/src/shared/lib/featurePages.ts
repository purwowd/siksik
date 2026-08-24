import type { Tab } from "@/shared/types";

export type FeaturePageMeta = {
  eyebrow: string;
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
    eyebrow: "Tahap 01 · Penerimaan kasus",
    title: "Pengambilan data",
    copy: "Hubungkan perangkat yang diizinkan atau unggah ZIP forensik. Pilih kedalaman analisa, lalu jalankan pipeline.",
  },
  findings: {
    eyebrow: "Tahap 02–04 · Meja analis",
    title: "Tinjauan temuan",
    copy: "Konfirmasi atau tolak sinyal terflag. Papan ketik: J/K navigasi · C konfirmasi · R tolak.",
  },
  gallery: {
    eyebrow: "Tahap 02 · Loker bukti",
    title: "Galeri",
    copy: "Media dan artefak yang sudah masuk sesi — termasuk yang tidak terflag.",
  },
  report: {
    eyebrow: "Tahap 05 · Keputusan akhir",
    title: "Laporan kasus & pengesahan",
    copy: "Ringkasan bukti, profil sosial, dan pengesahan pimpinan — satu meja keputusan.",
  },
  dashboard: {
    eyebrow: "Meja komando",
    title: "Dasbor petugas",
    copy: "Prioritas antrean, modul analisis sesi, dan tren indikasi — pilih sesi di atas untuk fokus per kasus.",
  },
};

export const FEATURE_EMPTY_NO_SESSION: Record<Exclude<Tab, "operator">, FeatureEmptyMeta> = {
  findings: {
    title: "Belum ada kasus aktif",
    body: "Pilih sesi di atas untuk membuka antrean temuan analis.",
    hint: "Pilih sesi untuk memulai tinjauan analis.",
  },
  gallery: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi untuk membuka loker bukti media.",
    hint: "Pilih sesi untuk menelusuri artefak galeri yang diambil.",
  },
  report: {
    title: "Belum ada kasus dipilih",
    body: "Pilih sesi untuk melihat laporan dan pengesahan.",
    hint: "Pilih kasus selesai untuk mengesahkan rekomendasi.",
  },
  dashboard: {
    title: "Belum ada sesi dipilih",
    body: "Pilih sesi calon untuk melihat modul analisis dan statistik agregat.",
    hint: "Gunakan pemilih sesi di atas atau mulai akuisisi baru.",
  },
};

export const OPERATOR_TELEMETRY_META: FeaturePageMeta = {
  eyebrow: "Telemetri langsung",
  title: "Status pipeline",
  copy: "Pantau progres akuisisi, indeks, dan analisa secara real-time.",
};
