import { describe, expect, it } from "vitest";
import { buildTabUrl, pathFromTab, tabFromPath, urlSessionToFollow } from "@/app/routes";
import { FEATURE_PAGE_META } from "@/shared/lib/featurePages";

describe("console routes", () => {
  it("maps tab paths bidirectionally", () => {
    expect(tabFromPath("/temuan")).toBe("findings");
    expect(tabFromPath("/galeri")).toBe("gallery");
    expect(tabFromPath("/laporan")).toBe("report");
    expect(tabFromPath("/ikhtisar")).toBe("dashboard");
    expect(tabFromPath("/dasbor")).toBe("dashboard");
    expect(pathFromTab("dashboard")).toBe("/ikhtisar");
    expect(tabFromPath("/penerimaan")).toBe("operator");
    expect(tabFromPath("/operator")).toBe("operator");
    expect(pathFromTab("operator")).toBe("/penerimaan");
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

  it("does not bounce back to the URL session while a pick is in flight", () => {
    const sessions = [{ id: "aaa-111" }, { id: "bbb-222" }];
    expect(urlSessionToFollow("aaa-111", sessions, "bbb-222", true)).toBeNull();
    expect(urlSessionToFollow("aaa-111", sessions, "bbb-222", false)).toBe("aaa-111");
    expect(urlSessionToFollow("bbb-222", sessions, "bbb-222", false)).toBeNull();
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

  it("keeps Indonesian page titles without English chrome", () => {
    expect(FEATURE_PAGE_META.operator.title).toMatch(/Penerimaan/);
    expect(FEATURE_PAGE_META.findings.title).toMatch(/temuan/i);
    expect(FEATURE_PAGE_META.gallery.title).toMatch(/Galeri/);
    expect(FEATURE_PAGE_META.report.title).toMatch(/Laporan/);
    expect(FEATURE_PAGE_META.dashboard.title).toMatch(/Ikhtisar/);
    for (const tab of ["operator", "findings", "gallery", "report", "dashboard"] as const) {
      const blob = `${FEATURE_PAGE_META[tab].title} ${FEATURE_PAGE_META[tab].copy}`;
      expect(blob).not.toMatch(/Intake|Evidence|Decision|Command|Live ops|stasiun/i);
    }
  });
});
