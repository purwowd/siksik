import { FindingsSkeleton } from "../components/FindingsSkeleton";
import { GalleryList } from "../components/GalleryList";
import { PanelTitle } from "../components/PanelTitle";
import { SessionPicker } from "../components/SessionPicker";
import type { GalleryAlbum, GalleryItem, Paginated, SessionSummary } from "../api";
import { ACCESS_FILTER_LABELS, ACCESS_FILTERS } from "../routes";

type Props = {
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  sessionPickerLocked?: boolean;
  loading: boolean;
  albums: GalleryAlbum[];
  album: string;
  setAlbum: (id: string) => void;
  data: Paginated<GalleryItem> | null;
  onPickSession: (id: string) => void;
  onPage: (page: number) => void;
};

export function GalleryPage({
  session,
  sessionList,
  sessionsLoading,
  sessionPickerLocked,
  loading,
  albums,
  album,
  setAlbum,
  data,
  onPickSession,
  onPage,
}: Props) {
  const originAlbums = albums.filter((item) => item.kind === "album" && item.count > 0);
  const classificationAlbums = albums.filter((item) => item.kind === "classification");
  const accessAlbums = ACCESS_FILTERS.map((id) => {
    const match = albums.find((item) => item.id === id);
    return {
      id,
      label: ACCESS_FILTER_LABELS[id],
      count: match?.count ?? 0,
    };
  });

  return (
    <section className="panel findings-panel">
      <PanelTitle title="Galeri" />

      <div className="findings-toolbar">
        <div className="findings-toolbar-left">
          <SessionPicker
            sessions={sessionList}
            value={session?.id ?? null}
            loading={sessionsLoading}
            locked={sessionPickerLocked}
            onChange={onPickSession}
          />
        </div>
        <p className="review-progress compact" role="note">
          Semua file berstatus pulled ditampilkan, termasuk metadata pendamping; flag tidak menyaring
          data. Trash dan recovered image dihitung terpisah.
        </p>
      </div>

      <div className="filter-row" role="group" aria-label="Filter akses galeri">
        <span className="filter-label">Akses</span>
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
      </div>

      <div className="filter-row" role="group" aria-label="Status data galeri">
        <span className="filter-label">Status data</span>
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
      </div>

      <div className="filter-row" role="group" aria-label="Sumber dan album galeri">
        <span className="filter-label">Sumber / album</span>
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

      {!session ? (
        <div className="empty">Pilih sesi di atas untuk melihat galeri</div>
      ) : loading && !data ? (
        <FindingsSkeleton />
      ) : !data || data.total === 0 ? (
        <div className="empty">{loading ? "Memuat galeri…" : "Tidak ada data pada album ini"}</div>
      ) : (
        <div className={loading ? "list-refreshing" : undefined} aria-busy={loading}>
          <GalleryList sessionId={session.id} data={data} onPage={onPage} />
        </div>
      )}
    </section>
  );
}
