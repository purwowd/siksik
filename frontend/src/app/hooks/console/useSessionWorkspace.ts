import { useCallback, useEffect, useRef } from "react";
import { DEFAULT_PAGE_SIZE } from "@/shared/ui/Pagination";
import {
  api,
  can,
  type AuthSession,
  type Finding,
  type GalleryAlbum,
  type GalleryItem,
  type Paginated,
  type SessionReport,
  type SessionSummary,
} from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import {
  pickBootstrapSession,
  SESSION_STORAGE_KEY,
  TERMINAL,
  type ReviewSummary,
} from "@/app/hooks/console/constants";
import { parseTabSearch, resolveSessionId, type ReviewFilterParam } from "@/app/routes";
import { useSessionStream } from "@/features/sessions/hooks/useSessionStream";
import type { Tab } from "@/shared/types";
import type { ToastPush } from "@/app/hooks/console/useToastStack";

type QuerySetters = {
  setFindingsData: React.Dispatch<React.SetStateAction<Paginated<Finding> | null>>;
  setFindingsPage: React.Dispatch<React.SetStateAction<number>>;
  setGalleryData: React.Dispatch<React.SetStateAction<Paginated<GalleryItem> | null>>;
  setGalleryAlbums: React.Dispatch<React.SetStateAction<GalleryAlbum[]>>;
  setGalleryPage: React.Dispatch<React.SetStateAction<number>>;
  setReportFindings: React.Dispatch<React.SetStateAction<Paginated<Finding> | null>>;
  setReportData: React.Dispatch<React.SetStateAction<SessionReport | null>>;
  setReportPage: React.Dispatch<React.SetStateAction<number>>;
};

type Params = {
  auth: AuthSession | null;
  location: ReturnType<typeof import("react-router-dom").useLocation>;
  bootstrapHealth: () => Promise<void>;
  setError: (e: string | null) => void;
  goToTab: (
    tab: Tab,
    opts?: {
      sesi?: string | null;
      filter?: ReviewFilterParam | null;
      album?: string | null;
      modul?: import("@/app/routes").ModuleFilterParam | null;
    },
  ) => void;
  pushToast: ToastPush;
  refreshGlobalPending: () => Promise<void>;
  tab: Tab | null;
  findingsPage: number;
  galleryPage: number;
  galleryAlbum: string;
  reportPage: number;
  reviewFilter: ReviewFilterParam;
  setModuleFilter: React.Dispatch<React.SetStateAction<import("@/app/routes").ModuleFilterParam | null>>;
  querySetters: QuerySetters;
  session: SessionSummary | null;
  setSession: React.Dispatch<React.SetStateAction<SessionSummary | null>>;
  sessionList: SessionSummary[];
  setSessionList: React.Dispatch<React.SetStateAction<SessionSummary[]>>;
  sessionsLoading: boolean;
  setSessionsLoading: React.Dispatch<React.SetStateAction<boolean>>;
  reviewSummary: ReviewSummary | null;
  setReviewSummary: React.Dispatch<React.SetStateAction<ReviewSummary | null>>;
};

