# Đặc tả và thiết kế

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Phạm vi:** Yêu cầu sản phẩm, hành vi nghiệp vụ và hợp đồng kỹ thuật

## 1. Bộ tài liệu

| Tài liệu | Nội dung quy định |
| --- | --- |
| [Tóm tắt sản phẩm](product-brief.md) | Vấn đề, người dùng, đề xuất giá trị, phạm vi và tín hiệu thành công. |
| [Yêu cầu sản phẩm](prd.md) | Yêu cầu chức năng và phi chức năng theo tác nhân và năng lực. |
| [Quy tắc và luồng nghiệp vụ](business-rules-and-flows.md) | Vòng đời ticket, xem xét, trùng lặp, gom nhóm và hành vi hiển thị. |
| [Tính điểm rủi ro](risk-scoring.md) | Thang đánh giá năm tiêu chí, blocker, dải ưu tiên và đồng hồ SLA. |
| [Thiết kế API](api-design.md) | Ranh giới HTTP, xác thực, quy ước phản hồi và các nhóm tài nguyên. |
| [Hợp đồng Agent–Backend](agent-backend-contract.md) | Đầu vào/đầu ra AI có cấu trúc và các bất biến của ranh giới tin cậy. |
| [Thiết kế phân công](assignment-design.md) | Phân công thủ công, trực quan và tự động cùng vòng đời kỹ thuật viên. |

## 2. Thứ bậc yêu cầu

Tóm tắt sản phẩm xác lập mục tiêu. PRD xác lập các yêu cầu người dùng có thể quan sát. Tài liệu nghiệp vụ và thiết kế cung cấp quy tắc chính xác cho từng phạm vi được đặt tên. OpenAPI được sinh và lược đồ xác thực khi chạy là dạng có thể thực thi của hợp đồng API.

Khi thay đổi hành vi:

1. Cập nhật yêu cầu hoặc quy tắc nghiệp vụ liên quan.
2. Cập nhật hợp đồng thiết kế tương ứng.
3. Triển khai thay đổi mã nguồn và bản di trú.
4. Thêm hoặc cập nhật kiểm thử chứng minh yêu cầu.
5. Cập nhật `Kiểm chứng lần cuối` sau khi tài liệu và hệ thống đang chạy thống nhất.

## 3. Thuật ngữ cốt lõi

| Thuật ngữ | Định nghĩa |
| --- | --- |
| Ticket | Báo cáo sự cố do cư dân gửi và bản ghi vòng đời của báo cáo đó. |
| Phân loại | Danh mục và bằng chứng có cấu trúc do AI tạo, được Backend xác thực. |
| Xem xét thủ công | Trạng thái phân loại cần điều phối viên quyết định; đây không phải là mức ưu tiên. |
| Mức ưu tiên | Dải rủi ro P1–P5, trong đó P5 là mức khẩn cấp. |
| Nhóm sự cố | Một nhóm được quản lý gồm các ticket riêng biệt mô tả cùng một sự cố chung. |
| Bản trùng được liên kết | Ticket mới được liên kết chắc chắn với một ticket gốc đang hoạt động và không có phân công riêng. |
| Phân công | Bản ghi công việc bền vững kết nối ticket đã phê duyệt với kỹ thuật viên. |
| Sự kiện điều phối | Bản ghi hàng đợi bền vững đại diện cho công việc phân công tự động. |
| Phân công trực quan | Thao tác trên bảng được điều phối viên xác nhận để xếp một hoặc nhiều ticket. |
| Phân công tự động | Quy trình phê duyệt và điều phối do Backend kiểm soát, dùng bộ lập lịch xác định và chỉ dùng Agent có giới hạn cho lựa chọn có rủi ro. |

## 4. Ranh giới sản phẩm

Hệ thống chịu trách nhiệm tiếp nhận sự cố, phân tích có hỗ trợ AI, xác định ưu tiên rủi ro, quản trị của điều phối viên, phân công kỹ thuật viên, bằng chứng hoàn thành, hiển thị tiến độ cho cư dân và kiểm toán. Ứng phó khẩn cấp bên ngoài, công việc sửa chữa vật lý và cấp danh tính nằm ngoài ranh giới ứng dụng.
