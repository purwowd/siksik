import { useCallback, useEffect } from "react";
import { can, type AuthSession } from "@/shared/api/client";
import {
  buildTabUrl,
  DEFAULT_GALLERY_ALBUM,
  parseTabSearch,
  pathFromTab,
  tabFromPath,
  type ModuleFilterParam,
  type ReviewFilterParam,
} from "@/app/routes";
import type { Tab } from "@/shared/types";

type Params = {
  auth: AuthSession | null;
  location: ReturnType<typeof import("react-router-dom").useLocation>;
  navigate: ReturnType<typeof import("react-router-dom").useNavigate>;
  allowedTabs: { id: Tab; label: string }[];
  landingTab: Tab;
  sessionId: string | null | undefined;
  reviewFilter: ReviewFilterParam;
  moduleFilter: ModuleFilterParam | null;
  galleryAlbum: string;
  setReviewFilter: (v: ReviewFilterParam) => void;
  setModuleFilter: (v: ModuleFilterParam | null) => void;
  setGalleryAlbum: (v: string) => void;
  urlFilterApplied: React.MutableRefObject<boolean>;
};

export function useConsoleNavigation(p: Params) {
  const tab = tabFromPath(p.location.pathname);

  const goToTab = useCallback(
    (
      next: Tab,
      opts?: {
        sesi?: string | null;
        filter?: ReviewFilterParam | null;
        album?: string | null;
        modul?: ModuleFilterParam | null;
      },
    ) => {
      p.navigate(
        buildTabUrl(next, {
          sesi: opts?.sesi ?? p.sessionId ?? null,
          filter: opts?.filter ?? (next === "findings" ? p.reviewFilter : null),
          album: opts?.album ?? (next === "gallery" ? p.galleryAlbum : null),
          modul: opts?.modul ?? (next === "findings" ? p.moduleFilter : null),
        }),
      );
    },
    [p.navigate, p.sessionId, p.reviewFilter, p.galleryAlbum, p.moduleFilter],
  );

  useEffect(() => {
    if (!p.auth || p.allowedTabs.length === 0) return;
    if (p.location.pathname === "/") return;
    const current = tabFromPath(p.location.pathname);
    if (!current || !p.allowedTabs.some((t) => t.id === current)) {
      p.navigate(pathFromTab(p.landingTab), { replace: true });
    }
  }, [p.auth, p.allowedTabs, p.landingTab, p.location.pathname, p.navigate]);

  useEffect(() => {
    if (!p.auth) return;
    if (!p.urlFilterApplied.current) {
      if (can(p.auth, "findings:review")) p.setReviewFilter("pending");
      p.urlFilterApplied.current = true;
    }
    const { filter, album, modul } = parseTabSearch(p.location.search);
    if (tab === "findings" && filter) p.setReviewFilter(filter);
    if (tab === "findings") p.setModuleFilter(modul);
    if (tab === "gallery") p.setGalleryAlbum(album ?? DEFAULT_GALLERY_ALBUM);
  }, [p.auth, p.location.search, tab, p.setReviewFilter, p.setModuleFilter, p.setGalleryAlbum, p.urlFilterApplied]);

  useEffect(() => {
    if (!p.auth || !tab) return;
    if (tab !== "findings" && tab !== "gallery" && tab !== "report" && tab !== "dashboard") return;
    const { sesi: urlSesi } = parseTabSearch(p.location.search);
    if (
      urlSesi &&
      p.sessionId &&
      urlSesi !== p.sessionId &&
      !p.sessionId.startsWith(urlSesi)
    ) {
      return;
    }
    const url = buildTabUrl(tab, {
      sesi: p.sessionId ?? urlSesi ?? null,
      filter: tab === "findings" ? p.reviewFilter : null,
      album: tab === "gallery" ? p.galleryAlbum : null,
      modul: tab === "findings" ? p.moduleFilter : null,
    });
    const current = `${p.location.pathname}${p.location.search}`;
    if (current !== url) p.navigate(url, { replace: true });
  }, [
    p.auth,
    tab,
    p.sessionId,
    p.reviewFilter,
    p.galleryAlbum,
    p.moduleFilter,
    p.location.pathname,
    p.location.search,
    p.navigate,
  ]);

  return { tab, goToTab };
}
