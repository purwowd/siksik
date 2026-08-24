import { ms, type SessionSummary } from "@/shared/api/client";
import { StatusPill } from "@/shared/ui/StatusPill";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import type { Tab } from "@/shared/types";

type Props = {
  session: SessionSummary;
  onOpen: (tab: Tab) => void;
};

export function DashboardSessionStrip({ session, onOpen }: Props) {
  const progress = session.progress;
  const findings = progress?.findings_count ?? 0;

  return (
    <section className="dash-session-strip" aria-label="Sesi aktif">
      <div className="dash-session-strip-main">
        <StatusPill status={session.status} recommendation={session.recommendation} />
        <div>
          <strong className="dash-session-strip-title">{session.label || session.device_id}</strong>
          <span className="dash-session-strip-meta">
            {humanLabel("method", progress?.acquisition_method || "unknown")} ·{" "}
            {session.mode === "full" ? "Penuh" : "Cepat"} · {findings} temuan
            {session.timing?.t_total_ms ? ` · ${ms(session.timing.t_total_ms)}` : ""}
          </span>
        </div>
      </div>
      <div className="dash-session-strip-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => onOpen("findings")}>
          Temuan
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpen("report")}>
          Laporan
        </button>
      </div>
    </section>
  );
}
