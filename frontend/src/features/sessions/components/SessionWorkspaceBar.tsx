import { SessionPicker } from "@/features/sessions/components/SessionPicker";
import type { SessionSummary } from "@/shared/api/client";

export type SessionWorkspaceProps = {
  sessionList: SessionSummary[];
  sessionId: string | null;
  sessionsLoading: boolean;
  onPickSession: (id: string) => void;
  compact?: boolean;
};

type Props = SessionWorkspaceProps & {
  note?: React.ReactNode;
  extra?: React.ReactNode;
};

export function SessionWorkspaceBar({
  sessionList,
  sessionId,
  sessionsLoading,
  onPickSession,
  compact = false,
  note,
  extra,
}: Props) {
  return (
    <div
      className={`findings-toolbar feature-workspace-bar${compact ? " workspace-bar-compact" : ""}`}
    >
      <div className="findings-toolbar-left">
        <SessionPicker
          sessions={sessionList}
          value={sessionId}
          loading={sessionsLoading}
          onChange={onPickSession}
          compact={compact}
        />
      </div>
      {note ? <div className="workspace-bar-note">{note}</div> : null}
      {extra ? <div className="findings-toolbar-right">{extra}</div> : null}
    </div>
  );
}
