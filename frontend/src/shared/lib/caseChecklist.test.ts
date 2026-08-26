import { describe, expect, it } from "vitest";
import type { SessionSummary } from "@/shared/api/client";
import { caseChecklist, occupyingLabel, occupyingSession, sessionIsAuthorized } from "@/shared/lib/caseChecklist";

function session(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "s1",
    device_id: "d1",
    device_type: "android",
    label: "Kasus A",
    mode: "quick",
    scenario: "lulus",
    status: "completed",
    progress: {
      phase: "completed",
      percent: 100,
      message: "Selesai",
      files_listed: 10,
      files_pulled: 10,
      files_indexed: 10,
      files_analyzed: 10,
      findings_count: 0,
      throughput_files_per_sec: 1,
    },
    timing: {
      t_detect_ms: 0,
      t_acquire_ms: 0,
      t_index_ms: 0,
      t_analyze_ms: 0,
      t_total_ms: 0,
    },
    participant: { full_name: "Ahmad", registration_no: "REG-1" },
    recommendation: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    error: null,
    ...over,
  };
}

describe("caseChecklist", () => {
  it("marks identity, data, review, and authorize independently", () => {
    const idle = caseChecklist(null, 0);
    expect(idle.map((c) => [c.id, c.done])).toEqual([
      ["identity", false],
      ["data", false],
      ["review", false],
      ["authorize", false],
    ]);

    const namedLive = caseChecklist(
      session({
        status: "acquiring",
        progress: {
          ...session().progress,
          phase: "acquiring",
          percent: 40,
          files_indexed: 0,
        },
      }),
      3,
    );
    expect(namedLive.find((c) => c.id === "identity")?.done).toBe(true);
    expect(namedLive.find((c) => c.id === "data")?.done).toBe(false);
    expect(namedLive.find((c) => c.id === "review")?.done).toBe(false);

    const ready = caseChecklist(
      session({
        progress: { ...session().progress, authorized_at: "2026-01-02T00:00:00Z" },
      }),
      0,
    );
    expect(ready.every((c) => c.done)).toBe(true);
  });
});

describe("occupyingSession", () => {
  it("prefers a live row from the list, then the current session", () => {
    const live = session({ id: "live", status: "acquiring" });
    const done = session({ id: "done", status: "completed" });
    expect(occupyingSession([done, live])?.id).toBe("live");
    expect(occupyingSession([done], live)?.id).toBe("live");
    expect(occupyingSession([done], done)).toBeNull();
  });

  it("labels occupancy with name and registration", () => {
    expect(occupyingLabel(session())).toBe("Ahmad · REG-1");
    expect(occupyingLabel(session({ participant: null }))).toBe("Kasus A");
  });
});

describe("sessionIsAuthorized", () => {
  it("locks only after pengesahan", () => {
    expect(sessionIsAuthorized(session())).toBe(false);
    expect(
      sessionIsAuthorized(
        session({ progress: { ...session().progress, authorized_at: "2026-01-02T00:00:00Z" } }),
      ),
    ).toBe(true);
  });
});
