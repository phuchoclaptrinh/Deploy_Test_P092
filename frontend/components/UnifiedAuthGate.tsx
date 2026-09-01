"use client";

import { CinematicLogin } from "@/components/CinematicLogin";
import { FormEvent, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { getCurrentUser, requireRoleAccess } from "@/api/auth.api";
import { signInWithPassword } from "@/api/supabase-auth.api";
import { backendRoles, clearAllAccessTokens, getAccessToken, saveAccessToken, type BackendRole } from "@/config/api";
import type { CurrentUser } from "@/types/api";

const roleHome: Record<BackendRole, string> = {
  resident: "/resident",
  manager: "/manager",
  technician: "/technician",
};

const backendRoleMap: Record<string, BackendRole> = {
  RESIDENT: "resident",
  COORDINATOR: "manager",
  TECHNICIAN: "technician",
};

const demoAccounts: Array<{ role: BackendRole; label: string; email: string; password: string }> = [
  {
    role: "resident",
    label: "Cư dân",
    email: process.env.NEXT_PUBLIC_DEMO_RESIDENT_EMAIL || "resident.test@homecare.local",
    password: process.env.NEXT_PUBLIC_DEMO_RESIDENT_PASSWORD || "HomeCareTest2026",
  },
  {
    role: "resident",
    label: "Minh Anh",
    email: process.env.NEXT_PUBLIC_DEMO_RESIDENT_2_EMAIL || "minhanh.cudan@homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_RESIDENT_2_PASSWORD || "homecare-demo",
  },
  {
    role: "resident",
    label: "Quốc Bảo",
    email: process.env.NEXT_PUBLIC_DEMO_RESIDENT_3_EMAIL || "quocbao.cudan@homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_RESIDENT_3_PASSWORD || "homecare-demo",
  },
  {
    role: "manager",
    label: "BQL",
    email: process.env.NEXT_PUBLIC_DEMO_MANAGER_EMAIL || "lan@bql.homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_MANAGER_PASSWORD || "homecare-demo",
  },
  {
    role: "technician",
    label: "KTV",
    email: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_EMAIL || "minhan@homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_PASSWORD || "homecare-demo",
  },
  {
    role: "technician",
    label: "Tuấn Điện",
    email: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_2_EMAIL || "tuandien@homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_2_PASSWORD || "homecare-demo",
  },
  {
    role: "technician",
    label: "Hoa Nước",
    email: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_3_EMAIL || "hoanuoc@homecare.vn",
    password: process.env.NEXT_PUBLIC_DEMO_TECHNICIAN_3_PASSWORD || "homecare-demo",
  },
];

export function UnifiedAuthGate({ role, children }: { role: BackendRole; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    let active = true;
    async function checkAccess() {
      if (getAccessToken(role)) {
        try {
          await requireRoleAccess(role);
          if (active) setAuthenticated(true);
          return;
        } catch {
          // Continue to look for another valid role session.
        }
      }
      const activeRole = await findExistingRole();
      if (!active) return;
      if (activeRole) router.replace(roleHome[activeRole]);
      else router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
    checkAccess().finally(() => { if (active) setReady(true); });
    return () => { active = false; };
  }, [pathname, role, router]);

  if (!ready) return <div className="authChecking managerAuthLoading" role="status"><span className="spinner" /><strong>Đang xác thực...</strong></div>;
  if (!authenticated) return <div className="authChecking managerAuthLoading" role="status"><span className="spinner" /><strong>Đang chuyển đến đăng nhập...</strong></div>;
  return children;
}

export function UnifiedLoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = sanitizeNextPath(searchParams.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    findExistingRole().then((role) => {
      if (active && role) router.replace(nextPath && nextPath.startsWith(roleHome[role]) ? nextPath : roleHome[role]);
    });
    return () => { active = false; };
  }, [nextPath, router]);

  const loginWithPassword = async (slotRole: BackendRole, loginEmail: string, loginPassword: string) => {
    setBusy(true);
    setError("");
    try {
      clearAllAccessTokens();
      const token = await signInWithPassword(slotRole, loginEmail, loginPassword);
      const user = await getCurrentUser(slotRole);
      const role = roleFromUser(user);
      if (!role) throw new Error("Tài khoản chưa được gán vai trò hợp lệ.");
      clearAllAccessTokens();
      saveAccessToken(role, token);
      router.replace(nextPath && nextPath.startsWith(roleHome[role]) ? nextPath : roleHome[role]);
    } catch (reason) {
      clearAllAccessTokens();
      setError(reason instanceof Error ? reason.message : "Đăng nhập không thành công.");
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void loginWithPassword("resident", email, password);
  };

  const loginDemoAccount = (account: (typeof demoAccounts)[number]) => {
    setEmail(account.email);
    setPassword(account.password);
    void loginWithPassword(account.role, account.email, account.password);
  };

  return <CinematicLogin
    email={email}
    password={password}
    error={error}
    busy={busy}
    showPassword={showPassword}
    onEmailChange={setEmail}
    onPasswordChange={setPassword}
    onPasswordVisibilityChange={() => setShowPassword((value) => !value)}
    onSubmit={submit}
    demoAccounts={demoAccounts}
    onDemoLogin={loginDemoAccount}
  />;
}

async function findExistingRole() {
  for (const role of backendRoles) {
    if (!getAccessToken(role)) continue;
    try {
      const user = await getCurrentUser(role);
      const actualRole = roleFromUser(user);
      if (actualRole === role) return role;
      if (actualRole) {
        const token = getAccessToken(role);
        clearAllAccessTokens();
        if (token) saveAccessToken(actualRole, token);
        return actualRole;
      }
    } catch {
      // Try the next token slot.
    }
  }
  clearAllAccessTokens();
  return null;
}

function roleFromUser(user: CurrentUser) {
  return backendRoleMap[String(user.role).toUpperCase()] || null;
}

function sanitizeNextPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "";
  if (value.startsWith("/login")) return "";
  return value;
}
