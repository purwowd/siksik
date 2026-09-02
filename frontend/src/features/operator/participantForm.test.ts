import { describe, expect, it } from "vitest";
import {
  EMPTY_PARTICIPANT,
  syncParticipantForm,
} from "@/features/operator/participantForm";

const saved = {
  full_name: "TES-POC-ios-hp4",
  registration_no: "321737241222",
  nik: null,
  organization: "gabungan",
};

describe("syncParticipantForm", () => {
  it("hydrates identity while the session is still running", () => {
    const next = syncParticipantForm({
      sessionId: "sesi-1",
      status: "acquiring",
      saved,
      hydratedSessionId: null,
    });
    expect(next.form).toEqual({
      fullName: "TES-POC-ios-hp4",
      registrationNo: "321737241222",
      nik: "",
      organization: "gabungan",
    });
    expect(next.hydratedSessionId).toBe("sesi-1");
  });

  it("keeps the filled form during the same active session", () => {
    const next = syncParticipantForm({
      sessionId: "sesi-1",
      status: "analyzing",
      saved,
      hydratedSessionId: "sesi-1",
    });
    expect(next.form).toBeNull();
    expect(next.hydratedSessionId).toBe("sesi-1");
  });

  it("clears the form after the pipeline finishes so a new case can start", () => {
    const next = syncParticipantForm({
      sessionId: "sesi-1",
      status: "completed",
      saved,
      hydratedSessionId: "sesi-1",
    });
    expect(next.form).toEqual(EMPTY_PARTICIPANT);
    expect(next.hydratedSessionId).toBe("cleared:sesi-1");
  });

  it("does not refill a completed session after refresh", () => {
    const next = syncParticipantForm({
      sessionId: "sesi-1",
      status: "completed",
      saved,
      hydratedSessionId: null,
    });
    expect(next.form).toEqual(EMPTY_PARTICIPANT);
    expect(next.hydratedSessionId).toBe("cleared:sesi-1");
  });

  it("does not wipe identity the operator is typing on a finished session", () => {
    const next = syncParticipantForm({
      sessionId: "sesi-1",
      status: "completed",
      saved,
      hydratedSessionId: "cleared:sesi-1",
    });
    expect(next.form).toBeNull();
    expect(next.hydratedSessionId).toBe("cleared:sesi-1");
  });

  it("clears after failed or cancelled runs", () => {
    for (const status of ["failed", "cancelled"] as const) {
      const next = syncParticipantForm({
        sessionId: "sesi-2",
        status,
        saved,
        hydratedSessionId: "sesi-2",
      });
      expect(next.form).toEqual(EMPTY_PARTICIPANT);
    }
  });
});
