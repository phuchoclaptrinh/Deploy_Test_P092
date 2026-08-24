import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FixIt",
  description: "Hệ thống tiếp nhận và điều phối xử lý phản ánh tòa nhà",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}
