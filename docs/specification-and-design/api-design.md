# Thiết kế API

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Giao thức:** JSON qua HTTPS
- **Đường dẫn gốc:** `/api/v1`

## 1. Giao diện công khai

Ứng dụng FastAPI công bố:

- `/docs` — Swagger UI;
- `/redoc` — ReDoc;
- `/openapi.json` — hợp đồng có thể đọc bằng máy được sinh tự động;
- `/health` — kiểm tra tiến trình sống công khai;
- `/ready` — kiểm tra mức sẵn sàng của các thành phần phụ thuộc.

OpenAPI được sinh tự động là hợp đồng ở cấp điểm cuối. Tài liệu này quy định các quy ước xuyên suốt và ranh giới tài nguyên.

## 2. Xác thực và phân quyền

Yêu cầu được bảo vệ dùng:

```http
Authorization: Bearer <supabase-access-token>
```

Backend xác minh chữ ký token và các thuộc tính khai báo, sau đó tải hồ sơ người dùng đáng tin cậy do Backend quản lý. Quyền theo vai trò được thực thi bằng thành phần phụ thuộc của tuyến và kiểm tra ở cấp tài nguyên.

| Tác nhân | Xác thực | Cơ sở phân quyền |
| --- | --- | --- |
| Cư dân | OTP qua số điện thoại | Hồ sơ `RESIDENT` và căn hộ đã liên kết |
| Điều phối viên | Tài khoản Supabase | Vai trò `COORDINATOR` do Backend quản lý |
| Kỹ thuật viên | Tài khoản Supabase | Hồ sơ `TECHNICIAN` đang hoạt động |

Không ID vai trò hoặc căn hộ nào do máy khách cung cấp có thể cấp quyền truy cập.

## 3. Cấu trúc phản hồi

Phản hồi thành công:

```json
{
  "data": {},
  "meta": {},
  "error": null,
  "request_id": "uuid"
}
```

Phản hồi lỗi:

```json
{
  "data": null,
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Thông báo dễ hiểu cho người dùng"
  },
  "request_id": "uuid"
}
```

API chấp nhận UUID `x-request-id` tùy chọn. Giá trị không hợp lệ hoặc bị thiếu sẽ được thay thế. Mọi phản hồi trả ID thực tế trong cả nội dung và tiêu đề `x-request-id` khi mô hình phản hồi có trường này.

## 4. Các nhóm tài nguyên

### Danh tính

| Phương thức/đường dẫn | Mục đích |
| --- | --- |
| `POST /auth/otp/request` | Yêu cầu OTP cho cư dân. |
| `POST /auth/otp/verify` | Xác minh OTP và thiết lập quyền truy cập. |
| `GET /me` | Đọc hồ sơ tác nhân hiện tại. |
| `POST /me/bind-unit` | Liên kết tài khoản cư dân đủ điều kiện với một căn hộ. |

### Danh mục và lưu trữ

| Tiền tố | Mục đích |
| --- | --- |
| `/catalog` | Danh mục phân loại và vị trí hiển thị cho cư dân. |
| `/storage` | Đích tải lên có chữ ký cho hình ảnh ticket và bằng chứng hoàn thành. |

### Ticket của cư dân

| Phương thức/đường dẫn | Mục đích |
| --- | --- |
| `POST /tickets` | Tạo ticket. |
| `GET /tickets` | Liệt kê ticket trong căn hộ theo phạm vi riêng tư. |
| `GET /tickets/{ticket_id}` | Đọc ticket được cấp quyền. |
| `POST /tickets/{ticket_id}/cancel` | Hủy khi được phép. |
| `POST /tickets/{ticket_id}/supplements` | Bổ sung bằng chứng của cư dân. |
| `GET /tickets/{ticket_id}/agent-question` | Đọc câu hỏi đang hoạt động của người báo cáo. |
| `POST /tickets/{ticket_id}/agent-question/{question_id}/answer` | Trả lời và tiếp tục phân tích. |

### Thao tác của điều phối viên

Ranh giới `/coordinator` bao gồm:

