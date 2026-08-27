import { useEffect, useState, type RefObject } from "react";
import {
  ms,
  type AcquisitionMode,
  type AnalysisScope,
  type DeviceInfo,
  type SessionSummary,
} from "@/shared/api/client";
import { FeaturePanel } from "@/shared/ui/FeaturePanel";
import { PipelineTrack } from "@/features/operator/components/PipelineTrack";
import { StatusPill } from "@/shared/ui/StatusPill";
import { VerdictNotice } from "@/features/findings/components/VerdictNotice";
import { ACTIVE, isThreatRecommendation } from "@/shared/constants";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import { FEATURE_PAGE_META, OPERATOR_TELEMETRY_META } from "@/shared/lib/featurePages";
import {
  ANALYSIS_SCOPE_OPTIONS,
  DEVICE_SOURCE_OPTIONS,
  SOCIAL_TARGET_OPTIONS,
  analysisPlanReady,
  planForScope,
  toggleChecked,
} from "@/features/operator/analysisScope";
import { IosSetupPanel } from "@/features/operator/IosSetupPanel";
import type { useIosSetup } from "@/features/operator/useIosSetup";

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
  mode: AcquisitionMode;
  setMode: (m: AcquisitionMode) => void;
  analysisScope: AnalysisScope;
  setAnalysisScope: (scope: AnalysisScope) => void;
  deviceSources: string[];
  setDeviceSources: (sources: string[]) => void;
  socialTargets: string[];
  setSocialTargets: (targets: string[]) => void;
  canStartLive: boolean;
  canStartZip: boolean;
  iosSetup: ReturnType<typeof useIosSetup>;
  busy: boolean;
  session: SessionSummary | null;
  start: () => void;
  startZip: () => void;
  cancel: () => void;
  onNavigateTab: (t: "findings" | "report" | "dashboard") => void;
  canFindings: boolean;
  canReport: boolean;
  canDashboard: boolean;
};

