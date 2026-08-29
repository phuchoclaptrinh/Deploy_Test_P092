# Đặc tả chi tiết Tính năng & Luồng nghiệp vụ

## Hệ thống phân loại & ưu tiên phản ánh chung cư bằng AI

> **Thay đổi so với v1:**
> - Bỏ hoàn toàn **NHÓM KỸ THUẬT VIÊN** (mục 3 cũ, gồm 3.1–3.6) — không còn vai trò này trong hệ thống.
> - Mục 2.5 đổi từ "Gán ticket cho Kỹ thuật viên cụ thể" thành **"Tự cập nhật trạng thái xử lý ticket"** — Điều phối viên tự chuyển trạng thái trực tiếp, không qua bước gán/nhận việc.
> - Bỏ mục "Ghi chú xử lý" và "Chụp ảnh xác nhận hoàn thành" — chỉ cần đổi trạng thái, không bắt buộc ghi chú/ảnh kèm theo.
> - Mục 2.9 cũ "Quản lý danh sách Kỹ thuật viên" bị bỏ.
> - Category không còn dùng để "định tuyến" — chỉ còn ý nghĩa tính điểm/thống kê (mục 3.1 mới, bước 7).
> - Ma trận phân quyền (0.3) và mọi nhắc tới "gán kỹ thuật viên" trong thông báo (1.5) được cập nhật theo.
> - Đánh số lại toàn bộ mục 2 và mục 3 (cũ là mục 4) cho liền mạch.

---

## 0. Bảng tham chiếu chung (dùng xuyên suốt tài liệu)

### 0.1. Định nghĩa Priority & SLA

| Priority | Ý nghĩa                                                              | Ví dụ                                                                   | Thời gian xử lý cam kết           |
| -------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------ |
| **P3**   | Cực kỳ nguy hiểm, ảnh hưởng trực tiếp tính mạng                      | Thang máy hỏng/kẹt, dấu hiệu cháy, gây rối trật tự công cộng, chập điện | 5 phút                            |
| **P2**   | Phiền toái nghiêm trọng, ảnh hưởng sinh hoạt                         | Điều hòa hỏng, mất điện, khóa cửa chính hỏng, mùi hôi thối              | 3 giờ                             |
| **P1**   | Vấn đề bình thường                                                   | Rò nước, hỏng đèn hành lang, phàn nàn hàng xóm thông thường             | 72 giờ                            |
| **P0**   | _(không phải mức độ nguy hiểm — là trạng thái "chưa xác định được")_ | Category từ ảnh và text không khớp nhau                                 | Chờ Điều phối viên duyệt thủ công |

### 0.2. Danh sách Category & Priority Ceiling

| Category                               | Priority Ceiling (mức trần khi tính điểm, không tính red-flag) |
| ----------------------------------------- | ------------------------------------------------------------------ |
| Rò nước                                | Không giới hạn                                                 |
| Chập điện                              | Không giới hạn                                                 |
| Thang máy                              | Không giới hạn                                                 |
| An ninh nghiêm trọng / Gây rối trật tự | Không giới hạn                                                 |
| Hỏng khóa / cửa                        | P2                                                             |
| Điều hòa / thông gió                   | P2                                                             |
| Mất điện (cục bộ)                      | P2                                                             |
| Kết cấu (nứt tường, thấm dột)          | P2                                                             |
| Hỏng đèn (khu vực chung)               | P2                                                             |
| Mùi hôi / vệ sinh                      | P1                                                             |
| Tiếng ồn / hàng xóm (thông thường)     | P1                                                             |

### 0.3. Ma trận phân quyền

| Hành động                  | Cư dân                   | Điều phối viên |
| ----------------------------- | ---------------------------- | ------------------ |
| Gửi ticket mới             | ✅                       | ❌             |
| Xem ticket của chính mình  | ✅                       | ✅ (tất cả)    |
| Duyệt ticket P0            | ❌                       | ✅             |
| Cập nhật trạng thái ticket | ❌                       | ✅             |
| Xem ảnh gốc                | ❌ (chỉ ticket của mình) | ✅             |

