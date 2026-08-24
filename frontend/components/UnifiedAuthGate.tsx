"use client";

import { ArrowRight, Eye, EyeOff, LockKeyhole, Wrench } from "lucide-react";
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

  return (
    <main className="managerLoginPage unifiedLoginPage">
      <section className="managerLoginPanel unifiedLoginPanel">
        <aside className="managerLoginBrand unifiedLoginBrand">
          <header>
            <span className="managerLoginMark"><Wrench size={25} /></span>
            <div><strong>FixIt</strong><span>One Access</span></div>
          </header>
          <div className="managerLoginBrandTitle">
            <h1>Phân loại và hỗ trợ xử lý phản ánh nhanh chóng</h1>
            <span>“Hệ thống hỗ trợ tiếp nhận, phân loại và chuyển phản ánh đến đúng bộ phận phụ trách.”</span>
          </div>
          <LoginWorkflowIllustration />
          <footer><LockKeyhole size={14} />Truy cập theo phân quyền</footer>
        </aside>
        <section className="managerLoginAccess unifiedLoginAccess">
          <header><p>FixIt</p><h2>Đăng nhập</h2><span>Nhập email và mật khẩu được Ban quản lý cấp.</span></header>
          <form className="managerLoginForm" onSubmit={submit}>
            {error && <div className="alert error" role="alert">{error}</div>}
            <div className="field managerLoginField">
              <label htmlFor="unified-email">Email</label>
              <div><input id="unified-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="ten@fixit.local" required /></div>
            </div>
            <div className="field managerLoginField">
              <label htmlFor="unified-password">Mật khẩu</label>
              <div><input id="unified-password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Nhập mật khẩu" required /><button type="button" aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} title={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={23} /> : <Eye size={23} />}</button></div>
            </div>
            <button className="button managerLoginSubmit" type="submit" disabled={busy}>{busy ? <><span className="spinner" />Đang xác thực...</> : <>Đăng nhập<ArrowRight size={27} /></>}</button>
          </form>
          <p className="unifiedLoginNote">Hệ thống sẽ tự động nhận diện vai trò của bạn.</p>
        </section>
      </section>
    </main>
  );
}

function LoginWorkflowIllustration() {
  return <svg className="unifiedLoginFlow" viewBox="0 0 570 294" fill="none" aria-hidden="true">
    <g stroke="currentColor" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="20" y="30" width="228" height="154" rx="20" />
      <path d="M58 78h124M58 110h86M58 142h52" />
      <path d="M248 107h86m-20-20 20 20-20 20" />
      <path d="M248 155v92h86m-20-20 20 20-20 20" />
      <rect x="360" y="14" width="202" height="80" rx="20" />
      <rect x="360" y="121" width="202" height="80" rx="20" />
      <rect x="360" y="228" width="202" height="80" rx="20" />
      <path d="M393 54h78M393 161h78M393 268h78" />
      <circle cx="520" cy="54" r="15" />
      <circle cx="520" cy="268" r="15" />
      <circle cx="520" cy="161" r="15" />
      <path d="m512 161 7 7 13-15" />
    </g>
  </svg>;
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
