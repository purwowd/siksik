/** Safe filename segment for macOS/Windows archives (ASCII slug). */
export function slugFilePart(value: string, maxLen = 40): string {
  const cleaned = value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLen)
    .replace(/-+$/g, "");
  return cleaned || "x";
}

function localStamp(d = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${day}_${hh}${mm}`;
}

export type ReportExportIdentity = {
  registration_no?: string | null;
  full_name?: string | null;
};

/** Default basename without extension, e.g. SATRIA_ASN-2026-0304_Dewi-Lestari_2026-08-23_0358 */
export function reportExportBasename(
  sessionId: string,
  identity?: ReportExportIdentity | null,
): string {
  const stamp = localStamp();
  const reg = identity?.registration_no?.trim();
  const name = identity?.full_name?.trim();
  if (reg && name) {
    return `SATRIA_${slugFilePart(reg, 32)}_${slugFilePart(name, 40)}_${stamp}`;
  }
  if (reg) return `SATRIA_${slugFilePart(reg, 32)}_${stamp}`;
  if (name) return `SATRIA_${slugFilePart(name, 40)}_${stamp}`;
  return `SATRIA_tanpa-identitas_${sessionId.slice(0, 8)}_${stamp}`;
}

export function reportExportFilename(
  sessionId: string,
  ext: "pdf" | "html" | "json",
  identity?: ReportExportIdentity | null,
): string {
  return `${reportExportBasename(sessionId, identity)}.${ext}`;
}
