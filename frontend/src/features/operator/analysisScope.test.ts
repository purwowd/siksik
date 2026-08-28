import { describe, expect, it } from "vitest";
import {
  DEFAULT_ANALYSIS_SCOPE,
  analysisPlanReady,
  planForScope,
  toggleChecked,
} from "@/features/operator/analysisScope";

describe("operator analysis scope", () => {
  it("defaults to combined device + social for Satria legacy behavior", () => {
    expect(DEFAULT_ANALYSIS_SCOPE).toBe("combined");
    expect(planForScope(DEFAULT_ANALYSIS_SCOPE).socialTargets).toEqual([
      "instagram",
      "facebook",
      "x",
    ]);
    expect(planForScope(DEFAULT_ANALYSIS_SCOPE).deviceSources.length).toBeGreaterThan(0);
    expect(planForScope(DEFAULT_ANALYSIS_SCOPE).deviceSources).toContain("notes");
  });

  it("resets checklists when switching focus", () => {
    expect(planForScope("device").socialTargets).toEqual([]);
    expect(planForScope("device").deviceSources.length).toBeGreaterThan(0);
    expect(planForScope("social").deviceSources).toEqual([]);
    expect(planForScope("social").socialTargets).toEqual(["instagram", "facebook", "x"]);
    expect(planForScope("combined").socialTargets.length).toBe(3);
  });

  it("blocks start when the selected focus has nothing checked", () => {
    expect(analysisPlanReady("device", [], [])).toBe(false);
    expect(analysisPlanReady("device", ["gallery"], [])).toBe(true);
    expect(analysisPlanReady("social", ["gallery"], [])).toBe(false);
    expect(analysisPlanReady("social", [], ["instagram"])).toBe(true);
    expect(analysisPlanReady("combined", ["gallery"], [])).toBe(false);
    expect(analysisPlanReady("combined", ["gallery"], ["x"])).toBe(true);
  });

  it("toggles checklist items without mutating order of remaining ids", () => {
    expect(toggleChecked(["gallery", "sms"], "sms")).toEqual(["gallery"]);
    expect(toggleChecked(["gallery"], "sms")).toEqual(["gallery", "sms"]);
  });
});
