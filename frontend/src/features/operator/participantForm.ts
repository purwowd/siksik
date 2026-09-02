import { ACTIVE } from "@/shared/constants";
import type { ParticipantIdentity } from "@/shared/api/types/session";
import type { ParticipantForm } from "@/features/operator/OperatorPage";

export const EMPTY_PARTICIPANT: ParticipantForm = {
  fullName: "",
  registrationNo: "",
  nik: "",
  organization: "",
};

export function participantFormFromIdentity(
  saved: ParticipantIdentity | null | undefined,
): ParticipantForm {
  return {
    fullName: saved?.full_name ?? "",
    registrationNo: saved?.registration_no ?? "",
    nik: saved?.nik ?? "",
    organization: saved?.organization ?? "",
  };
}

export function syncParticipantForm(opts: {
  sessionId: string | null;
  status: string | null | undefined;
  saved: ParticipantIdentity | null | undefined;
  hydratedSessionId: string | null;
}): { form: ParticipantForm | null; hydratedSessionId: string | null } {
  const { sessionId, status, saved, hydratedSessionId } = opts;
  const isActive = !!status && ACTIVE.has(status);
  const isTerminal =
    status === "completed" || status === "failed" || status === "cancelled";
  const hasIdentity = !!(saved?.full_name?.trim() || saved?.registration_no?.trim());

  if (isActive && sessionId && hasIdentity && hydratedSessionId !== sessionId) {
    return {
      form: participantFormFromIdentity(saved),
      hydratedSessionId: sessionId,
    };
  }

  if (sessionId && isTerminal && hydratedSessionId !== `cleared:${sessionId}`) {
    return {
      form: EMPTY_PARTICIPANT,
      hydratedSessionId: `cleared:${sessionId}`,
    };
  }

  return { form: null, hydratedSessionId };
}
