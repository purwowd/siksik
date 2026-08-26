import type { AuthSession } from "./types/auth";

export const BASE = "/api/v1";
const AUTH_KEY = "sadt_auth";

let authToken: string | null = null;

export function getAuthToken(): string | null {
  return authToken;
}

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

export function humanApiError(status: number, detail: unknown): string {
  if (typeof detail === "string") {
    const text = detail.trim();
    const looksTechnical =
      text.startsWith("{") ||
      text.startsWith("[") ||
      text.includes("Traceback") ||
      text.includes("  File ") ||
      text.length > 280;
    if (text && !looksTechnical) return text;
  }
  if (status === 401) return "Sesi berakhir. Masuk kembali.";
  if (status === 403) return "Aksi ini tidak diizinkan untuk peran Anda.";
  if (status === 404) return "Data tidak ditemukan.";
  if (status === 409) return "Tidak dapat dilanjutkan — ada sesi lain yang berjalan atau kondisi belum siap.";
  if (status === 413) return "Berkas terlalu besar untuk diunggah.";
  if (status === 422) return "Data tidak valid. Periksa isian lalu coba lagi.";
  if (status >= 500) return "Konsol sedang bermasalah. Coba beberapa saat lagi.";
  return "Permintaan gagal. Coba lagi atau hubungi admin instalasi.";
}

export async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const message = humanApiError(res.status, err.detail ?? res.statusText);
    if (import.meta.env.DEV) {
      console.warn("API error", res.status, path, err.detail);
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  return headers;
}
