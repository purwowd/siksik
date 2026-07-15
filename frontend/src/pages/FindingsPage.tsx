import { useEffect, useMemo, useRef } from "react";
import {
  can,
  type AuthSession,
  type Finding,
  type Paginated,
  type ReviewStatus,
  type SessionSummary,
} from "../api";
import { FindingsList } from "../components/FindingsList";
import { FindingsSkeleton } from "../components/FindingsSkeleton";
import { PanelTitle } from "../components/PanelTitle";
import { SessionPicker } from "../components/SessionPicker";
import { VerdictNotice } from "../components/VerdictNotice";
import { isOpenRecommendation, isThreatRecommendation } from "../constants";

type Props = {
  auth: AuthSession;
  session: SessionSummary | null;
  sessionList: SessionSummary[];
  sessionsLoading: boolean;
  findingsLoading: boolean;
  reviewSummary: { pending: number; confirmed: number; rejected: number; total: number } | null;
  onPickSession: (id: string) => void;
  refreshSessionList: () => void;
  reviewFilter: "all" | ReviewStatus;
  setReviewFilter: (v: "all" | ReviewStatus) => void;
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
  reviewFilter,
  setReviewFilter,
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
  const canReview = can(auth, "findings:review");

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
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canReview, focusableItems, focusIndex, focusedFindingId, onReview, setFocusedFindingId, reviewBusyId, bulkBusy]);

  return (
    <section
      ref={panelRef}
      className={`panel findings-panel${isThreatRecommendation(session?.recommendation) || (findingsData?.total ?? 0) > 0 ? " threat" : ""}`}
    >
      <PanelTitle title="Daftar temuan" />

      <div className="findings-toolbar">
        <div className="findings-toolbar-left">
          <SessionPicker
            sessions={sessionList}
            value={session?.id ?? null}
            loading={sessionsLoading}
            onChange={onPickSession}
          />
        </div>
        <div className="findings-toolbar-right">
          {showVerdict && <VerdictNotice recommendation={session.recommendation} />}
          {reviewSummary && reviewSummary.total > 0 && (
            <p className="review-progress compact" role="status">
              <strong>{reviewSummary.pending}</strong> / {reviewSummary.total} menunggu
              {reviewSummary.confirmed > 0 && (
                <span className="review-progress-sub">
                  · {reviewSummary.confirmed} OK · {reviewSummary.rejected} tolak
                </span>
              )}
            </p>
          )}
        </div>
      </div>

      <div className="filter-row" role="group" aria-label="Filter verifikasi">
        <span className="filter-label">Filter</span>
        {(
          [
            ["all", "Semua"],
            ["pending", "Menunggu"],
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
          onClick={() => void refreshSessionList()}
        >
          Muat ulang
        </button>
        {canReview && focusableItems.length > 0 && (
          <span className="keyboard-hint-inline" title="J/K pilih · C konfirmasi · R tolak">
            <kbd>J</kbd>
            <kbd>K</kbd>
            <kbd>C</kbd>
            <kbd>R</kbd>
          </span>
        )}
      </div>

      {!session ? (
        <div className="empty">Pilih sesi di atas untuk melihat temuan</div>
      ) : !findingsData && findingsLoading ? (
        <FindingsSkeleton />
      ) : !findingsData || findingsData.total === 0 ? (
        <div className="empty">
          {findingsLoading
            ? "Memuat temuan…"
            : reviewFilter === "pending"
              ? "Tidak ada temuan menunggu untuk sesi ini"
              : "Belum ada temuan pada sesi ini"}
        </div>
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
    </section>
  );
}
