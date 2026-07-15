import type { AuthSession, Finding, Paginated, SessionSummary } from "../api";
import { api, can } from "../api";
import { FindingOriginBadge } from "../components/FindingOriginBadge";
import { PanelTitle } from "../components/PanelTitle";
import { SessionPicker } from "../components/SessionPicker";
import { VerdictNotice } from "../components/VerdictNotice";
import { REC_MENUNGGU_REVIEW, isThreatRecommendation } from "../constants";
import { humanLabel } from "../lib/dashboardLabels";
import { Pagination } from "../Pagination";

const REVIEW_LABEL = {
  pending: "Menunggu",
  confirmed: "Dikonfirmasi",
  rejected: "Ditolak",
} as const;

type ReviewSummary = {
  pending: number;
  confirmed: number;
  rejected: number;
  total: number;
};

type Props = {
  auth: AuthSession;
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  reportFindings: Paginated<Finding> | null;
  reportLoading: boolean;
  reviewSummary: ReviewSummary | null;
  setReportPage: (p: number) => void;
  authorizeNote: string;
  setAuthorizeNote: (v: string) => void;
  setSession: (s: SessionSummary) => void;
  refreshSessionList: () => void;
  setError: (e: string | null) => void;
  onToast: (message: string, tone?: "ok" | "warn" | "info") => void;
  onPickSession: (id: string) => void;
};

