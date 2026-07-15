/** Badge singkat dari layer / prefix label media-text. */
export function FindingOriginBadge({
  layer,
  label,
}: {
  layer: string;
  label: string;
}) {
  const low = label.toLowerCase();
  let kind = layer;
  let tone: "muted" | "ok" | "warn" | "bad" = "muted";
  if (low.includes("audio") || low.includes("lirik") || low.includes("whisper")) {
    kind = "ASR";
    tone = "warn";
  } else if (low.includes("ocr") || low.includes("on-screen") || low.includes("dokumen")) {
    kind = "OCR";
    tone = "ok";
  } else if (low.includes("video keyframe") || low.startsWith("cv ")) {
    kind = "CV";
    tone = "muted";
  } else if (low.includes("nama file") || low.includes("path") || low.includes("indikasi:")) {
    kind = "L1/L2";
    tone = "muted";
  }
  return (
    <span className={`pill ${tone}`} title={label}>
      {kind} · {layer}
    </span>
  );
}
