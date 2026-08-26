/** Badge singkat dari layer / jenis deteksi, bahasa panitia. */
export function FindingOriginBadge({
  layer,
  label,
}: {
  layer: string;
  label: string;
}) {
  const low = label.toLowerCase();
  let kind = "Berkas";
  let tone: "muted" | "ok" | "warn" | "bad" = "muted";
  if (low.includes("ketelanjangan") || low.includes("nudenet")) {
    kind = "Konten dewasa";
    tone = "bad";
  } else if (low.includes("audio") || low.includes("lirik") || low.includes("whisper")) {
    kind = "Audio";
    tone = "warn";
  } else if (low.includes("ocr") || low.includes("on-screen") || low.includes("dokumen")) {
    kind = "Teks pada foto";
    tone = "ok";
  } else if (low.includes("video keyframe") || low.startsWith("cv ")) {
    kind = "Analisis visual";
    tone = "muted";
  } else if (low.includes("nama file") || low.includes("path") || low.includes("indikasi:")) {
    kind = "Nama berkas";
    tone = "muted";
  } else if (layer === "OCR") {
    kind = "Teks pada foto";
    tone = "ok";
  } else if (layer === "ASR") {
    kind = "Audio";
    tone = "warn";
  } else if (layer === "L3") {
    kind = "Teks pada foto";
    tone = "ok";
  } else if (layer === "L4") {
    kind = "Analisis visual";
  } else if (layer === "L1" || layer === "L2") {
    kind = "Nama berkas";
  }
  return (
    <span className={`pill ${tone}`} title={label}>
      {kind}
    </span>
  );
}