export function ReportPage({
  auth,
  session,
  sessionList,
  sessionsLoading,
  reportFindings,
  reportLoading,
  reviewSummary,
  setReportPage,
  authorizeNote,
  setAuthorizeNote,
  setSession,
  refreshSessionList,
  setError,
  onToast,
  onPickSession,
}: Props) {
  const progress = session?.progress;
  const canAuthorize = can(auth, "report:authorize");
  const awaitingReview = session?.recommendation === REC_MENUNGGU_REVIEW;
  const blockAuthorize = awaitingReview || (reviewSummary?.pending ?? 0) > 0;

  return (
    <section className={`panel${isThreatRecommendation(session?.recommendation) ? " threat" : ""}`}>
      <PanelTitle title="Laporan sesi" />

      <SessionPicker
        sessions={sessionList}
        value={session?.id ?? null}
        loading={sessionsLoading}
        onChange={onPickSession}
      />

      {!session ? (
        <div className="empty">Pilih sesi di atas untuk melihat laporan / pengesahan</div>
      ) : (
        <div className="report-layout">
          <aside className="report-aside">
            <VerdictNotice recommendation={session.recommendation} />

            {reviewSummary && reviewSummary.total > 0 && (
              <div className="review-summary-box" role="status">
                <span className="pill warn">{reviewSummary.pending} menunggu</span>
                <span className="pill bad">{reviewSummary.confirmed} dikonfirmasi</span>
                <span className="pill muted">{reviewSummary.rejected} ditolak</span>
              </div>
            )}

            <div className="report-meta">
              <div>
                <span className="report-meta-label">Perangkat</span>
                <strong>{session.label}</strong>
              </div>
              <div>
                <span className="report-meta-label">Cara ambil data</span>
                <strong>
                  {humanLabel("method", progress?.acquisition_method || "unknown")}
                </strong>
              </div>
              <div>
                <span className="report-meta-label">Mode</span>
                <strong>{session.mode === "full" ? "Penuh" : "Cepat"}</strong>
              </div>
              <div>
                <span className="report-meta-label">Indikasi</span>
                <strong>{progress?.findings_count ?? reportFindings?.total ?? 0}</strong>
              </div>
            </div>

            <div className="actions">
              <button
                className="btn btn-primary"
                type="button"
                onClick={() =>
                  api
                    .openReport(session.id, "html")
                    .catch((e) => setError(e instanceof Error ? e.message : "Gagal buka laporan"))
                }
              >
                Buka laporan HTML
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() =>
                  api
                    .openReport(session.id, "json")
                    .catch((e) => setError(e instanceof Error ? e.message : "Gagal unduh JSON"))
                }
              >
                Unduh JSON
              </button>
            </div>

            {canAuthorize && session.status === "completed" && (
              <div className="authorize-box">
                {blockAuthorize && (
                  <div className="error-banner authorize-block" role="alert">
                    Pengesahan diblokir — masih ada temuan belum diverifikasi. Selesaikan di{" "}
                    <strong>Temuan</strong> dulu.
                  </div>
                )}
                <label htmlFor="authorize-note">Catatan pengesahan</label>
                <textarea
                  id="authorize-note"
                  rows={3}
                  value={authorizeNote}
                  onChange={(e) => setAuthorizeNote(e.target.value)}
                  placeholder="Ringkasan keputusan pimpinan (opsional)"
                  disabled={blockAuthorize}
                />
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={blockAuthorize}
                  onClick={async () => {
                    if (blockAuthorize) return;
                    try {
                      await api.authorizeSession(
                        session.id,
                        authorizeNote.trim() || "Disahkan pimpinan (PoC)",
                      );
                      setSession(await api.session(session.id));
                      setAuthorizeNote("");
                      void refreshSessionList();
                      onToast("Rekomendasi disahkan", "ok");
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "Gagal mengesahkan");
                    }
                  }}
                >
                  Sahkan rekomendasi
                </button>
              </div>
            )}

            {progress?.authorized_by && (
              <div className="authorize-meta">
                <span className="pill ok">Disahkan · {progress.authorized_by}</span>
                {progress.authorized_at && (
                  <span className="pill muted">{progress.authorized_at}</span>
                )}
                {progress.authorize_note && (
                  <p className="authorize-note">{progress.authorize_note}</p>
                )}
              </div>
            )}
          </aside>

          <div className="report-main">
            <h3 className="dash-section-title">Ringkasan temuan</h3>
            <p className="dash-section-copy">Daftar indikasi + status verifikasi analis.</p>
            {reportLoading && !reportFindings ? (
              <div className="empty">Memuat ringkasan temuan…</div>
            ) : !reportFindings || reportFindings.total === 0 ? (
              <div className="empty">{reportLoading ? "Memuat…" : "Tidak ada temuan"}</div>
            ) : (
              <div className={reportLoading ? "list-refreshing" : undefined} aria-busy={reportLoading}>
                <div className="findings-desktop">
                  <table className="table findings-table">
                    <thead>
                      <tr>
                        <th>Label</th>
                        <th>Asal</th>
                        <th>Keyakinan</th>
                        <th>Verifikasi</th>
                        <th>Path</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reportFindings.items.map((f) => (
                        <tr key={f.id} className="hit-row">
                          <td className="finding-label">{f.label}</td>
                          <td>
                            <FindingOriginBadge layer={f.layer_origin} label={f.label} />
                          </td>
                          <td>{(f.confidence * 100).toFixed(0)}%</td>
                          <td>
                            <span
                              className={`pill ${
                                f.review_status === "confirmed"
                                  ? "bad"
                                  : f.review_status === "pending"
                                    ? "warn"
                                    : "muted"
                              }`}
                            >
                              {REVIEW_LABEL[f.review_status]}
                            </span>
                          </td>
                          <td className="finding-path">{f.path}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="findings-cards report-cards">
                  {reportFindings.items.map((f) => (
                    <article key={f.id} className="finding-card">
                      <strong className="finding-label">{f.label}</strong>
                      <div className="finding-meta">
                        <FindingOriginBadge layer={f.layer_origin} label={f.label} />
                        <span>· {(f.confidence * 100).toFixed(0)}%</span>
                        <span
                          className={`pill ${
                            f.review_status === "confirmed"
                              ? "bad"
                              : f.review_status === "pending"
                                ? "warn"
                                : "muted"
                          }`}
                        >
                          {REVIEW_LABEL[f.review_status]}
                        </span>
                      </div>
                      <div className="finding-path">{f.path}</div>
                    </article>
                  ))}
                </div>
                <Pagination
                  page={reportFindings.page}
                  pages={reportFindings.pages}
                  total={reportFindings.total}
                  page_size={reportFindings.page_size}
                  onPage={setReportPage}
                />
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
