export type DeviceType = "android" | "ios" | "simulated";
export type AcquisitionMode = "quick" | "full";
export type AnalysisScope = "device" | "social" | "combined";
export type Scenario = "lulus" | "tidak_lulus";
export type Role = "operator" | "analis" | "pimpinan" | "admin";
export type SessionStatus =
  | "pending"
  | "detecting"
  | "preparing_agent"
  | "awaiting_access"
  | "acquiring"
  | "selecting"
  | "awaiting_review"
  | "indexing"
  | "analyzing"
  | "completed"
  | "failed"
  | "cancelled";
export type ReviewStatus = "pending" | "confirmed" | "rejected";

export interface Paginated<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface NamedCount {
  name: string;
  count: number;
}
