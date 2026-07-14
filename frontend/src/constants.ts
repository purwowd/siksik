import type { Tab } from "./types";

export const ACTIVE = new Set([
  "pending",
  "detecting",
  "acquiring",
  "indexing",
  "analyzing",
]);

export const PIPELINE = [
  { id: "detect", label: "Detect", match: ["pending", "detecting"] },
  { id: "acquire", label: "Acquire", match: ["acquiring"] },
  { id: "index", label: "Index", match: ["indexing"] },
  { id: "analyze", label: "Analyze", match: ["analyzing"] },
  { id: "report", label: "Findings", match: ["completed"] },
] as const;

export const TAB_PERMS: Record<Tab, string> = {
  operator: "sessions:start",
  dashboard: "dashboard",
  findings: "findings:read",
  report: "report:read",
};

/** Lab demo usernames only — passwords filled on click for PoC convenience. */
export const DEMO_ACCOUNTS = [
  { user: "operator", pass: "Ops@2026", role: "Operator" },
  { user: "analis", pass: "Analis@2026", role: "Analis" },
  { user: "pimpinan", pass: "Pimpinan@2026", role: "Pimpinan" },
  { user: "admin", pass: "Admin@2026", role: "Admin" },
];
