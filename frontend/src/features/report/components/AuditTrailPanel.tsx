import { useEffect, useState } from "react";
import { api, type SessionSummary } from "@/shared/api/client";
import { PageLoading } from "@/shared/ui/PageLoading";

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

const ACTION_LABEL: Record<string, string> = {
  session_started: "Pemeriksaan dimulai",
  session_cancelled: "Pemeriksaan dibatalkan",
  finding_reviewed: "Temuan ditinjau",
  findings_bulk_reviewed: "Tinjauan massal",
  report_authorized: "Rekomendasi disahkan",
  report_downloaded: "Laporan diunduh",
  session_wiped: "Staging sesi dihapus",
};

type AuditEvent = {
  at: string;
  who: string;
  what: string;
  detail?: string;
};

function reconstructedEvents(session: SessionSummary): AuditEvent[] {
  const p = session.progress;
  const events: AuditEvent[] = [];
  if (session.created_at) {
    events.push({
      at: session.created_at,
      who: "Sistem",
      what: "Kasus dibuka",
      detail: session.label || session.device_id,
    });
  }
  if (session.status === "completed" && session.updated_at) {
    events.push({
      at: session.updated_at,
      who: "Sistem",
      what: "Analisa selesai",
      detail: session.recommendation || undefined,
    });
  }
  if (p?.authorized_by) {
    events.push({
      at: p.authorized_at || session.updated_at,
      who: p.authorized_by,
      what: "Rekomendasi disahkan",
      detail: p.authorize_note || p.report_sha256 || undefined,
    });
  }
  return events;
}

export function AuditTrailPanel({ session }: { session: SessionSummary | null }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!session?.id) {
      setEvents([]);
      setLoading(false);
      return;
    }
    const snapshot = session;
    let cancelled = false;
    setEvents([]);
    setLoading(true);
    api
      .sessionAudit(snapshot.id)
      .then((rows) => {
        if (cancelled) return;
        if (rows.length) {
          setEvents(
            rows.map((row) => ({
              at: row.created_at,
              who: row.actor,
              what: ACTION_LABEL[row.action] || row.action,
              detail: row.detail || undefined,
            })),
          );
          return;
        }
        setEvents(reconstructedEvents(snapshot));
      })
      .catch(() => {
        if (!cancelled) setEvents(reconstructedEvents(snapshot));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session?.id]);

  if (!session) return null;
  if (loading) {
    return (
      <section className="ent-audit-trail" aria-label="Jejak audit kasus">
        <p className="ent-eyebrow">Jejak audit</p>
        <PageLoading />
      </section>
    );
  }
  if (events.length === 0) return null;

  return (
    <section className="ent-audit-trail" aria-label="Jejak audit kasus">
      <p className="ent-eyebrow">Jejak audit</p>
      {session.progress?.report_sha256 && (
        <p className="ent-audit-hash mono" title={session.progress.report_sha256}>
          SHA-256 laporan: {session.progress.report_sha256.slice(0, 16)}…
          {session.progress.authorized_confirmed_findings != null
            ? ` · ${session.progress.authorized_confirmed_findings} temuan dikonfirmasi`
            : ""}
        </p>
      )}
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
