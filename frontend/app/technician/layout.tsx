import type { Metadata } from "next";
import { UnifiedAuthGate } from "@/components/UnifiedAuthGate";

export const metadata: Metadata = { title: "Kỹ thuật viên | FixIt" };

export default function TechnicianLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <UnifiedAuthGate role="technician">{children}</UnifiedAuthGate>;
}
