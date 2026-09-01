import { describe, expect, it } from "vitest";

import { buildAnalysisModules } from "@/features/dashboard/lib/satriaModules";
import type { DashboardStats, Finding, SessionSummary } from "@/shared/api/client";

function session(device_type: "android" | "ios"): SessionSummary {
  return {
    id: "session-test",
    device_id: "device-test",
    device_type,
    label: "Sesi test",
    mode: "quick",
    scenario: "lulus",
    status: "completed",
    progress: {
      phase: "completed",
      percent: 100,
      message: "Selesai",
      files_listed: 6,
      files_pulled: 6,
      files_indexed: 6,
      files_analyzed: 6,
      findings_count: 0,
      throughput_files_per_sec: 1,
      notes_state: "complete",
      notes_flow: "ui_walk",
      notes_captured: 1,
    },
    timing: {
      t_detect_ms: 0,
      t_acquire_ms: 0,
      t_index_ms: 0,
      t_analyze_ms: 0,
      t_total_ms: 0,
    },
    recommendation: "LULUS",
    created_at: "2026-08-28T00:00:00Z",
    updated_at: "2026-08-28T00:00:00Z",
    error: null,
  };
}

const dashboard = {
  total_sessions: 1,
  completed_sessions: 1,
  active_sessions: 0,
  total_findings: 0,
  pending_reviews: 0,
  avg_total_ms: 0,
  avg_acquire_ms: 0,
  avg_analyze_ms: 0,
  findings_by_source: [],
  files_by_source: [
    { name: "email", count: 2 },
    { name: "browser_history_full", count: 3 },
    { name: "notes", count: 1 },
  ],
  analyzed_files_by_source: [
    { name: "email", count: 2 },
    { name: "browser_history_full", count: 3 },
    { name: "notes", count: 1 },
  ],
} as DashboardStats;

describe("buildAnalysisModules", () => {
  it("shows clean email and browser data as analyzed instead of zero findings", () => {
    const modules = buildAnalysisModules({
      session: session("android"),
      dash: dashboard,
      findings: [],
    });
    const email = modules.find((item) => item.id === "email");
    const browser = modules.find((item) => item.id === "browser");
    expect(email?.availabilityLabel).toBe("Data email dianalisis");
    expect(email?.metrics.map((item) => item.value).slice(0, 3)).toEqual(["2", "2", "0"]);
    expect(email?.drillDown).toBe(false);
    expect(browser?.availabilityLabel).toBe("Data browser dianalisis");
    expect(browser?.metrics.map((item) => item.value).slice(0, 3)).toEqual(["3", "3", "0"]);
    expect(browser?.drillDown).toBe(false);
  });

  it("shows the notes module only for Android sessions", () => {
    const android = buildAnalysisModules({
      session: session("android"),
      dash: dashboard,
      findings: [],
    });
    const ios = buildAnalysisModules({
      session: session("ios"),
      dash: dashboard,
      findings: [],
    });
    const notes = android.find((item) => item.id === "notes");
    expect(notes?.availabilityLabel).toBe("Data catatan dianalisis");
    expect(notes?.metrics.map((item) => item.value)).toEqual(["1", "1", "1", "0"]);
    expect(ios.some((item) => item.id === "notes")).toBe(false);
  });

  it("splits gallery political memes from generic memes", () => {
    const finding = (
      id: string,
      category: string,
      label: string,
    ): Finding => ({
      id,
      session_id: "session-test",
      file_id: id,
      source: "gallery",
      path: `gallery/${id}.jpg`,
      category,
      label,
      confidence: 0.8,
      layer_origin: "L3",
      evidence: "Berkas: gallery/x.jpg",
      review_status: "pending",
      created_at: "2026-08-28T00:00:00Z",
    });
    const modules = buildAnalysisModules({
      session: session("android"),
      dash: dashboard,
      findings: [
        finding("pol", "political_meme", "Meme politik"),
        finding("fun", "meme", "Meme"),
      ],
    });
    const gallery = modules.find((item) => item.id === "gallery");
    expect(gallery?.notes).toEqual(["Konten Politik: 1", "Meme: 1"]);
  });
});
