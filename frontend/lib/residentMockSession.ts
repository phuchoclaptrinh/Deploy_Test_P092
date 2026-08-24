import type { CurrentUser } from "@/types/api";

export const residentMockOtpEnabled = process.env.NEXT_PUBLIC_RESIDENT_AUTH_MODE === "otp_mock";
const residentMockAuthKey = "fixit-resident-otp-mock-v2";
const residentMockProfileKey = "fixit-resident-otp-profile-v2";

type ResidentMockProfile = {
  full_name: string;
  phone_e164: string;
  unit_code: string;
  building_code: string;
};

export function hasResidentMockSession() {
  return typeof window !== "undefined" && window.sessionStorage.getItem(residentMockAuthKey) === "1";
}

export function saveResidentMockSession(profile: ResidentMockProfile) {
  window.sessionStorage.setItem(residentMockAuthKey, "1");
  window.sessionStorage.setItem(residentMockProfileKey, JSON.stringify(profile));
}

export function readResidentMockUser(): CurrentUser {
  let profile: ResidentMockProfile | null = null;
  try {
    const stored = window.sessionStorage.getItem(residentMockProfileKey);
    profile = stored ? JSON.parse(stored) as ResidentMockProfile : null;
  } catch {
    profile = null;
  }
  return {
    user_id: "resident-mock",
    role: "RESIDENT",
    full_name: profile?.full_name || "Cư dân",
    phone_e164: profile?.phone_e164 || null,
    unit: profile ? { id: "unit-mock", unit_code: profile.unit_code, building_code: profile.building_code, floor_code: "" } : null,
  };
}

export function clearResidentMockSession() {
  window.sessionStorage.removeItem(residentMockAuthKey);
  window.sessionStorage.removeItem(residentMockProfileKey);
  window.sessionStorage.removeItem("resident-demo-auth");
  window.sessionStorage.removeItem("resident-demo-profile");
}
