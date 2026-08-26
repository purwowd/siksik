import type { SessionSummary } from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import { caseChecklist } from "@/shared/lib/caseChecklist";
import { humanProgressMessage } from "@/shared/lib/humanProgress";
import { missionNextAction } from "@/shared/lib/missionNext";
import { sessionStatusLabel } from "@/shared/lib/sessionStatus";

type Props = {
  session: SessionSummary | null;
  role: string;
  pending?: number;
  loading?: boolean;
};

export function CaseFlowBar({ session, role, pending = 0, loading = false }: Props) {
  const live = !!session && ACTIVE.has(session.status);
  const next = loading && session
    ? { kicker: "Memuat", action: "Mengambil data kasus…" }
    : missionNextAction(role, session, pending);
  const checks = caseChecklist(session, pending);
  const subject = session
    ? session.participant?.full_name
      ? `${session.participant.full_name}${
          session.participant.registration_no ? ` · ${session.participant.registration_no}` : ""
        }`
      : session.label || "Kasus aktif"
    : "Tidak ada kasus aktif";
  const statusLine = session
    ? humanProgressMessage(session.progress?.message) || sessionStatusLabel(session.status)
    : "Satu sesi per mesin";

  return (
    <section className={`ent-case-flow${live ? " is-live" : ""}`} aria-label="Alur kasus">
      <div className="ent-case-id">
        <p className="ent-case-title">
          {live ? <span className="ent-live-dot" aria-hidden /> : null}
          {subject}
        </p>
        <p className="ent-case-msg">{statusLine}</p>
      </div>
      <ol className="ent-case-checks" aria-label="Kelengkapan kasus">
        {checks.map((item) => (
          <li key={item.id} className={item.done ? "on" : ""}>
            <span aria-hidden>{item.done ? "●" : "○"}</span>
            {item.label}
          </li>
        ))}
      </ol>
      <p className="ent-case-next" role="status">
        <span className="ent-case-next-kicker">{next.kicker}</span>
        {next.action}
        {session?.recommendation ? (
          <span className="ent-meta-chip rec">{session.recommendation}</span>
        ) : null}
      </p>
    </section>
  );
}
