import type { RefObject } from "react";
import { ms, type AcquisitionMode, type DeviceInfo, type SessionSummary } from "../api";
import { PanelTitle } from "../components/PanelTitle";
import { PipelineTrack } from "../components/PipelineTrack";
import { StatusPill } from "../components/StatusPill";
import { VerdictNotice } from "../components/VerdictNotice";
import { ACTIVE, isThreatRecommendation } from "../constants";

type Props = {
  teleRef: RefObject<HTMLElement | null>;
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
  modeHint: string;
  canStartLive: boolean;
  canStartZip: boolean;
  busy: boolean;
  session: SessionSummary | null;
  start: () => void;
  startZip: () => void;
  cancel: () => void;
  onNavigateTab: (t: "findings" | "report" | "dashboard") => void;
  canDashboard: boolean;
};

export function OperatorPage(p: Props) {
  const progress = p.session?.progress;
  const timing = p.session?.timing;

  return (
    <div className="grid-2">
      <section className="panel">
        <PanelTitle title="Akuisisi target" />
        <div className="form-grid">
          <div className="field">
            <label htmlFor="acq-source">Sumber analisa</label>
            <select
              id="acq-source"
              value={p.acqSource === "zip" && !p.zipEnabled ? "live" : p.acqSource}
              onChange={(e) => p.setAcqSource(e.target.value as "live" | "zip")}
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
              <p className="login-hint">
                ZIP dipakai bila HP tidak tersedia di workstation. Isi folder galeri/DCIM seperti
                hasil akuisisi.
              </p>
            </div>
          )}

          <div className="field">
            <label htmlFor="acq-mode">Mode analisa</label>
            <select
              id="acq-mode"
              value={p.mode}
              onChange={(e) => p.setMode(e.target.value as AcquisitionMode)}
            >
              <option value="quick">Cepat — sampling lebih ringkas</option>
              <option value="full">Penuh — cakupan lebih luas</option>
            </select>
            <p className="mode-hint">{p.modeHint}</p>
          </div>

          <div className="actions">
            {p.acqSource === "live" ? (
              <button className="btn btn-primary" disabled={!p.canStartLive} onClick={p.start}>
                {p.busy ? "Memulai…" : "Jalankan akuisisi"}
              </button>
            ) : (
              <button className="btn btn-primary" disabled={!p.canStartZip} onClick={p.startZip}>
                {p.busy ? "Mengunggah…" : "Analisa ZIP"}
              </button>
            )}
            {p.session && ACTIVE.has(p.session.status) && (
              <button className="btn btn-danger" onClick={p.cancel} disabled={p.busy}>
                Batalkan
              </button>
            )}
          </div>
        </div>
      </section>

      <section
        ref={p.teleRef}
        className={`panel${isThreatRecommendation(p.session?.recommendation) ? " threat" : ""}`}
      >
        <PanelTitle title="Telemetri sesi" />
        {!p.session ? (
          <div className="standby">
            <p className="standby-title">Pipeline siap</p>
            <p className="standby-copy">
              Pilih perangkat live atau unggah ZIP dump, lalu jalankan. Alur: Deteksi/Unggah →
              Akuisisi → Indeks → Analisa → Temuan.
            </p>
            <PipelineTrack />
          </div>
        ) : (
          <>
            <PipelineTrack status={p.session.status} />
            <div className="tel-live">
              <StatusPill status={p.session.status} recommendation={p.session.recommendation} />
              <span className="pill muted">{p.session.mode === "full" ? "Penuh" : "Cepat"}</span>
              <span className="pill muted">{progress?.acquisition_method || "…"}</span>
              <span className="pill muted">{p.session.device_id}</span>
            </div>

            {p.session.error && <div className="error-banner spaced">{p.session.error}</div>}

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
                Masuk
                <strong>
                  {progress?.files_pulled ?? 0}
                  {progress?.files_listed ? ` / ${progress.files_listed}` : ""}
                </strong>
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
                <strong>{ms(timing?.t_total_ms ?? 0)}</strong>
              </div>
            </div>

            <details className="tech-collapse oper-tech">
              <summary>Rincian waktu & teknis</summary>
              <div className="timing">
                <div>
                  Deteksi
                  <strong>{ms(timing?.t_detect_ms ?? 0)}</strong>
                </div>
                <div>
                  Akuisisi
                  <strong>{ms(timing?.t_acquire_ms ?? 0)}</strong>
                </div>
                <div>
                  Indeks
                  <strong>{ms(timing?.t_index_ms ?? 0)}</strong>
                </div>
                <div>
                  Analisa
                  <strong>{ms(timing?.t_analyze_ms ?? 0)}</strong>
                </div>
                <div>
                  L3 / L4
                  <strong>
                    {progress?.hits_l3 ?? 0} / {progress?.hits_l4 ?? 0}
                  </strong>
                </div>
                <div>
                  OCR
                  <strong>{progress?.hits_ocr ?? 0}</strong>
                </div>
                <div>
                  ASR
                  <strong>{progress?.hits_asr ?? 0}</strong>
                </div>
                <div>
                  Throughput
                  <strong>{progress?.throughput_files_per_sec ?? 0} b/d</strong>
                </div>
              </div>
            </details>

            {p.session.status === "completed" && (
              <div className="actions actions-spaced">
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() => p.onNavigateTab("findings")}
                >
                  Buka temuan
                </button>
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={() => p.onNavigateTab("report")}
                >
                  Buka laporan
                </button>
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
      </section>
    </div>
  );
}
