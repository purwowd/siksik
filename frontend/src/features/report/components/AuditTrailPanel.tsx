import type { SessionSummary } from "@/shared/api/client";

function formatAuditTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  try {
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(t);
  } catch {
    return iso;
  }
}

export function AuditTrailPanel({ session }: { session: SessionSummary | null }) {
  if (!session) return null;

  const p = session.progress;
  const events: { at: string; who: string; what: string; detail?: string }[] = [];

  if (session.created_at) {
    events.push({
      at: session.created_at,
      who: "Sistem",
      what: "Kasus dibuka",
      detail: `${session.label || session.device_id} · mode ${session.mode === "full" ? "Penuh" : "Cepat"}`,
    });
  }
  if (session.status === "completed" && session.updated_at) {
    events.push({
      at: session.updated_at,
      who: "Pipeline",
      what: "Analisa selesai",
      detail: session.recommendation || undefined,
    });
  }
  if (p?.authorized_by) {
    events.push({
      at: p.authorized_at || session.updated_at,
      who: p.authorized_by,
      what: "Rekomendasi disahkan",
      detail: p.authorize_note || undefined,
    });
  }

  if (events.length === 0) return null;

  return (
    <section className="ent-audit-trail" aria-label="Jejak audit kasus">
      <p className="ent-eyebrow">Jejak audit</p>
      <ol className="ent-audit-list">
        {events.map((e, i) => (
          <li key={`${e.at}-${i}`}>
            <time className="mono" dateTime={e.at}>
              {formatAuditTime(e.at)}
            </time>
            <div>
              <strong>{e.what}</strong>
              <span className="ent-audit-who">
                {e.who}
                {e.detail ? ` · ${e.detail}` : ""}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
