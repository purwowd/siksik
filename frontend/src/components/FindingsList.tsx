import { can, type AuthSession, type Finding, type Paginated, type ReviewStatus } from "../api";
import { Pagination } from "../Pagination";
import { FindingOriginBadge } from "./FindingOriginBadge";
import { MediaPreview } from "./MediaPreview";

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  pending: "Menunggu",
  confirmed: "Dikonfirmasi",
  rejected: "Ditolak",
};

type Props = {
  auth: AuthSession | null;
  sessionId: string;
  data: Paginated<Finding>;
  expandedEvidence: string | null;
  reviewBusyId: string | null;
  focusedFindingId?: string | null;
  onExpand: (id: string | null) => void;
  onReview: (id: string, status: "confirmed" | "rejected") => void;
  onPage: (page: number) => void;
  onFocusFinding?: (id: string) => void;
};

function FindingActions({
  f,
  auth,
  reviewBusyId,
  onReview,
}: {
  f: Finding;
  auth: AuthSession | null;
  reviewBusyId: string | null;
  onReview: (id: string, status: "confirmed" | "rejected") => void;
}) {
  if (f.review_status === "pending") {
    if (can(auth, "findings:review")) {
      return (
        <div className="row-actions">
          <button
            type="button"
            disabled={!!reviewBusyId}
            onClick={() => void onReview(f.id, "confirmed")}
          >
            Konfirmasi
          </button>
          <button
            type="button"
            disabled={!!reviewBusyId}
            onClick={() => void onReview(f.id, "rejected")}
          >
            Tolak
          </button>
        </div>
      );
    }
    return <span className="pill warn">Menunggu</span>;
  }
  return (
    <span className={`pill ${f.review_status === "confirmed" ? "bad" : "muted"}`}>
      {REVIEW_LABEL[f.review_status]}
    </span>
  );
}

export function FindingsList({
  auth,
  sessionId,
  data,
  expandedEvidence,
  reviewBusyId,
  focusedFindingId,
  onExpand,
  onReview,
  onPage,
  onFocusFinding,
}: Props) {
  return (
    <>
      <div className="findings-desktop">
        <table className="table findings-table">
          <thead>
            <tr>
              <th>Pratinjau</th>
              <th>Label</th>
              <th>Sumber</th>
              <th>Asal</th>
              <th>Keyakinan</th>
              <th>Bukti</th>
              <th>Verifikasi</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((f) => {
              const open = expandedEvidence === f.id;
              const long = (f.evidence || "").length > 120;
              const focused = focusedFindingId === f.id;
              return (
                <tr
                  key={f.id}
                  className={`hit-row${focused ? " finding-focused" : ""}`}
                  onMouseEnter={() => onFocusFinding?.(f.id)}
                >
                  <td>
                    <MediaPreview sessionId={sessionId} path={f.path} />
                  </td>
                  <td>
                    <strong className="finding-label">{f.label}</strong>
                    <div className="finding-meta">{f.category.replace(/_/g, " ")}</div>
                  </td>
                  <td>
                    <span className="finding-source">{f.source}</span>
                    <div className="finding-path">{f.path}</div>
                  </td>
                  <td>
                    <FindingOriginBadge layer={f.layer_origin} label={f.label} />
                  </td>
                  <td>{(f.confidence * 100).toFixed(0)}%</td>
                  <td className="evidence-cell">
                    <div className={`evidence-body ${open ? "open" : ""}`}>{f.evidence || "—"}</div>
                    {long && (
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => onExpand(open ? null : f.id)}
                      >
                        {open ? "Sembunyikan" : "Selengkapnya"}
                      </button>
                    )}
                  </td>
                  <td>
                    <FindingActions
                      f={f}
                      auth={auth}
                      reviewBusyId={reviewBusyId}
                      onReview={onReview}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="findings-cards" aria-label="Daftar temuan">
        {data.items.map((f) => {
          const open = expandedEvidence === f.id;
          const long = (f.evidence || "").length > 100;
          const focused = focusedFindingId === f.id;
          return (
            <article
              key={f.id}
              className={`finding-card${focused ? " finding-focused" : ""}`}
              onMouseEnter={() => onFocusFinding?.(f.id)}
            >
              <div className="finding-card-media">
                <MediaPreview sessionId={sessionId} path={f.path} />
              </div>
              <div className="finding-card-body">
                <strong className="finding-label">{f.label}</strong>
                <div className="finding-meta">
                  <span>{f.category.replace(/_/g, " ")}</span>
                  <span>·</span>
                  <span>{(f.confidence * 100).toFixed(0)}%</span>
                  <FindingOriginBadge layer={f.layer_origin} label={f.label} />
                </div>
                <div className="finding-path">{f.path}</div>
                <div className={`evidence-body ${open ? "open" : ""}`}>{f.evidence || "—"}</div>
                {long && (
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => onExpand(open ? null : f.id)}
                  >
                    {open ? "Sembunyikan" : "Selengkapnya"}
                  </button>
                )}
                <div className="finding-card-actions">
                  <FindingActions
                    f={f}
                    auth={auth}
                    reviewBusyId={reviewBusyId}
                    onReview={onReview}
                  />
                </div>
              </div>
            </article>
          );
        })}
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
