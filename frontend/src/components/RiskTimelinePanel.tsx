import type { RiskTimeline } from "../api";

const TREND_LABEL: Record<string, string> = {
  improved: "Situasi mereda",
  improving: "Cenderung menurun",
  elevated: "Masih tinggi — perlu perhatian",
  stable: "Stabil dari tahun ke tahun",
  unknown: "Data belum cukup",
};

const TREND_HINT: Record<string, string> = {
  improved: "Jumlah konten berisiko di galeri menurun dibanding tahun sebelumnya.",
  improving: "Tren konten berisiko cenderung turun.",
  elevated: "Konten berisiko masih tinggi di tahun berjalan.",
  stable: "Pola dari tahun ke tahun relatif sama.",
  unknown: "Belum cukup foto bertanggal untuk membaca tren.",
};

export function RiskTimelinePanel({
  timeline,
  sessionLabel,
}: {
  timeline: RiskTimeline;
  sessionLabel?: string | null;
}) {
  const max = Math.max(1, ...timeline.series.map((s) => s.total));
  const trend = timeline.trend || "unknown";

  return (
    <div className="timeline-panel">
      <div className="dash-section-head">
        <div>
          <h3 className="dash-section-title">Riwayat konten berisiko · {timeline.years_back} tahun</h3>
          <p className="dash-section-copy">
            Jumlah indikasi di galeri per tahun, berdasarkan tanggal foto (bukan tanggal analisa).
          </p>
        </div>
      </div>

      {sessionLabel && (
        <p className="timeline-session">
          Fokus perangkat: <strong>{sessionLabel}</strong>
        </p>
      )}

      <div className={`timeline-insight trend-${trend}`}>
        <span className="pill warn">{TREND_LABEL[trend] || trend}</span>
        <p>{timeline.insight || TREND_HINT[trend]}</p>
      </div>

      <div className="year-bars">
        {timeline.series.map((s) => (
          <div key={s.year} className="year-bar-row">
            <div className="year-bar-label">{s.year}</div>
            <div className="year-bar-track">
              <span style={{ width: `${(s.total / max) * 100}%` }} />
            </div>
            <div className="year-bar-count">
              {s.total === 0 ? "bersih" : `${s.total} indikasi`}
            </div>
            <div className="year-bar-cats">
              {s.by_category.slice(0, 3).map((c) => (
                <span key={c.name} className="pill muted">
                  {c.name.replace(/_/g, " ")} · {c.count}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      <p className="timeline-foot">
        {timeline.peak_year
          ? `Puncak di ${timeline.peak_year} (${timeline.peak_count} indikasi). `
          : ""}
        Tahun ini: {timeline.current_year_count} indikasi.
        {timeline.unknown_date > 0
          ? ` ${timeline.unknown_date} media tanpa tanggal jelas diabaikan dari grafik.`
          : ""}
      </p>
    </div>
  );
}
