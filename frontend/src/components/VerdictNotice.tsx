import { REC_LULUS, REC_MENUNGGU_REVIEW, REC_TIDAK_LULUS } from "../constants";

type Props = {
  recommendation?: string | null;
};

/** Tiga status rekomendasi sesi setelah analisa selesai. */
export function VerdictNotice({ recommendation }: Props) {
  if (recommendation === REC_TIDAK_LULUS) {
    return <div className="verdict fail">Rekomendasi · Tidak lulus</div>;
  }
  if (recommendation === REC_MENUNGGU_REVIEW) {
    return (
      <div className="verdict pending">
        Rekomendasi · Menunggu review
        <p className="verdict-hint">
          Ada temuan yang belum diverifikasi. Tekan <strong>Konfirmasi</strong> atau{" "}
          <strong>Tolak</strong> pada setiap temuan sebelum keputusan akhir.
        </p>
      </div>
    );
  }
  if (recommendation === REC_LULUS) {
    return <div className="verdict">Rekomendasi · Lulus</div>;
  }
  return null;
}
