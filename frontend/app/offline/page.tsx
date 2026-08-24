import Link from "next/link";
import { WifiOff } from "lucide-react";

export default function OfflinePage() {
  return (
    <main className="offlinePage">
      <WifiOff size={34} />
      <h1>Đang ngoại tuyến</h1>
      <p>Kiểm tra kết nối mạng rồi thử tải lại trang.</p>
      <div className="actionRow">
        <Link className="button" href="/resident">Về trang cư dân</Link>
      </div>
    </main>
  );
}
