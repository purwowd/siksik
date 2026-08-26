import type { GalleryItem, Paginated } from "@/shared/api/client";
import { Pagination } from "@/shared/ui/Pagination";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import { displayMediaName, displayPath, displayStamp } from "@/shared/lib/displayPath";
import { MediaPreview } from "./MediaPreview";

type Props = {
  sessionId: string;
  data: Paginated<GalleryItem>;
  onPage: (page: number) => void;
};

function capturedLabel(value: string | null | undefined): string {
  return displayStamp(value);
}

export function GalleryList({ sessionId, data, onPage }: Props) {
  return (
    <>
      <div className="findings-desktop">
        <table className="table findings-table">
          <thead>
            <tr>
              <th>Pratinjau</th>
              <th>Nama</th>
              <th>Album</th>
              <th>Sumber</th>
              <th>Waktu</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id} className="hit-row">
                <td>
                  <MediaPreview
                    sessionId={sessionId}
                    path={item.preview_path || item.path}
                    text={item.preview_text || item.label}
                    mime={item.mime}
                  />
                </td>
                <td>
                  <strong className="finding-label" title={item.label}>
                    {displayMediaName(item.label, item.path, item.album)}
                  </strong>
                  {item.favorite ? <div className="finding-meta">Favorit perangkat</div> : null}
                </td>
                <td>
                  <span className="finding-source">{item.album}</span>
                </td>
                <td>
                  <span className="finding-source">{humanLabel("source", item.source)}</span>
                  <div className="finding-path" title={item.path}>{displayPath(item.path)}</div>
                </td>
                <td>{capturedLabel(item.captured_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="findings-cards" aria-label="Daftar galeri">
        {data.items.map((item) => (
          <article key={item.id} className="finding-card">
            <div className="finding-card-media">
              <MediaPreview
                sessionId={sessionId}
                path={item.preview_path || item.path}
                text={item.preview_text || item.label}
                mime={item.mime}
              />
            </div>
            <div className="finding-card-body">
              <strong className="finding-label" title={item.label}>
                {displayMediaName(item.label, item.path, item.album)}
              </strong>
              <div className="finding-meta">
                <span>{item.album}</span>
                <span>·</span>
                <span>{humanLabel("source", item.source)}</span>
                {item.favorite ? (
                  <>
                    <span>·</span>
                    <span>Favorit</span>
                  </>
                ) : null}
              </div>
              <div className="finding-path" title={item.path}>{displayPath(item.path)}</div>
              <div className="evidence-body">{capturedLabel(item.captured_at)}</div>
            </div>
          </article>
        ))}
      </div>

      <Pagination
        page={data.page}
        pages={data.pages}
        total={data.total}
        page_size={data.page_size}
        onPage={onPage}
      />
    </>
  );
}
