import { ACTIVE } from "@/shared/constants";
import type { SessionSummary } from "@/shared/api/client";

export type CaseCheck = {
  id: "identity" | "data" | "review" | "authorize";
  label: string;
  done: boolean;
};

export function caseChecklist(session: SessionSummary | null, pending = 0): CaseCheck[] {
  const named = !!(session?.participant?.full_name && session.participant.registration_no);
  const dataIn =
    !!session &&
    (session.status === "completed" ||
      (session.progress?.files_indexed ?? 0) > 0 ||
      (session.progress?.percent ?? 0) >= 100);
  const reviewed = !!session && session.status === "completed" && pending === 0;
  const authorized = !!session?.progress?.authorized_at;
  return [
    { id: "identity", label: "Identitas", done: named },
    { id: "data", label: "Data masuk", done: dataIn },
    { id: "review", label: "Tinjauan", done: reviewed },
    { id: "authorize", label: "Pengesahan", done: authorized },
  ];
}
