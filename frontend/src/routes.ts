import type { ReviewStatus } from "./api";
import type { Tab } from "./types";

/** URL path per menu — bahasa Indonesia untuk operator lapangan. */
export const TAB_PATHS: Record<Tab, string> = {
  operator: "/operator",
  findings: "/temuan",
  report: "/laporan",
  dashboard: "/dasbor",
};

const PATH_TO_TAB = Object.fromEntries(
  (Object.entries(TAB_PATHS) as [Tab, string][]).map(([tab, path]) => [path, tab]),
) as Record<string, Tab>;

export type ReviewFilterParam = "all" | ReviewStatus;

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
} {
  const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const filter = sp.get("filter");
  const validFilters = new Set(["all", "pending", "confirmed", "rejected"]);
  return {
    sesi: sp.get("sesi"),
    filter: filter && validFilters.has(filter) ? (filter as ReviewFilterParam) : null,
  };
}

export function buildTabUrl(
  tab: Tab,
  params?: { sesi?: string | null; filter?: ReviewFilterParam | null },
): string {
  const path = pathFromTab(tab);
  const sp = new URLSearchParams();
  if (params?.sesi) sp.set("sesi", params.sesi);
  if (params?.filter && params.filter !== "all") sp.set("filter", params.filter);
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
