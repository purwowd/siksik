type Props = {
  label?: string;
};

/** Loading penuh halaman — ganti body, jangan tampilkan data sesi lama. */
export function PageLoading({ label = "Memuat…" }: Props) {
  return (
    <div className="page-loading" role="status" aria-busy="true" aria-live="polite">
      {label}
    </div>
  );
}
