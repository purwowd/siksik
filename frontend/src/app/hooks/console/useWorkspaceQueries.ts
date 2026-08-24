import { useCallback, useEffect, useState } from "react";
import { DEFAULT_PAGE_SIZE } from "@/shared/ui/Pagination";
import {
  api,
  type DashboardStats,
  type Finding,
  type GalleryAlbum,
  type GalleryItem,
  type Paginated,
  type SessionReport,
  type SessionSummary,
} from "@/shared/api/client";
import type { ModuleFilterParam, ReviewFilterParam } from "@/app/routes";
import type { Tab } from "@/shared/types";

type Params = {
  tab: Tab | null;
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  setSessionList: React.Dispatch<React.SetStateAction<SessionSummary[]>>;
  reviewFilter: ReviewFilterParam;
  moduleFilter: ModuleFilterParam | null;
  galleryAlbum: string;
  refreshSessionList: (opts?: { soft?: boolean }) => Promise<SessionSummary[] | undefined>;
  setGlobalPending: (n: number) => void;
  setError: (e: string | null) => void;
};

export function useWorkspaceQueries(p: Params) {
  const [dash, setDash] = useState<DashboardStats | null>(null);
  const [dashLoading, setDashLoading] = useState(false);
  const [findingsData, setFindingsData] = useState<Paginated<Finding> | null>(null);
  const [galleryData, setGalleryData] = useState<Paginated<GalleryItem> | null>(null);
  const [galleryAlbums, setGalleryAlbums] = useState<GalleryAlbum[]>([]);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [findingsPage, setFindingsPage] = useState(1);
  const [galleryPage, setGalleryPage] = useState(1);
  const [galleryRefreshToken, setGalleryRefreshToken] = useState(0);
  const [reportFindings, setReportFindings] = useState<Paginated<Finding> | null>(null);
  const [reportData, setReportData] = useState<SessionReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportPage, setReportPage] = useState(1);
  const [dashSessions, setDashSessions] = useState<Paginated<SessionSummary> | null>(null);
  const [dashSessionsPage, setDashSessionsPage] = useState(1);
  const [dashFindings, setDashFindings] = useState<Paginated<Finding> | null>(null);
  const [dashFindingsPage, setDashFindingsPage] = useState(1);

  const resetQueries = useCallback(() => {
    setDash(null);
    setFindingsData(null);
    setGalleryData(null);
    setGalleryAlbums([]);
    setReportFindings(null);
    setReportData(null);
    setDashFindings(null);
  }, []);

  useEffect(() => {
    if (p.tab !== "dashboard") return;
    setDash(null);
    let cancelled = false;
    setDashLoading(true);
    Promise.all([
      api.dashboard(p.session?.id),
      api.sessions(dashSessionsPage, DEFAULT_PAGE_SIZE),
      p.session?.id
        ? api.findings(p.session.id, dashFindingsPage, DEFAULT_PAGE_SIZE)
        : Promise.resolve({
            items: [],
            total: 0,
            page: dashFindingsPage,
            page_size: DEFAULT_PAGE_SIZE,
            pages: 0,
          }),
    ])
      .then(([d, sessionsRes, findingsRes]) => {
        if (cancelled) return;
        setDash(d);
        setDashSessions(sessionsRes);
        setDashFindings(findingsRes);
        p.setGlobalPending(d.pending_reviews ?? 0);
        p.setSessionList((prev) => {
          const map = new Map(prev.map((s) => [s.id, s]));
          for (const s of sessionsRes.items) map.set(s.id, s);
          return Array.from(map.values());
        });
      })
      .catch((e) => {
        if (!cancelled) p.setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setDashLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [p.tab, dashSessionsPage, dashFindingsPage, p.session?.id, p.setGlobalPending, p.setSessionList, p.setError]);

  useEffect(() => {
    if (p.tab !== "dashboard") return;
    void p.refreshSessionList({ soft: true });
  }, [p.tab, p.refreshSessionList]);

  useEffect(() => {
    if (p.tab !== "findings" && p.tab !== "gallery" && p.tab !== "report") return;
    if (p.sessionList.length === 0) void p.refreshSessionList();
  }, [p.tab, p.sessionList.length, p.refreshSessionList]);

  useEffect(() => {
    setReportPage((prev) => (prev === 1 ? prev : 1));
  }, [p.session?.id]);

  useEffect(() => {
    if (p.tab !== "findings") return;
    if (!p.session?.id) {
      setFindingsData(null);
      setFindingsLoading(false);
      return;
    }
    let cancelled = false;
    setFindingsLoading(true);
    api
      .findings(p.session.id, findingsPage, DEFAULT_PAGE_SIZE, {
        ...(p.reviewFilter !== "all" ? { review_status: p.reviewFilter } : {}),
        ...(p.moduleFilter ? { module: p.moduleFilter } : {}),
      })
      .then((data) => {
        if (!cancelled) setFindingsData(data);
      })
      .catch((e) => {
        if (!cancelled) p.setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setFindingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [p.tab, p.session?.id, findingsPage, p.reviewFilter, p.moduleFilter, p.setError]);

  useEffect(() => {
    if (p.tab !== "gallery") return;
    if (!p.session?.id) {
      setGalleryData(null);
      setGalleryAlbums([]);
      setGalleryLoading(false);
      return;
    }
    let cancelled = false;
    setGalleryLoading(true);
    Promise.all([
      api.galleryAlbums(p.session.id),
      api.gallery(p.session.id, p.galleryAlbum, galleryPage, DEFAULT_PAGE_SIZE),
    ])
      .then(([albums, items]) => {
        if (cancelled) return;
        setGalleryAlbums(albums);
        setGalleryData(items);
      })
      .catch((e) => {
        if (!cancelled) p.setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setGalleryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [p.tab, p.session?.id, p.session?.status, p.galleryAlbum, galleryPage, galleryRefreshToken, p.setError]);

  const refreshGallery = useCallback(() => {
    setGalleryRefreshToken((t) => t + 1);
  }, []);

  useEffect(() => {
    setDash(null);
    setFindingsData(null);
    setGalleryData(null);
    setGalleryAlbums([]);
    setGalleryPage(1);
    setReportFindings(null);
    setReportData(null);
  }, [p.session?.id]);

  useEffect(() => {
    if (p.tab !== "report" || !p.session?.id) return;
    let cancelled = false;
    setReportLoading(true);
    Promise.all([
      api.findings(p.session.id, reportPage, DEFAULT_PAGE_SIZE),
      api.report(p.session.id),
    ])
      .then(([findings, report]) => {
        if (cancelled) return;
        setReportFindings(findings);
        setReportData(report);
      })
      .catch((e) => {
        if (!cancelled) p.setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setReportLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [p.tab, p.session?.id, reportPage, p.setError]);

  return {
    dash,
    dashLoading,
    dashSessions,
    dashSessionsPage,
    setDashSessionsPage,
    dashFindings,
    dashFindingsPage,
    setDashFindingsPage,
    findingsData,
    setFindingsData,
    galleryData,
    galleryAlbums,
    findingsLoading,
    galleryLoading,
    findingsPage,
    setFindingsPage,
    galleryPage,
    setGalleryPage,
    reportFindings,
    setReportFindings,
    reportData,
    reportLoading,
    reportPage,
    setReportPage,
    setGalleryAlbums,
    setGalleryData,
    setDashFindings,
    setReportData,
    resetQueries,
    refreshGallery,
  };
}
