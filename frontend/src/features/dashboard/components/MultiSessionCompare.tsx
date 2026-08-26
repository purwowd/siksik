type Props = {
  lulus: number;
  menunggu: number;
  tidak: number;
  totalSessions: number;
};

export function MultiSessionCompare({ lulus, menunggu, tidak, totalSessions }: Props) {
  const decided = lulus + menunggu + tidak;
  const total = Math.max(1, decided);
  const hasData = decided > 0;

  const bars = [
    { key: "lulus", label: "Lulus", n: lulus, cls: "ok" },
    { key: "menunggu", label: "Perlu dicek", n: menunggu, cls: "warn" },
    { key: "tidak", label: "Tidak lulus", n: tidak, cls: "bad" },
  ];

  return (
    <section
      className={`ent-multi-compare${hasData ? "" : " is-empty"}`}
      aria-label="Perbandingan hasil lintas sesi"
    >
      <div className="ent-multi-head">
        <h3 className="dash-section-title">Proporsi keputusan</h3>
        <p className="dash-section-copy">
          {totalSessions > 0
            ? `${totalSessions} sesi tercatat · ${decided} sudah ada keputusan`
            : "Belum ada sesi selesai"}
        </p>
      </div>

      {hasData ? (
        <div className="ent-multi-bar" role="img" aria-label="Proporsi lulus, menunggu, tidak lulus">
          {bars.map((b) =>
            b.n > 0 ? (
              <span
                key={b.key}
                className={`ent-multi-seg ${b.cls}`}
                style={{ width: `${(b.n / total) * 100}%` }}
                title={`${b.label}: ${b.n}`}
              />
            ) : null,
          )}
        </div>
      ) : (
        <p className="dash-empty">Jalankan pemeriksaan atau pilih sesi untuk mengisi proporsi ini.</p>
      )}

      <div className="ent-multi-legend">
        {bars.map((b) => (
          <div key={b.key} className={`ent-multi-item ${b.cls}`}>
            <strong>{b.n}</strong>
            <span>{b.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
