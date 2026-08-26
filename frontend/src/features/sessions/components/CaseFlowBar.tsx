/** Bilah alur kasus — stepper Satria + checklist aditif + chip crawl. */
import { WorkflowStepper } from "./WorkflowStepper";
import type { SessionSummary } from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import { caseChecklist } from "@/shared/lib/caseChecklist";
import { missionNextAction } from "@/shared/lib/missionNext";

type Props = {
  session: SessionSummary | null;
  role?: string;
  pending?: number;
};

export function CaseFlowBar({ session, role = "operator", pending = 0 }: Props) {
  const live = !!session && ACTIVE.has(session.status);
  const pct = Math.max(0, Math.min(100, session?.progress?.percent ?? 0));
  const scope = session?.progress?.crawl_scope?.replaceAll("_", " ");
  const attempt = session?.progress?.crawl_attempt;
  const attemptState = session?.progress?.crawl_attempt_state;
  const socialCrawlActive = session?.progress?.crawl_state === "social_automation";
  const checks = caseChecklist(session, pending);
  const next = missionNextAction(role, session, pending);

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
          <p className="ent-case-next" role="status">
            <span className="ent-case-next-kicker">{next.kicker}</span> {next.action}
          </p>
        </div>
        <div className="ent-case-meta-right">
          {session ? (
            <>
              <span className="ent-meta-chip">{session.mode === "full" ? "PENUH" : "CEPAT"}</span>
              {session.progress?.analysis_scope && (
                <span className="ent-meta-chip">
                  {session.progress.analysis_scope === "device"
                    ? "HP"
                    : session.progress.analysis_scope === "social"
                      ? "Sosmed"
                      : "Gabungan"}
                </span>
              )}
              <span className="ent-meta-chip mono">{session.id.slice(0, 8)}</span>
              {session.recommendation && (
                <span className="ent-meta-chip rec">{session.recommendation}</span>
              )}
              {live && (
                <span className="ent-meta-chip pct" title="Progres pipeline">
                  {pct.toFixed(0)}%
                </span>
              )}
              {live && socialCrawlActive && scope && (
                <span className="ent-meta-chip" title="Scope crawl sosial aktif">
                  {scope}
                </span>
              )}
              {live && socialCrawlActive && attemptState && (
                <span className="ent-meta-chip mono" title="State percobaan crawl sosial">
                  {attempt && attempt > 0 ? `#${attempt} · ` : ""}
                  {attemptState}
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
      <ol className="ent-case-checks" aria-label="Kelengkapan kasus">
        {checks.map((item) => (
          <li key={item.id} className={item.done ? "on" : ""}>
            <span aria-hidden>{item.done ? "●" : "○"}</span>
            {item.label}
          </li>
        ))}
      </ol>
      <WorkflowStepper session={session} />
    </section>
  );
}
