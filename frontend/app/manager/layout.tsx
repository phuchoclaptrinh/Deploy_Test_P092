import type { Metadata } from "next";
import { UnifiedAuthGate } from "@/components/UnifiedAuthGate";

export const metadata: Metadata = {
  title: "Điều phối BQL | FixIt",
};

export default function ManagerLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <UnifiedAuthGate role="manager">{children}</UnifiedAuthGate>;
}
