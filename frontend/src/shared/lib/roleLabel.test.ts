import { describe, expect, it } from "vitest";
import { ROLE_LABEL, roleLabel } from "@/shared/lib/roleLabel";
import { TAB_DEFS } from "@/shared/constants";
import { FEATURE_PAGE_META } from "@/shared/lib/featurePages";

describe("role labels", () => {
  it("maps every console role to Indonesian duty names", () => {
    expect(roleLabel("operator")).toBe("Penerimaan");
    expect(roleLabel("analis")).toBe("Analis");
    expect(roleLabel("pimpinan")).toBe("Pimpinan");
    expect(roleLabel("admin")).toBe("Admin");
    expect(Object.keys(ROLE_LABEL)).toEqual(["operator", "analis", "pimpinan", "admin"]);
  });
});

describe("tab labels", () => {
  it("uses the same Indonesian names as page titles, without station codes", () => {
    const byId = Object.fromEntries(TAB_DEFS.map((t) => [t.id, t.label]));
    expect(byId.operator).toMatch(/Penerimaan/);
    expect(byId.findings).toMatch(/Temuan/);
    expect(byId.gallery).toBe("Galeri");
    expect(byId.report).toBe("Laporan");
    expect(byId.dashboard).toBe("Ikhtisar");
    expect(FEATURE_PAGE_META.operator.title).toMatch(/Penerimaan/);
    expect(FEATURE_PAGE_META.dashboard.title).toBe("Ikhtisar");
    for (const tab of TAB_DEFS) {
      expect(tab).not.toHaveProperty("code");
    }
  });
});
