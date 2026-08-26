import { useEffect, useMemo, useRef, useState } from "react";
import {
  can,
  type AuthSession,
  type Finding,
  type Paginated,
  type SessionSummary,
} from "@/shared/api/client";
import { FindingsList } from "@/features/findings/components/FindingsList";
import { EmptyState } from "@/shared/ui/EmptyState";
import { FeatureKpiGrid } from "@/shared/ui/FeatureKpiGrid";
import { FeaturePageShell } from "@/shared/ui/FeaturePageShell";
import { isContentLoading } from "@/shared/lib/pageLoad";
import { KeyboardHelpPanel } from "@/features/findings/components/KeyboardHelpPanel";
import { VerdictNotice } from "@/features/findings/components/VerdictNotice";
import { isOpenRecommendation, isThreatRecommendation } from "@/shared/constants";
import { FEATURE_EMPTY_NO_SESSION, FEATURE_PAGE_META } from "@/shared/lib/featurePages";
import { sessionIsAuthorized } from "@/shared/lib/caseChecklist";
import { MODULE_FILTER_LABELS } from "@/features/dashboard/lib/moduleFilters";
import type { ModuleFilterParam, ReviewFilterParam } from "@/app/routes";

type Props = {
  auth: AuthSession;
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  findingsLoading: boolean;
  reviewSummary: { pending: number; confirmed: number; rejected: number; total: number } | null;
  onPickSession: (id: string) => void;
  refreshSessionList: () => void;
  refreshFindings: () => void;
  reviewFilter: ReviewFilterParam;
  setReviewFilter: (v: ReviewFilterParam) => void;
  moduleFilter: ModuleFilterParam | null;
  setModuleFilter: (v: ModuleFilterParam | null) => void;
  findingsData: Paginated<Finding> | null;
  expandedEvidence: string | null;
  setExpandedEvidence: (id: string | null) => void;
  reviewBusyId: string | null;
  bulkBusy: boolean;
  onReview: (id: string, status: "confirmed" | "rejected") => void;
  onBulkReview: (status: "confirmed" | "rejected") => void;
  onPage: (page: number) => void;
  focusedFindingId: string | null;
  setFocusedFindingId: (id: string | null) => void;
};

