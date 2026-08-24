/** SATRIA SPD workflow — five-stage mapping over existing session statuses. */

export const WORKFLOW_STEPS = [
  {
    id: "acquire",
    num: 1,
    label: "Pengambilan Data",
    match: [
      "pending",
      "detecting",
      "preparing_agent",
      "awaiting_access",
      "acquiring",
      "selecting",
      "awaiting_review",
    ],
  },
  {
    id: "content",
    num: 2,
    label: "Analisis Konten",
    match: ["indexing"],
  },
  {
    id: "cross",
    num: 3,
    label: "Analisis Lintas Sumber",
    match: ["analyzing"],
  },
  {
    id: "integrity",
    num: 4,
    label: "Penilaian Integritas",
    match: ["completed"],
  },
  {
    id: "final",
    num: 5,
    label: "Hasil Akhir",
    match: ["completed"],
  },
] as const;

export type WorkflowStepState = "idle" | "live" | "done" | "fail";

export type WorkflowSessionLike = {
  status?: string | null;
  recommendation?: string | null;
  progress?: {
    files_indexed?: number;
    files_analyzed?: number;
    authorized_at?: string | null;
    percent?: number;
  } | null;
};

/** Active step index 0–4, or -1 when unknown/failed. */
export function workflowActiveIndex(session: WorkflowSessionLike | null | undefined): number {
  const status = session?.status;
  if (!status) return -1;
  if (status === "failed" || status === "cancelled") return -1;
  if (
    (
      [
        "pending",
        "detecting",
        "preparing_agent",
        "awaiting_access",
        "acquiring",
        "selecting",
        "awaiting_review",
      ] as const
    ).includes(status as never)
  ) {
    return 0;
  }
  if (status === "indexing") return 1;
  if (status === "analyzing") {
    const indexed = session?.progress?.files_indexed ?? 0;
    const analyzed = session?.progress?.files_analyzed ?? 0;
    if (indexed > 0 && analyzed >= Math.floor(indexed * 0.85)) return 2;
    return 1;
  }
  if (status === "completed") {
    if (session?.progress?.authorized_at) return 4;
    if (session?.recommendation) return 4;
    return 3;
  }
  return -1;
}

export function workflowStepStates(
  session: WorkflowSessionLike | null | undefined,
): WorkflowStepState[] {
  const failed = session?.status === "failed" || session?.status === "cancelled";
  const active = workflowActiveIndex(session);
  const authorized = !!session?.progress?.authorized_at;
  return WORKFLOW_STEPS.map((_, i) => {
    if (failed) return i <= Math.max(active, 0) ? "fail" : "idle";
    if (active < 0) return "idle";
    if (authorized || (session?.status === "completed" && i < active)) return "done";
    if (i < active) return "done";
    if (i === active) {
      if (session?.status === "completed" && (authorized || i === 4)) return "done";
      return "live";
    }
    return "idle";
  });
}
