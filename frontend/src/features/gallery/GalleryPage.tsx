import { FindingsSkeleton } from "@/features/findings/components/FindingsSkeleton";
import { GalleryList } from "@/features/gallery/components/GalleryList";
import { EmptyState } from "@/shared/ui/EmptyState";
import { FeaturePageShell } from "@/shared/ui/FeaturePageShell";
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
  const classificationAlbums = albums.filter(
    (item) => item.kind === "classification" && item.count > 0,
  );
  const accessAlbums = ACCESS_FILTERS.map((id) => {
    const match = albums.find((item) => item.id === id);
    return {
      id,
      label: ACCESS_FILTER_LABELS[id],
      count: match?.count ?? 0,
    };
  });
  const empty = FEATURE_EMPTY_NO_SESSION.gallery;

  return (
    <FeaturePageShell
      meta={FEATURE_PAGE_META.gallery}
      panelClass="findings-panel"
      session={{
        sessionList,
        sessionId: session?.id ?? null,
        sessionsLoading,
        onPickSession,
      }}
      toolbarNote={
        <p className="review-progress compact" role="note">
          Semua data yang berhasil ditarik pada sesi ditampilkan. Flag tidak menyaring data; Trash
          dan hasil recovery dihitung terpisah.
        </p>
      }
      filters={
        <div className="filter-row gallery-filter-row" role="group" aria-label="Album galeri">
          <span className="filter-label">Galeri</span>
          {onRefresh ? (
            <button
              type="button"
              className="chip"
              disabled={!session || loading}
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
            >
              {item.label} {item.count}
            </button>
          ))}
          {classificationAlbums.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`chip ${album === item.id ? "active" : ""}`}
              aria-pressed={album === item.id}
              onClick={() => setAlbum(item.id)}
            >
              {item.label} {item.count}
            </button>
          ))}
          {originAlbums.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`chip ${album === item.id ? "active" : ""}`}
              aria-pressed={album === item.id}
              onClick={() => setAlbum(item.id)}
            >
              {item.label} {item.count}
            </button>
          ))}
        </div>
      }
    >
      {!session ? (
        <EmptyState title={empty.title} body={empty.body} hint={empty.hint} />
      ) : loading && !data ? (
        <FindingsSkeleton />
      ) : !data || data.total === 0 ? (
        <EmptyState
          title={loading ? "Memuat galeri…" : "Album kosong"}
          body={loading ? "Memuat media…" : "Tidak ada media pada album / filter ini."}
          hint={!loading ? "Coba album lain atau tunggu transfer selesai." : undefined}
        />
      ) : (
        <div className={loading ? "list-refreshing" : undefined} aria-busy={loading}>
          <GalleryList sessionId={session.id} data={data} onPage={onPage} />
        </div>
      )}
    </FeaturePageShell>
  );
}
