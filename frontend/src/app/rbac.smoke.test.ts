import { describe, expect, it } from "vitest";
import { can, type AuthSession } from "@/shared/api/client";
import { TAB_DEFS, TAB_PERMS, preferredLandingTab } from "@/shared/constants";

function mockAuth(role: AuthSession["role"], permissions: string[]): AuthSession {
  return {
    token: "t",
    username: role,
    role,
    display_name: role,
    permissions,
  };
}

const ROLE_PERMS: Record<AuthSession["role"], string[]> = {
  operator: [
    "health",
    "devices",
    "sessions:start",
    "sessions:read",
    "sessions:cancel",
    "candidates:review",
  ],
  analis: [
    "health",
    "devices",
    "sessions:read",
    "findings:read",
    "findings:review",
    "dashboard",
    "report:read",
  ],
  pimpinan: ["health", "sessions:read", "findings:read", "dashboard", "report:read", "report:authorize"],
  admin: [
    "health",
    "devices",
    "sessions:start",
    "sessions:read",
    "sessions:cancel",
    "candidates:review",
    "findings:read",
    "findings:review",
    "dashboard",
    "report:read",
    "report:authorize",
    "users:manage",
  ],
};

function allowedTabs(auth: AuthSession) {
  return TAB_DEFS.filter((t) => can(auth, TAB_PERMS[t.id]));
}

describe("RBAC tab visibility", () => {
  it("operator only sees acquisition tab", () => {
    const auth = mockAuth("operator", ROLE_PERMS.operator);
    const tabs = allowedTabs(auth).map((t) => t.id);
    expect(tabs).toEqual(["operator"]);
    expect(preferredLandingTab(auth, allowedTabs(auth))).toBe("operator");
  });

  it("analis lands on findings", () => {
    const auth = mockAuth("analis", ROLE_PERMS.analis);
    const tabs = allowedTabs(auth).map((t) => t.id);
    expect(tabs).toContain("findings");
    expect(tabs).toContain("dashboard");
    expect(tabs).not.toContain("operator");
    expect(preferredLandingTab(auth, allowedTabs(auth))).toBe("findings");
  });

  it("pimpinan lands on report", () => {
    const auth = mockAuth("pimpinan", ROLE_PERMS.pimpinan);
    const tabs = allowedTabs(auth).map((t) => t.id);
    expect(tabs).toContain("report");
    expect(tabs).not.toContain("operator");
    expect(preferredLandingTab(auth, allowedTabs(auth))).toBe("report");
  });

  it("admin sees all tabs", () => {
    const auth = mockAuth("admin", ROLE_PERMS.admin);
    const tabs = allowedTabs(auth).map((t) => t.id);
    expect(tabs).toEqual(["operator", "findings", "gallery", "report", "dashboard"]);
  });
});
