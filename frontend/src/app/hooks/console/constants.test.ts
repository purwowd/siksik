import { describe, expect, it } from "vitest";
import type { SessionSummary } from "@/shared/api/client";
import { pickBootstrapSession } from "@/app/hooks/console/constants";

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
    recommendation: "LULUS",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    error: null,
    ...over,
  };
}

describe("pickBootstrapSession", () => {
  it("prefers a live session over a stored completed case", () => {
    const live = session({ id: "live", status: "acquiring", recommendation: null });
    const done = session({ id: "done", status: "completed" });
    expect(pickBootstrapSession([done, live], "done")?.id).toBe("live");
  });

  it("honors an explicit URL session even when another is live", () => {
    const live = session({ id: "live", status: "acquiring", recommendation: null });
    const done = session({ id: "done", status: "completed" });
    expect(pickBootstrapSession([done, live], "done", { fromUrl: true })?.id).toBe("done");
  });

  it("falls back to the stored id when nothing is live", () => {
    const older = session({ id: "older", progress: { ...session().progress, findings_count: 9 } });
    const stored = session({ id: "stored", progress: { ...session().progress, findings_count: 1 } });
    expect(pickBootstrapSession([older, stored], "stored")?.id).toBe("stored");
  });
});
