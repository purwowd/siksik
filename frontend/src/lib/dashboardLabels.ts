/** Label bahasa awam untuk nilai teknis di dasbor. */

const CATEGORY_LABELS: Record<string, string> = {
  konten_visual: "Konten visual berisiko",
  konten_teks: "Teks berisiko",
  dokumen: "Dokumen",
  pesan: "Pesan",
  audio: "Audio / rekaman",
  video: "Video",
  politik: "Konten politik",
  makar: "Indikasi makar",
  senjata: "Senjata / bom",
  lainnya: "Lainnya",
};

const LAYER_LABELS: Record<string, string> = {
  L1: "Pindai teks cepat",
  L2: "Pola nama file",
  L3: "Baca teks di foto (OCR)",
  L4: "Analisa gambar / video",
  OCR: "Baca teks di foto",
  ASR: "Transkrips suara",
};

const SOURCE_LABELS: Record<string, string> = {
  image: "Foto / screenshot",
  video: "Video",
  audio: "Audio",
  document: "Dokumen",
  text: "Teks",
  gallery: "Galeri HP",
  dcim: "Kamera HP",
  download: "Folder unduhan",
};

const METHOD_LABELS: Record<string, string> = {
  adb_pull: "USB Android (ADB)",
  zip_upload: "Unggah ZIP",
  simulated: "Simulasi lab",
  idevice: "USB iPhone",
  unknown: "Tidak diketahui",
};

const REVIEW_LABELS: Record<string, string> = {
  pending: "Belum dicek",
  confirmed: "Dikonfirmasi analis",
  rejected: "Ditolak (bukan ancaman)",
};

export function humanLabel(
  kind: "category" | "layer" | "source" | "method" | "review",
  raw: string,
): string {
  const key = raw.trim();
  const maps = {
    category: CATEGORY_LABELS,
    layer: LAYER_LABELS,
    source: SOURCE_LABELS,
    method: METHOD_LABELS,
    review: REVIEW_LABELS,
  } as const;
  const hit = maps[kind][key] ?? maps[kind][key.toLowerCase()];
  if (hit) return hit;
  return key.replace(/_/g, " ");
}

export function mapNamedCounts(
  kind: "category" | "layer" | "source" | "method",
  items?: { name: string; count: number }[] | null,
): { name: string; count: number }[] {
  return (items ?? []).map((i) => ({
    name: humanLabel(kind, i.name),
    count: i.count,
  }));
}
