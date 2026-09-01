import type { BackendRole } from "@/config/api";
import { API_TIMEOUT_MS, saveAccessToken } from "@/config/api";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

export async function signInWithPassword(role: BackendRole, email: string, password: string) {
  if (!supabaseUrl || !publishableKey) throw new Error("Frontend chưa cấu hình Supabase Auth.");
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const response = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { apikey: publishableKey, "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim(), password }),
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => null) as { access_token?: string; msg?: string; error_description?: string } | null;
    if (!response.ok || !payload?.access_token) {
      const message = payload?.error_description || payload?.msg;
      if (response.status === 400 && message?.toLowerCase().includes("invalid login credentials")) throw new Error("Sai email hoặc mật khẩu đăng nhập.");
      throw new Error(message || "Đăng nhập Supabase không thành công.");
    }
    saveAccessToken(role, payload.access_token);
    return payload.access_token;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw new Error("Dịch vụ đăng nhập phản hồi quá thời gian.");
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
  }
}
