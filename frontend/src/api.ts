export type DeviceType = "android" | "ios" | "simulated";
export type AcquisitionMode = "quick" | "full";
export type Scenario = "lulus" | "tidak_lulus";
export type Role = "operator" | "analis" | "pimpinan" | "admin";
export type SessionStatus =
  | "pending"
  | "detecting"
  | "acquiring"
  | "indexing"
  | "analyzing"
  | "completed"
  | "failed"
  | "cancelled";
export type ReviewStatus = "pending" | "confirmed" | "rejected";

export interface AuthSession {
  token: string;
  username: string;
  role: Role;
  display_name: string;
  permissions: string[];
}

export interface DeviceInfo {
  device_id: string;
  device_type: DeviceType;
  label: string;
  os_version?: string | null;
  connected: boolean;
  simulated: boolean;
}

export interface SessionProgress {
  phase: SessionStatus;
  percent: number;
  message: string;
  files_listed: number;
  files_pulled: number;
  files_indexed: number;
  files_analyzed: number;
  findings_count: number;
  throughput_files_per_sec: number;
  acquisition_method?: string | null;
  hits_l1?: number;
  hits_l2?: number;
  hits_l3?: number;
  hits_l4?: number;
  hits_ocr?: number;
  hits_asr?: number;
  authorized_by?: string | null;
  authorized_at?: string | null;
  authorize_note?: string | null;
}

export interface TimingBreakdown {
  t_detect_ms: number;
  t_acquire_ms: number;
  t_index_ms: number;
  t_analyze_ms: number;
  t_total_ms: number;
}

export interface SessionSummary {
  id: string;
  device_id: string;
  device_type: DeviceType;
  label: string;
  mode: AcquisitionMode;
  scenario: Scenario;
  status: SessionStatus;
  progress: SessionProgress;
  timing: TimingBreakdown;
  recommendation: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
}

export interface Finding {
  id: string;
  session_id: string;
  file_id: string;
  source: string;
  path: string;
  category: string;
  label: string;
  confidence: number;
  layer_origin: string;
  evidence: string;
  review_status: ReviewStatus;
  created_at: string;
  media_year?: number | null;
  media_captured_at?: string | null;
}

export interface NamedCount {
  name: string;
  count: number;
}

export interface YearRiskBucket {
  year: number;
  total: number;
  by_category: NamedCount[];
}

export interface RiskTimeline {
  years_back: number;
  year_from: number;
  year_to: number;
  series: YearRiskBucket[];
  older_than_window: number;
  unknown_date: number;
  trend: string;
  insight: string;
  peak_year: number | null;
  peak_count: number;
  current_year_count: number;
  prior_avg: number;
}

export interface DashboardStats {
  total_sessions: number;
  completed_sessions: number;
  active_sessions: number;
  failed_sessions?: number;
  total_findings: number;
  pending_reviews: number;
  confirmed_findings?: number;
  rejected_findings?: number;
  lulus_count?: number;
  tidak_lulus_count?: number;
  menunggu_review_count?: number;
  avg_total_ms: number;
  avg_acquire_ms: number;
  avg_analyze_ms: number;
  avg_index_ms?: number;
  throughput_peak_fps?: number;
  findings_by_category?: NamedCount[];
  findings_by_layer?: NamedCount[];
  findings_by_source?: NamedCount[];
  acquisition_methods?: NamedCount[];
  toolchain?: Record<string, boolean>;
  gpu_available?: boolean;
  risk_timeline?: RiskTimeline | null;
  timeline_session_id?: string | null;
  timeline_session_label?: string | null;
}

export interface Paginated<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface VisionHealth {
  pillow?: boolean;
  ffmpeg?: boolean;
  image_cap_quick?: number;
  ocr?: { enabled?: boolean; available?: boolean; backend?: string };
  media_text?: {
    enabled?: boolean;
    video_overlay_keyframes?: number;
    whisper?: boolean;
  };
  gpu_stack?: {
    enabled?: boolean;
    device?: string | null;
    backends?: Record<string, { available?: boolean; configured?: boolean }>;
  };
}

export interface HealthInfo {
  status: string;
  gpu_available: boolean;
  app: string;
  extras?: {
    toolchain?: Record<string, boolean>;
    vision?: VisionHealth;
    rbac?: boolean;
    image_cap_quick?: number;
    image_cap_full?: number;
    zip_enabled?: boolean;
    zip_max_mb?: number;
    focus_scope?: string;
  };
}

const BASE = "/api/v1";
const AUTH_KEY = "sadt_auth";

let authToken: string | null = null;

export function loadAuth(): AuthSession | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthSession;
    authToken = parsed.token;
    return parsed;
  } catch {
    return null;
  }
}

export function saveAuth(session: AuthSession | null) {
  if (!session) {
    localStorage.removeItem(AUTH_KEY);
    authToken = null;
    return;
  }
  localStorage.setItem(AUTH_KEY, JSON.stringify(session));
  authToken = session.token;
}

