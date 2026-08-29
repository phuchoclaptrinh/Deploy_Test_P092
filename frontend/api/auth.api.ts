import { apiRequest } from "@/api/client";
import { clearAccessToken, saveAccessToken, type BackendRole } from "@/config/api";
import type { CurrentUser, OtpSession } from "@/types/api";
export const requestResidentOtp = (phone: string) => apiRequest<{ accepted: boolean }>("/auth/otp/request", { method: "POST", body: JSON.stringify({ phone }), token: null });
export const verifyResidentOtp = (phone: string, otp: string) => apiRequest<OtpSession>("/auth/otp/verify", { method: "POST", body: JSON.stringify({ phone, otp }), token: null });
export const getCurrentUser = (role: BackendRole = "resident") => apiRequest<CurrentUser>("/me", { role });
const expectedRole: Record<BackendRole, string> = {
  resident: "RESIDENT",
  manager: "COORDINATOR",
  technician: "TECHNICIAN",
};
const roleAccessMessage: Record<BackendRole, string> = {
  resident: "Tài khoản này không có quyền truy cập ứng dụng Cư dân.",
  manager: "Tài khoản này không có quyền truy cập trang Ban quản lý.",
  technician: "Tài khoản này không có quyền truy cập ứng dụng Kỹ thuật viên.",
};
export async function requireRoleAccess(role: BackendRole) {
  try {
    const user = await getCurrentUser(role);
    if (String(user.role).toUpperCase() !== expectedRole[role]) throw new Error(roleAccessMessage[role]);
    return user;
  } catch (error) {
    clearAccessToken(role);
    throw error;
  }
}
export const bindResidentUnit = (unitCode: string) => apiRequest<CurrentUser>("/me/bind-unit", { method: "POST", role: "resident", body: JSON.stringify({ unit_code: unitCode.trim() }) });
export const saveResidentSession = (session: OtpSession) => saveAccessToken("resident", session.access_token);
