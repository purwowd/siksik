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

export function occupyingSession(
  list: SessionSummary[],
  current: SessionSummary | null = null,
): SessionSummary | null {
  const fromList = list.find((item) => ACTIVE.has(item.status));
  if (fromList) return fromList;
  if (current && ACTIVE.has(current.status)) return current;
  return null;
}

export function occupyingLabel(session: SessionSummary): string {
  const name = session.participant?.full_name?.trim();
  const reg = session.participant?.registration_no?.trim();
  if (name && reg) return `${name} · ${reg}`;
  return name || session.label || "pemeriksaan aktif";
}

export function sessionIsAuthorized(session: SessionSummary | null | undefined): boolean {
  return !!session?.progress?.authorized_at;
}
