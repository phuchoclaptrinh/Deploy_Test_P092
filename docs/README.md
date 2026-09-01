# FixIt Agent — Tài liệu dự án

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Phạm vi:** Hành vi sản phẩm và thiết kế của hệ thống đang vận hành

Thư mục này trình bày hệ thống qua hai nhóm tài liệu chính.

| Nhóm tài liệu | Mục đích | Điểm bắt đầu |
| --- | --- | --- |
| Kiến trúc | Mô tả hệ thống đang vận hành, các thành phần, ranh giới dữ liệu, luồng Agent và cấu trúc triển khai. | [Kiến trúc](architecture/README.md) |
| Đặc tả và thiết kế | Quy định sản phẩm, quy tắc nghiệp vụ, ranh giới API, hợp đồng AI, cách tính rủi ro và hành vi phân công. | [Đặc tả và thiết kế](specification-and-design/README.md) |

## Thứ tự đọc

1. Bắt đầu với [kiến trúc hệ thống](architecture/README.md).
2. Đọc [tóm tắt sản phẩm](specification-and-design/product-brief.md) và [PRD](specification-and-design/prd.md).
3. Dùng [quy tắc và luồng nghiệp vụ](specification-and-design/business-rules-and-flows.md) để hiểu hành vi đầu cuối.
4. Dùng các tài liệu thiết kế để xác định ranh giới triển khai:
   - [Thiết kế API](specification-and-design/api-design.md)
   - [Hợp đồng Agent–Backend](specification-and-design/agent-backend-contract.md)
   - [Tính điểm rủi ro](specification-and-design/risk-scoring.md)
   - [Thiết kế phân công](specification-and-design/assignment-design.md)

## Quy tắc tài liệu

- Tài liệu có trạng thái **Hiện hành** mô tả hành vi được kỳ vọng tồn tại trong ứng dụng đang chạy.
- Yêu cầu sản phẩm mô tả những gì người dùng và người vận hành có thể dựa vào; tài liệu thiết kế giải thích cách triển khai các yêu cầu đó.
- Mã nguồn khi chạy, bản di trú cơ sở dữ liệu và OpenAPI được sinh ra là các điểm kiểm chứng có thể thực thi.
- Tên file được giữ ổn định. Các lần sửa đổi được theo dõi bằng Git và trường `Kiểm chứng lần cuối`, không tạo thêm các phiên bản tên file song song.
- Các tham chiếu trong bộ tài liệu này dùng liên kết tương đối để có thể kiểm tra liên kết trên máy cục bộ và trong CI.

## Tóm tắt hệ thống

FixIt Agent tiếp nhận báo cáo sự cố căn hộ từ cư dân, dùng Agent đa phương thức có trạng thái để phân loại và đánh giá rủi ro, sau đó chuyển công việc có thể xử lý đến nhân sự quản lý tòa nhà và kỹ thuật viên. Backend là ranh giới tin cậy: Backend xác thực đầu ra của Agent, tính mức ưu tiên theo quy tắc xác định, quản lý chuyển đổi trạng thái và ghi lại mọi quyết định quan trọng để kiểm toán.
