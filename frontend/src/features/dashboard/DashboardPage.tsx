import { useMemo } from "react";
import {
  ms,
  type DashboardStats,
  type Finding,
  type Paginated,
  type SessionSummary,
} from "@/shared/api/client";
import { DistBars } from "@/features/dashboard/components/DistBars";
import { FeaturePageShell } from "@/shared/ui/FeaturePageShell";
import { isContentLoading } from "@/shared/lib/pageLoad";
import { FeatureKpiGrid } from "@/shared/ui/FeatureKpiGrid";
import { RiskTimelinePanel } from "@/features/dashboard/components/RiskTimelinePanel";
import { StatusPill } from "@/shared/ui/StatusPill";
import { AnalysisColumn } from "@/features/dashboard/components/AnalysisColumn";
import { DashboardSessionStrip } from "@/features/dashboard/components/DashboardSessionStrip";
import { IntegrityScorePanel } from "@/features/dashboard/components/IntegrityScorePanel";
import { MultiSessionCompare } from "@/features/dashboard/components/MultiSessionCompare";
import { humanLabel, mapNamedCounts, methodSummary } from "@/features/dashboard/lib/dashboardLabels";
import { buildAnalysisModules } from "@/features/dashboard/lib/satriaModules";
import { FEATURE_EMPTY_NO_SESSION, FEATURE_PAGE_META } from "@/shared/lib/featurePages";
import type { ModuleFilterParam } from "@/app/routes";
import { Pagination } from "@/shared/ui/Pagination";
import type { Tab } from "@/shared/types";

type Props = {
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  onPickSession: (id: string) => void;
  dash: DashboardStats | null;
  dashLoading?: boolean;
  dashSessions: Paginated<SessionSummary> | null;
  dashFindings: Paginated<Finding> | null;
  setDashSessionsPage: (p: number) => void;
  setDashFindingsPage: (p: number) => void;
  openSession: (id: string, tab: Tab) => void;
  onModuleDrillDown?: (modul: ModuleFilterParam) => void;
  showLabDiagnostics?: boolean;
};

