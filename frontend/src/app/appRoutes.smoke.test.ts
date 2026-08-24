import { describe, expect, it } from "vitest";
import { buildTabUrl, pathFromTab, tabFromPath } from "@/app/routes";
import { FEATURE_PAGE_META } from "@/shared/lib/featurePages";

describe("console routes", () => {
  it("maps tab paths bidirectionally", () => {
    expect(tabFromPath("/temuan")).toBe("findings");
    expect(tabFromPath("/galeri")).toBe("gallery");
    expect(tabFromPath("/laporan")).toBe("report");
    expect(tabFromPath("/dasbor")).toBe("dashboard");
    expect(pathFromTab("operator")).toBe("/operator");
  });

  it("builds session query params", () => {
    const url = buildTabUrl("findings", {
      sesi: "abc123",
      filter: "pending",
      modul: "gallery",
    });
    expect(url).toContain("/temuan");
    expect(url).toContain("sesi=abc123");
    expect(url).toContain("filter=pending");
    expect(url).toContain("modul=gallery");
  });
});

describe("feature page metadata", () => {
  it("defines copy for every tab", () => {
    for (const tab of ["operator", "findings", "gallery", "report", "dashboard"] as const) {
      const meta = FEATURE_PAGE_META[tab];
      expect(meta.title.length).toBeGreaterThan(2);
      expect(meta.copy.length).toBeGreaterThan(10);
    }
  });
});
