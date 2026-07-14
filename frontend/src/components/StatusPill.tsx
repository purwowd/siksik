import { ACTIVE } from "../constants";

export function StatusPill({
  status,
  recommendation,
}: {
  status: string;
  recommendation?: string | null;
}) {
  if (recommendation === "LULUS") return <span className="pill ok">LULUS</span>;
  if (recommendation === "TIDAK LULUS") return <span className="pill bad">TIDAK LULUS</span>;
  if (status === "completed") return <span className="pill ok">{status}</span>;
  if (status === "failed") return <span className="pill bad">{status}</span>;
  if (ACTIVE.has(status)) return <span className="pill warn">{status}</span>;
  return <span className="pill muted">{status}</span>;
}
