import { Suspense } from "react";
import { UnifiedLoginPage } from "@/components/UnifiedAuthGate";

export default function LoginPage() {
  return <Suspense fallback={<div className="authChecking managerAuthLoading" role="status"><span className="spinner" /><strong>Đang tải đăng nhập...</strong></div>}><UnifiedLoginPage /></Suspense>;
}
