import {
  reportExportFilename,
  type ReportExportIdentity,
} from "@/shared/lib/reportExportName";
import { authHeaders, BASE, getAuthToken, humanApiError, req } from "./http";
import type { AuthSession } from "./types/auth";
import type { Role, AcquisitionMode, AnalysisScope, DeviceType, Paginated, ReviewStatus, Scenario } from "./types/common";
import type { DashboardStats } from "./types/dashboard";
import type { DeviceInfo } from "./types/device";
import type { GalleryAlbum, GalleryItem } from "./types/gallery";
import type { HealthInfo } from "./types/health";
import type { SessionReport } from "./types/report";
import type { Finding, SessionSummary } from "./types/session";
import type { RiskTimeline } from "./types/dashboard";

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
    req<RiskTimeline>(`/sessions/${sessionId}/risk-timeline?years_back=${yearsBack}`),
  sessions: (page = 1, pageSize = 10) =>
    req<Paginated<SessionSummary>>(`/sessions?page=${page}&page_size=${pageSize}`),
  session: (id: string) => req<SessionSummary>(`/sessions/${id}`),
  sessionAudit: (id: string) =>
    req<
      {
        id: string;
        session_id: string | null;
        actor: string;
        action: string;
        detail: string | null;
        created_at: string;
      }[]
    >(`/sessions/${id}/audit`),
  startSession: (body: {
    device_id?: string;
    device_type: DeviceType;
    mode: AcquisitionMode;
    analysis_scope?: AnalysisScope;
    device_sources?: string[];
    social_targets?: string[];
    scenario: Scenario;
    file_count: number;
    label?: string;
    participant?: {
      full_name: string;
      registration_no: string;
      nik?: string | null;
      organization?: string | null;
    };
    force_simulated?: boolean;
  }) =>
    req<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelSession: (id: string) =>
    req<SessionSummary>(`/sessions/${id}/cancel`, { method: "POST" }),
  updateSessionParticipant: (
    id: string,
    participant: {
      full_name: string;
      registration_no: string;
      nik?: string | null;
      organization?: string | null;
    },
  ) =>
    req<SessionSummary>(`/sessions/${id}/participant`, {
      method: "PATCH",
      body: JSON.stringify({ participant }),
    }),
  startSessionFromZip: (
    file: File,
    opts?: {
      mode?: AcquisitionMode;
      analysis_scope?: AnalysisScope;
      device_sources?: string[];
      social_targets?: string[];
      label?: string;
      participant?: {
        full_name: string;
        registration_no: string;
        nik?: string | null;
        organization?: string | null;
      };
      onUploadProgress?: (pct: number) => void;
    },
  ) =>
    new Promise<SessionSummary>((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      form.append("mode", opts?.mode || "quick");
      form.append("analysis_scope", opts?.analysis_scope || "device");
      if (opts?.device_sources?.length) form.append("device_sources", opts.device_sources.join(","));
      if (opts?.social_targets?.length) form.append("social_targets", opts.social_targets.join(","));
      if (opts?.label) form.append("label", opts.label);
      if (opts?.participant) {
        form.append("participant_full_name", opts.participant.full_name);
        form.append("participant_registration_no", opts.participant.registration_no);
        if (opts.participant.nik) form.append("participant_nik", opts.participant.nik);
        if (opts.participant.organization) {
          form.append("participant_organization", opts.participant.organization);
        }
      }

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/sessions/from-zip`);
      const token = getAuthToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

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
          reject(new Error(humanApiError(xhr.status, detail ?? xhr.statusText)));
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
    opts?: { review_status?: ReviewStatus; module?: string },
  ) => {
    const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (opts?.review_status) q.set("review_status", opts.review_status);
    if (opts?.module) q.set("module", opts.module);
    if (sessionId) {
      return req<Paginated<Finding>>(`/sessions/${sessionId}/findings?${q}`);
    }
    return req<Paginated<Finding>>(`/findings?${q}`);
  },
  galleryAlbums: (sessionId: string) =>
    req<GalleryAlbum[]>(`/sessions/${sessionId}/gallery/albums`),
  gallery: (sessionId: string, album: string, page = 1, pageSize = 10) => {
    const q = new URLSearchParams({
      album,
      page: String(page),
      page_size: String(pageSize),
    });
    return req<Paginated<GalleryItem>>(`/sessions/${sessionId}/gallery?${q}`);
  },
  reviewFinding: (id: string, review_status: ReviewStatus) =>
    req<Finding>(`/findings/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ review_status }),
    }),
  bulkReviewFindings: (sessionId: string, review_status: ReviewStatus) =>
    req<{ session_id: string; review_status: ReviewStatus; updated: number; reviewed_by: string }>(
      `/sessions/${sessionId}/findings/bulk-review`,
      {
        method: "POST",
        body: JSON.stringify({ review_status }),
      },
    ),
  report: (sessionId: string) =>
    req<SessionReport>(`/sessions/${sessionId}/report?format=json`),
  openReport: async (
    sessionId: string,
    format: "json" | "html" | "print" = "html",
    identity?: ReportExportIdentity | null,
  ): Promise<string | void> => {
    const res = await fetch(`${BASE}/sessions/${sessionId}/report?format=${format}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(humanApiError(res.status, err.detail ?? res.statusText));
    }

    if (format === "print") {
      const html = await res.text();
      if (isTauriShell()) {
        return savePdfNative(html, reportExportFilename(sessionId, "pdf", identity));
      }
      const preview = window.open("about:blank", "_blank");
      if (preview) {
        preview.document.open();
        preview.document.write(html);
        preview.document.close();
        return;
      }
      printHtmlViaIframe(html);
      return;
    }

    if (format === "html") {
      const html = await res.text();
      const filename = reportExportFilename(sessionId, "html", identity);
      if (isTauriShell()) {
        await saveBytesNative(filename, new TextEncoder().encode(html), "html");
        return;
      }
      const preview = window.open("about:blank", "_blank");
      if (preview) {
        preview.document.open();
        preview.document.write(html);
        preview.document.close();
        return;
      }
      downloadBlob(filename, new Blob([html], { type: "text/html;charset=utf-8" }));
      return;
    }

    const blob = await res.blob();
    const filename = reportExportFilename(sessionId, "json", identity);
    if (isTauriShell()) {
      await saveBytesNative(filename, new Uint8Array(await blob.arrayBuffer()), "json");
      return;
    }
    downloadBlob(filename, blob);
  },
  openReportPdf: (
    sessionId: string,
    identity?: ReportExportIdentity | null,
  ): Promise<string | void> => api.openReport(sessionId, "print", identity),
};

function isTauriShell(): boolean {
  return typeof window !== "undefined" && ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
}

async function pickSavePath(
  defaultName: string,
  kind: "html" | "json" | "pdf",
): Promise<string | null> {
  const { save } = await import("@tauri-apps/plugin-dialog");
  const filters =
    kind === "pdf"
      ? [{ name: "PDF", extensions: ["pdf"] }]
      : kind === "html"
        ? [{ name: "HTML", extensions: ["html", "htm"] }]
        : [{ name: "JSON", extensions: ["json"] }];
  return save({
    defaultPath: defaultName,
    filters,
    title: "Simpan laporan SATRIA",
  });
}

async function savePdfNative(html: string, defaultName: string): Promise<string> {
  const { invoke } = await import("@tauri-apps/api/core");
  const path = await pickSavePath(defaultName, "pdf");
  if (!path) {
    throw new Error("Ekspor dibatalkan");
  }
  const outputPath = path.toLowerCase().endsWith(".pdf") ? path : `${path}.pdf`;
  await invoke("render_report_pdf", { html, outputPath });
  return outputPath;
}

async function saveBytesNative(
  defaultName: string,
  contents: Uint8Array,
  kind: "html" | "json",
): Promise<string> {
  const { invoke } = await import("@tauri-apps/api/core");
  const path = await pickSavePath(defaultName, kind);
  if (!path) {
    throw new Error("Ekspor dibatalkan");
  }
  await invoke("write_export_file", {
    path,
    contents: Array.from(contents),
  });
  return path;
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Cetak HTML tanpa membuka tab (fallback bila popup diblokir / WebView Tauri). */
function printHtmlViaIframe(html: string) {
  const iframe = document.createElement("iframe");
  iframe.setAttribute("aria-hidden", "true");
  iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.appendChild(iframe);
  const doc = iframe.contentDocument ?? iframe.contentWindow?.document;
  if (!doc) {
    iframe.remove();
    throw new Error("Tidak bisa membuka pratinjau cetak di jendela ini");
  }
  const safeHtml = html.replace(/window\.print\s*\(\s*\)/g, "/* print deferred */");
  doc.open();
  doc.write(safeHtml);
  doc.close();
  const run = () => {
    try {
      iframe.contentWindow?.focus();
      iframe.contentWindow?.print();
    } finally {
      window.setTimeout(() => iframe.remove(), 1500);
    }
  };
  window.setTimeout(run, 400);
}
