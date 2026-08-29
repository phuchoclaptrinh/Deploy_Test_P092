"use client";

import { Eye, EyeOff, LockKeyhole, LogIn, Mail, Wrench } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { signInWithPassword } from "@/api/supabase-auth.api";
import { requireRoleAccess } from "@/api/auth.api";
import { getAccessToken } from "@/config/api";

export function ManagerAuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getAccessToken("manager")) { setReady(true); return; }
    let active = true;
    requireRoleAccess("manager")
      .then(() => { if (active) setAuthenticated(true); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Không thể xác thực quyền truy cập."); })
      .finally(() => { if (active) setReady(true); });
    return () => { active = false; };
  }, []);

  if (!ready) return <div className="authChecking managerAuthLoading" role="status"><span className="spinner" /><strong>Đang kết nối backend...</strong></div>;
  if (authenticated) return children;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await signInWithPassword("manager", email, password);
      await requireRoleAccess("manager");
      setAuthenticated(true);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Đăng nhập không thành công."); }
    finally { setBusy(false); }
  };

  return (
    <main className="managerLoginPage">
      <section className="managerLoginPanel">
        <aside className="managerLoginBrand">
          <header>
            <span className="managerLoginMark"><Wrench size={25} /></span>
            <div><strong>FixIt</strong><span>Operations</span></div>
          </header>
          <div className="managerLoginBrandTitle">
            <p>Ban quản lý tòa nhà</p>
            <h1>Trung tâm<br />điều phối</h1>
            <span>Không gian làm việc dành riêng cho điều phối viên.</span>
          </div>
          <footer><LockKeyhole size={14} />Truy cập nội bộ</footer>
        </aside>
        <section className="managerLoginAccess">
          <header><p>Ban quản lý</p><h2>Đăng nhập</h2><span>Nhập tài khoản điều phối viên của bạn.</span></header>
          <form className="managerLoginForm" onSubmit={submit}>
            {error && <div className="alert error" role="alert">{error}</div>}
            <div className="field managerLoginField">
              <label htmlFor="manager-email">Email</label>
              <div><Mail size={17} /><input id="manager-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@fixit.vn" required /></div>
            </div>
            <div className="field managerLoginField">
              <label htmlFor="manager-password">Mật khẩu</label>
              <div><LockKeyhole size={17} /><input id="manager-password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Nhập mật khẩu" required /><button type="button" aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} title={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>
            </div>
            <button className="button managerLoginSubmit" type="submit" disabled={busy}>{busy ? <><span className="spinner" />Đang xác thực...</> : <><LogIn size={18} />Đăng nhập</>}</button>
          </form>
          <small>Tài khoản được phân quyền bởi quản trị hệ thống.</small>
        </section>
      </section>
    </main>
  );
}
