import { useMemo, useState } from "react";
import type { SessionSummary } from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import { StatusPill } from "@/shared/ui/StatusPill";

type StatusFilter = "all" | "active" | "completed" | "failed";
type SortKey = "newest" | "oldest" | "findings";

function sessionTitle(s: SessionSummary): string {
  const p = s.participant;
  if (p?.full_name) {
    const reg = p.registration_no ? ` · ${p.registration_no}` : "";
    return `${p.full_name}${reg}`;
  }
  return s.label || s.device_id;
}

function sessionOptionLabel(s: SessionSummary): string {
  const name = sessionTitle(s);
  const findings = s.progress?.findings_count ?? 0;
  const rec = s.recommendation ? ` · ${s.recommendation}` : "";
  return `${name} · ${findings} temuan · ${s.mode === "full" ? "PENUH" : "CEPAT"}${rec}`;
}

function matchesFilter(s: SessionSummary, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return ACTIVE.has(s.status);
  if (filter === "completed") return s.status === "completed";
  return s.status === "failed" || s.status === "cancelled";
}

export function SessionPicker({
  sessions,
  value,
  onChange,
  loading,
  compact = false,
}: {
  sessions: SessionSummary[];
  value: string | null;
  onChange: (sessionId: string) => void;
  loading?: boolean;
  /** Laporan / dasbor: tanpa baris filter pencarian. */
  compact?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("newest");

  const filtered = useMemo(() => {
    if (compact) {
      return [...sessions].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    }
    const q = query.trim().toLowerCase();
    let list = sessions.filter((s) => matchesFilter(s, statusFilter));
    if (q) {
      list = list.filter((s) => {
        const p = s.participant;
        const hay = [
          s.label,
          s.device_id,
          s.id,
          s.recommendation || "",
          p?.full_name || "",
          p?.registration_no || "",
          p?.organization || "",
        ]
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      });
    }
    list = [...list].sort((a, b) => {
      if (sort === "findings") {
        return (b.progress?.findings_count ?? 0) - (a.progress?.findings_count ?? 0);
      }
      const ta = Date.parse(a.created_at);
      const tb = Date.parse(b.created_at);
      return sort === "newest" ? tb - ta : ta - tb;
    });
    return list;
  }, [sessions, query, statusFilter, sort, compact]);

  const selected = sessions.find((x) => x.id === value);
  const activeOutsideFilter = Boolean(
    value && selected && !filtered.some((s) => s.id === value),
  );
  const selectDisabled = loading || (filtered.length === 0 && !activeOutsideFilter);

  return (
    <div className={`session-picker session-picker-enhanced${compact ? " session-picker-compact" : ""}`}>
      {!compact && (
        <div className="session-picker-controls">
          <div className="field session-picker-search">
            <label htmlFor="session-search">Cari sesi</label>
            <input
              id="session-search"
              type="search"
              placeholder="Nama peserta, no. ASN, perangkat…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="session-status-filter">Status</label>
            <select
              id="session-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="all">Semua</option>
              <option value="active">Berjalan</option>
              <option value="completed">Selesai</option>
              <option value="failed">Gagal / batal</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="session-sort">Urutkan</label>
            <select id="session-sort" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="newest">Terbaru</option>
              <option value="oldest">Terlama</option>
              <option value="findings">Temuan terbanyak</option>
            </select>
          </div>
        </div>
      )}

      <div className="field">
        <label htmlFor="sadt-session-pick">Sesi aktif</label>
        <select
          id="sadt-session-pick"
          disabled={selectDisabled}
          value={value || ""}
          onChange={(e) => {
            const id = e.target.value;
            if (id) onChange(id);
          }}
        >
          {!value && filtered.length === 0 ? (
            <option value="">Tidak ada sesi cocok</option>
          ) : null}
          {activeOutsideFilter && selected ? (
            <option value={selected.id}>{sessionOptionLabel(selected)} · di luar filter</option>
          ) : null}
          {filtered.map((s) => (
            <option key={s.id} value={s.id}>
              {sessionOptionLabel(s)}
            </option>
          ))}
        </select>
        {!compact && filtered.length !== sessions.length && (
          <small className="field-note">
            Menampilkan {filtered.length} dari {sessions.length} sesi
            {activeOutsideFilter ? " · sesi aktif dipertahankan" : ""}
          </small>
        )}
      </div>

      {selected && (
        <div className="session-picker-meta">
          <StatusPill status={selected.status} recommendation={selected.recommendation} />
          {!compact && (
            <>
              <span className="pill muted">
                {humanLabel("method", selected.progress?.acquisition_method || "unknown")}
              </span>
              <span className="pill muted">{selected.progress?.findings_count ?? 0} temuan</span>
            </>
          )}
          <span className="pill muted mono">{selected.id.slice(0, 8)}</span>
        </div>
      )}
    </div>
  );
}
