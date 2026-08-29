# Apartment Issue Triage - Frontend Gate 1 v4

Frontend demo cho quy trình tiếp nhận và xử lý phản ánh tại chung cư, được triển khai theo `WireFrame và UI Flow_v4.html`, `Logic_xử_lý_chính_v3.md` và đặc tả nghiệp vụ v3.

Phiên bản v4 có ba vai trò:

- **Cư dân:** tạo phản ánh, theo dõi tiến độ, bổ sung thông tin và nhận thông báo.
- **Ban quản lý (BQL):** duyệt thủ công, sửa category/priority, gán KTV, quản lý nguồn lực, danh mục, cụm ticket, báo cáo và audit log.
- **Kỹ thuật viên:** xem hàng việc theo thứ tự, bắt đầu xử lý, hoàn thành với ghi chú + ảnh hoặc báo không xử lý được kèm lý do.

## Công nghệ

- Next.js 15, App Router
- React 19, TypeScript
- CSS global responsive
- `lucide-react`
- Mock store bằng `localStorage`
- PWA dành cho giao diện cư dân

## Chạy dự án

Nếu đã chạy `scripts/setup.ps1` (hoặc `setup.sh`) ở repo root thì `.env.local` và `node_modules` đã sẵn sàng — chỉ cần:

```powershell
cd frontend
npm.cmd run dev
```

Làm tay từ đầu:

```powershell
cd frontend
Copy-Item .env.example .env.local   # rồi điền NEXT_PUBLIC_SUPABASE_*
npm.cmd install
npm.cmd run dev
```

Mở `http://localhost:3000`. Backend cần chạy sẵn ở `http://127.0.0.1:8000` — xem §6 của README gốc.

Chạy bản production:

```powershell
npm.cmd run build
npm.cmd start
```

`npm start` chỉ hoạt động sau khi `npm run build` tạo thư mục `.next`.

## Kiểm tra mã nguồn

```powershell
npm.cmd run typecheck
npm.cmd run build
```

## Routes

### Cư dân

| Route | Chức năng |
| --- | --- |
| `/resident` | Đăng nhập giả lập, trang chủ và danh sách phản ánh |
| `/resident/new` | Tạo phản ánh mới và mô phỏng AI phân tích |
| `/resident/tickets/[id]` | Chi tiết, timeline, hủy hoặc bổ sung thông tin |
| `/resident/history` | Lịch sử theo thời gian và loại vấn đề |
| `/resident/notifications` | Hộp thư thông báo |
| `/resident/profile` | Thông tin căn hộ và reset dữ liệu demo |

### Ban quản lý

| Route | Chức năng |
| --- | --- |
| `/manager` | Dashboard bảng ticket, tìm kiếm và bộ lọc |
| `/manager/tickets/[id]` | Chi tiết, duyệt thủ công, override, duyệt và gán KTV |
| `/manager/clusters` | Theo dõi cụm rò nước/chập điện cùng khu vực |
| `/manager/technicians` | Quản lý kỹ thuật viên và trạng thái sẵn sàng |
| `/manager/categories` | Quản trị Category và Priority Ceiling |
| `/manager/reports` | KPI, biểu đồ, SLA và xuất CSV/PDF |
| `/manager/audit` | Nhật ký thay đổi nghiệp vụ |

### Kỹ thuật viên

| Route | Chức năng |
| --- | --- |
| `/technician` | Đăng nhập giả lập và danh sách việc được giao |
| `/technician/tickets/[id]` | Chi tiết việc, bắt đầu xử lý và xác nhận kết quả |

## Logic nghiệp vụ v3

### Priority

- `P3`: rất khẩn cấp, mục tiêu xử lý 5 phút.
- `P2`: khẩn cấp, mục tiêu xử lý 3 giờ.
- `P1`: thông thường, mục tiêu xử lý 72 giờ.
- `P0`: ảnh và mô tả chưa thống nhất, cần BQL duyệt thủ công; đây không phải mức nguy hiểm.

Cư dân chỉ thấy diễn giải thân thiện, không thấy mã priority, điểm hoặc từ `SLA`.

### Vòng đời ticket

```text
new -> approved -> assigned -> accepted -> in_progress -> completed
                                                   -> cannot_resolve
```

- BQL duyệt và gán việc; chỉ KTV được cập nhật trạng thái xử lý thực tế.
- Mỗi thay đổi tạo timeline, audit log và thông báo cho cư dân.
- Chỉ ticket `new` được cư dân hủy.
- Override category/priority bắt buộc nhập lý do.
- Ticket `P0` có thể chọn kết quả từ ảnh, từ text, category khác hoặc yêu cầu cư dân bổ sung.

### Tạo phản ánh

- Bắt buộc chọn tòa nhà, tầng và vị trí cụ thể.
- Mô tả bằng chữ là bắt buộc; ảnh là tùy chọn.
- Hỗ trợ tối đa 5 ảnh, mỗi ảnh 2 MB và tổng 5 MB, lưu dạng data URL.
- Mô tả ngắn mô phỏng màn AI hỏi bổ sung ngay trong luồng phân tích.
- Tên ảnh chứa `mờ` mô phỏng trường hợp ảnh không đọc được và không tạo ticket.

### Cụm ticket

Cụm được tạo khi có ít nhất hai ticket rò nước hoặc chập điện trong ba ngày, cùng tòa và cùng tầng hoặc tầng liền kề. Mỗi ticket trong cụm vẫn được cập nhật trạng thái độc lập.

## Dữ liệu demo

Dữ liệu được lưu trong `localStorage` để luồng xuyên vai trò hoạt động trong cùng trình duyệt. Dùng nút **Đặt lại dữ liệu demo** tại trang hồ sơ cư dân khi cần quay về seed ban đầu.

Store hiện cung cấp các thao tác chính:

```text
createTicket           reviewP0              rejectTicket
answerAiQuestion       overrideTicket        assignTicket
updateTechnicianTicket listTechnicians       listCategoryConfigs
cancelTicket           listClusters          resetDemoData
```

Khi backend có ticket/auth API, có thể thay phần triển khai trong `lib/mockService.ts` mà không cần đổi cấu trúc màn hình.

## PWA cư dân

- Manifest: `public/resident.webmanifest`
- Service worker: `public/sw.js`
- Offline fallback: `/offline`
- Runtime đăng ký PWA: `components/PwaRuntime.tsx`

PWA hỗ trợ cài giao diện cư dân và cache app shell. Dữ liệu nghiệp vụ vẫn là mock cục bộ, chưa đồng bộ máy chủ.