export function FindingsPage({
  auth,
  session,
  sessionList,
  sessionsLoading,
  findingsLoading,
  reviewSummary,
  onPickSession,
  refreshSessionList,
  refreshFindings,
  reviewFilter,
  setReviewFilter,
  moduleFilter,
  setModuleFilter,
  findingsData,
  expandedEvidence,
  setExpandedEvidence,
  reviewBusyId,
  bulkBusy,
  onReview,
  onBulkReview,
  onPage,
  focusedFindingId,
  setFocusedFindingId,
}: Props) {
  const panelRef = useRef<HTMLElement>(null);
  const canReview = can(auth, "findings:review") && !sessionIsAuthorized(session);
  const [kbdOpen, setKbdOpen] = useState(false);

  const showVerdict =
    !!session && session.status === "completed" && isOpenRecommendation(session.recommendation);

  const focusableItems = useMemo(
    () => findingsData?.items.filter((f) => f.review_status === "pending" && canReview) ?? [],
    [findingsData, canReview],
  );

  const focusIndex = focusedFindingId
    ? focusableItems.findIndex((f) => f.id === focusedFindingId)
    : focusableItems.length > 0
      ? 0
      : -1;

  useEffect(() => {
    if (focusableItems.length === 0) {
      setFocusedFindingId(null);
      return;
    }
    if (!focusedFindingId || !focusableItems.some((f) => f.id === focusedFindingId)) {
      setFocusedFindingId(focusableItems[0].id);
    }
  }, [focusableItems, focusedFindingId, setFocusedFindingId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!canReview || focusableItems.length === 0) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const next = Math.min(focusIndex + 1, focusableItems.length - 1);
        setFocusedFindingId(focusableItems[next]?.id ?? null);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const prev = Math.max(focusIndex - 1, 0);
        setFocusedFindingId(focusableItems[prev]?.id ?? null);
      } else if (e.key === "c" && focusedFindingId) {
        e.preventDefault();
        if (reviewBusyId || bulkBusy) return;
        void onReview(focusedFindingId, "confirmed");
      } else if (e.key === "r" && focusedFindingId) {
        e.preventDefault();
        if (reviewBusyId || bulkBusy) return;
        void onReview(focusedFindingId, "rejected");
      } else if (e.key === "?") {
        e.preventDefault();
        setKbdOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canReview, focusableItems, focusIndex, focusedFindingId, onReview, setFocusedFindingId, reviewBusyId, bulkBusy]);

  const pending = reviewSummary?.pending ?? 0;
  const total = reviewSummary?.total ?? findingsData?.total ?? 0;
  const empty = FEATURE_EMPTY_NO_SESSION.findings;
  const loading = !!session && isContentLoading(findingsLoading, findingsData);
  const threat =
    isThreatRecommendation(session?.recommendation) || (findingsData?.total ?? 0) > 0;

  return (
    <FeaturePageShell
      meta={FEATURE_PAGE_META.findings}
      panelRef={panelRef}
      panelClass="findings-panel"
      threat={threat}
      loading={loading}
      kpis={
        session && !loading ? (
        <FeatureKpiGrid
          ariaLabel="Metrik tinjauan"
          items={[
            { label: "Sisa antrean", value: pending, tone: "warn" },
            { label: "Dikonfirmasi", value: reviewSummary?.confirmed ?? 0, tone: (reviewSummary?.confirmed ?? 0) > 0 ? "bad" : "muted" },
            { label: "Ditolak", value: reviewSummary?.rejected ?? 0, tone: "muted" },
            { label: "Total", value: total },
          ]}
        />
        ) : undefined
      }
      session={{
        sessionList,
        sessionId: session?.id ?? null,
        sessionsLoading,
        onPickSession,
      }}
      toolbarExtra={
        loading ? undefined : (
        <>
          {showVerdict && session && <VerdictNotice recommendation={session.recommendation} />}
          {reviewSummary && reviewSummary.total > 0 && (
            <p className="review-progress compact" role="status">
              <strong>{reviewSummary.pending}</strong> / {reviewSummary.total} menunggu
              {reviewSummary.confirmed > 0 && (
                <span className="review-progress-sub">
                  · {reviewSummary.confirmed} dikonfirmasi · {reviewSummary.rejected} ditolak
                </span>
              )}
            </p>
          )}
        </>
        )
      }
      filters={
        <>
          <KeyboardHelpPanel open={kbdOpen} onClose={() => setKbdOpen(false)} />
          <div className="filter-row" role="group" aria-label="Filter verifikasi">
            <span className="filter-label">Filter</span>
            {(
              [
                ["pending", "Menunggu"],
                ["all", "Semua"],
                ["confirmed", "Dikonfirmasi"],
                ["rejected", "Ditolak"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`chip ${reviewFilter === id ? "active" : ""}`}
                aria-pressed={reviewFilter === id}
                onClick={() => setReviewFilter(id)}
              >
                {label}
              </button>
            ))}
            {canReview && reviewSummary && reviewSummary.pending > 0 && (
              <div className="bulk-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={bulkBusy || !!reviewBusyId}
                  onClick={() => void onBulkReview("confirmed")}
                >
                  Konfirmasi semua
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={bulkBusy || !!reviewBusyId}
                  onClick={() => void onBulkReview("rejected")}
                >
                  Tolak semua
                </button>
              </div>
            )}
            <button
              type="button"
              className="btn btn-ghost filter-refresh"
              onClick={() => setKbdOpen(true)}
              title="Pintasan keyboard"
            >
              Bantuan ?
            </button>
            <button
              type="button"
              className="btn btn-ghost filter-refresh"
              onClick={() => {
                void refreshSessionList();
                refreshFindings();
              }}
            >
              Muat ulang
            </button>
            {canReview && (
              <span className="keyboard-hint-inline queue-kbd" role="note">
                Antrean: <kbd>J</kbd>/<kbd>K</kbd> pindah · <kbd>C</kbd> konfirmasi ·{" "}
                <kbd>R</kbd> tolak
              </span>
            )}
          </div>

          {moduleFilter && (
            <div className="filter-row module-filter-row">
              <span className="filter-label">Modul</span>
              <span className="chip active">{MODULE_FILTER_LABELS[moduleFilter]}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setModuleFilter(null)}>
                Hapus filter modul
              </button>
            </div>
          )}
        </>
      }
    >
      {!session ? (
        <EmptyState title={empty.title} body={empty.body} hint={empty.hint} />
      ) : !findingsData || findingsData.total === 0 ? (
        reviewFilter === "pending" ? (
          <EmptyState
            title="Antrean kosong"
            body={
              (reviewSummary?.total ?? 0) > 0
                ? "Tidak ada temuan menunggu. Buka filter Semua atau Ditolak untuk melihat hasil tinjauan."
                : "Tidak ada temuan menunggu untuk sesi ini."
            }
            hint={
              (reviewSummary?.total ?? 0) > 0
                ? "Tinjauan antrean selesai."
                : "Tidak ada temuan menunggu pada filter ini."
            }
            tone="ok"
          />
        ) : (
          <EmptyState
            title="Tidak ada temuan"
            body="Belum ada temuan pada sesi ini — atau filter menyembunyikan hasil."
            hint="Coba filter lain atau tunggu analisa selesai."
          />
        )
      ) : (
        <div className={findingsLoading ? "list-refreshing" : undefined} aria-busy={findingsLoading}>
          <FindingsList
            auth={auth}
            sessionId={session.id}
            data={findingsData}
            expandedEvidence={expandedEvidence}
            reviewBusyId={reviewBusyId}
            focusedFindingId={focusedFindingId}
            onExpand={setExpandedEvidence}
            onReview={onReview}
            onPage={onPage}
            onFocusFinding={setFocusedFindingId}
          />
        </div>
      )}
    </FeaturePageShell>
  );
}
