/** Pesan progres/error untuk petugas panitia — tanpa ADB/APK/agent. */
export function humanProgressMessage(raw?: string | null): string {
  const text = (raw || "").trim();
  if (!text) return "";
  let out = text;
  const replacements: [RegExp, string][] = [
    [/Build APK Android agent terbaru/gi, "Menyiapkan aplikasi SATRIA di HP"],
    [/Build APK agent gagal\.?/gi, "Gagal menyiapkan aplikasi SATRIA di HP"],
    [/Build APK agent sedang berjalan\.?/gi, "Menyiapkan aplikasi SATRIA di HP"],
    [/Memeriksa package Android agent/gi, "Memeriksa aplikasi SATRIA di HP"],
    [/Memasang Android agent terbaru ke perangkat/gi, "Memasang aplikasi SATRIA di HP"],
    [/Menerapkan izin runtime Android agent/gi, "Mengatur izin di HP"],
    [/Memverifikasi special access Android/gi, "Memeriksa izin lanjutan di HP"],
    [/Menjalankan Android agent/gi, "Menjalankan aplikasi SATRIA di HP"],
    [/Membuat koneksi ADB lokal/gi, "Menghubungkan HP ke konsol"],
    [/Memverifikasi sesi Android agent/gi, "Memverifikasi koneksi HP"],
    [/Persiapan Android agent gagal/gi, "Persiapan aplikasi di HP gagal"],
    [/Persiapan Android agent dibatalkan/gi, "Persiapan aplikasi di HP dibatalkan"],
    [/Sesi Android agent ditutup/gi, "Koneksi HP ditutup"],
    [/Android agent siap/gi, "Aplikasi SATRIA di HP siap"],
    [/Akuisisi Android selesai/gi, "Pengambilan data HP selesai"],
    [/Selection candidate dikonfirmasi/gi, "Berkas terpilih dikonfirmasi"],
    [/Selection candidate selesai/gi, "Seleksi berkas selesai"],
    [/Menyeleksi candidate secara lokal di Android/gi, "Menyeleksi berkas di HP"],
    [/Preprocess dan selection Android selesai/gi, "Seleksi berkas HP selesai"],
    [/Preprocessing lokal Android selesai/gi, "Pra-proses di HP selesai"],
    [/Android agent/gi, "aplikasi SATRIA di HP"],
    [/\badb pull\b/gi, "pengambilan USB"],
    [/\bAPK\b/g, "aplikasi"],
    [/\bADB\b/g, "USB"],
    [/\bpipeline\b/gi, "pemeriksaan"],
    [/\bmsgstore\.db\b/gi, "data percakapan"],
  ];
  for (const [pattern, label] of replacements) {
    out = out.replace(pattern, label);
  }
  return out;
}
