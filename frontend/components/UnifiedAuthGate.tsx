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

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      clearAllAccessTokens();
      const token = await signInWithPassword("resident", email, password);
      const user = await getCurrentUser("resident");
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
