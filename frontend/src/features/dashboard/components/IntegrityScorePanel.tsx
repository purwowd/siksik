import {
  recommendationsForLevel,
  riskFromRecommendation,
  type RiskLevel,
} from "@/features/dashboard/lib/satriaModules";

export function IntegrityScorePanel({
  recommendation,
}: {
  recommendation?: string | null;
}) {
  const risk = riskFromRecommendation(recommendation);
  const actions = recommendationsForLevel(risk.level);
  const tone = levelTone(risk.level);

  return (
    <section className={`satria-score tone-${tone}`} aria-label="Skor integritas">
      <div className="satria-score-main">
        <p className="satria-score-kicker">Skor resiko integritas keseluruhan</p>
        <div className="satria-score-row">
          <strong className="satria-score-pct">{risk.scorePct}%</strong>
          <span className={`pill ${tone}`}>{risk.level}</span>
        </div>
        <p className="satria-score-statement">{risk.statement}</p>
        {recommendation && (
          <p className="satria-score-rec">
            Rekomendasi sesi: <strong>{recommendation}</strong>
          </p>
        )}
      </div>
      <div className="satria-score-actions">
        <h3>Rekomendasi petugas</h3>
        <ul>
          {actions.map((a) => (
            <li key={a.id} className={a.active ? "active" : "muted"}>
              <span className="satria-check" aria-hidden>
                {a.active ? "●" : "○"}
              </span>
              {a.label}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function levelTone(level: RiskLevel): string {
  if (level === "Tinggi") return "danger";
  if (level === "Sedang") return "warn";
  return "ok";
}
