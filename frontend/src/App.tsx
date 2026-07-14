import { useEffect, useMemo, useRef, useState, useTransition, type FormEvent } from "react";
import { DEFAULT_PAGE_SIZE, Pagination } from "./Pagination";
import {
  api,
  can,
  loadAuth,
  ms,
  saveAuth,
  type AcquisitionMode,
  type AuthSession,
  type DashboardStats,
  type DeviceInfo,
  type Finding,
  type Paginated,
  type SessionSummary,
} from "./api";
import { DistBars } from "./components/DistBars";
import { LoginScreen } from "./components/LoginScreen";
import { PipelineTrack } from "./components/PipelineTrack";
import { StatusPill } from "./components/StatusPill";
import { ACTIVE, TAB_PERMS } from "./constants";
import type { Tab } from "./types";

export default function App() {
  const [auth, setAuth] = useState<AuthSession | null>(() => loadAuth());
  const [loginUser, setLoginUser] = useState("operator");
  const [loginPass, setLoginPass] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("operator");
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [selected, setSelected] = useState<DeviceInfo | null>(null);
  const [mode, setMode] = useState<AcquisitionMode>("quick");
  const [fileCount] = useState(1200);
  const [acqSource, setAcqSource] = useState<"live" | "zip">("live");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [dash, setDash] = useState<DashboardStats | null>(null);
  const [findingsData, setFindingsData] = useState<Paginated<Finding> | null>(null);
  const [findingsPage, setFindingsPage] = useState(1);
  const [reportFindings, setReportFindings] = useState<Paginated<Finding> | null>(null);
  const [reportPage, setReportPage] = useState(1);
  const [dashSessions, setDashSessions] = useState<Paginated<SessionSummary> | null>(null);
  const [dashSessionsPage, setDashSessionsPage] = useState(1);
  const [dashFindings, setDashFindings] = useState<Paginated<Finding> | null>(null);
  const [dashFindingsPage, setDashFindingsPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [gpu, setGpu] = useState(false);
  const [toolchain, setToolchain] = useState<Record<string, boolean>>({});
  const [vision, setVision] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [, startTransition] = useTransition();
  const teleRef = useRef<HTMLElement | null>(null);

  const allowedTabs = useMemo(() => {
    const all: { id: Tab; label: string }[] = [
      { id: "operator", label: "Operator" },
      { id: "dashboard", label: "Dasbor" },
      { id: "findings", label: "Temuan" },
      { id: "report", label: "Laporan" },
    ];
    return all.filter((t) => can(auth, TAB_PERMS[t.id]));
  }, [auth]);

  useEffect(() => {
    if (!auth || allowedTabs.length === 0) return;
    if (!allowedTabs.some((t) => t.id === tab)) {
      setTab(allowedTabs[0].id);
    }
  }, [auth, allowedTabs, tab]);

  async function refreshDevices() {
    const [h, d] = await Promise.all([api.health(), api.devices()]);
    setGpu(h.gpu_available);
    setToolchain(h.extras?.toolchain || {});
    setVision((h.extras as { vision?: Record<string, unknown> })?.vision || {});
    const live = d.filter((x) => !x.simulated);
    setDevices(live);
    setSelected((prev) => live.find((x) => x.device_id === prev?.device_id) ?? live[0] ?? null);
  }

  useEffect(() => {
    if (!auth) return;
    refreshDevices().catch((e) => {
      const msg = e instanceof Error ? e.message : "Gagal memuat API";
      setError(msg);
      if (String(msg).toLowerCase().includes("autentikasi")) {
        saveAuth(null);
        setAuth(null);
      }
    });
  }, [auth]);

  useEffect(() => {
    if (!session || !ACTIVE.has(session.status)) return;
    const t = setInterval(async () => {
      try {
        const s = await api.session(session.id);
        startTransition(() => setSession(s));
        if (!ACTIVE.has(s.status)) {
          const f = await api.findings(s.id, 1, DEFAULT_PAGE_SIZE);
          setFindingsData(f);
          setFindingsPage(1);
        }
      } catch {
        /* ignore */
      }
    }, 400);
    return () => clearInterval(t);
  }, [session?.id, session?.status]);

  useEffect(() => {
    if (tab !== "dashboard") return;
    setDash(null);
    Promise.all([
      api.dashboard(),
      api.sessions(dashSessionsPage, DEFAULT_PAGE_SIZE),
      api.findings(undefined, dashFindingsPage, DEFAULT_PAGE_SIZE),
    ])
      .then(([d, sessionsRes, findingsRes]) => {
        setError(null);
        setDash(d);
        setDashSessions(sessionsRes);
        setDashFindings(findingsRes);
      })
      .catch((e) => {
        setDash(null);
        setDashSessions(null);
        setDashFindings(null);
        setError(String(e.message || e));
      });
  }, [tab, dashSessionsPage, dashFindingsPage]);

  useEffect(() => {
    if (tab !== "findings") return;
    api
      .findings(session?.id, findingsPage, DEFAULT_PAGE_SIZE)
      .then((data) => {
        setFindingsData(data);
        setFindingsPage(data.page);
      })
      .catch((e) => setError(String(e.message || e)));
  }, [tab, session?.id, findingsPage]);

  useEffect(() => {
    if (tab !== "report" || !session?.id) return;
    api
      .findings(session.id, reportPage, DEFAULT_PAGE_SIZE)
      .then((data) => {
        setReportFindings(data);
        setReportPage(data.page);
      })
      .catch((e) => setError(String(e.message || e)));
  }, [tab, session?.id, reportPage]);

  useEffect(() => {
    setFindingsPage(1);
  }, [session?.id]);

  useEffect(() => {
    setReportPage(1);
  }, [session?.id]);

  const progress = session?.progress;
  const timing = session?.timing;
  const liveDevices = devices.filter((d) => !d.simulated);
  const canStartLive = useMemo(
    () =>
      acqSource === "live" &&
      !!selected &&
      !selected.simulated &&
      !busy &&
      !(session && ACTIVE.has(session.status)),
    [acqSource, selected, busy, session],
  );
  const canStartZip = useMemo(
    () =>
      acqSource === "zip" &&
      !!zipFile &&
      !busy &&
      !(session && ACTIVE.has(session.status)),
    [acqSource, zipFile, busy, session],
  );

  async function start() {
    if (!selected || selected.simulated) return;
    setError(null);
    setBusy(true);
    try {
      const s = await api.startSession({
        device_id: selected.device_id,
        device_type: selected.device_type === "simulated" ? "android" : selected.device_type,
        mode,
        scenario: "lulus",
        file_count: fileCount,
        label: selected.label,
        force_simulated: false,
      });
      setSession(s);
      setFindingsData(null);
      setFindingsPage(1);
      setReportFindings(null);
      setReportPage(1);
      setTab("operator");
      requestAnimationFrame(() => {
        teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memulai sesi");
    } finally {
      setBusy(false);
    }
  }

  async function startZip() {
    if (!zipFile) return;
    setError(null);
    setBusy(true);
    try {
      const s = await api.startSessionFromZip(zipFile, {
        mode,
        label: `ZIP · ${zipFile.name}`,
      });
      setSession(s);
      setFindingsData(null);
      setFindingsPage(1);
      setReportFindings(null);
      setReportPage(1);
      setTab("operator");
      requestAnimationFrame(() => {
        teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal analisa ZIP");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!session) return;
    setBusy(true);
    try {
      setSession(await api.cancelSession(session.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal membatalkan");
    } finally {
      setBusy(false);
    }
  }

  async function review(id: string, review_status: "confirmed" | "rejected") {
    await api.reviewFinding(id, review_status);
    const patch = (prev: Paginated<Finding> | null) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((f) => (f.id === id ? { ...f, review_status } : f)),
          }
        : prev;
    setFindingsData(patch);
    setReportFindings(patch);
    setDashFindings(patch);
  }

  async function doLogin(e?: FormEvent) {
    e?.preventDefault();
    setLoginBusy(true);
    setError(null);
    try {
      const sessionAuth = await api.login(loginUser, loginPass);
      saveAuth(sessionAuth);
      setAuth(sessionAuth);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login gagal");
    } finally {
      setLoginBusy(false);
    }
  }

  async function doLogout() {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    saveAuth(null);
    setAuth(null);
    setSession(null);
    setFindingsData(null);
    setReportFindings(null);
    setDash(null);
  }

  if (!auth) {
    return (
      <LoginScreen
        loginUser={loginUser}
        loginPass={loginPass}
        loginBusy={loginBusy}
        error={error}
        onUserChange={setLoginUser}
        onPassChange={setLoginPass}
        onPickDemo={(user, pass) => {
          setLoginUser(user);
          setLoginPass(pass);
        }}
        onSubmit={doLogin}
      />
    );
  }

  const ocrOn =
    !!(vision.ocr as { enabled?: boolean } | undefined)?.enabled &&
    !!(vision.ocr as { available?: boolean } | undefined)?.available;

  return (
    <div className="app-shell wide">
      <div className="classify-rail">
        <span>Restriksi internal · Defense &amp; Intel</span>
        <span>Galeri · PoC</span>
      </div>

      <header className="ops-topbar">
        <div className="ops-brand">
          <strong>SADT // OPS</strong>
          <span>Sistem Analisis Digital Terpadu</span>
        </div>
        <div className="user-chip compact">
          <div>
            <strong>{auth.display_name}</strong>
            <span>
              {auth.username} · {auth.role}
            </span>
          </div>
          <button className="btn btn-ghost" type="button" onClick={doLogout}>
            Logout
          </button>
        </div>
      </header>

      <nav className="tabs">
        {allowedTabs.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      {error && <div className="error-banner">{error}</div>}

      {tab === "operator" && can(auth, "sessions:start") && (
        <div className="grid-2">
          <section className="panel">
            <div className="panel-title">
              <h2>Akuisisi target</h2>
              <span className="code">MOD-ACQ-01</span>
            </div>
            <div className="form-grid">
              <div className="field">
                <label>Sumber analisa</label>
                <select
                  value={acqSource}
                  onChange={(e) => setAcqSource(e.target.value as "live" | "zip")}
                >
                  <option value="live">Perangkat live (ADB / iOS)</option>
                  <option value="zip">Upload ZIP hasil ADB (tanpa akuisisi)</option>
                </select>
              </div>

              {acqSource === "live" && (
                <div className="field">
                  <label>Perangkat live</label>
                  <div className="actions" style={{ marginTop: 0, marginBottom: 10 }}>
                    <button
                      className="btn btn-ghost"
                      type="button"
                      onClick={() => refreshDevices().catch(console.error)}
                    >
                      Rescan USB
                    </button>
                  </div>
                  <div className="device-list">
                    {liveDevices.length === 0 && (
                      <div className="empty" style={{ padding: 14 }}>
                        Tidak ada HP terdeteksi — sambungkan Android (USB debug) atau iPhone
                      </div>
                    )}
                    {liveDevices.map((d) => (
                      <div
                        key={d.device_id}
                        className={`device-item ${selected?.device_id === d.device_id ? "selected" : ""}`}
                        onClick={() => setSelected(d)}
                      >
                        <div>
                          <strong>{d.label}</strong>
                          <br />
                          <small>
                            {d.device_type} · OS {d.os_version || "?"} · LIVE ·{" "}
                            {d.device_type === "android" ? "ADB pull" : "idevicebackup2"}
                          </small>
                        </div>
                        <span className="pill ok">LIVE</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {acqSource === "zip" && (
                <div className="field">
                  <label>Arsip ZIP (hasil adb pull / dump media)</label>
                  <input
                    type="file"
                    accept=".zip,application/zip"
                    onChange={(e) => setZipFile(e.target.files?.[0] ?? null)}
                  />
                  {zipFile && (
                    <small style={{ display: "block", marginTop: 8, opacity: 0.75 }}>
                      {zipFile.name} · {(zipFile.size / (1024 * 1024)).toFixed(1)} MB
                    </small>
                  )}
                  <p className="login-hint" style={{ marginTop: 8 }}>
                    Struktur bebas: folder DCIM/Pictures/Download, atau flat file media. Maks sesuai
                    server (default 512 MB).
                  </p>
                </div>
              )}

              <div className="field">
                <label>Mode analisa</label>
                <select value={mode} onChange={(e) => setMode(e.target.value as AcquisitionMode)}>
                  <option value="quick">QUICK — sampling lebih cepat</option>
                  <option value="full">FULL — cakupan lebih luas</option>
                </select>
              </div>

              <div className="actions">
                {acqSource === "live" ? (
                  <button className="btn btn-primary" disabled={!canStartLive} onClick={start}>
                    {busy ? "Memulai…" : "Jalankan akuisisi"}
                  </button>
                ) : (
                  <button className="btn btn-primary" disabled={!canStartZip} onClick={startZip}>
                    {busy ? "Mengunggah…" : "Analisa ZIP"}
                  </button>
                )}
                {session && ACTIVE.has(session.status) && (
                  <button className="btn btn-danger" onClick={cancel} disabled={busy}>
                    Batalkan
                  </button>
                )}
              </div>
            </div>
          </section>

          <section
            ref={teleRef}
            className={`panel${session?.recommendation === "TIDAK LULUS" ? " threat" : ""}`}
          >
            <div className="panel-title">
              <h2>Telemetry sesi</h2>
              <span className="code">MOD-TEL-02</span>
            </div>
            {!session ? (
              <div className="standby">
                <p className="standby-title">Pipeline siap</p>
                <p className="standby-copy">
                  Pilih perangkat, lalu jalankan sesi. Alur: Detect → Acquire → Index → Analyze →
                  Findings.
                </p>
                <PipelineTrack />
              </div>
            ) : (
              <>
                <PipelineTrack status={session.status} />
                <div className="tel-live">
                  <StatusPill status={session.status} recommendation={session.recommendation} />
                  <span className="pill muted">{session.mode}</span>
                  <span className="pill muted">{progress?.acquisition_method || "…"}</span>
                  <span className="pill muted">{session.device_id}</span>
                </div>

                {session.error && <div className="error-banner" style={{ marginTop: 12 }}>{session.error}</div>}

                <div className="progress-wrap">
                  <div className="progress-meta">
                    <span>{progress?.message}</span>
                    <strong>{progress?.percent?.toFixed(0) ?? 0}%</strong>
                  </div>
                  <div className={`bar ${session && ACTIVE.has(session.status) ? "active" : ""}`}>
                    <span style={{ width: `${progress?.percent ?? 0}%` }} />
                  </div>
                </div>

                <div className="timing">
                  <div>
                    Ingest
                    <strong>
                      {progress?.files_pulled ?? 0}
                      {progress?.files_listed ? ` / ${progress.files_listed}` : ""}
                    </strong>
                  </div>
                  <div>
                    Analyzed
                    <strong>{progress?.files_analyzed ?? 0}</strong>
                  </div>
                  <div>
                    Hits
                    <strong>{progress?.findings_count ?? 0}</strong>
                  </div>
                  <div>
                    Throughput
                    <strong>{progress?.throughput_files_per_sec ?? 0} f/s</strong>
                  </div>
                </div>

                <h3 style={{ marginTop: 18 }}>Time breakdown</h3>
                <div className="timing">
                  <div>
                    Detect
                    <strong>{ms(timing?.t_detect_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Acquire
                    <strong>{ms(timing?.t_acquire_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Index
                    <strong>{ms(timing?.t_index_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Analyze
                    <strong>{ms(timing?.t_analyze_ms ?? 0)}</strong>
                  </div>
                  <div>
                    Total
                    <strong>{ms(timing?.t_total_ms ?? 0)}</strong>
                  </div>
                </div>

                {session.status === "completed" && (
                  <div className="actions" style={{ marginTop: 16 }}>
                    <button className="btn btn-ghost" onClick={() => setTab("findings")}>
                      Open findings
                    </button>
                    <button className="btn btn-ghost" onClick={() => setTab("report")}>
                      Open report
                    </button>
                    <button className="btn btn-ghost" onClick={() => setTab("dashboard")}>
                      Open dasbor
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}

      {tab === "dashboard" && can(auth, "dashboard") && (
        <section className="panel">
            <div className="panel-title">
              <h2>Dasbor operasional</h2>
              <span className="code">MOD-DASH-03</span>
            </div>
          {!dash ? (
            <div className="empty">SYNC…</div>
          ) : (
            <>
              <div className="grid-stats">
                <div className="stat">
                  <div className="label">Sesi</div>
                  <div className="value">
                    {dash.completed_sessions ?? 0}/{dash.total_sessions ?? 0}
                  </div>
                </div>
                <div className="stat threat">
                  <div className="label">TIDAK LULUS</div>
                  <div className="value">{dash.tidak_lulus_count ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="label">LULUS</div>
                  <div className="value">{dash.lulus_count ?? 0}</div>
                </div>
                <div className="stat threat">
                  <div className="label">Temuan</div>
                  <div className="value">{dash.total_findings ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="label">Pending review</div>
                  <div className="value">{dash.pending_reviews ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="label">Confirmed</div>
                  <div className="value">{dash.confirmed_findings ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="label">Avg total</div>
                  <div className="value">{ms(dash.avg_total_ms ?? 0)}</div>
                </div>
                <div className="stat">
                  <div className="label">Peak f/s</div>
                  <div className="value">{(dash.throughput_peak_fps ?? 0).toFixed(0)}</div>
                </div>
              </div>

              <div className="timing" style={{ marginBottom: 18 }}>
                <div>
                  Avg acquire
                  <strong>{ms(dash.avg_acquire_ms ?? 0)}</strong>
                </div>
                <div>
                  Avg index
                  <strong>{ms(dash.avg_index_ms ?? 0)}</strong>
                </div>
                <div>
                  Avg analyze
                  <strong>{ms(dash.avg_analyze_ms ?? 0)}</strong>
                </div>
                <div>
                  Failed / Active
                  <strong>
                    {dash.failed_sessions ?? 0} / {dash.active_sessions ?? 0}
                  </strong>
                </div>
              </div>

              <div className="grid-3">
                <DistBars title="Temuan / kategori" items={dash.findings_by_category} />
                <DistBars title="Temuan / layer AI" items={dash.findings_by_layer} />
                <DistBars title="Temuan / sumber" items={dash.findings_by_source} />
              </div>

              <div className="grid-3" style={{ marginTop: 14 }}>
                <DistBars title="Metode akuisisi" items={dash.acquisition_methods} />
                <div className="dist-card">
                  <h3>Toolchain</h3>
                  <div className="tool-pills" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                    <span className={`pill ${dash.toolchain?.adb ? "ok" : "muted"}`}>
                      ADB {dash.toolchain?.adb ? "READY" : "MISSING"}
                    </span>
                    <span className={`pill ${dash.toolchain?.idevice_id ? "ok" : "muted"}`}>
                      idevice_id {dash.toolchain?.idevice_id ? "READY" : "MISSING"}
                    </span>
                    <span className={`pill ${dash.toolchain?.idevicebackup2 ? "ok" : "muted"}`}>
                      idevicebackup2 {dash.toolchain?.idevicebackup2 ? "READY" : "MISSING"}
                    </span>
                    <span className={`pill ${dash.gpu_available ? "ok" : "muted"}`}>
                      GPU {dash.gpu_available ? "READY" : "CPU MODE"}
                    </span>
                  </div>
                </div>
                <div className="dist-card">
                  <h3>Temuan terbaru</h3>
                  {!dashFindings || dashFindings.total === 0 ? (
                    <div className="empty" style={{ padding: 12 }}>
                      —
                    </div>
                  ) : (
                    <>
                      <div className="recent-list">
                        {dashFindings.items.map((f) => (
                          <div key={f.id} className="recent-item">
                            <strong>{f.label}</strong>
                            <span>
                              {f.source} · {(f.confidence * 100).toFixed(0)}% · {f.layer_origin}
                            </span>
                          </div>
                        ))}
                      </div>
                      <Pagination
                        page={dashFindings.page}
                        pages={dashFindings.pages}
                        total={dashFindings.total}
                        page_size={dashFindings.page_size}
                        onPage={setDashFindingsPage}
                        label="Temuan"
                      />
                    </>
                  )}
                </div>
              </div>

              <h3 style={{ marginTop: 18 }}>Sesi terakhir</h3>
              {!dashSessions || dashSessions.total === 0 ? (
                <div className="empty">Belum ada sesi</div>
              ) : (
                <>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Perangkat</th>
                        <th>Status</th>
                        <th>Method</th>
                        <th>Mode</th>
                        <th>Total</th>
                        <th>Hits</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dashSessions.items.map((s) => (
                        <tr key={s.id}>
                          <td>
                            {s.label}
                            <div className="evidence">{s.device_id}</div>
                          </td>
                          <td>
                            <StatusPill status={s.status} recommendation={s.recommendation} />
                          </td>
                          <td>{s.progress?.acquisition_method || "—"}</td>
                          <td>{s.mode}</td>
                          <td>{ms(s.timing?.t_total_ms ?? 0)}</td>
                          <td>{s.progress?.findings_count ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
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
            </>
          )}
        </section>
      )}

      {tab === "findings" && can(auth, "findings:read") && (
        <section className={`panel${(findingsData?.total ?? 0) > 0 ? " threat" : ""}`}>
          <div className="panel-title">
            <h2>Daftar temuan</h2>
            <span className="code">MOD-HIT-04</span>
          </div>
          {!findingsData || findingsData.total === 0 ? (
            <div className="empty">Belum ada temuan</div>
          ) : (
            <>
              <table className="table">
                <thead>
                  <tr>
                    <th>Label</th>
                    <th>Sumber</th>
                    <th>Layer</th>
                    <th>Confidence</th>
                    <th>Evidence</th>
                    <th>Verifikasi</th>
                  </tr>
                </thead>
                <tbody>
                  {findingsData.items.map((f) => (
                    <tr key={f.id} className="hit-row">
                      <td>
                        <strong>{f.label}</strong>
                        <div className="evidence">{f.category}</div>
                      </td>
                      <td>
                        {f.source}
                        <div className="evidence">{f.path}</div>
                      </td>
                      <td>
                        <span className="pill muted">{f.layer_origin}</span>
                      </td>
                      <td>{(f.confidence * 100).toFixed(0)}%</td>
                      <td className="evidence">{f.evidence}</td>
                      <td>
                        {f.review_status === "pending" ? (
                          can(auth, "findings:review") ? (
                            <div className="row-actions">
                              <button onClick={() => review(f.id, "confirmed")}>Confirm</button>
                              <button onClick={() => review(f.id, "rejected")}>Reject</button>
                            </div>
                          ) : (
                            <span className="pill warn">pending</span>
                          )
                        ) : (
                          <span className={`pill ${f.review_status === "confirmed" ? "bad" : "muted"}`}>
                            {f.review_status}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination
                page={findingsData.page}
                pages={findingsData.pages}
                total={findingsData.total}
                page_size={findingsData.page_size}
                onPage={setFindingsPage}
              />
            </>
          )}
        </section>
      )}

      {tab === "report" && can(auth, "report:read") && (
        <section className={`panel${session?.recommendation === "TIDAK LULUS" ? " threat" : ""}`}>
          <div className="panel-title">
            <h2>Laporan sesi</h2>
            <span className="code">MOD-RPT-05</span>
          </div>
          {!session ? (
            <div className="empty">Jalankan sesi terlebih dahulu</div>
          ) : (
            <>
              {session.recommendation === "TIDAK LULUS" && (
                <div className="verdict fail">Rekomendasi akhir · Tidak Lulus</div>
              )}
              {session.recommendation === "LULUS" && (
                <div className="verdict">Rekomendasi akhir · Lulus</div>
              )}
              <div className="timing" style={{ marginBottom: 14, marginTop: 14 }}>
                <div>
                  Session
                  <strong>{session.id.slice(0, 8)}…</strong>
                </div>
                <div>
                  Rekomendasi
                  <strong>{session.recommendation || "—"}</strong>
                </div>
                <div>
                  Method
                  <strong>{progress?.acquisition_method || "—"}</strong>
                </div>
                <div>
                  Hits
                  <strong>{progress?.findings_count ?? reportFindings?.total ?? 0}</strong>
                </div>
              </div>
              <div className="actions">
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() =>
                    api.openReport(session.id, "html").catch((e) =>
                      setError(e instanceof Error ? e.message : "Gagal buka laporan"),
                    )
                  }
                >
                  Buka laporan HTML
                </button>
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={() =>
                    api.openReport(session.id, "json").catch((e) =>
                      setError(e instanceof Error ? e.message : "Gagal unduh JSON"),
                    )
                  }
                >
                  Unduh JSON
                </button>
                {can(auth, "report:authorize") && session.status === "completed" && (
                  <button
                    className="btn btn-primary"
                    type="button"
                    onClick={async () => {
                      try {
                        await api.authorizeSession(session.id, "Disahkan pimpinan (PoC)");
                        setSession(await api.session(session.id));
                      } catch (e) {
                        setError(e instanceof Error ? e.message : "Gagal authorize");
                      }
                    }}
                  >
                    Sahkan rekomendasi
                  </button>
                )}
              </div>
              {progress?.authorized_by && (
                <div className="pill ok" style={{ marginTop: 12 }}>
                  Authorized by {progress.authorized_by}
                </div>
              )}
              <h3 style={{ marginTop: 18 }}>Ringkasan temuan</h3>
              {!reportFindings || reportFindings.total === 0 ? (
                <div className="empty">Tidak ada temuan</div>
              ) : (
                <>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Label</th>
                        <th>Layer</th>
                        <th>Conf</th>
                        <th>Path</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reportFindings.items.map((f) => (
                        <tr key={f.id} className="hit-row">
                          <td>{f.label}</td>
                          <td>{f.layer_origin}</td>
                          <td>{(f.confidence * 100).toFixed(0)}%</td>
                          <td className="evidence">{f.path}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Pagination
                    page={reportFindings.page}
                    pages={reportFindings.pages}
                    total={reportFindings.total}
                    page_size={reportFindings.page_size}
                    onPage={setReportPage}
                  />
                </>
              )}
            </>
          )}
        </section>
      )}

      <footer className="ops-footer">
        <div className="ops-footer-left">
          <span className={`badge thin ${gpu ? "armed" : ""}`}>
            <span className="dot" />
            {gpu ? "GPU" : "CPU"}
          </span>
          <div className="tool-pills">
            <span className={`pill ${toolchain.adb ? "ok" : "muted"}`}>
              ADB {toolchain.adb ? "OK" : "N/A"}
            </span>
            <span className={`pill ${toolchain.idevice_id ? "ok" : "muted"}`}>
              iOS {toolchain.idevice_id ? "OK" : "N/A"}
            </span>
            <span className={`pill ${vision.pillow ? "ok" : "muted"}`}>
              CV {vision.pillow ? "OK" : "N/A"}
            </span>
            <span className={`pill ${ocrOn ? "ok" : "muted"}`}>OCR {ocrOn ? "ON" : "OFF"}</span>
          </div>
        </div>
        <span className="ops-footer-right">SADT · gallery focus · PoC</span>
      </footer>
    </div>
  );
}
