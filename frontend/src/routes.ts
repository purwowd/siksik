import type { ReviewStatus } from "./api";
import type { Tab } from "./types";

/** URL path per menu — bahasa Indonesia untuk operator lapangan. */
export const TAB_PATHS: Record<Tab, string> = {
  operator: "/operator",
  findings: "/temuan",
  gallery: "/galeri",
  report: "/laporan",
  dashboard: "/dasbor",
};

const PATH_TO_TAB = Object.fromEntries(
  (Object.entries(TAB_PATHS) as [Tab, string][]).map(([tab, path]) => [path, tab]),
) as Record<string, Tab>;

export type ReviewFilterParam = "all" | ReviewStatus;
export type AccessFilter = "all" | "frequent" | "recent" | "favorite";

export const ACCESS_FILTERS: AccessFilter[] = ["all", "frequent", "recent", "favorite"];
export const ACCESS_FILTER_LABELS: Record<AccessFilter, string> = {
  all: "Semua",
  frequent: "Paling sering",
  recent: "Terbaru diakses",
  favorite: "Favorit",
};
export const DEFAULT_GALLERY_ALBUM: AccessFilter = "all";

const ALBUM_RE = /^[a-z0-9-]{1,64}$/;

export function pathFromTab(tab: Tab): string {
  return TAB_PATHS[tab];
}

export function tabFromPath(pathname: string): Tab | null {
  const base = pathname.split("?")[0].replace(/\/+$/, "") || "/";
  return PATH_TO_TAB[base] ?? null;
}

export function parseTabSearch(search: string): {
  sesi: string | null;
  filter: ReviewFilterParam | null;
  album: string | null;
} {
  const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const filter = sp.get("filter");
  const album = sp.get("album");
  const validFilters = new Set(["all", "pending", "confirmed", "rejected"]);
  return {
    sesi: sp.get("sesi"),
    filter: filter && validFilters.has(filter) ? (filter as ReviewFilterParam) : null,
    album: album && ALBUM_RE.test(album) ? album : null,
  };
}

export function buildTabUrl(
  tab: Tab,
  params?: { sesi?: string | null; filter?: ReviewFilterParam | null; album?: string | null },
): string {
  const path = pathFromTab(tab);
  const sp = new URLSearchParams();
  if (params?.sesi) sp.set("sesi", params.sesi);
  if (tab === "findings" && params?.filter && params.filter !== "all") {
    sp.set("filter", params.filter);
  }
  if (tab === "gallery" && params?.album && params.album !== DEFAULT_GALLERY_ALBUM) {
    sp.set("album", params.album);
  }
  const q = sp.toString();
  return q ? `${path}?${q}` : path;
}

/** Cocokkan sesi dari query ?sesi= (full UUID atau 8 char prefix). */
export function resolveSessionId(
  querySesi: string | null,
  sessions: { id: string }[],
): string | null {
  if (!querySesi) return null;
  const exact = sessions.find((s) => s.id === querySesi);
  if (exact) return exact.id;
  const pref = sessions.find((s) => s.id.startsWith(querySesi));
  return pref?.id ?? null;
}