---

## 1. NHÓM CƯ DÂN

### 1.1. Đăng ký / Đăng nhập

**Luồng nghiệp vụ:**

1. Cư dân nhập số điện thoại và mã otp .
2. Nếu là lần đầu đăng nhập: hệ thống yêu cầu nhập **mã căn hộ (`unit_id`)** do BQL cấp sẵn từ trước (không tự chọn được).
3. Hệ thống kiểm tra: `unit_id` này **chưa từng gắn với tài khoản nào khác** → tạo tài khoản mới, liên kết SĐT ↔ `unit_id`.
4. Nếu `unit_id` đã có người đăng ký → báo lỗi, hướng dẫn liên hệ BQL nếu có tranh chấp (ví dụ đổi người thuê).

**Quy tắc nghiệp vụ quan trọng:** 1 `unit_id` chỉ gắn với đúng 1 tài khoản chính — đảm bảo nhiều người trong cùng 1 hộ gửi ticket vẫn được tính là **1 nguồn duy nhất** khi tính Density (tránh làm giả số lượng báo cáo để đẩy priority).

### 1.2. Gửi ticket mới

**Luồng nghiệp vụ:**

1. Cư dân chọn **Tầng** → chọn **Loại vị trí cụ thể** (từ danh sách dropdown cố định: hành lang, cầu thang thoát hiểm, bãi xe hầm, trong căn hộ...).
2. Nhập mô tả bằng chữ (text) — **và/hoặc** chụp/tải lên ảnh hiện trường.
3. Bắt buộc phải có **ít nhất 1 trong 2**: text hoặc ảnh. Vị trí luôn bắt buộc.
4. Nhấn gửi → ticket được tạo với trạng thái `Mới` → chuyển ngay sang xử lý AI (xem mục 3.1).
5. Trong lúc AI xử lý (vài giây), hiển thị trạng thái "Đang phân tích..." cho cư dân.

**Trường hợp đặc biệt:** nếu ảnh gửi lên quá mờ/model không đọc được → hệ thống phản hồi ngay: _"Hệ thống không thể xác định vấn đề của bạn, để tiện cho quá trình xử lý vui lòng mô tả vấn đề hoặc chụp ảnh rõ hơn"_, chưa tạo ticket chính thức.

### 1.3. Xem danh sách ticket của mình + trạng thái

**Luồng nghiệp vụ:**

- Hiển thị danh sách ticket đã gửi (chỉ của `unit_id` mình), sắp xếp theo thời gian gửi gần nhất.
- Mỗi dòng hiển thị: mô tả ngắn, Category, trạng thái hiện tại (`Mới` / `Đã duyệt` / `Đang xử lý` / `Hoàn thành` / `Không xử lý được`).

### 1.4. Xem chi tiết 1 ticket

**Luồng nghiệp vụ:**

- Hiển thị: Category (tên dễ hiểu, ví dụ "Điện" thay vì mã `dien`), mức độ ưu tiên diễn giải theo ngôn ngữ thân thiện, và **thời gian dự kiến xử lý** (tra theo bảng 0.1 — ví dụ "dự kiến xử lý trong vòng 3 giờ").
- **Không hiển thị** thuật ngữ kỹ thuật "SLA" hay mã "P1/P2/P3" trực tiếp cho cư dân.

### 1.5. Nhận thông báo khi ticket có cập nhật

**Luồng nghiệp vụ:**

- Trigger gửi thông báo (push notification/SMS) mỗi khi trạng thái ticket thay đổi: được duyệt → đang xử lý → hoàn thành.
- Đây là hành động của **cư dân nhận** thông báo — phần **hệ thống tạo ra** thông báo đó thuộc mục 3.5.

### 1.6. Hủy ticket

**Luồng nghiệp vụ:**

- Chỉ cho phép hủy khi ticket còn ở trạng thái `Mới` (chưa được Điều phối viên duyệt).
- Sau khi đã `Đã duyệt` trở đi → không cho hủy trực tiếp, cư dân cần liên hệ Điều phối viên.

