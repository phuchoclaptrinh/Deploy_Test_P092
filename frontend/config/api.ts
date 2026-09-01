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
  // Old behavior used localStorage, which is shared by every browser tab:
  // return window.localStorage.getItem(tokenKey(role));
  return window.sessionStorage.getItem(tokenKey(role));
}
export function saveAccessToken(role: BackendRole, token: string) {
  // Old behavior used localStorage, which made one role login overwrite other tabs:
  // window.localStorage.setItem(tokenKey(role), token);
  window.sessionStorage.setItem(tokenKey(role), token);
}
export function clearAccessToken(role: BackendRole) {
  // Keep clearing the old localStorage slot during migration so stale tokens do
  // not accidentally authenticate an old tab after this change.
  window.localStorage.removeItem(tokenKey(role));
  window.sessionStorage.removeItem(tokenKey(role));
}
export function clearAllAccessTokens() { backendRoles.forEach(clearAccessToken); }
export function hasBackendSession(role: BackendRole) { return Boolean(getAccessToken(role)); }
