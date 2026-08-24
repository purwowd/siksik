import { useCallback, useState } from "react";
import { DEFAULT_PAGE_SIZE } from "@/shared/ui/Pagination";
import {
  api,
  type Finding,
  type Paginated,
  type SessionSummary,
} from "@/shared/api/client";
import type { ReviewFilterParam } from "@/app/routes";
import type { Tab } from "@/shared/types";
import type { ReviewSummary } from "@/app/hooks/console/constants";
import type { ToastPush } from "@/app/hooks/console/useToastStack";

type Params = {
  tab: Tab | null;
  session: SessionSummary | null;
  setSession: React.Dispatch<React.SetStateAction<SessionSummary | null>>;
  reviewFilter: ReviewFilterParam;
  reviewSummary: ReviewSummary | null;
  setFindingsData: React.Dispatch<React.SetStateAction<Paginated<Finding> | null>>;
  setReportFindings: React.Dispatch<React.SetStateAction<Paginated<Finding> | null>>;
  setDashFindings: React.Dispatch<React.SetStateAction<Paginated<Finding> | null>>;
  setFindingsPage: React.Dispatch<React.SetStateAction<number>>;
  refreshReviewSummary: (sessionId: string) => Promise<void>;
  refreshSessionList: (opts?: { soft?: boolean }) => Promise<SessionSummary[] | undefined>;
  refreshGlobalPending: () => Promise<void>;
  pushToast: ToastPush;
  setError: (e: string | null) => void;
};

export function useReviewActions(p: Params) {
  const [reviewBusyId, setReviewBusyId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const review = useCallback(
    async (id: string, review_status: "confirmed" | "rejected") => {
      if (reviewBusyId || bulkBusy) return;
      setReviewBusyId(id);
      try {
        await api.reviewFinding(id, review_status);
        const patch = (prev: Paginated<Finding> | null) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((f) => (f.id === id ? { ...f, review_status } : f)),
              }
            : prev;
        if (p.reviewFilter === "pending") {
          p.setFindingsData((prev) =>
            prev
              ? {
                  ...prev,
                  items: prev.items.filter((f) => f.id !== id),
                  total: Math.max(0, prev.total - 1),
                }
              : prev,
          );
        } else {
          p.setFindingsData(patch);
        }
        p.setReportFindings(patch);
        p.setDashFindings(patch);
        if (p.session?.id) {
          const refreshed = await api.session(p.session.id);
          p.setSession(refreshed);
          void p.refreshReviewSummary(p.session.id);
          void p.refreshSessionList({ soft: true });
          void p.refreshGlobalPending();
          p.pushToast(
            review_status === "confirmed" ? "Temuan dikonfirmasi" : "Temuan ditolak",
            review_status === "confirmed" ? "warn" : "ok",
            { ttlMs: 2200, dedupe: true },
          );
        }
      } catch (e) {
        p.setError(e instanceof Error ? e.message : "Gagal menyimpan verifikasi");
      } finally {
        setReviewBusyId(null);
      }
    },
    [reviewBusyId, bulkBusy, p],
  );

  const bulkReview = useCallback(
    async (review_status: "confirmed" | "rejected") => {
      if (!p.session?.id || !p.reviewSummary?.pending) return;
      const total = p.reviewSummary.pending;
      const verb = review_status === "confirmed" ? "konfirmasi" : "tolak";
      const capNote =
        total > 500
          ? `\n\nCatatan: antrean menampilkan hingga 500 temuan per halaman; bulk akan memproses semua pending di sesi.`
          : "";
      if (!window.confirm(`Yakin ingin ${verb} semua ${total} temuan pending?${capNote}`)) {
        return;
      }
      setBulkBusy(true);
      try {
        const result = await api.bulkReviewFindings(p.session.id, review_status);
        const refreshed = await api.session(p.session.id);
        p.setSession(refreshed);
        p.setFindingsPage(1);
        void p.refreshReviewSummary(p.session.id);
        void p.refreshSessionList({ soft: true });
        void p.refreshGlobalPending();
        p.pushToast(
          `${result.updated} temuan di-${verb}`,
          review_status === "confirmed" ? "warn" : "ok",
          { ttlMs: 4000, dedupe: true },
        );
        if (p.tab === "findings") {
          const data = await api.findings(
            p.session.id,
            1,
            DEFAULT_PAGE_SIZE,
            p.reviewFilter === "all" ? undefined : { review_status: p.reviewFilter },
          );
          p.setFindingsData(data);
        }
      } catch (e) {
        p.setError(e instanceof Error ? e.message : "Gagal bulk review");
      } finally {
        setBulkBusy(false);
      }
    },
    [p],
  );

  return { reviewBusyId, bulkBusy, review, bulkReview };
}