- danh sách/chi tiết ticket, phê duyệt, thử lại phân tích và yêu cầu thông tin;
- quyết định về phân loại, trùng lặp và xem xét khẩn cấp;
- phân công thủ công và Phân công trực quan;
- danh sách, phê duyệt, phân công và xóa thành viên của nhóm sự cố;
- cấu hình Phân công tự động và kiểm tra điều phối;
- tài khoản cư dân/kỹ thuật viên, danh mục và danh bạ kỹ thuật viên;
- kiểm toán, báo cáo và mô phỏng năng lực.

### Thao tác của kỹ thuật viên

| Tiền tố | Mục đích |
| --- | --- |
| `/technician/availability` | Đọc hoặc thay đổi trạng thái sẵn sàng cá nhân. |
| `/technician/queue` | Đọc hàng đợi công việc cá nhân đã sắp thứ tự. |
| `/technician/assignments` | Liệt kê/xem chi tiết công việc được giao. |
| `/technician/assignments/{id}/start` | Bắt đầu công việc. |
| `/technician/assignments/{id}/reject` | Từ chối kèm lý do. |
| `/technician/assignments/{id}/unable-to-handle` | Kết thúc vì không thể xử lý, kèm lý do. |
| `/technician/assignments/{id}/complete` | Hoàn thành kèm ghi chú và bằng chứng. |

### Thông báo

- `GET /notifications` liệt kê thông báo của người dùng hiện tại.
- `POST /notifications/{notification_id}/read` đánh dấu một thông báo là đã đọc.

## 5. Giao thức tệp đính kèm

1. Máy khách yêu cầu đích tải lên có chữ ký kèm tên file, loại MIME và số byte.
2. Backend xác thực loại phương tiện được hỗ trợ và giới hạn kích thước đã cấu hình.
3. Máy khách tải trực tiếp lên kho lưu trữ đối tượng.
4. Máy khách gửi ID phiên tải lên nhận được trong yêu cầu nghiệp vụ.
5. Backend xác thực quyền sở hữu, thời hạn và siêu dữ liệu của đối tượng trước khi tạo tệp đính kèm.
6. Thao tác tải xuống dùng URL ký số có thời hạn ngắn và được phân quyền.

Bản ghi API bền vững không bao giờ làm lộ URL đối tượng công khai có thể tái sử dụng.

## 6. Ngữ nghĩa lỗi

| Trạng thái HTTP | Ý nghĩa |
| ---: | --- |
| `400` | Yêu cầu hợp lệ về cú pháp nhưng vi phạm quy tắc của yêu cầu. |
| `401` | Thiếu xác thực hoặc xác thực không hợp lệ. |
| `403` | Tác nhân đã xác thực nhưng thiếu quyền theo vai trò/tài nguyên. |
| `404` | Tài nguyên không tồn tại hoặc được chủ động ẩn khỏi tác nhân. |
| `409` | Xung đột chuyển đổi trạng thái, tính duy nhất hoặc đồng thời. |
| `422` | Xác thực lược đồ thất bại. |
| `500` | Lỗi máy chủ ngoài dự kiến kèm ID yêu cầu. |
| `503` | Thành phần phụ thuộc bắt buộc hoặc điều kiện sẵn sàng không khả dụng. |

Mã lỗi ổn định là bề mặt ra quyết định cho máy khách; thông báo đã bản địa hóa chỉ là văn bản hiển thị.

## 7. Quy tắc giao dịch và đồng thời

- Tuyến chuyển thao tác thay đổi trạng thái cho dịch vụ quản lý giao dịch cơ sở dữ liệu.
- Điều kiện phân công, rủi ro và vòng đời chạy trong cùng giao dịch với thao tác ghi.
- Các dòng có thể xảy ra tranh chấp được khóa trước khi xác thực.
- Ràng buộc duy nhất là lớp bảo vệ cuối cùng chống nhiều phân công đang hoạt động hoặc nhiều công việc điều phối đang mở bị trùng.
- Các hiệu ứng phụ được biểu diễn trong cơ sở dữ liệu — lịch sử, thông báo và kiểm toán — được xác nhận cùng hành động khởi tạo.

## 8. Vị trí triển khai

- Ứng dụng và OpenAPI: `src/main.py`
- Tổ hợp bộ định tuyến: `src/api/router.py`
- Module tuyến: `src/api/routes`
- Thành phần phụ thuộc phục vụ xác thực: `src/api/dependencies/auth.py`
- Mô hình phản hồi: `src/models/api/common.py`
- Danh mục lỗi: `src/models/api/errors.py`
