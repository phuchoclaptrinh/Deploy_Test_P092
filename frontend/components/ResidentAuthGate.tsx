"use client";

import { ArrowLeft, ArrowRight, Wrench } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { requireRoleAccess } from "@/api/auth.api";
import { signInWithPassword } from "@/api/supabase-auth.api";
import { getAccessToken } from "@/config/api";
import { hasResidentMockSession, residentMockOtpEnabled, saveResidentMockSession } from "@/lib/residentMockSession";

export function ResidentAuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (residentMockOtpEnabled) {
      setAuthenticated(hasResidentMockSession());
      setReady(true);
      return;
    }
    if (!getAccessToken("resident")) { setReady(true); return; }
    let active = true;
    requireRoleAccess("resident")
      .then(() => { if (active) setAuthenticated(true); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Không thể xác thực quyền truy cập."); })
      .finally(() => { if (active) setReady(true); });
    return () => { active = false; };
  }, []);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signInWithPassword("resident", email, password);
      await requireRoleAccess("resident");
      setAuthenticated(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể xác thực.");
    } finally {
      setBusy(false);
    }
  };

  if (!ready) return <div className="authChecking residentAuthChecking" role="status"><span className="spinner" /><strong>Đang xác thực...</strong></div>;
  if (authenticated) return children;
  if (residentMockOtpEnabled) return <ResidentMockOtpLogin onDone={() => setAuthenticated(true)} />;

  return <main className="authShell residentAuthShell">
    <section className="authVisual residentAuthVisual"><div><span className="residentAuthLogo"><Wrench size={23} /> FixIt</span><p>Dành cho cư dân</p><h1>Hỗ trợ khi bạn cần.</h1></div></section>
    <section className="authPanel residentAuthPanel">
      <div className="residentAuthTitle"><h2>Đăng nhập</h2><p>Gửi phản ánh và theo dõi tiến độ xử lý tại căn hộ.</p></div>
      <form className="formStack" onSubmit={login}>
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="field"><label htmlFor="resident-email">Email</label><input id="resident-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></div>
        <div className="field"><label htmlFor="resident-password">Mật khẩu</label><input id="resident-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></div>
        <button className="button residentPrimaryButton" type="submit" disabled={busy}>{busy ? "Đang xác thực..." : "Đăng nhập"}<ArrowRight size={17} /></button>
      </form>
      <p className="residentAuthHelp">Cần hỗ trợ? <a href="tel:1900232389">Liên hệ Ban quản lý</a></p>
    </section>
  </main>;
}

function ResidentMockOtpLogin({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(1);
  const [phone, setPhone] = useState("0912345456");
  const [otp, setOtp] = useState("202608");
  const [unitCode, setUnitCode] = useState("A-1203");
  const [error, setError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (step === 1) {
      if (phone.replace(/\D/g, "").length < 9) { setError("Vui lòng nhập số điện thoại hợp lệ."); return; }
      setStep(2);
      return;
    }
    if (step === 2) {
      if (otp !== "202608") { setError("Mã OTP không đúng."); return; }
      setStep(3);
      return;
    }
    if (!unitCode.trim()) { setError("Vui lòng nhập mã căn hộ."); return; }
    const normalizedUnit = unitCode.trim().toUpperCase();
    saveResidentMockSession({
      full_name: "Cư dân",
      phone_e164: phone.trim(),
      unit_code: normalizedUnit,
      building_code: normalizedUnit.split("-")[0] || "A",
    });
    onDone();
  };

  return <main className="authShell residentAuthShell residentMockOtpShell">
    <section className="authVisual residentAuthVisual"><div><span className="residentAuthLogo"><Wrench size={23} /> FixIt</span><p>Dành cho cư dân</p><h1>Hỗ trợ khi bạn cần.</h1></div></section>
    <section className="authPanel residentAuthPanel">
      <div className="residentAuthTitle"><h2>{step === 1 ? "Đăng nhập" : step === 2 ? "Nhập mã OTP" : "Liên kết căn hộ"}</h2><p>{step === 1 ? "Nhập số điện thoại đã đăng ký với Ban quản lý." : step === 2 ? "Nhập mã xác thực gồm 6 chữ số." : "Xác nhận căn hộ để hoàn tất đăng nhập."}</p></div>
      <div className="residentMockProgress"><span>Bước {step}/3</span><div>{[1, 2, 3].map((value) => <i className={value <= step ? "active" : ""} key={value} />)}</div></div>
      <form className="formStack residentMockOtpForm" onSubmit={submit}>
        {error && <div className="alert error" role="alert">{error}</div>}
        {step === 1 && <div className="field"><label htmlFor="resident-mock-phone">Số điện thoại</label><input id="resident-mock-phone" inputMode="tel" autoComplete="tel" value={phone} onChange={(event) => setPhone(event.target.value)} required /></div>}
        {step === 2 && <div className="field"><label htmlFor="resident-mock-otp">Mã OTP 6 số</label><input id="resident-mock-otp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={otp} onChange={(event) => setOtp(event.target.value.replace(/\D/g, "").slice(0, 6))} required /></div>}
        {step === 3 && <div className="field"><label htmlFor="resident-mock-unit">Mã căn hộ</label><input id="resident-mock-unit" autoComplete="off" value={unitCode} onChange={(event) => setUnitCode(event.target.value)} required /></div>}
        <div className={`residentMockActions${step === 1 ? " single" : ""}`}>
          {step > 1 && <button className="residentMockBack" type="button" title="Quay lại bước trước" aria-label="Quay lại bước trước" onClick={() => { setError(""); setStep((value) => value - 1); }}><ArrowLeft size={18} /></button>}
          <button className="button residentPrimaryButton" type="submit">{step === 1 ? "Gửi mã OTP" : step === 2 ? "Xác thực" : "Vào ứng dụng"}<ArrowRight size={17} /></button>
        </div>
      </form>
      <p className="residentAuthHelp">Cần hỗ trợ? <a href="tel:1900232389">Liên hệ Ban quản lý</a></p>
    </section>
  </main>;
}
