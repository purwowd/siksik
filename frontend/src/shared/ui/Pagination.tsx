export const DEFAULT_PAGE_SIZE = 10;

export interface PageResult {
  page: number;
  pages: number;
  total: number;
  page_size: number;
}

interface PaginationProps extends PageResult {
  onPage: (page: number) => void;
  label?: string;
}

export function Pagination({ page, pages, total, page_size, onPage, label = "Baris" }: PaginationProps) {
  if (total === 0) return null;

  const from = (page - 1) * page_size + 1;
  const to = Math.min(page * page_size, total);

  return (
    <div className="pagination">
      <span className="pagination-meta">
        {label}: {from}–{to} dari {total}
      </span>
      <div className="pagination-controls">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Sebelumnya
        </button>
        <span className="pagination-page">
          Hal {page} / {pages}
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Berikutnya
        </button>
      </div>
    </div>
  );
}