export function can(session: AuthSession | null, permission: string): boolean {
  if (!session) return false;
  if (session.role === "admin") return true;
  return session.permissions.includes(permission);
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(
      typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail || res.statusText),
    );
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<HealthInfo>("/health"),
  login: (username: string, password: string) =>
    req<AuthSession>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => req<{ status: string }>("/auth/logout", { method: "POST" }),
  me: () =>
    req<{
      id: string;
      username: string;
      role: Role;
      display_name: string;
      permissions: string[];
    }>("/auth/me"),
  roles: () =>
    req<{ roles: { role: string; label: string; permissions: string[] }[] }>("/auth/roles"),
  devices: () => req<DeviceInfo[]>("/devices"),
  toolchain: () => req<{ toolchain: Record<string, boolean>; gpu_available: boolean }>("/toolchain"),
  dashboard: (sessionId?: string) => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return req<DashboardStats>(`/dashboard${q}`);
  },
  riskTimeline: (sessionId: string, yearsBack = 5) =>
    req<RiskTimeline>(
      `/sessions/${sessionId}/risk-timeline?years_back=${yearsBack}`,
    ),
  sessions: (page = 1, pageSize = 10) =>
    req<Paginated<SessionSummary>>(`/sessions?page=${page}&page_size=${pageSize}`),
  session: (id: string) => req<SessionSummary>(`/sessions/${id}`),
  startSession: (body: {
    device_id?: string;
    device_type: DeviceType;
    mode: AcquisitionMode;
    scenario: Scenario;
    file_count: number;
    label?: string;
    force_simulated?: boolean;
  }) =>
    req<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelSession: (id: string) =>
    req<SessionSummary>(`/sessions/${id}/cancel`, { method: "POST" }),
  startSessionFromZip: (
    file: File,
    opts?: {
      mode?: AcquisitionMode;
      label?: string;
      onUploadProgress?: (pct: number) => void;
    },
  ) =>
    new Promise<SessionSummary>((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      form.append("mode", opts?.mode || "quick");
      if (opts?.label) form.append("label", opts.label);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/sessions/from-zip`);
      if (authToken) xhr.setRequestHeader("Authorization", `Bearer ${authToken}`);

      xhr.upload.onprogress = (ev) => {
        if (!ev.lengthComputable || !opts?.onUploadProgress) return;
        opts.onUploadProgress(Math.round((ev.loaded / ev.total) * 100));
      };
      xhr.onload = () => {
        try {
          const body = JSON.parse(xhr.responseText || "{}");
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(body as SessionSummary);
            return;
          }
          const detail = body.detail;
          reject(
            new Error(
              typeof detail === "string" ? detail : JSON.stringify(detail || xhr.statusText),
            ),
          );
        } catch (e) {
          reject(e instanceof Error ? e : new Error(xhr.statusText));
        }
      };
      xhr.onerror = () => reject(new Error("Upload jaringan gagal"));
      xhr.send(form);
    }),
  authorizeSession: (id: string, note?: string) =>
    req<{ status: string; authorized_by: string }>(`/sessions/${id}/authorize`, {
      method: "POST",
      body: JSON.stringify({ note: note || null }),
    }),
  findings: (
    sessionId?: string,
    page = 1,
    pageSize = 10,
    opts?: { review_status?: ReviewStatus },
  ) => {
    const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (opts?.review_status) q.set("review_status", opts.review_status);
    if (sessionId) {
      return req<Paginated<Finding>>(`/sessions/${sessionId}/findings?${q}`);
    }
    return req<Paginated<Finding>>(`/findings?${q}`);
  },
  reviewFinding: (id: string, review_status: ReviewStatus) =>
    req<Finding>(`/findings/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ review_status }),
    }),
  /** Fetch report with Authorization header and open blob URL (no token in query). */
  openReport: async (sessionId: string, format: "json" | "html" = "html") => {
    const headers: Record<string, string> = {};
    if (authToken) headers.Authorization = `Bearer ${authToken}`;
    const res = await fetch(`${BASE}/sessions/${sessionId}/report?format=${format}`, {
      headers,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(
        typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail || res.statusText),
      );
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (!win) {
      // popup blocked — trigger download instead
      const a = document.createElement("a");
      a.href = url;
      a.download = `sadt-report-${sessionId.slice(0, 8)}.${format === "html" ? "html" : "json"}`;
      a.click();
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },
};

export function mediaUrl(sessionId: string, path: string) {
  const q = new URLSearchParams({ path });
  return `${BASE}/sessions/${sessionId}/media?${q}`;
}

/** Fetch staging media with Bearer token → blob object URL (revoke saat unmount). */
export async function fetchMediaBlobUrl(sessionId: string, path: string): Promise<string> {
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(mediaUrl(sessionId, path), { headers });
  if (!res.ok) {
    throw new Error(`Media ${res.status}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function ms(v: number) {
  if (!v) return "—";
  if (v < 1000) return `${v.toFixed(0)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}
