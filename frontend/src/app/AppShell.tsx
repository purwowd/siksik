import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { can } from "@/shared/api/client";
import { LoginScreen } from "@/features/auth/components/LoginScreen";
import { CaseFlowBar } from "@/features/sessions/components/CaseFlowBar";
import { DemoTour, DEMO_TOUR_STEPS } from "@/features/sessions/components/DemoTour";
import { ToastStack } from "@/shared/ui/Toast";
import { TopLoadingBar } from "@/shared/ui/TopLoadingBar";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { FindingsPage } from "@/features/findings/FindingsPage";
import { OperatorPage } from "@/features/operator/OperatorPage";
import { ReportPage } from "@/features/report/ReportPage";
import { GalleryPage } from "@/features/gallery/GalleryPage";
import { buildTabUrl, pathFromTab } from "@/app/routes";
import type { ConsoleAppViewModel } from "@/app/hooks/useConsoleApp.types";
import { BrandLogo } from "@/shared/ui/BrandLogo";
import { LAB_UI } from "@/shared/lib/labUi";
import { APP_VERSION } from "@/shared/lib/appVersion";
import { occupyingSession } from "@/shared/lib/caseChecklist";
import { isContentLoading } from "@/shared/lib/pageLoad";
import { roleLabel } from "@/shared/lib/roleLabel";

function PreserveSearchRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export function AppShell(props: ConsoleAppViewModel) {
  const {
    auth, loginUser, loginPass, loginBusy, setLoginUser, setLoginPass, doLogin,
    tab, allowedTabs, landingTab, goToTab, session, sessionList, sessionsLoading,
    dash, dashLoading, dashSessions, dashFindings, setDashSessionsPage, setDashFindingsPage,
    findingsData, galleryData, galleryAlbums, findingsLoading, galleryLoading,
    setFindingsPage, setGalleryPage, reviewFilter, moduleFilter, galleryAlbum,
    reportFindings, reportData, reportLoading, reviewSummary, error, setError, toasts, dismissToast, pushToast,
    tourActive, setTourActive, tourStep, setTourStep, busy, reviewBusyId, bulkBusy,
    expandedEvidence, setExpandedEvidence, focusedFindingId, setFocusedFindingId, teleRef,
    liveDevices, devicesLoading, canStartLive, canStartZip,
    participant, setParticipant,
    acqSource, setAcqSource, zipEnabled, zipFile, setZipFile, zipMaxMb, uploadPct, selected, setSelected,
    analysisScope, setAnalysisScope, deviceSources, setDeviceSources, socialTargets, setSocialTargets,
    authorizeNote, setAuthorizeNote, refreshDevices, refreshSessionList, refreshGallery, refreshFindings, onPickSession,
    openSession, openSessionWithModule, changeReviewFilter, changeModuleFilter, changeGalleryAlbum,
    start, startZip, cancel, review, bulkReview, doLogout, setSession, setReportPage, topBarActive,
  } = props;

  const occupying = occupyingSession(sessionList, session);
  const contentLoading =
    (tab === "dashboard" && isContentLoading(dashLoading, dash)) ||
    (tab === "findings" && !!session && isContentLoading(findingsLoading, findingsData)) ||
    (tab === "gallery" && !!session && isContentLoading(galleryLoading, galleryData)) ||
    (tab === "report" && !!session && isContentLoading(reportLoading, reportData));

  if (!auth) {
    return (
      <>
        <TopLoadingBar active={loginBusy} />
        <LoginScreen
          loginUser={loginUser}
          loginPass={loginPass}
          loginBusy={loginBusy}
          error={error}
          onUserChange={setLoginUser}
          onPassChange={setLoginPass}
          onPickDemo={
            LAB_UI
              ? (user, pass) => {
                  setLoginUser(user);
                  setLoginPass(pass);
                }
              : undefined
          }
          onSubmit={doLogin}
        />
      </>
    );
  }

  return (
    <div className="app-shell wide ent-shell">
      <TopLoadingBar active={topBarActive} />
      <header className="ent-cmdbar">
        <div className="ent-brand">
          <BrandLogo size="sm" />
        </div>
        <nav className="tabs ent-tabs" role="tablist" aria-label="Navigasi konsol">
          {allowedTabs.map((t) => (
            <NavLink
              key={t.id}
              to={buildTabUrl(t.id, {
                sesi: session?.id,
                filter: t.id === "findings" ? reviewFilter : null,
                album: t.id === "gallery" ? galleryAlbum : null,
                modul: t.id === "findings" ? moduleFilter : null,
              })}
              role="tab"
              aria-selected={tab === t.id}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {t.label}
              {t.id === "findings" && session && (reviewSummary?.pending ?? 0) > 0 && (
                <span className="tab-badge" aria-label={`${reviewSummary?.pending} temuan menunggu`}>
                  {reviewSummary?.pending}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="ent-cmd-end">
          <p className="ent-cmd-meta">
            <span>{roleLabel(auth.role)}</span>
            <span>v{APP_VERSION}</span>
          </p>
          <div className="ent-user">
            <div className="ent-user-text">
              <strong>{auth.display_name}</strong>
              <span>{auth.username}</span>
            </div>
            <button className="btn btn-ghost ent-logout" type="button" onClick={() => void doLogout()}>
              Keluar
            </button>
            {LAB_UI && (
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              onClick={() => {
                setTourActive(true);
                setTourStep(0);
              }}
            >
              Panduan singkat
            </button>
            )}
          </div>
        </div>
      </header>

      {LAB_UI && tourActive && (
        <DemoTour
          step={tourStep}
          onNext={() => {
            if (tourStep >= DEMO_TOUR_STEPS.length - 1) setTourActive(false);
            else setTourStep((s) => s + 1);
          }}
          onPrev={() => setTourStep((s) => Math.max(0, s - 1))}
          onClose={() => setTourActive(false)}
          onJumpTab={(t) => goToTab(t)}
        />
      )}

      <CaseFlowBar
        session={session}
        role={auth.role}
        pending={reviewSummary?.pending ?? 0}
        loading={contentLoading}
      />

      {error && (
        <div className="error-banner dismissible" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="error-dismiss"
            onClick={() => setError(null)}
            aria-label="Tutup"
          >
            Tutup
          </button>
        </div>
      )}

      <ToastStack items={toasts} onDismiss={dismissToast} />

      <main className="ent-main">
      <Routes>
        <Route path="/" element={<Navigate to={pathFromTab(landingTab)} replace />} />

        {can(auth, "sessions:start") && (
          <Route
            path="/penerimaan"
            element={
              <OperatorPage
                teleRef={teleRef}
                participant={participant}
                setParticipant={setParticipant}
                acqSource={acqSource}
                setAcqSource={setAcqSource}
                zipEnabled={zipEnabled}
                zipFile={zipFile}
                setZipFile={setZipFile}
                zipMaxMb={zipMaxMb}
                uploadPct={uploadPct}
                liveDevices={liveDevices}
                selected={selected}
                setSelected={setSelected}
                refreshDevices={refreshDevices}
                devicesLoading={devicesLoading}
                analysisScope={analysisScope}
                setAnalysisScope={setAnalysisScope}
                deviceSources={deviceSources}
                setDeviceSources={setDeviceSources}
                socialTargets={socialTargets}
                setSocialTargets={setSocialTargets}
                canStartLive={canStartLive}
                canStartZip={canStartZip}
                busy={busy}
                session={session}
                occupying={occupying}
                start={() => void start()}
                startZip={() => void startZip()}
                cancel={(id) => void cancel(id)}
                onNavigateTab={goToTab}
                canFindings={can(auth, "findings:read")}
                canReport={can(auth, "report:read")}
                canDashboard={can(auth, "dashboard")}
              />
            }
          />
        )}
        {can(auth, "sessions:start") && (
          <Route path="/operator" element={<PreserveSearchRedirect to="/penerimaan" />} />
        )}

        {can(auth, "dashboard") && (
          <Route
            path="/ikhtisar"
            element={
              <DashboardPage
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                onPickSession={(id) => void onPickSession(id)}
                dash={dash}
                dashLoading={dashLoading}
                dashSessions={dashSessions}
                dashFindings={dashFindings}
                setDashSessionsPage={setDashSessionsPage}
                setDashFindingsPage={setDashFindingsPage}
                openSession={(id, t) => void openSession(id, t)}
                onModuleDrillDown={(modul) => {
                  if (session?.id) void openSessionWithModule(session.id, modul);
                }}
                showLabDiagnostics={auth.role === "admin"}
              />
            }
          />
        )}
        {can(auth, "dashboard") && (
          <Route path="/dasbor" element={<PreserveSearchRedirect to="/ikhtisar" />} />
        )}

        {can(auth, "findings:read") && (
          <Route
            path="/temuan"
            element={
              <FindingsPage
                auth={auth}
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                findingsLoading={findingsLoading}
                reviewSummary={reviewSummary}
                onPickSession={(id) => void onPickSession(id)}
                refreshSessionList={() => void refreshSessionList()}
                refreshFindings={refreshFindings}
                reviewFilter={reviewFilter}
                setReviewFilter={changeReviewFilter}
                moduleFilter={moduleFilter}
                setModuleFilter={changeModuleFilter}
                findingsData={findingsData}
                expandedEvidence={expandedEvidence}
                setExpandedEvidence={setExpandedEvidence}
                reviewBusyId={reviewBusyId}
                bulkBusy={bulkBusy}
                onReview={(id, st) => void review(id, st)}
                onBulkReview={(st) => void bulkReview(st)}
                onPage={setFindingsPage}
                focusedFindingId={focusedFindingId}
                setFocusedFindingId={setFocusedFindingId}
              />
            }
          />
        )}

        {can(auth, "findings:read") && (
          <Route
            path="/galeri"
            element={
              <GalleryPage
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                loading={galleryLoading}
                albums={galleryAlbums}
                album={galleryAlbum}
                setAlbum={changeGalleryAlbum}
                data={galleryData}
                onPickSession={(id) => void onPickSession(id)}
                onPage={setGalleryPage}
                onRefresh={refreshGallery}
              />
            }
          />
        )}

        {can(auth, "report:read") && (
          <Route
            path="/laporan"
            element={
              <ReportPage
                auth={auth}
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                onPickSession={(id) => void onPickSession(id)}
                reportFindings={reportFindings}
                reportData={reportData}
                reportLoading={reportLoading}
                reviewSummary={reviewSummary}
                setReportPage={setReportPage}
                authorizeNote={authorizeNote}
                setAuthorizeNote={setAuthorizeNote}
                setSession={setSession}
                refreshSessionList={() => void refreshSessionList()}
                setError={setError}
                onToast={(msg, tone) => pushToast(msg, tone ?? "ok", { ttlMs: 4000, dedupe: true })}
              />
            }
          />
        )}

        <Route path="*" element={<Navigate to={pathFromTab(landingTab)} replace />} />
      </Routes>
      </main>
    </div>
  );

}
