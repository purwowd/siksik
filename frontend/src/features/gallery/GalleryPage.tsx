import { GalleryList } from "@/features/gallery/components/GalleryList";
import { EmptyState } from "@/shared/ui/EmptyState";
import { FeaturePageShell } from "@/shared/ui/FeaturePageShell";
import { isContentLoading } from "@/shared/lib/pageLoad";
import { FEATURE_EMPTY_NO_SESSION, FEATURE_PAGE_META } from "@/shared/lib/featurePages";
import type { GalleryAlbum, GalleryItem, Paginated, SessionSummary } from "@/shared/api/client";
import { ACCESS_FILTER_LABELS, ACCESS_FILTERS } from "@/app/routes";

type Props = {
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  loading: boolean;
  albums: GalleryAlbum[];
  album: string;
  setAlbum: (id: string) => void;
  data: Paginated<GalleryItem> | null;
  onPickSession: (id: string) => void;
  onPage: (page: number) => void;
  onRefresh?: () => void;
};

export function GalleryPage({
  session,
  sessionList,
  sessionsLoading,
  loading,
  albums,
  album,
  setAlbum,
  data,
  onPickSession,
  onPage,
  onRefresh,
}: Props) {
  const originAlbums = albums.filter((item) => item.kind === "album" && item.count > 0);
  const accessAlbums = ACCESS_FILTERS.map((id) => {
    const match = albums.find((item) => item.id === id);
    return {
      id,
      label: ACCESS_FILTER_LABELS[id],
      count: match?.count ?? 0,
    };
  });
  const empty = FEATURE_EMPTY_NO_SESSION.gallery;
  const contentLoading = !!session && isContentLoading(loading, data);
  const accessSet = new Set<string>(ACCESS_FILTERS);
  const originSelected = !!album && !accessSet.has(album);

  return (
    <FeaturePageShell
      meta={FEATURE_PAGE_META.gallery}
      panelClass="findings-panel"
      loading={contentLoading}
      session={{
        sessionList,
        sessionId: session?.id ?? null,
        sessionsLoading,
        onPickSession,
      }}
      toolbarNote={
        <p className="review-progress compact" role="note">
          Bukan seluruh isi HP — hanya media yang diambil pada sesi aktif.
        </p>
      }
      filters={
        <div className="gallery-filters">
          <div className="filter-row gallery-filter-row" role="group" aria-label="Filter akses">
            <span className="filter-label">Akses</span>
            {onRefresh ? (
              <button
                type="button"
                className="chip"
                disabled={!session || contentLoading}
                onClick={onRefresh}
              >
                Muat ulang
              </button>
            ) : null}
            {accessAlbums.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`chip ${album === item.id ? "active" : ""}`}
                aria-pressed={album === item.id}
                onClick={() => setAlbum(item.id)}
                disabled={contentLoading}
              >
                {item.label} {item.count}
              </button>
            ))}
          </div>
          {originAlbums.length > 0 ? (
            <div className="field gallery-album-pick">
              <label htmlFor="gallery-album">Folder perangkat</label>
              <select
                id="gallery-album"
                value={originSelected ? album : ""}
                disabled={contentLoading || !session}
                onChange={(e) => setAlbum(e.target.value || "all")}
              >
                <option value="">Semua folder</option>
                {originAlbums.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} ({item.count})
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
      }
    >
      {!session ? (
        <EmptyState title={empty.title} body={empty.body} hint={empty.hint} />
      ) : !data || data.total === 0 ? (
        <EmptyState
          title="Album kosong"
          body="Tidak ada media pada album / filter ini."
          hint="Coba album lain atau tunggu transfer selesai."
        />
      ) : (
        <div className={loading ? "list-refreshing" : undefined} aria-busy={loading}>
          <GalleryList sessionId={session.id} data={data} onPage={onPage} />
        </div>
      )}
    </FeaturePageShell>
  );
}