### 1.7. Xem lịch sử ticket cũ

**Luồng nghiệp vụ:**

- Danh sách đầy đủ toàn bộ ticket đã gửi từ trước tới nay (không giới hạn theo trạng thái), có thể lọc theo khoảng thời gian hoặc Category.

---

## 2. NHÓM ĐIỀU PHỐI VIÊN BQL

### 2.1. Dashboard tổng quan ticket

**Luồng nghiệp vụ:**

- Hiển thị toàn bộ ticket trong hệ thống (không giới hạn theo `unit_id` như cư dân).
- Tìm kiếm lọc theo: Category, Priority, trạng thái, khoảng thời gian.
- Sắp xếp mặc định theo **Priority giảm dần** (P3 trước, P1 sau) để Điều phối viên xử lý đúng thứ tự khẩn cấp.

### 2.2. Xem chi tiết ticket

**Luồng nghiệp vụ:**

- Hiển thị đầy đủ: ảnh gốc, text gốc, vị trí, **và điểm số tổng** (con số cuối cùng sau khi tính công thức ở mục 3.1) — **không hiển thị breakdown** từng thành phần (Category base / Vị trí×Category / Density / Mức nghiêm trọng riêng lẻ), giữ giao diện đơn giản cho Điều phối viên.

### 2.3. Duyệt ticket P0

**Luồng nghiệp vụ:**

1. Ticket rơi vào trạng thái `P0` khi Category rút ra từ Ảnh và từ Text **không khớp nhau** (xem mục 3.4).
2. Điều phối viên mở ticket P0, tự đọc lại text + xem ảnh gốc.
3. Chọn 1 trong 2 hướng xử lý:
   - **Xác nhận Category đúng** (chọn Category từ Ảnh, từ Text, hoặc nhập Category khác) → hệ thống tính lại điểm số theo công thức bình thường (mục 3.1), ticket thoát trạng thái P0.
   - **Yêu cầu cư dân bổ sung thông tin** (mô tả rõ hơn / chụp ảnh khác) → ticket quay lại trạng thái chờ, gửi thông báo yêu cầu bổ sung tới cư dân.

### 2.4. Ghi đè (override) Priority/Category thủ công

**Luồng nghiệp vụ:**

- Điều phối viên có thể sửa trực tiếp Category hoặc Priority của bất kỳ ticket nào nếu thấy AI phân loại/tính điểm không hợp lý.
- Mỗi lần override đều ghi lại: ai override, thời gian, giá trị cũ → giá trị mới (phục vụ audit log ở mục 2.7).

### 2.5. Tự cập nhật trạng thái xử lý ticket

**Luồng nghiệp vụ:**

1. Điều phối viên chọn ticket đã duyệt (thường theo thứ tự Priority).
2. Chuyển trạng thái trực tiếp: `Đã duyệt` → `Đang xử lý` → `Hoàn thành`, hoặc `Không xử lý được`.
3. Mỗi lần đổi trạng thái đều kích hoạt thông báo tới cư dân (mục 1.5) — không qua bước gán hay chờ ai khác xác nhận.

*(Không cần nhập ghi chú hay đính kèm ảnh xác nhận — chỉ cần đổi trạng thái là đủ để cư dân theo dõi tiến độ.)*

### 2.6. Xuất báo cáo/thống kê định kỳ

**Luồng nghiệp vụ:**

- Xuất báo cáo theo tuần/tháng: tổng số ticket theo Category, theo Priority, thời gian xử lý thực tế so với SLA cam kết (mục 0.1).

### 2.7. Xem audit log

**Luồng nghiệp vụ:**

- Danh sách lịch sử các hành động quan trọng: ai duyệt ticket P0 nào, lúc nào; ai override Priority/Category nào, giá trị cũ/mới là gì.

---

## 3. NHÓM HỆ THỐNG / AI (chạy ngầm, không thuộc vai trò cụ thể)

