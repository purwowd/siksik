import type { AnalysisModuleCard } from "@/features/dashboard/lib/satriaModules";

type Props = {
  card: AnalysisModuleCard;
  onDrillDown?: (moduleId: string) => void;
};

export function AnalysisColumn({ card, onDrillDown }: Props) {
  const locked = card.availability === "planned" || card.availability === "unavailable";
  const canDrill = !locked && card.availability === "live" && onDrillDown;

  return (
    <article
      className={`satria-col availability-${card.availability}${locked ? " is-locked" : ""}`}
    >
      <header className="satria-col-head">
        <h3>{card.title}</h3>
        <p>{card.subtitle}</p>
        <span className={`satria-avail pill ${card.availability}`}>{card.availabilityLabel}</span>
      </header>
      <ul className="satria-metrics">
        {card.metrics.map((m) => (
          <li key={m.label}>
            <span>{m.label}</span>
            <strong>{m.value}</strong>
          </li>
        ))}
      </ul>
      {card.notes && card.notes.length > 0 && (
        <ul className="satria-notes">
          {card.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {canDrill && (
        <button
          type="button"
          className="btn btn-ghost btn-sm satria-drill-btn"
          onClick={() => onDrillDown?.(card.id)}
        >
          Lihat temuan modul
        </button>
      )}
    </article>
  );
}
