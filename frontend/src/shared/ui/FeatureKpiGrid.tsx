export type FeatureKpi = {
  label: string;
  value: number | string;
  tone?: "warn" | "bad" | "muted";
};

type Props = {
  items: FeatureKpi[];
  ariaLabel: string;
};

export function FeatureKpiGrid({ items, ariaLabel }: Props) {
  if (items.length === 0) return null;
  return (
    <div className="ent-desk-kpis" aria-label={ariaLabel}>
      {items.map((k) => (
        <div key={k.label} className={`ent-kpi${k.tone ? ` ${k.tone}` : ""}`}>
          <span>{k.label}</span>
          <strong>{k.value}</strong>
        </div>
      ))}
    </div>
  );
}
