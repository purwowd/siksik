import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { can } from "@/shared/api/client";
import { Breadcrumb } from "@/features/sessions/components/Breadcrumb";
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

export function AppShell(props: ConsoleAppViewModel) {
  const {
    auth, loginUser, loginPass, loginBusy, setLoginUser, setLoginPass, doLogin,
    location, tab, allowedTabs, landingTab, goToTab, session, sessionList, sessionsLoading,
    dash, dashSessions, dashFindings, setDashSessionsPage, setDashFindingsPage,
    findingsData, galleryData, galleryAlbums, findingsLoading, galleryLoading,
    setFindingsPage, setGalleryPage, reviewFilter, moduleFilter, galleryAlbum,
    reportFindings, reportData, reportLoading,     reviewSummary, error, setError, toasts, dismissToast, pushToast,
    tourActive, setTourActive, tourStep, setTourStep, busy, reviewBusyId, bulkBusy,
    expandedEvidence, setExpandedEvidence, focusedFindingId, setFocusedFindingId, teleRef,
    liveDevices, canStartLive, canStartZip,
    participant, setParticipant,
    acqSource, setAcqSource, zipEnabled, zipFile, setZipFile, zipMaxMb, uploadPct, selected, setSelected,
    mode, setMode, authorizeNote, setAuthorizeNote, refreshDevices, refreshSessionList, refreshGallery, onPickSession,
    openSession, openSessionWithModule, changeReviewFilter, changeModuleFilter, changeGalleryAlbum,
    start, startZip, cancel, review, bulkReview, doLogout, setSession, setReportPage, topBarActive,
  } = props;

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
          onPickDemo={(user, pass) => {
            setLoginUser(user);
            setLoginPass(pass);
          }}
          onSubmit={doLogin}
        />
      </>
    );
  }

  return (
    <div className="app-shell wide ent-shell">
      <TopLoadingBar active={topBarActive} />
      <header className="ent-topbar ent-glass-bar">
        <div className="ent-brand">
          <BrandLogo size="sm" />
        </div>
        <div className="ent-user">
          <div className="ent-user-text">
            <strong>{auth.display_name}</strong>
            <span>
              {auth.username} · {auth.role}
            </span>
          </div>
          <button className="btn btn-ghost ent-logout" type="button" onClick={() => void doLogout()}>
            Keluar
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={() => {
              setTourActive(true);
              setTourStep(0);
            }}
          >
            Tur demo
          </button>
        </div>
      </header>

      {tourActive && (
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

      <div className="ent-nav-row">
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
                <span className="tab-badge" aria-label={`${reviewSummary?.pending} temuan pending`}>
                  {reviewSummary?.pending}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <Breadcrumb pathname={location.pathname} session={session} />
      </div>

      <CaseFlowBar session={session} />

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
            path="/operator"
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
                mode={mode}
                setMode={setMode}
                canStartLive={canStartLive}
                canStartZip={canStartZip}
                busy={busy}
                session={session}
                start={() => void start()}
                startZip={() => void startZip()}
                cancel={() => void cancel()}
                onNavigateTab={goToTab}
                canFindings={can(auth, "findings:read")}
                canReport={can(auth, "report:read")}
                canDashboard={can(auth, "dashboard")}
              />
            }
          />
        )}

        {can(auth, "dashboard") && (
          <Route
            path="/dasbor"
            element={
              <DashboardPage
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                onPickSession={(id) => void onPickSession(id)}
                dash={dash}
                dashSessions={dashSessions}
                dashFindings={dashFindings}
                setDashSessionsPage={setDashSessionsPage}
                setDashFindingsPage={setDashFindingsPage}
                openSession={(id, t) => void openSession(id, t)}
                onModuleDrillDown={(modul) => {
                  if (session?.id) void openSessionWithModule(session.id, modul);
                }}
              />
            }
          />
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