export function useSessionWorkspace(p: Params) {
  const defaultSessionTried = useRef(false);
  const sessionIdRef = useRef<string | null>(null);
  const prevSessionStatusRef = useRef<string | null>(null);
  const completionToastIds = useRef<Set<string>>(new Set());
  const pollEpochRef = useRef(0);
  const liveFindingsCountRef = useRef(0);
  const liveReportRecordsRef = useRef(0);

  const refreshSessionList = useCallback(
    async (opts?: { soft?: boolean }) => {
      if (!opts?.soft) p.setSessionsLoading(true);
      try {
        const first = await api.sessions(1, 200);
        const items = [...first.items];
        if (first.pages > 1) {
          for (let page = 2; page <= first.pages && page <= 5; page += 1) {
            const next = await api.sessions(page, 200);
            items.push(...next.items);
          }
        }
        p.setSessionList(items);
        return items;
      } finally {
        if (!opts?.soft) p.setSessionsLoading(false);
      }
    },
    [p.setSessionList, p.setSessionsLoading],
  );

  const refreshReviewSummary = useCallback(async (sessionId: string) => {
    const [pending, confirmed, rejected] = await Promise.all([
      api.findings(sessionId, 1, 1, { review_status: "pending" }),
      api.findings(sessionId, 1, 1, { review_status: "confirmed" }),
      api.findings(sessionId, 1, 1, { review_status: "rejected" }),
    ]);
    p.setReviewSummary({
      pending: pending.total,
      confirmed: confirmed.total,
      rejected: rejected.total,
      total: pending.total + confirmed.total + rejected.total,
    });
  }, [p.setReviewSummary]);

  const selectSessionById = useCallback(
    async (id: string) => {
      const s = await api.session(id);
      p.setSession(s);
      p.querySetters.setFindingsPage(1);
      p.querySetters.setReportPage(1);
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, id);
      } catch {
        /* ignore */
      }
      void refreshReviewSummary(id);
      return s;
    },
    [p.setSession, p.querySetters, refreshReviewSummary],
  );

  const resetWorkspace = useCallback(() => {
    p.setSession(null);
    p.setReviewSummary(null);
    p.setSessionList([]);
  }, [p.setSession, p.setReviewSummary, p.setSessionList]);

  useEffect(() => {
    sessionIdRef.current = p.session?.id ?? null;
  }, [p.session?.id]);

  useEffect(() => {
    prevSessionStatusRef.current = null;
    liveFindingsCountRef.current = 0;
    liveReportRecordsRef.current = 0;
    pollEpochRef.current += 1;
  }, [p.session?.id]);

  useEffect(() => {
    if (!p.auth) return;
    defaultSessionTried.current = false;
    void p.refreshGlobalPending();
    void p.bootstrapHealth();
    refreshSessionList()
      .then(async (items) => {
        if (defaultSessionTried.current || !items) return;
        defaultSessionTried.current = true;
        if (sessionIdRef.current) return;

        const { sesi } = parseTabSearch(p.location.search);
        const fromUrl = sesi ? resolveSessionId(sesi, items) : null;
        let preferId: string | null = fromUrl;
        if (!preferId) {
          try {
            preferId = localStorage.getItem(SESSION_STORAGE_KEY);
          } catch {
            preferId = null;
          }
        }
        const preferred = pickBootstrapSession(items, preferId);
        if (preferred) {
          try {
            await selectSessionById(preferred.id);
          } catch {
            /* ignore bootstrap */
          }
        }
      })
      .catch(() => {
        /* list optional on first paint */
      });
  }, [p.auth, refreshSessionList, selectSessionById, p.refreshGlobalPending, p.location.search, p.bootstrapHealth]);

  useEffect(() => {
    if (!p.auth || p.sessionList.length === 0) return;
    const { sesi } = parseTabSearch(p.location.search);
    if (!sesi) return;
    const resolved = resolveSessionId(sesi, p.sessionList);
    if (resolved && resolved !== p.session?.id) {
      void selectSessionById(resolved).catch(() => {
        p.setError("Sesi dari URL tidak ditemukan");
      });
    }
  }, [p.auth, p.location.search, p.sessionList, p.session?.id, selectSessionById, p.setError]);

  useEffect(() => {
    if (!p.session?.id) {
      p.setReviewSummary(null);
      return;
    }
    void refreshReviewSummary(p.session.id);
  }, [p.session?.id, p.session?.recommendation, refreshReviewSummary, p.setReviewSummary]);

  useEffect(() => {
    if (!p.session) {
      prevSessionStatusRef.current = null;
      return;
    }
    const prev = prevSessionStatusRef.current;
    prevSessionStatusRef.current = p.session.status;
    if (
      prev &&
      ACTIVE.has(prev) &&
      p.session.status === "completed" &&
      !completionToastIds.current.has(p.session.id)
    ) {
      completionToastIds.current.add(p.session.id);
      const n = p.session.progress?.findings_count ?? 0;
      if (n > 0) {
        if (can(p.auth, "findings:read")) {
          p.pushToast(`Analisa selesai · ${n} temuan`, "info", {
            ttlMs: 6000,
            action: {
              label: "Buka review",
              onClick: () => p.goToTab("findings", { sesi: p.session!.id, filter: "pending" }),
            },
          });
          p.goToTab("findings", { sesi: p.session.id, filter: "pending" });
        } else {
          p.pushToast(`Analisa selesai · ${n} temuan menunggu analis`, "ok", { ttlMs: 6000 });
        }
      } else {
        p.pushToast("Analisa selesai · tidak ada temuan", "ok", { ttlMs: 4000 });
      }
      void p.refreshGlobalPending();
    }
  }, [p.session, p.auth, p.pushToast, p.goToTab, p.refreshGlobalPending]);

  useEffect(() => {
    if (!p.session || !ACTIVE.has(p.session.status)) return;
    const sessionId = p.session.id;
    const epoch = ++pollEpochRef.current;
    let stopped = false;
    let inFlight = false;
    const qs = p.querySetters;

    const applyPolled = (s: SessionSummary) => {
      p.setSession((curr) => {
        if (!curr || curr.id !== s.id) return curr;
        if (TERMINAL.has(curr.status) && ACTIVE.has(s.status)) return curr;
        return s;
      });
    };

    const tick = async () => {
      if (stopped || pollEpochRef.current !== epoch || inFlight) return;
      inFlight = true;
      try {
        const s = await api.session(sessionId);
        if (stopped || pollEpochRef.current !== epoch) return;
        const nextFindingsCount = s.progress?.findings_count ?? 0;
        const findingsChanged = nextFindingsCount > liveFindingsCountRef.current;
        const nextReportRecords = Math.max(
          s.progress?.transfer_records ?? 0,
          s.progress?.files_pulled ?? 0,
        );
        const reportRecordsChanged = nextReportRecords > liveReportRecordsRef.current;
        liveFindingsCountRef.current = nextFindingsCount;
        liveReportRecordsRef.current = nextReportRecords;
        applyPolled(s);
        if (
          ACTIVE.has(s.status) &&
          (findingsChanged || reportRecordsChanged) &&
          can(p.auth, "findings:read")
        ) {
          if (p.tab === "findings") {
            try {
              const liveFindings = await api.findings(
                s.id,
                p.findingsPage,
                DEFAULT_PAGE_SIZE,
                p.reviewFilter !== "all" ? { review_status: p.reviewFilter } : undefined,
              );
              if (!stopped && pollEpochRef.current === epoch) qs.setFindingsData(liveFindings);
            } catch {
              /* poll continues */
            }
          }
          if (p.tab === "gallery") {
            try {
              const [albums, items] = await Promise.all([
                api.galleryAlbums(s.id),
                api.gallery(s.id, p.galleryAlbum, p.galleryPage, DEFAULT_PAGE_SIZE),
              ]);
              if (!stopped && pollEpochRef.current === epoch) {
                qs.setGalleryAlbums(albums);
                qs.setGalleryData(items);
              }
            } catch {
              /* poll continues */
            }
          }
          if (p.tab === "report") {
            try {
              const [liveReportFindings, liveReport] = await Promise.all([
                api.findings(s.id, p.reportPage, DEFAULT_PAGE_SIZE),
                api.report(s.id),
              ]);
              if (!stopped && pollEpochRef.current === epoch) {
                qs.setReportFindings(liveReportFindings);
                qs.setReportData(liveReport);
              }
            } catch {
              /* poll continues */
            }
          }
          void refreshReviewSummary(s.id).catch(() => undefined);
          void p.refreshGlobalPending();
        }
        if (!ACTIVE.has(s.status)) {
          stopped = true;
          const f = await api.findings(
            s.id,
            1,
            DEFAULT_PAGE_SIZE,
            p.reviewFilter !== "all" ? { review_status: p.reviewFilter } : undefined,
          );
          if (pollEpochRef.current !== epoch) return;
          qs.setFindingsData(f);
          qs.setFindingsPage(1);
          if (p.tab === "gallery") {
            try {
              const [albums, items] = await Promise.all([
                api.galleryAlbums(s.id),
                api.gallery(s.id, p.galleryAlbum, 1, DEFAULT_PAGE_SIZE),
              ]);
              if (pollEpochRef.current !== epoch) return;
              qs.setGalleryAlbums(albums);
              qs.setGalleryData(items);
              qs.setGalleryPage(1);
            } catch {
              /* gallery refreshes on tab open */
            }
          }
          if (p.tab === "report") {
            const [completedReportFindings, completedReport] = await Promise.all([
              api.findings(s.id, 1, DEFAULT_PAGE_SIZE),
              api.report(s.id),
            ]);
            if (pollEpochRef.current !== epoch) return;
            qs.setReportFindings(completedReportFindings);
            qs.setReportData(completedReport);
            qs.setReportPage(1);
          }
          void refreshSessionList({ soft: true });
          void refreshReviewSummary(s.id);
          void p.refreshGlobalPending();
        }
      } catch {
        if (!stopped) {
          p.setError("Koneksi telemetri terputus — coba muat ulang atau pilih sesi ulang");
        }
      } finally {
        inFlight = false;
      }
    };

    const t = window.setInterval(() => void tick(), 500);
    return () => {
      stopped = true;
      window.clearInterval(t);
      if (pollEpochRef.current === epoch) pollEpochRef.current += 1;
    };
  }, [
    p.session?.id,
    p.session?.status,
    p.auth,
    p.tab,
    p.findingsPage,
    p.galleryPage,
    p.galleryAlbum,
    p.reportPage,
    p.reviewFilter,
    p.querySetters,
    p.setSession,
    refreshSessionList,
    refreshReviewSummary,
    p.refreshGlobalPending,
    p.setError,
  ]);

  const applyStreamUpdate = useCallback(
    (s: SessionSummary) => {
      p.setSession((curr) => {
        if (!curr || curr.id !== s.id) return curr;
        if (TERMINAL.has(curr.status) && ACTIVE.has(s.status)) return curr;
        return s;
      });
    },
    [p.setSession],
  );

  useSessionStream(
    p.session?.id,
    !!p.session && ACTIVE.has(p.session.status),
    applyStreamUpdate,
    !!p.auth,
  );

  async function onPickSession(id: string) {
    try {
      p.setError(null);
      await selectSessionById(id);
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal memuat sesi");
    }
  }

  async function openSession(id: string, nextTab: Tab) {
    try {
      p.setError(null);
      await selectSessionById(id);
      p.goToTab(nextTab, { sesi: id, filter: nextTab === "findings" ? p.reviewFilter : null });
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal memuat sesi");
    }
  }

  async function openSessionWithModule(id: string, modul: import("@/app/routes").ModuleFilterParam) {
    try {
      p.setError(null);
      await selectSessionById(id);
      p.setModuleFilter(modul);
      p.querySetters.setFindingsPage(1);
      p.goToTab("findings", { sesi: id, modul, filter: "all" });
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal memuat sesi");
    }
  }

  return {
    session: p.session,
    setSession: p.setSession,
    sessionList: p.sessionList,
    sessionsLoading: p.sessionsLoading,
    reviewSummary: p.reviewSummary,
    refreshSessionList,
    refreshReviewSummary,
    selectSessionById,
    onPickSession,
    openSession,
    openSessionWithModule,
    resetWorkspace,
  };
}
