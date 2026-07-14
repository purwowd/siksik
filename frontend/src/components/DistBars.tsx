export function DistBars({
  title,
  items,
}: {
  title: string;
  items?: { name: string; count: number }[] | null;
}) {
  const list = items ?? [];
  const max = Math.max(1, ...list.map((i) => i.count));
  return (
    <div className="dist-card">
      <h3>{title}</h3>
      {list.length === 0 ? (
        <div className="empty" style={{ padding: 12 }}>
          —
        </div>
      ) : (
        <div className="dist-list">
          {list.map((i) => (
            <div key={i.name} className="dist-row">
              <div className="dist-meta">
                <span>{i.name}</span>
                <strong>{i.count}</strong>
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
