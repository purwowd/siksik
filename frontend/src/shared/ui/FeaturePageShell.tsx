import type { ReactNode } from "react";
import {
  SessionWorkspaceBar,
  type SessionWorkspaceProps,
} from "@/features/sessions/components/SessionWorkspaceBar";
import { PageLoading } from "@/shared/ui/PageLoading";
import { PanelTitle } from "@/shared/ui/PanelTitle";
import type { FeaturePageMeta } from "@/shared/lib/featurePages";

type Props = {
  meta: FeaturePageMeta;
  threat?: boolean;
  panelClass?: string;
  panelRef?: React.RefObject<HTMLElement | null>;
  hero?: ReactNode;
  kpis?: ReactNode;
  session?: SessionWorkspaceProps;
  toolbarNote?: ReactNode;
  toolbarExtra?: ReactNode;
  filters?: ReactNode;
  /** Ganti children dengan Memuat… — jangan tampilkan data sesi lama. */
  loading?: boolean;
  children: ReactNode;
};

/** Layout standar halaman fitur konsol. */
export function FeaturePageShell({
  meta,
  threat,
  panelClass,
  panelRef,
  hero,
  kpis,
  session,
  toolbarNote,
  toolbarExtra,
  filters,
  loading = false,
  children,
}: Props) {
  return (
    <section
      ref={panelRef}
      className={`panel ent-panel ent-desk${panelClass ? ` ${panelClass}` : ""}${threat ? " threat" : ""}`}
    >
      {hero}

      <div className="ent-desk-head">
        <div>
          <PanelTitle title={meta.title} />
          <p className="ent-panel-copy">{meta.copy}</p>
        </div>
        {kpis}
      </div>

      {session ? (
        <SessionWorkspaceBar
          sessionList={session.sessionList}
          sessionId={session.sessionId}
          sessionsLoading={session.sessionsLoading}
          onPickSession={session.onPickSession}
          compact={session.compact}
          note={toolbarNote}
          extra={toolbarExtra}
        />
      ) : null}

      {filters}
      {loading ? <PageLoading /> : children}
    </section>
  );
}
