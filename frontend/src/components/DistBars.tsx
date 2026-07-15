export function DistBars({
  title,
  subtitle,
  items,
  emptyHint = "Belum ada data",
  tone = "default",
}: {
  title: string;
  subtitle?: string;
  items?: { name: string; count: number }[] | null;
  emptyHint?: string;
  tone?: "default" | "danger" | "amber";
}) {
  const list = items ?? [];
  const max = Math.max(1, ...list.map((i) => i.count));
  const total = list.reduce((s, i) => s + i.count, 0);
  return (
    <div className={`dist-card dist-${tone}`}>
      <h3>{title}</h3>
      {subtitle && <p className="dist-subtitle">{subtitle}</p>}
      {list.length === 0 ? (
        <div className="empty empty-soft">{emptyHint}</div>
      ) : (
        <div className="dist-list">
          {list.map((i) => (
            <div key={i.name} className="dist-row">
              <div className="dist-meta">
                <span>{i.name}</span>
                <strong>
                  {i.count}
                  {total > 0 && (
                    <span className="dist-pct"> · {Math.round((i.count / total) * 100)}%</span>
                  )}
                </strong>
              </div>
              <div className="dist-bar">
                <span style={{ width: `${(i.count / max) * 100}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
