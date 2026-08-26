const STATUS_LABELS: Record<string, string> = {
  pending: "Menunggu",
  detecting: "Mendeteksi perangkat",
  preparing_agent: "Menyiapkan aplikasi di HP",
  awaiting_access: "Menunggu izin HP",
  acquiring: "Mengambil data",
  selecting: "Menyeleksi berkas",
  awaiting_review: "Menunggu tinjauan seleksi",
  indexing: "Mengindeks data",
  analyzing: "Menganalisa",
  completed: "Selesai",
  failed: "Gagal",
  cancelled: "Dibatalkan",
};

export function sessionStatusLabel(status: string | null | undefined): string {
  const key = (status || "").trim();
  return STATUS_LABELS[key] || key || "—";
}
