import type {
  AuthSession,
  Finding,
  Paginated,
  SessionReport,
  SessionSummary,
} from "@/shared/api/client";
import { api, can } from "@/shared/api/client";
import { FindingOriginBadge } from "@/features/findings/components/FindingOriginBadge";
import { EmptyState } from "@/shared/ui/EmptyState";
import { FeatureKpiGrid } from "@/shared/ui/FeatureKpiGrid";
import { FeaturePageShell } from "@/shared/ui/FeaturePageShell";
import { AuditTrailPanel } from "@/features/report/components/AuditTrailPanel";
import { ParticipantIdentityEditor } from "@/features/report/components/ParticipantIdentityEditor";
import { VerdictNotice } from "@/features/findings/components/VerdictNotice";
import { REC_MENUNGGU_REVIEW, isThreatRecommendation } from "@/shared/constants";
import { FEATURE_EMPTY_NO_SESSION, FEATURE_PAGE_META } from "@/shared/lib/featurePages";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import { Pagination } from "@/shared/ui/Pagination";

const REVIEW_LABEL = {
  pending: "Menunggu",
  confirmed: "Dikonfirmasi",
  rejected: "Ditolak",
} as const;

const PROFILE_METRIC_LABEL: Record<string, string> = {
  posts: "Postingan / tweet",
  followers: "Pengikut",
  friends: "Teman",
  following: "Mengikuti",
};

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
  reportData: SessionReport | null;
  reportLoading: boolean;
  reviewSummary: ReviewSummary | null;
  setReportPage: (p: number) => void;
  authorizeNote: string;
  setAuthorizeNote: (v: string) => void;
  setSession: React.Dispatch<React.SetStateAction<SessionSummary | null>>;
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
  reportData,
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
  const canEditParticipant = can(auth, "sessions:update_participant");
  const awaitingReview = session?.recommendation === REC_MENUNGGU_REVIEW;
  const blockAuthorize = awaitingReview || (reviewSummary?.pending ?? 0) > 0;
  const empty = FEATURE_EMPTY_NO_SESSION.report;

  return (
    <FeaturePageShell
      meta={FEATURE_PAGE_META.report}
      panelClass="report-panel findings-panel"
      threat={isThreatRecommendation(session?.recommendation)}
      kpis={
        session ? (
          <FeatureKpiGrid
            ariaLabel="Metrik keputusan"
            items={[
              { label: "Menunggu", value: reviewSummary?.pending ?? 0, tone: "warn" },
              { label: "Dikonfirmasi", value: reviewSummary?.confirmed ?? 0, tone: "bad" },
              {
                label: "Ditolak",
                value: reviewSummary?.rejected ?? 0,
                tone: "muted",
              },
              {
                label: "Sinyal",
                value: progress?.findings_count ?? reportFindings?.total ?? 0,
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
        compact: true,
      }}
    >
      {!session ? (
        <EmptyState title={empty.title} body={empty.body} hint={empty.hint} />
      ) : (
        <div className="report-stack">
          <ParticipantIdentityEditor
            session={session}
            canEdit={canEditParticipant}
            onSaved={(s) => {
              setSession((current) =>
                current?.id === s.id ? s : current,
              );
              refreshSessionList();
            }}
            onError={setError}
            onToast={onToast}
          />
          <section className="report-decision" aria-label="Keputusan & ekspor">
            <div className="report-decision-main">
              <VerdictNotice recommendation={session.recommendation} />
              <dl className="report-meta-inline">
                {session.participant?.full_name ? (
                  <>
                    <div>
                      <dt>Peserta</dt>
                      <dd>{session.participant.full_name}</dd>
                    </div>
                    <div>
                      <dt>No. peserta</dt>
                      <dd>{session.participant.registration_no || "—"}</dd>
                    </div>
                    {session.participant.organization ? (
                      <div>
                        <dt>Instansi</dt>
                        <dd>{session.participant.organization}</dd>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div>
                    <dt>Perangkat</dt>
                    <dd>{session.label}</dd>
                  </div>
                )}
                <div>
                  <dt>Metode</dt>
                  <dd>{humanLabel("method", progress?.acquisition_method || "unknown")}</dd>
                </div>
                <div>
                  <dt>Mode</dt>
                  <dd>{session.mode === "full" ? "Penuh" : "Cepat"}</dd>
                </div>
              </dl>
            </div>

            <div className="report-decision-actions">
              <div className="report-export" role="group" aria-label="Ekspor laporan">
                <span className="report-export-label">Ekspor</span>
                <div className="report-export-btns">
                  <button
                    className="report-chip"
                    type="button"
                    onClick={() =>
                      api
                        .openReport(session.id, "html", session.participant)
                        .then(() => onToast("Laporan HTML disimpan / dibuka", "ok"))
                        .catch((e) =>
                          setError(e instanceof Error ? e.message : "Gagal buka laporan"),
                        )
                    }
                  >
                    HTML
                  </button>
                  <button
                    className="report-chip"
                    type="button"
                    onClick={() =>
                      api
                        .openReport(session.id, "json", session.participant)
                        .then(() => onToast("Laporan JSON disimpan", "ok"))
                        .catch((e) =>
                          setError(e instanceof Error ? e.message : "Gagal unduh JSON"),
                        )
                    }
                  >
                    JSON
                  </button>
                  <button
                    className="report-chip"
                    type="button"
                    onClick={() =>
                      api
                        .openReportPdf(session.id, session.participant)
                        .then((path) =>
                          onToast(
                            typeof path === "string" && path
                              ? `PDF disimpan: ${path}`
                              : "PDF siap",
                            "ok",
                          ),
                        )
                        .catch((e) =>
                          setError(e instanceof Error ? e.message : "Gagal simpan PDF"),
                        )
                    }
                  >
                    PDF / Cetak
                  </button>
                </div>
              </div>

              {canAuthorize && session.status === "completed" && (
                <div className="authorize-box">
                  {blockAuthorize && (
                    <div className="error-banner authorize-block" role="alert">
                      Pengesahan diblokir — selesaikan verifikasi di <strong>Temuan</strong> dulu.
                    </div>
                  )}
                  <label htmlFor="authorize-note">Catatan pengesahan</label>
                  {!blockAuthorize && session.recommendation ? (
                    <p className="field-note">
                      Menyahkan keputusan akhir: <strong>{session.recommendation}</strong> — berlaku
                      untuk LULUS maupun TIDAK LULUS setelah review analis selesai.
                    </p>
                  ) : null}
                  <div className="authorize-row">
                    <textarea
                      id="authorize-note"
                      rows={2}
                      value={authorizeNote}
                      onChange={(e) => setAuthorizeNote(e.target.value)}
                      placeholder="Ringkasan keputusan pimpinan (opsional)"
                      disabled={blockAuthorize}
                    />
                    <button
                      className="report-chip report-chip-primary"
                      type="button"
                      disabled={blockAuthorize}
                      aria-label="Sahkan rekomendasi"
                      onClick={async () => {
                        if (blockAuthorize) return;
                        const sessionId = session.id;
                        try {
                          await api.authorizeSession(
                            sessionId,
                            authorizeNote.trim() || "Disahkan pimpinan (SATRIA)",
                          );
                          const refreshed = await api.session(sessionId);
                          setSession((current) =>
                            current?.id === sessionId ? refreshed : current,
                          );
                          setAuthorizeNote("");
                          void refreshSessionList();
                          onToast("Rekomendasi disahkan", "ok");
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "Gagal mengesahkan");
                        }
                      }}
                    >
                      Sahkan
                    </button>
                  </div>
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
            </div>
          </section>

          <section className="report-main">
            <h3 className="dash-section-title">Ringkasan temuan</h3>
            <p className="dash-section-copy">Indikasi terflag + status verifikasi analis.</p>
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
                            <div className="finding-meta">{humanLabel("source", f.source)}</div>
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
                        <span>· {humanLabel("source", f.source)}</span>
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

            {(reportData?.whatsapp_rooms?.length ?? 0) > 0 || progress?.whatsapp_state ? (
              <>
                <h3 className="dash-section-title dash-section-spaced">Percakapan WhatsApp</h3>
                <p className="dash-section-copy">
                  Pesan canonical hasil backup Crypt15. Penanda temuan mengikuti hasil analisis dan
                  review pada pesan yang sama.
                </p>
                {!reportData || reportData.whatsapp_rooms.length === 0 ? (
                  <p className="report-muted-note">
                    {progress?.whatsapp_state === "parse_unavailable"
                      ? "Backup diperoleh, tetapi format database belum dapat diparse."
                      : progress?.whatsapp_state === "not_installed"
                        ? "WhatsApp tidak terpasang pada perangkat ini."
                        : progress?.whatsapp_state === "not_signed_in"
                          ? "WhatsApp terpasang, tetapi belum login nomor telepon."
                          : "Tidak ada pesan WhatsApp pada rentang waktu sesi ini."}
                  </p>
                ) : (
                  <div className="wa-room-list report-wa-rooms">
                    {reportData.whatsapp_rooms.map((room) => (
                      <section className="wa-room" key={room.conversation_id}>
                        <header className="wa-room-header">
                          <div className="wa-room-avatar" aria-hidden>
                            WA
                          </div>
                          <div className="wa-room-identity">
                            <strong>{room.name}</strong>
                            <span>
                              {room.type === "group" ? "Grup WhatsApp" : "Chat WhatsApp"}
                              {room.address && room.address !== room.name
                                ? ` · ${room.address}`
                                : ""}
                            </span>
                          </div>
                          <div className="wa-room-counts">
                            <span>{room.messages.length} pesan</span>
                            {room.finding_count > 0 ? (
                              <span className="wa-room-findings">
                                {room.finding_count} temuan
                              </span>
                            ) : null}
                          </div>
                        </header>
                        <div className="wa-thread">
                          {room.messages.map((message) => {
                            const isSystem =
                              message.actor_kind === "system" ||
                              message.message_type === "system";
                            if (isSystem) {
                              return (
                                <article
                                  className={`wa-system-event ${
                                    message.flagged ? "wa-message-finding" : ""
                                  }`}
                                  key={message.message_id}
                                >
                                  <div className="wa-system-event-card">
                                    <div className="wa-system-event-head">
                                      <strong>Sistem WhatsApp</strong>
                                      {message.system_action_type !== null &&
                                      message.system_action_type !== undefined ? (
                                        <span>Aksi {message.system_action_type}</span>
                                      ) : null}
                                      {message.flagged ? (
                                        <strong className="wa-finding-marker">Temuan</strong>
                                      ) : null}
                                    </div>
                                    <p>{message.preview_text}</p>
                                    <time dateTime={message.timestamp || undefined}>
                                      {message.timestamp || "Waktu tidak tersedia"}
                                    </time>
                                  </div>
                                </article>
                              );
                            }
                            return (
                              <article
                                className={`wa-message wa-message-${message.direction.toLowerCase()} ${
                                  message.flagged ? "wa-message-finding" : ""
                                }`}
                                key={message.message_id}
                              >
                                <div className="wa-message-bubble">
                                  <div className="wa-message-head">
                                    <span>
                                      {message.actor_kind === "self"
                                        ? "Anda"
                                        : message.actor_kind === "unknown"
                                          ? "Aktor tidak diketahui"
                                          : message.sender || room.name}
                                    </span>
                                    {message.flagged ? (
                                      <strong className="wa-finding-marker">Temuan</strong>
                                    ) : null}
                                  </div>
                                  {message.quoted_text ? (
                                    <blockquote className="wa-quote">
                                      {message.quoted_text}
                                    </blockquote>
                                  ) : null}
                                  <p
                                    className={
                                      message.revoked ? "wa-message-revoked" : undefined
                                    }
                                  >
                                    {message.preview_text}
                                  </p>
                                  {message.finding_labels.length > 0 ? (
                                    <div className="wa-finding-badges">
                                      {message.finding_labels.map((label) => (
                                        <span key={label}>{label}</span>
                                      ))}
                                    </div>
                                  ) : null}
                                  <footer className="wa-message-foot">
                                    <span>{message.message_type.replace(/_/g, " ")}</span>
                                    {message.direction === "UNKNOWN" ? (
                                      <span>Arah tidak diketahui</span>
                                    ) : null}
                                    {message.forwarded ? <span>Diteruskan</span> : null}
                                    {message.edited_at ? <span>Diedit</span> : null}
                                    {message.starred ? <span>★</span> : null}
                                    <time dateTime={message.timestamp || undefined}>
                                      {message.timestamp || "Waktu tidak tersedia"}
                                    </time>
                                  </footer>
                                </div>
                              </article>
                            );
                          })}
                        </div>
                      </section>
                    ))}
                    {reportData.whatsapp_data.truncated ? (
                      <div className="empty">
                        Tampilan dibatasi {reportData.whatsapp_data.maximum_messages} pesan dari{" "}
                        {reportData.whatsapp_data.total_messages} pesan.
                      </div>
                    ) : null}
                  </div>
                )}
              </>
            ) : null}

            <h3 className="dash-section-title dash-section-spaced">Data akun &amp; sosial</h3>
            <p className="dash-section-copy">
              Profil dan aktivitas sosial yang dikoleksi — bukan temuan.
            </p>
            {reportLoading && !reportData ? (
              <EmptyState title="Memuat berkas sosial…" body="Memuat data akun & aktivitas." />
            ) : !reportData || reportData.social_accounts.length === 0 ? (
              <p className="report-muted-note">Tidak ada data akun sosial terverifikasi pada sesi ini.</p>
            ) : (
              <div>
                {reportData.social_accounts.map((account) => (
                  <section className="result-stack" key={account.source_app}>
                    <div className="result-stack-head">
                      <h4 className="dash-section-title">{account.platform}</h4>
                      <p className="dash-section-copy">
                        {account.username
                          ? `${account.display_name ? `${account.display_name} · ` : ""}@${account.username}`
                          : account.display_name || "Username tidak terbaca"}
                      </p>
                    </div>
                    <div className="report-meta">
                      {Object.entries(account.profile_metrics ?? {})
                        .filter(([, count]) => typeof count === "number")
                        .map(([metric, count]) => (
                          <div key={`profile-${metric}`}>
                            <span className="report-meta-label">
                              {PROFILE_METRIC_LABEL[metric] ?? metric}
                            </span>
                            <strong>{Number(count).toLocaleString("id-ID")}</strong>
                          </div>
                        ))}
                    </div>
                    <div className="authorize-meta">
                      <span className="report-meta-label">Bio / profil terlihat</span>
                      <p className="authorize-note">{account.bio || "—"}</p>
                      <span className="report-meta-label">Link profil</span>
                      {account.profile_links.length > 0 ? (
                        account.profile_links.map((link) => (
                          <span className="finding-path" key={link}>
                            {link}
                          </span>
                        ))
                      ) : (
                        <span className="finding-path">—</span>
                      )}
                    </div>

                    {account.items.length > 0 && (
                      <>
                        <div className="findings-desktop">
                          <table className="table findings-table">
                            <thead>
                              <tr>
                                <th>Jenis data</th>
                                <th>Waktu</th>
                                <th>Preview</th>
                              </tr>
                            </thead>
                            <tbody>
                              {account.items.map((item) => (
                                <tr key={item.record_id}>
                                  <td>{item.scope_label}</td>
                                  <td>{item.observed_at || "—"}</td>
                                  <td className="evidence-body">{item.preview_text || "—"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="findings-cards report-cards">
                          {account.items.map((item) => (
                            <article className="finding-card" key={item.record_id}>
                              <strong className="finding-label">{item.scope_label}</strong>
                              <div className="finding-meta">{item.observed_at || "—"}</div>
                              <div className="evidence-body">{item.preview_text || "—"}</div>
                            </article>
                          ))}
                        </div>
                      </>
                    )}
                  </section>
                ))}
                {reportData.social_data.truncated && (
                  <div className="empty">
                    Tampilan dibatasi {reportData.social_data.maximum_items} item dari{" "}
                    {reportData.social_data.total_items} data sosial.
                  </div>
                )}
              </div>
            )}

            <AuditTrailPanel session={session} />
          </section>
        </div>
      )}
    </FeaturePageShell>
  );
}
