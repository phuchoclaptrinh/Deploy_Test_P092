import { API_BASE_URL, API_TIMEOUT_MS, getAccessToken, type BackendRole } from "@/config/api";

type ApiEnvelope<T> = { data: T; error: { code?: string; message?: string } | null };
export class ApiError extends Error {
  constructor(message: string, public readonly status = 0, public readonly code = "REQUEST_FAILED") { super(message); this.name = "ApiError"; }
}
type RequestOptions = RequestInit & { role?: BackendRole; token?: string | null; timeoutMs?: number; fresh?: boolean };
const GET_CACHE_TTL_MS = 5_000;
const cacheTtlFor = (path: string) => path === "/me" ? 60_000 : GET_CACHE_TTL_MS;
const responseCache = new Map<string, { expiresAt: number; data: unknown }>();
const inFlightRequests = new Map<string, Promise<unknown>>();
/** Bumped whenever a write invalidates the cache. A GET that was already in
 *  flight at that moment describes the world before the write, so its response
 *  still reaches the caller that asked for it but is never cached or handed to
 *  a later caller — otherwise it would land in the cache after the clear and
 *  outlive the change that invalidated it. */
let cacheGeneration = 0;

export async function apiRequest<T>(path: string, options: RequestOptions = {}) {
  const method = (options.method || "GET").toUpperCase();
  const role = options.role || "resident";
  const token = options.token === undefined ? getAccessToken(role) : options.token;
  const cacheKey = `${role}:${token || "anonymous"}:${path}`;
  const useGetCache = method === "GET" && !options.fresh;
  if (useGetCache) {
    const cached = responseCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.data as T;
    if (cached) responseCache.delete(cacheKey);
    const pending = inFlightRequests.get(cacheKey);
    if (pending) return pending as Promise<T>;
  }

  const generation = cacheGeneration;
  const request = executeRequest<T>(path, options, token).then((data) => {
    if (useGetCache) {
      if (generation === cacheGeneration) responseCache.set(cacheKey, { data, expiresAt: Date.now() + cacheTtlFor(path) });
    } else if (method !== "GET") {
      responseCache.clear();
      inFlightRequests.clear();
      cacheGeneration += 1;
    }
    return data;
  }).finally(() => {
    // Only if this request is still the registered one; a write may have
    // cleared the map and a newer request taken its place.
    if (useGetCache && inFlightRequests.get(cacheKey) === request) inFlightRequests.delete(cacheKey);
  });
  if (useGetCache) inFlightRequests.set(cacheKey, request);
  return request;
}

async function executeRequest<T>(path: string, options: RequestOptions, token: string | null) {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), options.timeoutMs ?? API_TIMEOUT_MS);
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  try {
    const { role: _role, token: _token, timeoutMs: _timeoutMs, fresh: _fresh, ...requestOptions } = options;
    const response = await fetch(`${API_BASE_URL}${path}`, { ...requestOptions, headers, signal: controller.signal });
    const payload = await response.json().catch(() => null) as ApiEnvelope<T> | null;
    if (!response.ok || payload?.error) throw new ApiError(payload?.error?.message || `Backend trả về HTTP ${response.status}.`, response.status, payload?.error?.code);
    if (!payload) throw new ApiError("Backend không trả về dữ liệu.", response.status, "EMPTY_RESPONSE");
    return payload.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw new ApiError("Backend phản hồi quá thời gian.", 408, "TIMEOUT");
    throw new ApiError("Không thể kết nối backend.", 0, "NETWORK_ERROR");
  } finally { globalThis.clearTimeout(timer); }
}
export async function uploadToSignedUrl(file: Blob, url: string, headers: Record<string, string>) {
  const response = await fetch(url, { method: "PUT", body: file, headers });
  if (!response.ok) throw new ApiError("Không thể tải ảnh lên storage.", response.status, "UPLOAD_FAILED");
}
