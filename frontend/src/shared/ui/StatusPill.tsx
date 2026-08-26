import { ACTIVE, REC_LULUS, REC_MENUNGGU_REVIEW, REC_TIDAK_LULUS } from "@/shared/constants";
import { sessionStatusLabel } from "@/shared/lib/sessionStatus";

export function StatusPill({
  status,
  recommendation,
}: {
  status: string;
  recommendation?: string | null;
}) {
  if (ACTIVE.has(status)) {
    return <span className="pill warn">{sessionStatusLabel(status)}</span>;
  }
  if (status === "completed") {
    if (recommendation === REC_LULUS) return <span className="pill ok">LULUS</span>;
    if (recommendation === REC_TIDAK_LULUS) {
      return <span className="pill bad">TIDAK LULUS</span>;
    }
    if (recommendation === REC_MENUNGGU_REVIEW) {
      return <span className="pill warn">MENUNGGU REVIEW</span>;
    }
    return <span className="pill ok">{sessionStatusLabel(status)}</span>;
  }
  if (recommendation === REC_LULUS) return <span className="pill ok">LULUS</span>;
  if (recommendation === REC_TIDAK_LULUS) return <span className="pill bad">TIDAK LULUS</span>;
  if (recommendation === REC_MENUNGGU_REVIEW) {
    return <span className="pill warn">MENUNGGU REVIEW</span>;
  }
  if (status === "failed") return <span className="pill bad">{sessionStatusLabel(status)}</span>;
  return <span className="pill muted">{sessionStatusLabel(status)}</span>;
}
