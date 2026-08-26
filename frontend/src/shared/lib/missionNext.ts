import { ACTIVE } from "@/shared/constants";
import type { SessionSummary } from "@/shared/api/client";

/** Langkah berikutnya — Bahasa Indonesia. */
export function missionNextAction(
  role: string,
  session: SessionSummary | null,
  pending = 0,
): { kicker: string; action: string } {
  if (!session) {
    if (role === "operator" || role === "admin") {
      return { kicker: "Lanjut", action: "Isi kasus, sambungkan HP, lalu jalankan pemeriksaan" };
    }
    return { kicker: "Lanjut", action: "Pilih kasus di pemilih sesi untuk mulai bekerja" };
  }
  if (session.status === "failed") {
    return { kicker: "Terblokir", action: "Pemeriksaan gagal — batalkan atau ulangi dari Penerimaan" };
  }
  if (session.status === "cancelled") {
    return { kicker: "Siaga", action: "Kasus dibatalkan — mulai pemeriksaan baru bila perlu" };
  }
  if (ACTIVE.has(session.status)) {
    return { kicker: "Berjalan", action: "Jangan cabut kabel — tunggu hingga pengambilan selesai" };
  }
  if (session.status === "completed") {
    if (pending > 0 && (role === "analis" || role === "admin")) {
      return { kicker: "Tinjau", action: `${pending} temuan menunggu konfirmasi analis` };
    }
    if (pending > 0) {
      return { kicker: "Tahan", action: "Menunggu analis menyelesaikan antrean temuan" };
    }
    if (!session.progress?.authorized_at && (role === "pimpinan" || role === "admin")) {
      return { kicker: "Keputusan", action: "Sahkan laporan PDF di tab Laporan" };
    }
    if (session.progress?.authorized_at) {
      return { kicker: "Selesai", action: "Laporan disahkan — unduh PDF bila diperlukan" };
    }
    return { kicker: "Siap", action: "Tinjauan selesai — lanjut ke Laporan" };
  }
  return { kicker: "Lanjut", action: "Lanjutkan alur kasus pada tab yang terbuka" };
}
