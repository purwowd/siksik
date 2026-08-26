/** Label bahasa awam untuk nilai teknis di dasbor. */

const CATEGORY_LABELS: Record<string, string> = {
  konten_visual: "Konten visual berisiko",
  ketelanjangan: "Ketelanjangan / konten eksplisit",
  konten_teks: "Teks berisiko",
  dokumen: "Dokumen",
  pesan: "Pesan",
  audio: "Audio / rekaman",
  video: "Video",
  politik: "Konten politik",
  "anti pemerintah": "Indikasi anti pemerintah",
  anti_pemerintah: "Indikasi anti pemerintah",
  makar: "Indikasi makar",
  senjata: "Senjata / bom",
  lainnya: "Lainnya",
};

const LAYER_LABELS: Record<string, string> = {
  L1: "Pindai teks cepat",
  L2: "Pola nama file",
  L3: "Baca teks di foto",
  L4: "Analisa gambar / video",
  OCR: "Baca teks di foto",
  ASR: "Transkrips suara",
};

const SOURCE_LABELS: Record<string, string> = {
  image: "Foto / screenshot",
  "media/image": "Foto / screenshot",
  "media image": "Foto / screenshot",
  video: "Video",
  audio: "Audio",
  document: "Dokumen",
  text: "Teks",
  gallery: "Galeri HP",
  dcim: "Kamera HP",
  download: "Folder unduhan",
  recovered_trash: "Sampah / media terhapus",
  recovered_cache: "Pratinjau cache galeri",
  ios_hidden: "Photos Tersembunyi (iOS)",
  ios_recently_deleted: "Baru Dihapus (iOS)",
  ios_recovered_cache: "Cache / preview Photos (iOS)",
  ios_deleted_metadata: "Jejak hapus permanen Photos (iOS)",
};

const METHOD_LABELS: Record<string, string> = {
  adb: "USB Android",
  adb_pull: "USB Android",
  android_agent: "Aplikasi SATRIA di HP",
  android_agent_inventory_complete: "Inventaris HP selesai",
  android_agent_inventory_partial: "Inventaris HP sebagian",
  preprocessing_complete: "Pra-pemrosesan selesai",
  preprocessing_partial: "Pra-pemrosesan sebagian",
  selection_confirmed: "Seleksi terkonfirmasi",
  android_agent_direct_manifest: "Transfer dari HP",
  android_agent_direct_manifest_resumed: "Transfer dari HP dilanjutkan",
  android_recovery_quick_complete: "Recovery sampah Android",
  android_recovery_quick_partial: "Recovery sampah Android (sebagian)",
  android_recovery_full_complete: "Recovery sampah Android",
  android_recovery_full_partial: "Recovery sampah Android (sebagian)",
  zip_upload: "Unggah arsip perangkat",
  simulated: "Uji internal",
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
  if (kind === "method" && key.includes("+")) {
    return key
      .split("+")
      .map((part) => humanLabel("method", part))
      .filter((part, index, values) => values.indexOf(part) === index)
      .join(" + ");
  }
  const hit = maps[kind][key] ?? maps[kind][key.toLowerCase()];
  if (hit) return hit;
  return key.replace(/_/g, " ");
}

/** Rantai metode lengkap → 1–2 langkah yang berguna di pill / strip. */
export function methodSummary(raw: string | null | undefined): string {
  const expanded = humanLabel("method", raw || "unknown");
  const parts = expanded
    .split(" + ")
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part, index, values) => values.indexOf(part) === index);
  if (parts.length === 0) return "Tidak diketahui";
  if (parts.length <= 2) return parts.join(" · ");
  const primary =
    parts.find((part) => /USB|Transfer dari HP|Unggah|Aplikasi SATRIA/i.test(part)) ?? parts[0];
  const secondary = [...parts]
    .reverse()
    .find((part) => part !== primary && /Recovery|Photos iOS/i.test(part));
  if (primary && secondary) return `${primary} · ${secondary}`;
  return primary;
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
