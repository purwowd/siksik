import { WorkflowStepper } from "./WorkflowStepper";
import type { SessionSummary } from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";

/** Bilah alur kasus global — petugas selalu tahu tahap proses. */
export function CaseFlowBar({ session }: { session: SessionSummary | null }) {
  const live = !!session && ACTIVE.has(session.status);
  const pct = Math.max(0, Math.min(100, session?.progress?.percent ?? 0));

  return (
    <section className={`ent-case-flow ent-glass${live ? " is-live" : ""}`} aria-label="Alur kasus">
      <div className="ent-case-flow-meta">
        <div className="ent-case-identity">
          <p className="ent-eyebrow">
            {live ? (
              <>
                <span className="ent-live-dot" aria-hidden />
                Kasus berjalan
              </>
            ) : (
              "Alur kerja kasus"
            )}
          </p>
          <h2 className="ent-case-title">
            {session ? (
              session.participant?.full_name ? (
                <>
                  {session.participant.full_name}
                  {session.participant.registration_no
                    ? ` · ${session.participant.registration_no}`
                    : ""}
                </>
              ) : (
                session.label || session.device_id
              )
            ) : (
              "Belum ada kasus aktif"
            )}
          </h2>
          {session?.progress?.message && (
            <p className="ent-case-msg">{session.progress.message}</p>
          )}
        </div>
        <div className="ent-case-meta-right">
          {session ? (
            <>
              <span className="ent-meta-chip">{session.mode === "full" ? "PENUH" : "CEPAT"}</span>
              <span className="ent-meta-chip mono">{session.id.slice(0, 8)}</span>
              {session.recommendation && (
                <span className="ent-meta-chip rec">{session.recommendation}</span>
              )}
              {live && (
                <span className="ent-meta-chip pct" title="Progres pipeline">
                  {pct.toFixed(0)}%
                </span>
              )}
            </>
          ) : (
            <span className="ent-meta-chip muted">Pilih atau mulai akuisisi</span>
          )}
        </div>
      </div>
      {live && (
        <div className="ent-case-progress" aria-hidden>
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      <WorkflowStepper session={session} />
    </section>
  );
}
