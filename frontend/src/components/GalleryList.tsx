import type { GalleryItem, Paginated } from "../api";
import { Pagination } from "../Pagination";
import { humanLabel } from "../lib/dashboardLabels";
import { MediaPreview } from "./MediaPreview";

type Props = {
  sessionId: string;
  data: Paginated<GalleryItem>;
  onPage: (page: number) => void;
};

function capturedLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const stamp = value.replace("T", " ").replace("Z", "");
  return stamp.slice(0, 16);
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
                    text={item.label}
                  />
                </td>
                <td>
                  <strong className="finding-label">{item.label}</strong>
                  {item.favorite ? <div className="finding-meta">Favorit perangkat</div> : null}
                </td>
                <td>
                  <span className="finding-source">{item.album}</span>
                </td>
                <td>
                  <span className="finding-source">{humanLabel("source", item.source)}</span>
                  <div className="finding-path">{item.path}</div>
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
                text={item.label}
              />
            </div>
            <div className="finding-card-body">
              <strong className="finding-label">{item.label}</strong>
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
              <div className="finding-path">{item.path}</div>
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