export function OperatorPage(p: Props) {
  const progress = p.session?.progress;
  const timing = p.session?.timing;
  const active = !!p.session && ACTIVE.has(p.session.status);
  const planReady = analysisPlanReady(p.analysisScope, p.deviceSources, p.socialTargets);
  const showDeviceChecks = p.analysisScope !== "social";
  const showSocialChecks = p.analysisScope !== "device";
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
  const depthReady = sourceReady && !!p.mode;
  const runReady = identityReady && (p.acqSource === "live" ? p.canStartLive : p.canStartZip);

  return (
    <div className="ent-operator">
      <div className="grid-2">
        <FeaturePanel meta={FEATURE_PAGE_META.operator}>
          <ol className="ent-intake-steps" aria-label="Langkah penerimaan">
            <li className={identityReady ? "on" : ""}>
              <span>1</span> Identitas
            </li>
            <li className={sourceReady ? "on" : ""}>
              <span>2</span> Sumber
            </li>
            <li className={depthReady ? "on" : ""}>
              <span>3</span> Kedalaman
            </li>
            <li className={runReady ? "on" : ""}>
              <span>4</span> Jalankan
            </li>
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
                <option value="live">Perangkat live (ADB / iOS)</option>
                <option value="zip" disabled={!p.zipEnabled}>
                  Unggah ZIP hasil ADB{" "}
                  {p.zipEnabled ? "(tanpa akuisisi)" : "(dinonaktifkan server)"}
                </option>
              </select>
            </div>

            {p.acqSource === "live" && (
              <div className="field">
                <label id="device-list-label">Perangkat live</label>
                <div className="actions field-actions">
                  <button
                    className="btn btn-ghost"
                    type="button"
                    onClick={() => p.refreshDevices().catch(console.error)}
                  >
                    Pindai ulang USB
                  </button>
                </div>
                <div className="device-list" role="listbox" aria-labelledby="device-list-label">
                  {p.liveDevices.length === 0 && (
                    <div className="empty empty-soft">
                      Tidak ada HP — sambungkan USB debug, atau ganti sumber ke Unggah ZIP
                    </div>
                  )}
                  {p.liveDevices.map((d) => {
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
                        <span className="pill muted">{d.device_type}</span>
                      </button>
                    );
                  })}
                </div>
                {p.iosSetup.visible && (
                  <IosSetupPanel
                    status={p.iosSetup.status}
                    busy={p.iosSetup.busy}
                    code={p.iosSetup.code}
                    setCode={p.iosSetup.setCode}
                    error={p.iosSetup.error}
                    showWdaSteps={p.iosSetup.showWdaSteps}
                    disabled={p.busy || active}
                    onStart={() => void p.iosSetup.start()}
                    onSubmitCode={() => void p.iosSetup.submitCode()}
                    onAckTrust={() => void p.iosSetup.ackTrust()}
                    onCancel={() => void p.iosSetup.cancel()}
                  />
                )}
              </div>
            )}

            {p.acqSource === "zip" && p.zipEnabled && (
              <div className="field">
                <label htmlFor="zip-file">Arsip ZIP (hasil adb pull / dump media)</label>
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

            <div className="field">
              <label htmlFor="acq-mode">Kedalaman analisa</label>
              <select
                id="acq-mode"
                value={p.mode}
                onChange={(e) => p.setMode(e.target.value as AcquisitionMode)}
                disabled={p.busy || active}
              >
                <option value="quick">Cepat — sampling lebih ringkas</option>
                <option value="full">Penuh — cakupan lebih luas</option>
              </select>
            </div>

            <div className="field-group" role="group" aria-labelledby="analysis-scope-heading">
              <p id="analysis-scope-heading" className="field-group-title">
                Fokus analisa
              </p>
              <div
                className="analysis-scope-grid"
                role="radiogroup"
                aria-labelledby="analysis-scope-heading"
              >
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
                    Gabungan menganalisa sumber HP dan crawl sosmed sekaligus. Kurangi checklist
                    jika waktu terbatas, atau pilih <strong>HP saja</strong> untuk jalur cepat.
                  </p>
                </div>
              )}
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
                            onChange={() =>
                              p.setDeviceSources(toggleChecked(p.deviceSources, option.id))
                            }
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
                            onChange={() =>
                              p.setSocialTargets(toggleChecked(p.socialTargets, option.id))
                            }
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
                  className="btn btn-primary ent-btn-shine"
                  disabled={!p.canStartLive}
                  onClick={p.start}
                >
                  {p.busy ? "Memulai…" : "Jalankan akuisisi"}
                </button>
              ) : (
                <button
                  className="btn btn-primary ent-btn-shine"
                  disabled={!p.canStartZip}
                  onClick={p.startZip}
                >
                  {p.busy ? "Mengunggah…" : "Analisa ZIP"}
                </button>
              )}
              {p.session && ACTIVE.has(p.session.status) && (
                <button className="btn btn-danger" onClick={p.cancel} disabled={p.busy}>
                  Batalkan
                </button>
              )}
            </div>
            {!identityReady && (
              <p className="field-note">Isi nama dan no. peserta sebelum menjalankan akuisisi.</p>
            )}
            {p.acqSource === "live" &&
              p.selected?.device_type === "ios" &&
              !p.selected.simulated &&
              !p.iosSetup.readyForAcquire && (
                <p className="field-note">
                  {p.analysisScope === "device"
                    ? "Selesaikan USB Trust iPhone sebelum akuisisi."
                    : "Selesaikan Siapkan iPhone (WDA) sebelum akuisisi sosmed."}
                </p>
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
              <p className="standby-title">Pipeline siap</p>
              <p className="standby-copy">
                Isi identitas peserta, pilih perangkat live atau unggah ZIP, lalu jalankan. Live
                Android otomatis membangun dan memasang APK agent terbaru. iOS: siapkan iPhone
                (USB Trust, Developer Mode, WebDriverAgent) sebelum akuisisi sosmed.
              </p>
              <PipelineTrack />
            </div>
          ) : (
            <>
              <PipelineTrack status={p.session.status} session={p.session} />
              <div className="tel-live">
                <StatusPill status={p.session.status} recommendation={p.session.recommendation} />
                <span className="pill muted">{p.session.mode === "full" ? "Penuh" : "Cepat"}</span>
                <span className="pill muted">
                  {humanLabel("method", progress?.acquisition_method || "unknown")}
                </span>
                {p.session.participant?.full_name ? (
                  <span className="pill muted">{p.session.participant.full_name}</span>
                ) : (
                  <span className="pill muted">{p.session.device_id}</span>
                )}
              </div>

              {p.session.error && <div className="error-banner spaced">{p.session.error}</div>}

              {p.session.status === "awaiting_access" && (
                <div className="access-hint" role="alert">
                  <strong>Izin perangkat diperlukan</strong>
                  <p>
                    Di HP calon: aktifkan <strong>Accessibility SATRIA</strong> (wajib). Mode{" "}
                    <strong>Penuh</strong> juga membutuhkan izin akses semua file. Notification
                    Listener dicoba otomatis — jika gagal, sesi tetap lanjut parsial.
                  </p>
                </div>
              )}

              {p.session.status === "completed" && (
                <VerdictNotice recommendation={p.session.recommendation} />
              )}

              <div className="progress-wrap">
                <div className="progress-meta">
                  <span>{progress?.message}</span>
                  <strong>{progress?.percent?.toFixed(0) ?? 0}%</strong>
                </div>
                <div className={`bar ${ACTIVE.has(p.session.status) ? "active" : ""}`}>
                  <span style={{ width: `${progress?.percent ?? 0}%` }} />
                </div>
              </div>

              <div className="timing">
                <div>
                  Rekam
                  <strong>
                    {selectedRecords}
                    {inventoried ? ` / ${inventoried}` : ""}
                  </strong>
                </div>
                <div>
                  Berkas
                  <strong>{indexedFiles}</strong>
                </div>
                <div>
                  Dianalisis
                  <strong>{progress?.files_analyzed ?? 0}</strong>
                </div>
                <div>
                  Temuan
                  <strong>{progress?.findings_count ?? 0}</strong>
                </div>
                <div>
                  Total waktu
                  <strong>{ms(liveTotalMs)}</strong>
                </div>
              </div>

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
                      Buka dasbor
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
