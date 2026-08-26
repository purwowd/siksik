import { useEffect, useState, type RefObject } from "react";
import { ms, type DeviceInfo, type SessionSummary } from "@/shared/api/client";
import { FeaturePanel } from "@/shared/ui/FeaturePanel";
import { StatusPill } from "@/shared/ui/StatusPill";
import { VerdictNotice } from "@/features/findings/components/VerdictNotice";
import { ACTIVE, isThreatRecommendation } from "@/shared/constants";
import { humanLabel, methodSummary } from "@/features/dashboard/lib/dashboardLabels";
import { FEATURE_PAGE_META, OPERATOR_TELEMETRY_META } from "@/shared/lib/featurePages";
import { occupyingLabel } from "@/shared/lib/caseChecklist";
import { humanProgressMessage } from "@/shared/lib/humanProgress";
import { PageLoading } from "@/shared/ui/PageLoading";
import { LAB_UI } from "@/shared/lib/labUi";
import { sessionStatusLabel } from "@/shared/lib/sessionStatus";
import {
  ANALYSIS_SCOPE_LABEL,
  ANALYSIS_SCOPE_OPTIONS,
  DEVICE_SOURCE_OPTIONS,
  SOCIAL_TARGET_OPTIONS,
  analysisPlanReady,
  planForScope,
  toggleChecked,
  type AnalysisScope,
  type DeviceSourceId,
  type SocialTargetId,
} from "@/features/operator/analysisScope";

export type ParticipantForm = {
  fullName: string;
  registrationNo: string;
  nik: string;
  organization: string;
};

type Props = {
  teleRef: RefObject<HTMLElement | null>;
  participant: ParticipantForm;
  setParticipant: (patch: Partial<ParticipantForm>) => void;
  acqSource: "live" | "zip";
  setAcqSource: (v: "live" | "zip") => void;
  zipEnabled: boolean;
  zipFile: File | null;
  setZipFile: (f: File | null) => void;
  zipMaxMb: number;
  uploadPct: number | null;
  liveDevices: DeviceInfo[];
  selected: DeviceInfo | null;
  setSelected: (d: DeviceInfo | null) => void;
  refreshDevices: () => Promise<void>;
  devicesLoading?: boolean;
  analysisScope: AnalysisScope;
  setAnalysisScope: (scope: AnalysisScope) => void;
  deviceSources: DeviceSourceId[];
  setDeviceSources: (sources: DeviceSourceId[]) => void;
  socialTargets: SocialTargetId[];
  setSocialTargets: (targets: SocialTargetId[]) => void;
  canStartLive: boolean;
  canStartZip: boolean;
  busy: boolean;
  session: SessionSummary | null;
  occupying: SessionSummary | null;
  start: () => void;
  startZip: () => void;
  cancel: (sessionId?: string) => void;
  onNavigateTab: (t: "findings" | "report" | "dashboard") => void;
  canFindings: boolean;
  canReport: boolean;
  canDashboard: boolean;
};

