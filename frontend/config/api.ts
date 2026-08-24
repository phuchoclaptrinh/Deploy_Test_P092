export type BackendRole = "resident" | "manager" | "technician";
export const backendRoles: BackendRole[] = ["resident", "manager", "technician"];

const defaultApiUrl = process.env.NODE_ENV === "production"
  ? "https://deploytestp092-production.up.railway.app"
  : "http://127.0.0.1:8000";
const apiUrl = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || defaultApiUrl;
const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
const normalizeApiBaseUrl = (value: string) => value.endsWith("/api/v1") ? value : `${value}/api/v1`;
export const API_BASE_URL = normalizeApiBaseUrl(configuredApiBaseUrl || apiUrl);
export const API_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS || 6_000);
const tokenKey = (role: BackendRole) => `fixit_${role}_access_token`;

export function getAccessToken(role: BackendRole) {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(tokenKey(role));
}
export function saveAccessToken(role: BackendRole, token: string) { window.localStorage.setItem(tokenKey(role), token); }
export function clearAccessToken(role: BackendRole) { window.localStorage.removeItem(tokenKey(role)); }
export function clearAllAccessTokens() { backendRoles.forEach(clearAccessToken); }
export function hasBackendSession(role: BackendRole) { return Boolean(getAccessToken(role)); }