export function DashboardPage({
  session,
  sessionList,
  sessionsLoading,
  onPickSession,
  dash,
  dashLoading = false,
  dashSessions,
  dashFindings,
  setDashSessionsPage,
  setDashFindingsPage,
  openSession,
  onModuleDrillDown,
  showLabDiagnostics = false,
}: Props) {
  const byCategory = useMemo(
    () => mapNamedCounts("category", dash?.findings_by_category),
    [dash?.findings_by_category],
  );
  const bySource = useMemo(
    () => mapNamedCounts("source", dash?.findings_by_source),
    [dash?.findings_by_source],
  );
  const byLayer = useMemo(
    () => mapNamedCounts("layer", dash?.findings_by_layer),
    [dash?.findings_by_layer],
  );
  const byMethod = useMemo(
    () => mapNamedCounts("method", dash?.acquisition_methods),
    [dash?.acquisition_methods],
  );

  const modules = useMemo(
    () =>
      buildAnalysisModules({
        session,
        dash,
        findings: dashFindings?.items,
      }),
    [session, dash, dashFindings?.items],
  );

  const lulus = dash?.lulus_count ?? 0;
  const menunggu = dash?.menunggu_review_count ?? 0;
  const tidak = dash?.tidak_lulus_count ?? 0;
  const empty = FEATURE_EMPTY_NO_SESSION.dashboard;
  const loading = isContentLoading(dashLoading, dash);

  return (
    <FeaturePageShell
      meta={FEATURE_PAGE_META.dashboard}
      panelClass="dash-panel satria-dash"
      loading={loading}
      kpis={
        dash && !loading ? (
          <FeatureKpiGrid
            ariaLabel="Ringkasan"
            items={[
              { label: "Perlu dicek", value: menunggu, tone: menunggu > 0 ? "warn" : undefined },
              { label: "Tidak lulus", value: tidak, tone: tidak > 0 ? "bad" : undefined },
              { label: "Lulus", value: lulus, tone: "muted" },
              {
                label: "Antrean review",
                value: dash.pending_reviews ?? 0,
                tone: (dash.pending_reviews ?? 0) > 0 ? "warn" : undefined,
              },
            ]}
          />
        ) : undefined
      }
      session={{
        sessionList,
        sessionId: session?.id ?? null,
        sessionsLoading,
        onPickSession,
      }}
    >
      {session ? (
        <DashboardSessionStrip session={session} onOpen={(tab) => openSession(session.id, tab)} />
      ) : (
        <p className="dash-pick-hint" role="status">
          {empty.hint}
        </p>
      )}

      {!dash ? null : (
        <div className="dash-body">
          <section className="dash-block dash-priority" aria-labelledby="dash-actions-heading">
            <div className="dash-section-head">
              <div>
                <h2 id="dash-actions-heading" className="dash-section-title">
                  Prioritas hari ini
                </h2>
                <p className="dash-section-copy">Langsung ke antrean atau sesi bermasalah.</p>
              </div>
            </div>
            <div className="dash-action-grid">
              <button
                type="button"
                className="dash-action-card warn"
                disabled={menunggu === 0 && (dash.pending_reviews ?? 0) === 0}
                onClick={() => {
                  const sid =
                    dashSessions?.items.find((s) => s.recommendation === "MENUNGGU REVIEW")?.id ||
                    session?.id;
                  if (sid) openSession(sid, "findings");
                }}
              >
                <span className="dash-action-label">Perlu dicek analis</span>
                <strong className="dash-action-value">{menunggu}</strong>
                <span className="dash-action-hint">
                  {dash.pending_reviews ?? 0} temuan menunggu konfirmasi
                </span>
              </button>
              <button
                type="button"
                className="dash-action-card bad"
                disabled={tidak === 0}
                onClick={() => {
                  const sid =
                    dashSessions?.items.find((s) => s.recommendation === "TIDAK LULUS")?.id ||
                    session?.id;
                  if (sid) openSession(sid, "report");
                }}
              >
                <span className="dash-action-label">Tidak lulus</span>
                <strong className="dash-action-value">{tidak}</strong>
                <span className="dash-action-hint">Buka laporan sesi bermasalah</span>
              </button>
              <div className="dash-action-card ok">
                <span className="dash-action-label">Lulus / aman</span>
                <strong className="dash-action-value">{lulus}</strong>
                <span className="dash-action-hint">Sesi bersih atau temuan ditolak</span>
              </div>
              <div className="dash-action-card muted">
                <span className="dash-action-label">Perangkat diproses</span>
                <strong className="dash-action-value">
                  {dash.completed_sessions ?? 0}
                  <span className="dash-action-sub">/{dash.total_sessions ?? 0}</span>
                </strong>
                <span className="dash-action-hint">
                  {dash.active_sessions ?? 0} berjalan · {dash.failed_sessions ?? 0} gagal
                </span>
              </div>
            </div>
          </section>

          <div className={`dash-overview-row${session ? "" : " dash-overview-row--solo"}`}>
            {session && <IntegrityScorePanel recommendation={session.recommendation} />}
            <MultiSessionCompare
              lulus={lulus}
              menunggu={menunggu}
              tidak={tidak}
              totalSessions={dash.total_sessions ?? 0}
            />
          </div>

          <section className="dash-block dash-modules" aria-labelledby="dash-modules-heading">
            <div className="dash-section-head">
              <div>
                <h2 id="dash-modules-heading" className="dash-section-title">
                  Modul analisis
                </h2>
                <p className="dash-section-copy">
                  {session
                    ? "Ringkasan per sumber untuk sesi aktif — modul planned ditandai jujur."
                    : "Pilih sesi untuk metrik per-kasus; angka agregat tetap tampil."}
                </p>
              </div>
            </div>
            <div className="satria-cols" role="list">
              {modules.map((card) => (
                <AnalysisColumn
                  key={card.id}
                  card={card}
                  onDrillDown={
                    card.drillDown && onModuleDrillDown && session
                      ? (id) => onModuleDrillDown(id as ModuleFilterParam)
                      : undefined
                  }
                />
              ))}
            </div>
          </section>

          <div className="dash-split-row dash-analytics">
            <DistBars
              title="Jenis konten berisiko"
              subtitle="Indikasi paling sering muncul"
              items={byCategory}
              tone="danger"
              emptyHint="Belum ada temuan terkategorisasi"
            />
            <DistBars
              title="Asal media"
              subtitle="Foto, video, dokumen, unduhan"
              items={bySource}
              emptyHint="Belum ada data sumber media"
            />

            <aside className="dash-feed dash-split-side">
              <div className="dash-section-head">
                <div>
                  <h3 className="dash-section-title">Indikasi terbaru</h3>
                  <p className="dash-section-copy">Klik baris untuk buka Temuan sesi terkait.</p>
                </div>
              </div>
              {!dashFindings || dashFindings.total === 0 ? (
                <p className="dash-empty">Belum ada indikasi — temuan akan muncul setelah analisa selesai.</p>
              ) : (
                <>
                  <div className="recent-list recent-list-rich">
                    {dashFindings.items.map((f) => (
                      <button
                        key={f.id}
                        type="button"
                        className="recent-item clickable recent-rich"
                        onClick={() => openSession(f.session_id, "findings")}
                      >
                        <div className="recent-rich-main">
                          <strong className="finding-label">{f.label}</strong>
                          <span className="finding-meta">
                            {humanLabel("source", f.source)} · keyakinan{" "}
                            {(f.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                        <span
                          className={`pill ${
                            f.review_status === "confirmed"
                              ? "bad"
                              : f.review_status === "pending"
                                ? "warn"
                                : "muted"
                          }`}
                        >
                          {humanLabel("review", f.review_status)}
                        </span>
                      </button>
                    ))}
                  </div>
                  <Pagination
                    page={dashFindings.page}
                    pages={dashFindings.pages}
                    total={dashFindings.total}
                    page_size={dashFindings.page_size}
                    onPage={setDashFindingsPage}
                    label="Indikasi"
                  />
                </>
              )}
            </aside>
          </div>

          <div className="dash-split-row dash-bottom">
            <div className="dash-bottom-timeline">
              {dash.risk_timeline ? (
                <RiskTimelinePanel
                  timeline={dash.risk_timeline}
                  sessionLabel={dash.timeline_session_label || session?.label}
                />
              ) : (
                <div className="dash-panel-slot">
                  <div className="dash-section-head">
                    <div>
                      <h3 className="dash-section-title">Riwayat risiko</h3>
                      <p className="dash-section-copy">Tren indikasi 5 tahun per sesi.</p>
                    </div>
                  </div>
                  <p className="dash-empty">
                    {session
                      ? "Belum ada riwayat risiko 5 tahun untuk sesi ini"
                      : "Pilih sesi untuk melihat riwayat risiko"}
                  </p>
                </div>
              )}
            </div>
            <div className="dash-bottom-sessions">
              <div className="dash-section-head">
                <div>
                  <h3 className="dash-section-title">Sesi terakhir</h3>
                  <p className="dash-section-copy">Keputusan akhir dan pintasan navigasi.</p>
                </div>
              </div>
              {!dashSessions || dashSessions.total === 0 ? (
                <p className="dash-empty">Belum ada sesi — mulai dari tab Penerimaan.</p>
              ) : (
                <>
                  <div className="dash-session-list">
                    {dashSessions.items.map((s) => (
                      <article
                        key={s.id}
                        className={`dash-session-row${session?.id === s.id ? " active" : ""}`}
                      >
                        <div className="dash-session-main">
                          <strong className="finding-label">{s.label}</strong>
                          <span
                            className="finding-meta"
                            title={humanLabel("method", s.progress?.acquisition_method || "unknown")}
                          >
                            {methodSummary(s.progress?.acquisition_method || "unknown")} ·{" "}
                            {s.progress?.findings_count ?? 0} temuan ·{" "}
                            {ms(s.timing?.t_total_ms ?? 0)}
                          </span>
                        </div>
                        <StatusPill status={s.status} recommendation={s.recommendation} />
                        <div className="row-actions">
                          <button type="button" onClick={() => openSession(s.id, "findings")}>
                            Temuan
                          </button>
                          <button type="button" onClick={() => openSession(s.id, "report")}>
                            Laporan
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                  <Pagination
                    page={dashSessions.page}
                    pages={dashSessions.pages}
                    total={dashSessions.total}
                    page_size={dashSessions.page_size}
                    onPage={setDashSessionsPage}
                    label="Sesi"
                  />
                </>
              )}
            </div>
          </div>

          {showLabDiagnostics && (
          <details className="dash-tech-collapse">
            <summary>Rincian teknis</summary>
            <div className="grid-3 grid-spaced">
              <DistBars
                title="Lapisan analisa"
                subtitle="Jenis deteksi"
                items={byLayer}
                emptyHint="Belum ada data layer"
              />
              <DistBars
                title="Metode pengambilan"
                subtitle="USB atau arsip"
                items={byMethod}
                emptyHint="Belum ada metode tercatat"
              />
              <div className="dist-card">
                <h3>Kesiapan alat</h3>
                <p className="dist-subtitle">Status perangkat lunak host</p>
                <div className="tool-pills tool-pills-col">
                  <span className={`pill ${dash.toolchain?.adb ? "ok" : "muted"}`}>
                    USB Android {dash.toolchain?.adb ? "siap" : "tidak aktif"}
                  </span>
                  <span className={`pill ${dash.toolchain?.idevice_id ? "ok" : "muted"}`}>
                    USB iPhone {dash.toolchain?.idevice_id ? "siap" : "tidak aktif"}
                  </span>
                  <span className={`pill ${dash.gpu_available ? "ok" : "muted"}`}>
                    GPU {dash.gpu_available ? "aktif" : "CPU"}
                  </span>
                </div>
              </div>
            </div>
          </details>
          )}
        </div>
      )}
    </FeaturePageShell>
  );
}