export function OperatorPage(p: Props) {
  const progress = p.session?.progress;
  const timing = p.session?.timing;
  const active = !!p.session && ACTIVE.has(p.session.status);
  const [clockMs, setClockMs] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    setClockMs(Date.now());
    const timer = window.setInterval(() => setClockMs(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [active, p.session?.id]);

  const sessionStartedMs = Date.parse(p.session?.created_at ?? "");
  const sessionUpdatedMs = Date.parse(p.session?.updated_at ?? "");
  const measuredTotalMs =
    Number.isFinite(sessionStartedMs) && Number.isFinite(active ? clockMs : sessionUpdatedMs)
      ? Math.max(0, (active ? clockMs : sessionUpdatedMs) - sessionStartedMs)
      : 0;
  const liveTotalMs = active
    ? Math.max(timing?.t_total_ms ?? 0, measuredTotalMs)
    : timing?.t_total_ms || measuredTotalMs;
  const inventoried = Math.max(
    progress?.files_listed ?? 0,
    progress?.crawl_discovered ?? 0,
    progress?.preprocessing_total ?? 0,
    progress?.selection_evaluated ?? 0,
  );
  const selectedRecords = Math.max(
    progress?.selection_selected ?? 0,
    progress?.transfer_records ?? 0,
  );
  const indexedFiles = Math.max(
    progress?.files_indexed ?? 0,
    progress?.files_pulled ?? 0,
  );

  const identityReady =
    p.participant.fullName.trim().length > 0 &&
    p.participant.registrationNo.trim().length > 0 &&
    (!p.participant.nik.trim() || /^\d{16}$/.test(p.participant.nik.trim()));
  const sourceReady = identityReady && (p.acqSource === "live" ? !!p.selected : p.zipEnabled && !!p.zipFile);
  const planReady = analysisPlanReady(p.analysisScope, p.deviceSources, p.socialTargets);
  const depthReady = sourceReady && planReady;
  const mutexBlocksStart = !!p.occupying;
  const runReady =
    !mutexBlocksStart &&
    identityReady &&
    (p.acqSource === "live" ? p.canStartLive : p.canStartZip);
  const showDeviceChecks = p.analysisScope !== "social";
  const showSocialChecks = p.analysisScope !== "device";
  const sessionScope = (progress?.analysis_scope ?? "combined") as AnalysisScope;
  const needsSocialAccess = sessionScope !== "device";

  return (
    <div className="ent-operator">
      {p.occupying && (
        <div className="ent-mutex" role="status">
          <div>
            <p className="ent-eyebrow">Mesin sedang dipakai</p>
            <p>
              Sesi <strong>{occupyingLabel(p.occupying)}</strong> masih berjalan. Satu pemeriksaan
              per mesin — batalkan dulu atau tunggu selesai.
            </p>
          </div>
          <button
            className="btn btn-danger"
            type="button"
            disabled={p.busy}
            onClick={() => p.cancel(p.occupying?.id)}
          >
            Batalkan sesi aktif
          </button>
        </div>
      )}
      <div className="grid-2">
        <FeaturePanel meta={FEATURE_PAGE_META.operator}>
          <ol className="ent-intake-steps" aria-label="Langkah penerimaan">
            <li className={identityReady ? "on" : ""}>Kasus</li>
            <li className={sourceReady ? "on" : ""}>Perangkat</li>
            <li className={depthReady ? "on" : ""}>Cakupan</li>
            <li className={runReady ? "on" : ""}>Jalankan</li>
          </ol>
          <div className="form-grid">
            <div className="field-group" role="group" aria-labelledby="participant-heading">
              <p id="participant-heading" className="field-group-title">
                Identitas peserta seleksi
              </p>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="participant-name">Nama lengkap</label>
                  <input
                    id="participant-name"
                    type="text"
                    autoComplete="name"
                    placeholder="Sesuai dokumen peserta"
                    value={p.participant.fullName}
                    onChange={(e) => p.setParticipant({ fullName: e.target.value })}
                    disabled={p.busy || active}
                  />
                </div>
                <div className="field">
                  <label htmlFor="participant-reg">No. peserta / registrasi</label>
                  <input
                    id="participant-reg"
                    type="text"
                    autoComplete="off"
                    placeholder="Contoh ASN-2026-0142"
                    value={p.participant.registrationNo}
                    onChange={(e) => p.setParticipant({ registrationNo: e.target.value })}
                    disabled={p.busy || active}
                  />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="participant-nik">
                    NIK <span className="field-optional">(opsional)</span>
                  </label>
                  <input
                    id="participant-nik"
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    placeholder="16 digit"
                    value={p.participant.nik}
                    onChange={(e) => p.setParticipant({ nik: e.target.value })}
                    disabled={p.busy || active}
                  />
                  {!!p.participant.nik.trim() && !/^\d{16}$/.test(p.participant.nik.trim()) && (
                    <small className="field-note">NIK harus 16 digit angka</small>
                  )}
                </div>
                <div className="field">
                  <label htmlFor="participant-org">
                    Instansi / formasi <span className="field-optional">(opsional)</span>
                  </label>
                  <input
                    id="participant-org"
                    type="text"
                    autoComplete="organization"
                    placeholder="Pemda X · CPNS"
                    value={p.participant.organization}
                    onChange={(e) => p.setParticipant({ organization: e.target.value })}
                    disabled={p.busy || active}
                  />
                </div>
              </div>
            </div>

            <div className="field">
              <label htmlFor="acq-source">Sumber analisa</label>
              <select
                id="acq-source"
                value={p.acqSource === "zip" && !p.zipEnabled ? "live" : p.acqSource}
                onChange={(e) => p.setAcqSource(e.target.value as "live" | "zip")}
                disabled={p.busy || active}
              >
                <option value="live">Perangkat terhubung (USB)</option>
                <option value="zip" disabled={!p.zipEnabled}>
                  Unggah arsip perangkat{" "}
                  {p.zipEnabled ? "(tanpa kabel USB)" : "(dinonaktifkan server)"}
                </option>
              </select>
            </div>

            {p.acqSource === "live" && (
              <div className="field">
                <label id="device-list-label">Perangkat terhubung</label>
                <div className="actions field-actions">
                  <button
                    className="btn btn-ghost"
                    type="button"
                    disabled={p.devicesLoading || p.busy || active}
                    onClick={() => p.refreshDevices().catch(console.error)}
                  >
                    {p.devicesLoading ? "Memuat…" : "Pindai ulang USB"}
                  </button>
                </div>
                <div className="device-list" role="listbox" aria-labelledby="device-list-label">
                  {p.devicesLoading ? (
                    <PageLoading />
                  ) : p.liveDevices.length === 0 ? (
                    <div className="empty empty-soft">
                      Tidak ada HP — sambungkan dengan kabel USB, atau ganti sumber ke unggah arsip
                    </div>
                  ) : null}
                  {!p.devicesLoading &&
                  p.liveDevices.map((d) => {
                    const selectedRow = p.selected?.device_id === d.device_id;
                    return (
                      <button
                        key={d.device_id}
                        type="button"
                        role="option"
                        aria-selected={selectedRow}
                        className={`device-item ${selectedRow ? "selected" : ""}`}
                        onClick={() => p.setSelected(d)}
                        disabled={p.busy || active}
                      >
                        <span>
                          <strong>{d.label}</strong>
                          <small>{d.device_id}</small>
                        </span>
                        <span className="pill muted">
                          {d.device_type === "ios" ? "iPhone" : d.device_type === "android" ? "Android" : "Perangkat"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {p.acqSource === "zip" && p.zipEnabled && (
              <div className="field">
                <label htmlFor="zip-file">Arsip perangkat (ZIP)</label>
                <input
                  id="zip-file"
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(e) => p.setZipFile(e.target.files?.[0] ?? null)}
                  disabled={p.busy || active}
                />
                {p.zipFile && (
                  <small className="field-note">
                    {(p.zipFile.size / (1024 * 1024)).toFixed(1)} MB · batas {p.zipMaxMb} MB
                  </small>
                )}
                {p.uploadPct != null && (
                  <div className="progress-wrap upload-progress">
                    <div className="progress-meta">
                      <span>Unggah ZIP</span>
                      <strong>{p.uploadPct}%</strong>
                    </div>
                    <div className="bar active">
                      <span style={{ width: `${p.uploadPct}%` }} />
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="field-group" role="group" aria-labelledby="analysis-scope-heading">
              <p id="analysis-scope-heading" className="field-group-title">
                Fokus analisa
              </p>
              <div className="analysis-scope-grid" role="radiogroup" aria-labelledby="analysis-scope-heading">
                {ANALYSIS_SCOPE_OPTIONS.map((option) => {
                  const selectedScope = p.analysisScope === option.id;
                  return (
                    <button
                      key={option.id}
                      type="button"
                      role="radio"
                      aria-checked={selectedScope}
                      className={`analysis-scope-card ${selectedScope ? "selected" : ""} ${option.id === "combined" ? "slow" : ""}`}
                      disabled={p.busy || active}
                      onClick={() => {
                        p.setAnalysisScope(option.id);
                        const next = planForScope(option.id);
                        p.setDeviceSources(next.deviceSources);
                        p.setSocialTargets(next.socialTargets);
                      }}
                    >
                      <strong>{option.label}</strong>
                      <small>{option.hint}</small>
                    </button>
                  );
                })}
              </div>
              {p.analysisScope === "combined" && (
                <div className="analysis-scope-warn" role="status">
                  <strong>Proses akan lebih lama</strong>
                  <p>
                    Gabungan menganalisa sumber HP dan crawl sosmed sekaligus. Waktu bisa naik
                    signifikan jika Instagram, Facebook, dan X semua dicentang. Kurangi checklist
                    jika waktu terbatas, atau pilih <strong>HP saja</strong> untuk jalur cepat.
                  </p>
                </div>
              )}
              <p className="field-note">
                Semakin sedikit yang dicentang, semakin cepat pemeriksaan.
              </p>

              <details className="operator-advanced">
                <summary>Atur sumber (opsional)</summary>
              {showDeviceChecks && (
                <fieldset className="analysis-check-set" disabled={p.busy || active}>
                  <legend>Sumber HP</legend>
                  <div className="analysis-check-grid">
                    {DEVICE_SOURCE_OPTIONS.map((option) => (
                      <label key={option.id} className="analysis-check">
                        <input
                          type="checkbox"
                          checked={p.deviceSources.includes(option.id)}
                          onChange={() => p.setDeviceSources(toggleChecked(p.deviceSources, option.id))}
                        />
                        <span>
                          <strong>{option.label}</strong>
                          <small>{option.hint}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}

              {showSocialChecks && (
                <fieldset className="analysis-check-set" disabled={p.busy || active}>
                  <legend>Akun sosmed</legend>
                  <div className="analysis-check-grid analysis-check-grid-compact">
                    {SOCIAL_TARGET_OPTIONS.map((option) => (
                      <label key={option.id} className="analysis-check">
                        <input
                          type="checkbox"
                          checked={p.socialTargets.includes(option.id)}
                          onChange={() => p.setSocialTargets(toggleChecked(p.socialTargets, option.id))}
                        />
                        <span>
                          <strong>{option.label}</strong>
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}

              {!planReady && (
                <p className="field-note">
                  {p.analysisScope === "device"
                    ? "Centang minimal satu sumber HP."
                    : p.analysisScope === "social"
                      ? "Centang minimal satu akun sosmed."
                      : "Gabungan membutuhkan minimal satu sumber HP dan satu akun sosmed."}
                </p>
              )}
              </details>
            </div>

            <div className="actions">
              {p.acqSource === "live" ? (
                <button
                  className="btn btn-primary"
                  disabled={!p.canStartLive || mutexBlocksStart}
                  title={mutexBlocksStart ? "Mesin sedang dipakai — batalkan sesi aktif dulu" : undefined}
                  onClick={p.start}
                >
                  {p.busy ? "Memulai…" : "Jalankan pemeriksaan"}
                </button>
              ) : (
                <button
                  className="btn btn-primary"
                  disabled={!p.canStartZip || mutexBlocksStart}
                  title={mutexBlocksStart ? "Mesin sedang dipakai — batalkan sesi aktif dulu" : undefined}
                  onClick={p.startZip}
                >
                  {p.busy ? "Mengunggah…" : "Analisa arsip"}
                </button>
              )}
              {p.session && ACTIVE.has(p.session.status) && (
                <button className="btn btn-danger" onClick={() => p.cancel()} disabled={p.busy}>
                  Batalkan
                </button>
              )}
            </div>
            {!identityReady && (
              <p className="field-note">Isi nama dan no. peserta sebelum menjalankan pemeriksaan.</p>
            )}
            {identityReady && !planReady && (
              <p className="field-note">Pilih fokus dan centang sumber yang akan dianalisa.</p>
            )}
          </div>
        </FeaturePanel>

        <FeaturePanel
          meta={OPERATOR_TELEMETRY_META}
          panelRef={p.teleRef}
          threat={isThreatRecommendation(p.session?.recommendation)}
        >
          {!p.session ? (
            <div className="standby">
              <p className="standby-title">Pemeriksaan siap</p>
              <p className="standby-copy">
                Isi identitas peserta, pilih perangkat terhubung atau unggah arsip, lalu jalankan.
              </p>
            </div>
          ) : (
            <>
              <div className="tel-live">
                <StatusPill status={p.session.status} recommendation={p.session.recommendation} />
                <span className="pill muted">
                  {ANALYSIS_SCOPE_LABEL[sessionScope] ?? ANALYSIS_SCOPE_LABEL.combined}
                </span>
                {LAB_UI && (
                <span
                  className="pill muted"
                  title={humanLabel("method", progress?.acquisition_method || "unknown")}
                >
                  {methodSummary(progress?.acquisition_method || "unknown")}
                </span>
                )}
                {p.session.participant?.full_name ? (
                  <span className="pill muted">{p.session.participant.full_name}</span>
                ) : null}
              </div>

              {p.session.error && (
                <div className="error-banner spaced">{humanProgressMessage(p.session.error)}</div>
              )}

              {p.session.status === "awaiting_access" && (
                <div className="access-hint" role="alert">
                  <strong>Izin di HP diperlukan</strong>
                  <p>
                    {needsSocialAccess
                      ? "Izinkan SATRIA membaca layar untuk crawl sosmed, lalu kembali ke konsol."
                      : "Mode HP saja biasanya tidak butuh izin aksesibilitas. Jika diminta, izinkan akses file lalu lanjut."}
                  </p>
                  <details className="operator-tech">
                    <summary>Bantuan teknis</summary>
                    <p>
                    {needsSocialAccess
                      ? "Aktifkan Accessibility SATRIA. Pemeriksaan mungkin meminta izin semua file. Izin notifikasi dicoba otomatis."
                      : "Pemeriksaan mungkin meminta izin semua file. Izin notifikasi dicoba otomatis."}
                    </p>
                  </details>
                </div>
              )}

              {p.session.status === "completed" && (
                <VerdictNotice recommendation={p.session.recommendation} />
              )}

              <div className="progress-wrap">
                <div className="progress-meta">
                  <span>{humanProgressMessage(progress?.message) || sessionStatusLabel(p.session.status)}</span>
                  <strong>{progress?.percent?.toFixed(0) ?? 0}%</strong>
                </div>
                <div className={`bar ${ACTIVE.has(p.session.status) ? "active" : ""}`}>
                  <span style={{ width: `${progress?.percent ?? 0}%` }} />
                </div>
              </div>

              <div className="timing">
                <div>
                  Berkas
                  <strong>{indexedFiles}</strong>
                </div>
                <div>
                  Temuan
                  <strong>{progress?.findings_count ?? 0}</strong>
                </div>
                <div>
                  Waktu
                  <strong>{ms(liveTotalMs)}</strong>
                </div>
              </div>
              <details className="operator-tech">
                <summary>Bantuan teknis</summary>
                <div className="timing">
                  <div>
                    Rekam
                    <strong>
                      {selectedRecords}
                      {inventoried ? ` / ${inventoried}` : ""}
                    </strong>
                  </div>
                  <div>
                    Dianalisis
                    <strong>{progress?.files_analyzed ?? 0}</strong>
                  </div>
                  {progress?.acquisition_method && (
                    <div>
                      Metode
                      <strong>{humanLabel("method", progress.acquisition_method)}</strong>
                    </div>
                  )}
                </div>
              </details>

              {p.session.status === "completed" && (p.canFindings || p.canReport || p.canDashboard) && (
                <div className="actions actions-spaced">
                  {p.canFindings && (
                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={() => p.onNavigateTab("findings")}
                    >
                      Buka temuan
                    </button>
                  )}
                  {p.canReport && (
                    <button
                      className="btn btn-ghost"
                      type="button"
                      onClick={() => p.onNavigateTab("report")}
                    >
                      Buka laporan
                    </button>
                  )}
                  {p.canDashboard && (
                    <button
                      className="btn btn-ghost"
                      type="button"
                      onClick={() => p.onNavigateTab("dashboard")}
                    >
                      Buka ikhtisar
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </FeaturePanel>
      </div>
    </div>
  );
}
