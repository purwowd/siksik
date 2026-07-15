import type { SessionSummary } from "../api";
import { StatusPill } from "./StatusPill";

function sessionOptionLabel(s: SessionSummary): string {
  const name = s.label || s.device_id;
  const rec = s.recommendation ? ` · ${s.recommendation}` : "";
  return `${name} · ${s.mode.toUpperCase()}${rec}`;
}

export function SessionPicker({
  sessions,
  value,
  onChange,
  loading,
}: {
  sessions: SessionSummary[];
  value: string | null;
  onChange: (sessionId: string) => void;
  loading?: boolean;
}) {
  return (
    <div className="session-picker">
      <label htmlFor="sadt-session-pick">Sesi aktif</label>
      <select
        id="sadt-session-pick"
        disabled={loading || sessions.length === 0}
        value={value || ""}
        onChange={(e) => {
          const id = e.target.value;
          if (id) onChange(id);
        }}
      >
        {sessions.length === 0 ? (
          <option value="">Belum ada sesi</option>
        ) : (
          sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {sessionOptionLabel(s)}
            </option>
          ))
        )}
      </select>
      {value && (
        <div className="session-picker-meta">
          {(() => {
            const s = sessions.find((x) => x.id === value);
            if (!s) return null;
            return (
              <>
                <StatusPill status={s.status} recommendation={s.recommendation} />
                <span className="pill muted">{s.progress?.acquisition_method || s.mode}</span>
                <span className="pill muted">{s.progress?.findings_count ?? 0} temuan</span>
                <span className="pill muted mono">{s.id.slice(0, 8)}</span>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
