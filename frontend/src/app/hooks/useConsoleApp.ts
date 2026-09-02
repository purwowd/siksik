import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, can, type AcquisitionMode, type AnalysisScope, type SessionSummary } from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import {
  findActiveSession,
  type ReviewSummary,
} from "@/app/hooks/console/constants";
import { useToastStack } from "@/app/hooks/console/useToastStack";
import { useAllowedTabs, useAuthSession } from "@/app/hooks/console/useAuthSession";
import { useConsoleNavigation } from "@/app/hooks/console/useConsoleNavigation";
import { useRuntimeHealth } from "@/app/hooks/console/useRuntimeHealth";
import { useWorkspaceQueries } from "@/app/hooks/console/useWorkspaceQueries";
import { useSessionWorkspace } from "@/app/hooks/console/useSessionWorkspace";
import { useAcquisitionControls } from "@/app/hooks/console/useAcquisitionControls";
import { useReviewActions } from "@/app/hooks/console/useReviewActions";
import { DEFAULT_GALLERY_ALBUM, type ModuleFilterParam, type ReviewFilterParam } from "@/app/routes";
import {
  DEFAULT_ANALYSIS_SCOPE,
  planForScope,
} from "@/features/operator/analysisScope";
import {
  EMPTY_PARTICIPANT,
  syncParticipantForm,
} from "@/features/operator/participantForm";

