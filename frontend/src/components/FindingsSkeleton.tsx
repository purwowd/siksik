export function FindingsSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="findings-skeleton" aria-busy="true" aria-label="Memuat temuan">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="finding-skel-card">
          <div className="skel-block skel-media" />
          <div className="skel-lines">
            <div className="skel-block skel-title" />
            <div className="skel-block skel-line" />
            <div className="skel-block skel-line short" />
          </div>
        </div>
      ))}
    </div>
  );
}
