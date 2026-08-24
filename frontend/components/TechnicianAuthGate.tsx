"use client";

import { ArrowRight, Eye, EyeOff, Wrench } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { signInWithPassword } from "@/api/supabase-auth.api";
import { requireRoleAccess } from "@/api/auth.api";
import { getAccessToken } from "@/config/api";

const technicianProfileKey = "fixit-technician-profile";

function cacheTechnicianProfile(user: unknown) {
  try {
    window.sessionStorage.setItem(technicianProfileKey, JSON.stringify(user));
  } catch {
    // The authenticated session remains valid when browser storage is unavailable.
  }
}

export function TechnicianAuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!getAccessToken("technician")) { setReady(true); return; }
    let active = true;
    requireRoleAccess("technician")
      .then((user) => {
        if (!active) return;
        cacheTechnicianProfile(user);
        setAuthenticated(true);
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Không thể xác thực quyền truy cập."); })
      .finally(() => { if (active) setReady(true); });
    return () => { active = false; };
  }, []);
  if (!ready) return <div className="authChecking technicianAuthLoading" role="status"><span className="spinner" /><strong>Đang kết nối backend...</strong></div>;
  if (authenticated) return children;
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { await signInWithPassword("technician", email, password); const user = await requireRoleAccess("technician"); cacheTechnicianProfile(user); setAuthenticated(true); } catch (reason) { setError(reason instanceof Error ? reason.message : "Đăng nhập không thành công."); } finally { setBusy(false); } };
  return <main className="authShell technicianAuthShell">
    <section className="authVisual technicianAuthVisual">
      <div>
        <span className="technicianAuthLogo"><Wrench size={22} /> FixIt</span>
        <p>Dành cho kỹ thuật viên</p>
        <h1>Sẵn sàng nhận việc.</h1>
      </div>
    </section>
    <section className="authPanel technicianAuthPanel">
      <div className="technicianAuthTitle">
        <h2>Đăng nhập</h2>
        <p>Xem công việc được giao và cập nhật tiến độ tại hiện trường.</p>
      </div>
      <form className="formStack technicianAuthForm" onSubmit={submit}>
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="field">
          <label htmlFor="technician-email">Email</label>
          <input id="technician-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="technician-password">Mật khẩu</label>
          <div className="passwordField">
            <input id="technician-password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
            <button type="button" onClick={() => setShowPassword((value) => !value)} title={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button>
          </div>
        </div>
        <button className="button technicianPrimaryButton" type="submit" disabled={busy}>{busy ? "Đang xác thực..." : "Đăng nhập"}<ArrowRight size={17} /></button>
      </form>
      <p className="technicianAuthHelp">Cần hỗ trợ? <a href="tel:1900232389">Liên hệ Ban quản lý</a></p>
    </section>
  </main>;
}
