import type { Metadata, Viewport } from "next";
import { PwaRegister } from "@/components/PwaRuntime";
import { UnifiedAuthGate } from "@/components/UnifiedAuthGate";
import { residentThemeBootstrap } from "@/lib/residentTheme";
import "./resident.css";

export const metadata: Metadata = {
  manifest: "/resident.webmanifest",
  applicationName: "FixIt Cư dân",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "FixIt Cư dân",
  },
  icons: {
    icon: "/icons/resident.svg",
    apple: "/icons/resident.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#2F6FED",
};

export default function ResidentLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <>
    {/* Runs before paint so a saved theme is already on <html> when the first
        pixels are drawn, instead of switching after hydration. */}
    <script dangerouslySetInnerHTML={{ __html: residentThemeBootstrap }} />
    <PwaRegister />
    <UnifiedAuthGate role="resident">{children}</UnifiedAuthGate>
  </>;
}
