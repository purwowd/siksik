/** Tampilkan nama berkas + folder terakhir, bukan path filesystem penuh. */
export function displayPath(path: string | null | undefined): string {
  const raw = (path || "").trim();
  if (!raw) return "—";
  const parts = raw.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length === 0) return raw;
  const last = parts[parts.length - 1];
  if (isOpaqueFileName(last)) {
    const kind = MEDIA_KIND[fileExt(last)] || "Berkas";
    if (parts.length === 1) return kind;
    return `${parts[parts.length - 2]}/${kind}`;
  }
  if (parts.length <= 2) return parts.join("/") || raw;
  return parts.slice(-2).join("/");
}

const HASH_FILE = /^[a-f0-9]{16,}(?:\.[a-z0-9]{1,8})?$/i;
const HASH_STEM = /^(?:record_)?[a-f0-9]{8,}(?:_[a-f0-9]{6,})?$/i;

const MEDIA_KIND: Record<string, string> = {
  jpg: "Foto",
  jpeg: "Foto",
  png: "Foto",
  webp: "Foto",
  heic: "Foto",
  gif: "Foto",
  bmp: "Foto",
  mp4: "Video",
  mov: "Video",
  webm: "Video",
  mkv: "Video",
  mp3: "Audio",
  m4a: "Audio",
  aac: "Audio",
  pdf: "Dokumen",
};

function basename(path: string): string {
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || path;
}

function fileStem(name: string): string {
  return name.replace(/\.[a-z0-9]{1,8}$/i, "");
}

function fileExt(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function displayStamp(value: string | null | undefined): string {
  if (!value) return "—";
  const stamp = value.replace("T", " ").replace("Z", "");
  return stamp.slice(0, 16);
}

/** Nama staging hash — jangan ditampilkan utuh ke petugas. */
export function isOpaqueFileName(name: string): boolean {
  const base = basename(name).trim();
  if (!base) return false;
  return HASH_FILE.test(base) || HASH_STEM.test(fileStem(base));
}

/** Label galeri: nama asli bila ada, selain itu jenis + album. */
export function displayMediaName(
  label: string | null | undefined,
  path?: string | null,
  album?: string | null,
): string {
  const raw = (label || "").trim() || basename(path || "");
  if (!raw) return "—";
  if (!isOpaqueFileName(raw)) return raw;
  const fromPath = basename(path || "");
  if (fromPath && fromPath !== raw && !isOpaqueFileName(fromPath)) return fromPath;
  const kind = MEDIA_KIND[fileExt(raw || fromPath)] || "Berkas";
  const place = (album || "").trim();
  return place ? `${kind} · ${place}` : kind;
}

