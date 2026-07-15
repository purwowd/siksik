import { useMemo } from "react";
import {
  ms,
  type DashboardStats,
  type Finding,
  type Paginated,
  type SessionSummary,
} from "../api";
import { DistBars } from "../components/DistBars";
import { PanelTitle } from "../components/PanelTitle";
import { RiskTimelinePanel } from "../components/RiskTimelinePanel";
import { SessionPicker } from "../components/SessionPicker";
import { StatusPill } from "../components/StatusPill";
import { humanLabel, mapNamedCounts } from "../lib/dashboardLabels";
import { Pagination } from "../Pagination";
import type { Tab } from "../types";

type Props = {
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  onPickSession: (id: string) => void;
  dash: DashboardStats | null;
  dashSessions: Paginated<SessionSummary> | null;
  dashFindings: Paginated<Finding> | null;
  setDashSessionsPage: (p: number) => void;
  setDashFindingsPage: (p: number) => void;
  openSession: (id: string, tab: Tab) => void;
};

function ResultStack({
  lulus,
  menunggu,
  tidak,
}: {
  lulus: number;
  menunggu: number;
  tidak: number;
}) {
  const total = Math.max(1, lulus + menunggu + tidak);
  const parts = [
    { key: "lulus", label: "Lulus", n: lulus, cls: "ok" },
    { key: "menunggu", label: "Perlu dicek", n: menunggu, cls: "warn" },
    { key: "tidak", label: "Tidak lulus", n: tidak, cls: "bad" },
  ];
  return (
    <div className="result-stack" aria-label="Ringkasan hasil sesi">
      <div className="result-stack-head">
        <h3 className="dash-section-title">Proporsi hasil</h3>
        <p className="dash-section-copy">Lulus · perlu dicek · tidak lulus</p>
      </div>
      <div className="result-stack-bar" role="img" aria-label="Proporsi hasil">
        {parts.map((p) =>
          p.n > 0 ? (
            <span
              key={p.key}
              className={`result-seg ${p.cls}`}
              style={{ width: `${(p.n / total) * 100}%` }}
              title={`${p.label}: ${p.n}`}
            />
          ) : null,
        )}
      </div>
      <div className="result-stack-legend">
        {parts.map((p) => (
          <div key={p.key} className={`result-legend-item ${p.cls}`}>
            <span className="result-dot" />
            <div>
              <strong>{p.n}</strong>
              <span>{p.label}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function headlineFor(dash: DashboardStats): { title: string; copy: string; tone: string } {
  const menunggu = dash.menunggu_review_count ?? 0;
  const pending = dash.pending_reviews ?? 0;
  const tidak = dash.tidak_lulus_count ?? 0;
  const active = dash.active_sessions ?? 0;

  if (active > 0) {
    return {
      title: "Sedang ada analisa berjalan",
      copy: `${active} perangkat masih diproses. Selesai nanti, temuan akan muncul di sini.`,
      tone: "info",
    };
  }
  if (menunggu > 0 || pending > 0) {
    return {
      title: "Ada yang harus dicek analis",
      copy: `${menunggu} sesi menunggu review · ${pending} temuan belum diverifikasi. Buka Temuan untuk Konfirmasi atau Tolak.`,
      tone: "warn",
    };
  }
  if (tidak > 0) {
    return {
      title: "Ada sesi yang tidak lulus",
      copy: `${tidak} sesi sudah dikonfirmasi berisiko. Cek Laporan untuk pengesahan pimpinan.`,
      tone: "danger",
    };
  }
  if ((dash.completed_sessions ?? 0) === 0) {
    return {
      title: "Belum ada sesi selesai",
      copy: "Mulai akuisisi di halaman Operator. Setelah selesai, ringkasan muncul di sini.",
      tone: "info",
    };
  }
  return {
    title: "Kondisi aman sejauh ini",
    copy: "Semua sesi selesai sudah lulus atau temuan sudah ditolak. Tidak ada antrean review.",
    tone: "ok",
  };
}

export function DashboardPage({
  session,
  sessionList,
  sessionsLoading,
  onPickSession,
  dash,
  dashSessions,
  dashFindings,
  setDashSessionsPage,
  setDashFindingsPage,
  openSession,
}: Props) {
  const headline = useMemo(() => (dash ? headlineFor(dash) : null), [dash]);

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

  const lulus = dash?.lulus_count ?? 0;
  const menunggu = dash?.menunggu_review_count ?? 0;
  const tidak = dash?.tidak_lulus_count ?? 0;

  return (
    <section className="panel dash-panel">
      <PanelTitle title="Dasbor ringkas" />

      <SessionPicker
        sessions={sessionList}
        value={session?.id ?? null}
        loading={sessionsLoading}
        onChange={onPickSession}
      />
      <p className="login-hint hint-spaced">
        Pilih perangkat/sesi untuk fokus grafik riwayat 5 tahun. Tanpa pilihan = sesi selesai
        terbaru.
      </p>

      {!dash || !headline ? (
        <div className="empty">Menyiapkan ringkasan…</div>
      ) : (
        <>
          <div className={`dash-hero tone-${headline.tone}`}>
            <div className="dash-hero-copy">
              <p className="dash-hero-kicker">Ringkasan situasional</p>
              <h2>{headline.title}</h2>
              <p>{headline.copy}</p>
            </div>
            <div className="dash-hero-actions">
              {(dash.pending_reviews ?? 0) > 0 && (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    const sid =
                      dashSessions?.items.find((s) => s.recommendation === "MENUNGGU REVIEW")
                        ?.id || session?.id;
                    if (sid) openSession(sid, "findings");
                  }}
                >
                  Buka antrean review
                </button>
              )}
              {(dash.tidak_lulus_count ?? 0) > 0 && (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => {
                    const sid =
                      dashSessions?.items.find((s) => s.recommendation === "TIDAK LULUS")?.id ||
                      session?.id;
                    if (sid) openSession(sid, "report");
                  }}
                >
                  Lihat yang tidak lulus
                </button>
              )}
            </div>
          </div>

          <div className="dash-action-grid">
            <button
              type="button"
              className="dash-action-card warn"
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
                {dash.pending_reviews ?? 0} temuan masih menunggu Konfirmasi/Tolak
              </span>
            </button>
            <button
              type="button"
              className="dash-action-card bad"
              onClick={() => {
                const sid =
                  dashSessions?.items.find((s) => s.recommendation === "TIDAK LULUS")?.id ||
                  session?.id;
                if (sid) openSession(sid, "report");
              }}
            >
              <span className="dash-action-label">Tidak lulus</span>
              <strong className="dash-action-value">{tidak}</strong>
              <span className="dash-action-hint">Sesi dengan temuan yang sudah dikonfirmasi</span>
            </button>
            <div className="dash-action-card ok">
              <span className="dash-action-label">Lulus / aman</span>
              <strong className="dash-action-value">{lulus}</strong>
              <span className="dash-action-hint">Sesi bersih atau semua temuan ditolak</span>
            </div>
            <div className="dash-action-card muted">
              <span className="dash-action-label">Perangkat diproses</span>
              <strong className="dash-action-value">
                {dash.completed_sessions ?? 0}
                <span className="dash-action-sub">/{dash.total_sessions ?? 0}</span>
              </strong>
              <span className="dash-action-hint">
                Selesai / total · {dash.active_sessions ?? 0} sedang jalan ·{" "}
                {dash.failed_sessions ?? 0} gagal
              </span>
            </div>
          </div>

          <ResultStack lulus={lulus} menunggu={menunggu} tidak={tidak} />

          <div className="dash-mid-row">
            <div className="dash-mid-charts">
              <DistBars
                title="Jenis konten berisiko"
                subtitle="Dari apa indikasi paling sering muncul"
                items={byCategory}
                tone="danger"
                emptyHint="Belum ada temuan terkategorisasi"
              />
              <DistBars
                title="Asal media"
                subtitle="Foto, video, dokumen, atau unduhan"
                items={bySource}
                emptyHint="Belum ada data sumber media"
              />
            </div>

            <aside className="dash-feed">
              <div className="dash-section-head">
                <div>
                  <h3 className="dash-section-title">Indikasi terbaru</h3>
                  <p className="dash-section-copy">Klik untuk buka halaman Temuan.</p>
                </div>
              </div>
              {!dashFindings || dashFindings.total === 0 ? (
                <div className="empty empty-soft">Belum ada indikasi</div>
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

          <div className="dash-bottom-row">
            <div className="dash-bottom-timeline">
              {dash.risk_timeline ? (
                <RiskTimelinePanel
                  timeline={dash.risk_timeline}
                  sessionLabel={dash.timeline_session_label || session?.label}
                />
              ) : (
                <div className="empty empty-soft">Belum ada riwayat 5 tahun untuk sesi ini</div>
              )}
            </div>
            <div className="dash-bottom-sessions">
              <div className="dash-section-head">
                <div>
                  <h3 className="dash-section-title">Perangkat terakhir</h3>
                  <p className="dash-section-copy">Keputusan akhir tiap sesi.</p>
                </div>
              </div>
              {!dashSessions || dashSessions.total === 0 ? (
                <div className="empty">Belum ada sesi</div>
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
                          <span className="finding-meta">
                            {humanLabel("method", s.progress?.acquisition_method || "unknown")} ·{" "}
                            {s.mode === "full" ? "Penuh" : "Cepat"} ·{" "}
                            {s.progress?.findings_count ?? 0} indikasi · {ms(s.timing?.t_total_ms ?? 0)}
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
                    label="Perangkat"
                  />
                </>
              )}
            </div>
          </div>

          <details className="dash-tech-collapse">
            <summary>Rincian teknis (untuk engineer / lab)</summary>
            <div className="grid-3 grid-spaced">
              <DistBars
                title="Cara mesin mendeteksi"
                subtitle="Lapisan analisa AI"
                items={byLayer}
                emptyHint="Belum ada data layer"
              />
              <DistBars
                title="Cara ambil data"
                subtitle="USB atau unggah ZIP"
                items={byMethod}
                emptyHint="Belum ada metode tercatat"
              />
              <div className="dist-card">
                <h3>Kesiapan alat</h3>
                <p className="dist-subtitle">Status koneksi perangkat & engine</p>
                <div className="tool-pills tool-pills-col">
                  <span className={`pill ${dash.toolchain?.adb ? "ok" : "muted"}`}>
                    Kabel Android {dash.toolchain?.adb ? "siap" : "tidak terdeteksi"}
                  </span>
                  <span className={`pill ${dash.toolchain?.idevice_id ? "ok" : "muted"}`}>
                    Kabel iPhone {dash.toolchain?.idevice_id ? "siap" : "tidak terdeteksi"}
                  </span>
                  <span className={`pill ${dash.toolchain?.idevicebackup2 ? "ok" : "muted"}`}>
                    Backup iOS {dash.toolchain?.idevicebackup2 ? "siap" : "tidak ada"}
                  </span>
                  <span className={`pill ${dash.gpu_available ? "ok" : "muted"}`}>
                    Akselerasi GPU {dash.gpu_available ? "aktif" : "mode CPU"}
                  </span>
                </div>
                <div className="timing timing-spaced dash-tech-timing">
                  <div>
                    Rata-rata total
                    <strong>{ms(dash.avg_total_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Ambil data
                    <strong>{ms(dash.avg_acquire_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Indeks
                    <strong>{ms(dash.avg_index_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Analisa
                    <strong>{ms(dash.avg_analyze_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Puncak kecepatan
                    <strong>{(dash.throughput_peak_fps ?? 0).toFixed(0)} berkas/dtk</strong>
                  </div>
                </div>
              </div>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