### 3.1. AI Agent tự động phân loại Category + Priority

**Luồng nghiệp vụ (đây là toàn bộ "bộ não" của hệ thống):**

1. **Trích xuất dữ liệu có cấu trúc** từ input thô (text/ảnh):
   - Từ Text: `Category` (có thể nhiều nhãn), `RedFlagText` (có/không chứa từ khóa nguy hiểm).
   - Từ Ảnh (nếu có): `Category`, `RedFlagSignal`, `Severity` (Thấp/Vừa/Cao).
   - **Nếu không có ảnh:** model NLP tự suy luận `Severity` trực tiếp từ nội dung text, dùng cùng thang 3 mức.
2. **Kiểm tra Red-flag** (mục 3.2) — nếu có → dừng lại, gán ngay P3.
3. **Đối chiếu Category Ảnh vs Text** (mục 3.4) — nếu không khớp → dừng lại, gán P0.
4. **Nếu khớp và không có red-flag** — chạy công thức tính điểm:
   ```
   Điểm thô = Category base + (Vị trí × Category) + Density + Mức nghiêm trọng
   ```
5. Quy đổi điểm thô sang Priority theo ngưỡng (< 30 → P1, 30–59 → P2, ≥ 60 → P3).
6. **Áp Priority Ceiling** theo Category (mục 0.2): `Priority cuối = MIN(Priority từ bước 5, Ceiling của Category)`.
7. Trả về: `Category` (giờ chỉ dùng để thống kê/báo cáo — mục 2.6, không còn dùng để định tuyến) và `Priority` cuối cùng (dùng để xếp hạng trong dashboard — mục 2.1).

### 3.2. Red-flag override

**Luồng nghiệp vụ:**

- Nếu `RedFlagText = Có` **hoặc** `RedFlagSignal = Có` (khói, lửa, dây điện hở, nước tràn, ngất xỉu, gây rối, mắc kẹt...) → **ép ngay Priority = P3**, bỏ qua toàn bộ bước tính điểm (3.1 bước 4–6).

### 3.3. Gộp ticket lan rộng theo vị trí liền kề

**Luồng nghiệp vụ:**

1. Chỉ chạy cho ticket thuộc Category **rò nước** hoặc **chập điện**, và đã qua được bước đối chiếu Category (3.4) mà không bị P0.
2. Truy vấn database: tìm ticket khác cùng Category, tạo trong 3 ngày gần đây, **vị trí liền kề** (cùng tầng, hoặc tầng ngay trên/dưới).
3. Nếu tìm thấy → gộp thành 1 "case", `Density` = tổng số ticket trong case (bao gồm ticket hiện tại).
4. `Density` được cộng vào công thức tính điểm ở bước 3.1.

### 3.4. Đối chiếu Category ảnh vs text

**Luồng nghiệp vụ:**

- So sánh `Category` rút ra từ Ảnh và từ Text.
- Nếu **không khớp** → gán ticket vào trạng thái `P0`, chuyển cho Điều phối viên duyệt thủ công (mục 2.3), không tính điểm tiếp.
- Nếu **khớp** → tiếp tục bước tính điểm (3.1).

### 3.5. Tự động gửi thông báo cập nhật tiến độ tới cư dân

**Luồng nghiệp vụ:**

- Hệ thống tự động kích hoạt gửi thông báo (không cần Điều phối viên thao tác thủ công riêng biệt ngoài chính hành động đổi trạng thái ở mục 2.5) mỗi khi trạng thái ticket thay đổi — đây là phần **hệ thống tạo và gửi**, tương ứng với phần **cư dân nhận** ở mục 1.5.

---

## Ghi chú áp dụng

Các con số cụ thể trong công thức tính điểm (Category base, bảng Vị trí × Category, Density, ngưỡng quy đổi) tham khảo chi tiết tại tài liệu kỹ thuật pipeline (mục H) — không lặp lại toàn bộ ở đây để tránh 2 tài liệu lệch nhau khi 1 trong 2 được cập nhật sau này.
