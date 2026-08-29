# Brief — Hệ thống phân loại & ưu tiên phản ánh chung cư bằng AI

## Problem (Vấn đề)

Ban quản lý (BQL) chung cư hiện xử lý phản ánh của cư dân (ồn ào, thang máy lỗi, rò nước, hỏng đèn, chập điện...) chủ yếu qua điện thoại/ghi chép thủ công. Cách làm này dẫn tới 2 rủi ro chính: **phân loại sai bộ phận xử lý** (gửi nhầm việc điện cho tổ nước) và **xếp sai mức độ ưu tiên** — sự cố thực sự nguy hiểm (thang máy kẹt người, dấu hiệu cháy) có thể bị xử lý chậm như 1 phản ánh thông thường, chỉ vì thiếu thông tin ngay từ lúc tiếp nhận.

## Solution (Giải pháp)

Một AI Agent nhận đồng thời **3 loại input**: ảnh chụp hiện trường(tùy chọn), mô tả bằng chữ, và vị trí sự cố. Agent tự động:

- Trích xuất **Category** (loại sự cố) để định tuyến đúng bộ phận kỹ thuật.
- Phát hiện **dấu hiệu nguy hiểm (red-flag)** trong ảnh/text (khói, lửa, mắc kẹt...) → ép ngay mức ưu tiên khẩn cấp, bỏ qua mọi bước tính toán khác.
- Với các trường hợp còn lại, tính điểm ưu tiên minh bạch theo công thức cộng điểm (loại sự cố + vị trí + mức độ lan rộng + mức nghiêm trọng), có giới hạn trần theo từng loại sự cố để tránh việc phóng đại mức nguy hiểm của các vấn đề không đe dọa tính mạng.
- Kết quả: mỗi ticket được gán đúng **Category** (để định tuyến) và **Priority P1/P2/P3** (để xếp hạng xử lý), kèm thời gian phản hồi bắt buộc tương ứng.

## Target Audience (Đối tượng)

- **Primary:** Cư dân chung cư — người gửi phản ánh qua ứng dụng.
- **Secondary:** Điều phối viên BQL (duyệt các ca không rõ ràng) và Kỹ thuật viên (nhận và xử lý ticket theo đúng chuyên môn, đúng thứ tự ưu tiên).

## Core Value (Giá trị cốt lõi)

Khác với các hệ thống chỉ dựa hoàn toàn vào 1 mô hình AI "hộp đen" để tự quyết định mức độ ưu tiên, hệ thống này kết hợp **khả năng đọc hiểu linh hoạt của AI** (nhận diện đa dạng cách diễn đạt của cư dân, đọc cả ảnh khi cần) với **1 lớp logic tường minh, có thể kiểm toán** bằng một công thức cộng điểm thành phần để có thể dễ dàng cải tiến và bảo trì. Nhờ vậy, sự cố đe dọa tính mạng luôn được đảm bảo xử lý tức thời, còn mọi quyết định ưu tiên khác đều giải thích được rõ ràng cho BQL và mentor .