export function useConsoleApp() {
  const [error, setError] = useState<string | null>(null);
  const { toasts, dismissToast, pushToast } = useToastStack();
  const urlFilterApplied = useRef(false);
  const logoutResetRef = useRef<() => void>(() => {});
  const refreshSessionListRef = useRef<
    (opts?: { soft?: boolean }) => Promise<SessionSummary[] | undefined>
  >(async () => undefined);

  const [session, setSession] = useState<SessionSummary | null>(null);
  const [sessionList, setSessionList] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [reviewSummary, setReviewSummary] = useState<ReviewSummary | null>(null);
  const [, setGlobalPending] = useState(0);

  const [reviewFilter, setReviewFilter] = useState<ReviewFilterParam>("all");
  const [moduleFilter, setModuleFilter] = useState<ModuleFilterParam | null>(null);
  const [galleryAlbum, setGalleryAlbum] = useState<string>(DEFAULT_GALLERY_ALBUM);

  const [mode, setMode] = useState<AcquisitionMode>("quick");
  const initialPlan = planForScope(DEFAULT_ANALYSIS_SCOPE);
  const [analysisScope, setAnalysisScope] = useState<AnalysisScope>(DEFAULT_ANALYSIS_SCOPE);
  const [deviceSources, setDeviceSources] = useState<string[]>([...initialPlan.deviceSources]);
  const [socialTargets, setSocialTargets] = useState<string[]>([...initialPlan.socialTargets]);
  const [fileCount] = useState(1200);
  const [acqSource, setAcqSource] = useState<"live" | "zip">("live");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [authorizeNote, setAuthorizeNote] = useState("");
  const [participant, setParticipantState] = useState(EMPTY_PARTICIPANT);
  const hydratedParticipantSessionId = useRef<string | null>(null);
  const setParticipant = useCallback((patch: Partial<typeof participant>) => {
    setParticipantState((prev) => ({ ...prev, ...patch }));
  }, []);
  const resetParticipant = useCallback(() => {
    hydratedParticipantSessionId.current = null;
    setParticipantState(EMPTY_PARTICIPANT);
  }, []);

  const [tourActive, setTourActive] = useState(false);
  const [tourStep, setTourStep] = useState(0);
  const [expandedEvidence, setExpandedEvidence] = useState<string | null>(null);
  const [focusedFindingId, setFocusedFindingId] = useState<string | null>(null);
  const teleRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const next = syncParticipantForm({
      sessionId: session?.id ?? null,
      status: session?.status,
      saved: session?.participant,
      hydratedSessionId: hydratedParticipantSessionId.current,
    });
    hydratedParticipantSessionId.current = next.hydratedSessionId;
    if (next.form) {
      setParticipantState(next.form);
    }
  }, [session?.id, session?.status, session?.participant]);

  const {
    auth,
    setAuth,
    loginUser,
    loginPass,
    loginBusy,
    setLoginUser,
    setLoginPass,
    doLogin,
    doLogout,
    location,
    navigate,
  } = useAuthSession({
    setError,
    onLogoutReset: () => logoutResetRef.current(),
    urlFilterApplied,
  });

  const { allowedTabs, landingTab } = useAllowedTabs(auth);
  const runtime = useRuntimeHealth(auth, setAuth, setError);
  const activeSessionId = findActiveSession(sessionList)?.id ?? null;

  const { tab, goToTab } = useConsoleNavigation({
    auth,
    location,
    navigate,
    allowedTabs,
    landingTab,
    sessionId: session?.id,
    activeSessionId,
    reviewFilter,
    moduleFilter,
    galleryAlbum,
    setReviewFilter,
    setModuleFilter,
    setGalleryAlbum,
    urlFilterApplied,
  });

  const refreshGlobalPending = useCallback(async () => {
    if (!auth || !can(auth, "dashboard")) return;
    try {
      const d = await api.dashboard(session?.id);
      setGlobalPending(d.pending_reviews ?? 0);
    } catch {
      /* optional */
    }
  }, [auth, session?.id]);

  const refreshSessionListBridge = useCallback(
    (opts?: { soft?: boolean }) => refreshSessionListRef.current(opts),
    [],
  );

  const queries = useWorkspaceQueries({
    tab,
    session,
    sessionList,
    setSessionList,
    reviewFilter,
    moduleFilter,
    galleryAlbum,
    refreshSessionList: refreshSessionListBridge,
    setGlobalPending,
    setError,
  });

  const querySetters = useMemo(
    () => ({
      setFindingsData: queries.setFindingsData,
      setFindingsPage: queries.setFindingsPage,
      setGalleryData: queries.setGalleryData,
      setGalleryAlbums: queries.setGalleryAlbums,
      setGalleryPage: queries.setGalleryPage,
      setReportFindings: queries.setReportFindings,
      setReportData: queries.setReportData,
      setReportPage: queries.setReportPage,
    }),
    [
      queries.setFindingsData,
      queries.setFindingsPage,
      queries.setGalleryData,
      queries.setGalleryAlbums,
      queries.setGalleryPage,
      queries.setReportFindings,
      queries.setReportData,
      queries.setReportPage,
    ],
  );

  const workspace = useSessionWorkspace({
    auth,
    location,
    bootstrapHealth: runtime.bootstrapHealth,
    setError,
    goToTab,
    pushToast,
    refreshGlobalPending,
    tab,
    findingsPage: queries.findingsPage,
    galleryPage: queries.galleryPage,
    galleryAlbum,
    reportPage: queries.reportPage,
    reviewFilter,
    setModuleFilter,
    querySetters,
    session,
    setSession,
    sessionList,
    setSessionList,
    sessionsLoading,
    setSessionsLoading,
    reviewSummary,
    setReviewSummary,
  });

  refreshSessionListRef.current = workspace.refreshSessionList;

  logoutResetRef.current = () => {
    workspace.resetWorkspace();
    queries.resetQueries();
    resetParticipant();
  };

  const changeReviewFilter = useCallback(
    (next: ReviewFilterParam) => {
      setReviewFilter(next);
      queries.setFindingsPage(1);
    },
    [queries.setFindingsPage],
  );

  const changeGalleryAlbum = useCallback(
    (next: string) => {
      setGalleryAlbum(next);
      queries.setGalleryPage(1);
    },
    [queries.setGalleryPage],
  );

  const changeModuleFilter = useCallback(
    (next: ModuleFilterParam | null) => {
      setModuleFilter(next);
      queries.setFindingsPage(1);
    },
    [queries.setFindingsPage],
  );

  const acquisition = useAcquisitionControls({
    selected: runtime.selected,
    session,
    setSession,
    mode,
    setMode,
    analysisScope,
    deviceSources,
    socialTargets,
    fileCount,
    acqSource,
    setAcqSource,
    zipFile,
    setZipFile,
    zipMaxMb: runtime.zipMaxMb,
    zipEnabled: runtime.zipEnabled,
    participant,
    authorizeNote,
    setAuthorizeNote,
    teleRef,
    goToTab,
    refreshSessionList: workspace.refreshSessionList,
    refreshDevices: runtime.refreshDevices,
    setError,
    clearQueryPages: () => {
      queries.setFindingsPage(1);
      queries.setReportPage(1);
    },
    clearFindingsData: () => queries.setFindingsData(null),
    clearReportData: () => {
      queries.setReportFindings(null);
      queries.setReportPage(1);
    },
  });

  const reviewActions = useReviewActions({
    session,
    setSession,
    reviewFilter,
    reviewSummary,
    tab,
    setFindingsData: queries.setFindingsData,
    setReportFindings: queries.setReportFindings,
    setDashFindings: queries.setDashFindings,
    setFindingsPage: queries.setFindingsPage,
    refreshReviewSummary: workspace.refreshReviewSummary,
    isSessionCurrent: workspace.isSessionCurrent,
    refreshSessionList: workspace.refreshSessionList,
    refreshGlobalPending,
    pushToast,
    setError,
  });

  const topBarActive =
    queries.findingsLoading ||
    queries.galleryLoading ||
    queries.reportLoading ||
    queries.dashLoading ||
    acquisition.busy ||
    reviewActions.bulkBusy ||
    !!reviewActions.reviewBusyId ||
    (sessionsLoading && sessionList.length === 0) ||
    (!!session && ACTIVE.has(session.status));

  return {
    auth,
    loginUser,
    loginPass,
    loginBusy,
    setLoginUser,
    setLoginPass,
    doLogin,
    location,
    tab,
    allowedTabs,
    landingTab,
    goToTab,
    session,
    sessionList,
    sessionsLoading,
    dash: queries.dash,
    dashLoading: queries.dashLoading,
    dashFindings: queries.dashFindings,
    dashSessions: queries.dashSessions,
    dashSessionsPage: queries.dashSessionsPage,
    dashFindingsPage: queries.dashFindingsPage,
    setDashSessionsPage: queries.setDashSessionsPage,
    setDashFindingsPage: queries.setDashFindingsPage,
    findingsData: queries.findingsData,
    galleryData: queries.galleryData,
    galleryAlbums: queries.galleryAlbums,
    findingsLoading: queries.findingsLoading,
    galleryLoading: queries.galleryLoading,
    findingsPage: queries.findingsPage,
    setFindingsPage: queries.setFindingsPage,
    galleryPage: queries.galleryPage,
    setGalleryPage: queries.setGalleryPage,
    reviewFilter,
    moduleFilter,
    galleryAlbum,
    reportFindings: queries.reportFindings,
    reportData: queries.reportData,
    reportLoading: queries.reportLoading,
    reportPage: queries.reportPage,
    reviewSummary,
    error,
    setError,
    toasts,
    dismissToast,
    pushToast,
    tourActive,
    setTourActive,
    tourStep,
    setTourStep,
    busy: acquisition.busy,
    reviewBusyId: reviewActions.reviewBusyId,
    bulkBusy: reviewActions.bulkBusy,
    expandedEvidence,
    setExpandedEvidence,
    focusedFindingId,
    setFocusedFindingId,
    teleRef,
    liveDevices: runtime.liveDevices,
    canStartLive: acquisition.canStartLive,
    canStartZip: acquisition.canStartZip,
    iosSetup: acquisition.iosSetup,
    participant,
    setParticipant,
    acqSource,
    setAcqSource,
    zipEnabled: runtime.zipEnabled,
    zipFile,
    setZipFile,
    zipMaxMb: runtime.zipMaxMb,
    uploadPct: acquisition.uploadPct,
    selected: runtime.selected,
    setSelected: runtime.setSelected,
    mode,
    setMode,
    analysisScope,
    setAnalysisScope,
    deviceSources,
    setDeviceSources,
    socialTargets,
    setSocialTargets,
    authorizeNote,
    setAuthorizeNote,
    refreshDevices: runtime.refreshDevices,
    refreshSessionList: workspace.refreshSessionList,
    refreshGallery: queries.refreshGallery,
    onPickSession: workspace.onPickSession,
    openSession: workspace.openSession,
    openSessionWithModule: workspace.openSessionWithModule,
    changeReviewFilter,
    changeModuleFilter,
    changeGalleryAlbum,
    start: acquisition.start,
    startZip: acquisition.startZip,
    cancel: acquisition.cancel,
    review: reviewActions.review,
    bulkReview: reviewActions.bulkReview,
    doLogout,
    setSession,
    setReportPage: queries.setReportPage,
    topBarActive,
  };
}

export type ConsoleAppViewModel = ReturnType<typeof useConsoleApp>;
