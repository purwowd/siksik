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

export async function req<T>(path: string, init?: RequestInit): Promise<T> {
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

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  return headers;
}
