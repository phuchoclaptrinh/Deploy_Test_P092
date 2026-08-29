# Đặc tả chi tiết Tính năng & Luồng nghiệp vụ

## Hệ thống phân loại & ưu tiên phản ánh chung cư bằng AI

---

## 0. Bảng tham chiếu chung

### 0.1. Định nghĩa Priority & SLA

| Priority | Ý nghĩa                                         | Ví dụ                                                                   | Thời gian xử lý cam kết |
| -------- | ----------------------------------------------- | ----------------------------------------------------------------------- | ----------------------- |
| **P3**   | Cực kỳ nguy hiểm, ảnh hưởng trực tiếp tính mạng | Thang máy hỏng/kẹt, dấu hiệu cháy, gây rối trật tự công cộng, chập điện | 5 phút                  |
| **P2**   | Phiền toái nghiêm trọng, ảnh hưởng sinh hoạt    | Điều hòa hỏng, mất điện, khóa cửa chính hỏng, mùi hôi thối              | 3 giờ                   |
| **P1**   | Vấn đề bình thường                              | Rò nước, hỏng đèn hành lang, phàn nàn hàng xóm thông thường             | 72 giờ                  |

Ngoài 3 mức trên, ticket có thể ở trạng thái **chờ Điều phối viên duyệt thủ công** — không phải mức độ nguy hiểm, mà là trạng thái "chưa xác định được", xảy ra khi Category từ ảnh và text không khớp, hoặc AI đã tra cứu/hỏi thêm tới giới hạn cho phép mà vẫn chưa đủ tự tin kết luận.

Một trạng thái riêng khác nữa là **"Không hợp lệ"** — ticket bị tự động đóng vì AI hỏi thêm mà không có phản hồi kịp trong 5 phút, và không có nghi ngờ nguy hiểm nào. Khác trạng thái chờ duyệt thủ công: "Không hợp lệ" đóng hẳn ticket, yêu cầu cư dân gửi lại.

### 0.2. Danh sách Category & Priority Ceiling

| Category                               | Priority Ceiling (mức trần khi tính điểm, không tính red-flag) |
| -------------------------------------- | -------------------------------------------------------------- |
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

Danh mục Category do BQL quản lý qua màn hình quản trị, có thể thêm/xóa/tắt hiệu lực bất kỳ lúc nào — AI luôn phân loại theo đúng danh mục đang hiệu lực tại thời điểm ticket được gửi, không dùng danh sách cũ.

### 0.3. Ma trận phân quyền

| Hành động                             |          Cư dân          | Điều phối viên |       Kỹ thuật viên       |
| ------------------------------------- | :----------------------: | :------------: | :-----------------------: |
| Gửi ticket mới                        |            ✅            |       ❌       |            ❌             |
| Trả lời câu hỏi bổ sung của AI        |            ✅            |       ❌       |            ❌             |
| Xem ticket của chính mình / được giao |            ✅            |  ✅ (tất cả)   | ✅ (chỉ ticket được giao) |
| Duyệt ticket chờ xử lý thủ công       |            ❌            |       ✅       |            ❌             |
| Duyệt ticket (APPROVE)                |            ❌            |       ✅       |            ❌             |
| Gán ticket cho Kỹ thuật viên          |            ❌            |       ✅       |            ❌             |
| Cập nhật trạng thái xử lý thực tế     |            ❌            |       ❌       | ✅ (chỉ ticket được giao) |
| Xem ảnh gốc                           | ❌ (chỉ ticket của mình) |       ✅       | ✅ (chỉ ticket được giao) |

---

## 1. NHÓM CƯ DÂN

### 1.1. Đăng ký / Đăng nhập

1. Cư dân nhập số điện thoại và mã OTP.
2. Lần đầu đăng nhập: hệ thống yêu cầu nhập mã căn hộ do BQL cấp sẵn.
3. Hệ thống kiểm tra mã căn hộ chưa từng gắn tài khoản nào khác → tạo tài khoản, liên kết SĐT với căn hộ.
4. Mã căn hộ đã có người đăng ký → báo lỗi, hướng dẫn liên hệ BQL nếu có tranh chấp.

Một căn hộ chỉ gắn với đúng 1 tài khoản chính, đảm bảo tính đúng số lượng ticket theo 1 nguồn duy nhất khi hệ thống cần gộp các ticket cùng khu vực.

### 1.2. Gửi ticket mới

1. Cư dân chọn Tầng và Vị trí cụ thể từ dropdown cố định.
2. Nhập mô tả bằng chữ và/hoặc chụp/tải lên ảnh hiện trường. Bắt buộc có text, ảnh không bắt buộc.
3. Nhấn gửi → ticket tạo ngay với trạng thái Mới, hiển thị "Yêu cầu của bạn đang được gửi ... Hãy chờ xác nhận nhé !".
4. Nếu ảnh gửi lên không đọc được hoặc không liên quan tới sự cố chung cư (ví dụ gửi nhầm ảnh khác), hoặc mô tả quá sơ sài để hiểu vấn đề → hệ thống phản hồi ngay yêu cầu mô tả rõ hơn hoặc chụp ảnh khác, **chưa tạo ticket chính thức**. Ảnh không liên quan bị chặn ngay lập tức dù mô tả bằng chữ có đầy đủ tới đâu.
5. Trong lúc phân tích, màn hình có thể chuyển thành câu hỏi bổ sung — xem mục 1.3.

### 1.3. Trả lời câu hỏi bổ sung từ AI

1. Trong lúc màn hình vẫn hiển thị "Đang phân tích...", nếu AI chưa đủ tự tin về Category hoặc mức độ nghiêm trọng, màn hình đổi ngay tại chỗ thành 1 câu hỏi — có thể ở dạng trắc nghiệm (nút bấm) hoặc cho phép cư dân tự nhập câu trả lời bằng chữ, áp dụng cho cả trường hợp mô tả chữ mơ hồ lẫn ảnh chưa rõ.
2. Với câu hỏi về ảnh, có thể có thêm lựa chọn "Chụp lại ảnh khác".
3. Cư dân trả lời → màn hình quay lại "Đang phân tích...".
4. Tối đa lặp lại tối đa 3 lần, tổng thời gian chờ trả lời cho cả 3 lần cộng lại không quá 5 phút.
5. Nếu hết 5 phút mà chưa trả lời kịp → ticket chuyển "Không hợp lệ", yêu cầu gửi lại ticket mới. Trả lời trễ không còn tác dụng.
6. Nếu ở bất kỳ vòng nào, câu trả lời (hoặc ảnh mới) cho thấy dấu hiệu nguy hiểm → hệ thống dừng hỏi ngay lập tức, xử lý ngay như sự cố khẩn cấp (mục 4.2), không chờ hết vòng/hết giờ, báo với người dân ảnh của bạn có dấu hiệu nguy hiểm nên chúng tôi sẽ liên hệ với BQL.

### 1.4. Xem danh sách ticket của mình + trạng thái

Danh sách ticket đã gửi, sắp xếp theo thời gian gần nhất. Trạng thái hiển thị: Mới / Đang chờ bạn trả lời / Đã duyệt / Đã gán kỹ thuật viên / Đang xử lý / Hoàn thành / Không xử lý được / Không hợp lệ.

### 1.5. Xem chi tiết 1 ticket

Hiển thị Category (tên dễ hiểu), mức ưu tiên diễn giải thân thiện, thời gian dự kiến xử lý (bảng 0.1). Không hiển thị "SLA" hay mã P1/P2/P3 trực tiếp, không hiển thị điểm số hay bất kỳ chi tiết kỹ thuật nào khác.

### 1.6. Nhận thông báo khi ticket có cập nhật

Trigger gửi thông báo mỗi khi trạng thái đổi: được duyệt → được gán kỹ thuật viên → đang xử lý → hoàn thành. Nếu ticket bị từ chối ở bước duyệt thủ công (không hợp lệ), cư dân cũng nhận thông báo kèm lý do.

### 1.7. Hủy ticket

Chỉ cho phép khi ticket còn ở trạng thái Mới. Sau khi đã duyệt, không cho hủy trực tiếp.

### 1.8. Xem lịch sử ticket cũ

Danh sách đầy đủ toàn bộ ticket đã gửi, lọc theo thời gian hoặc Category.

---

## 2. NHÓM ĐIỀU PHỐI VIÊN BQL

### 2.1. Dashboard tổng quan ticket

Toàn bộ ticket trong hệ thống, lọc theo Category/Priority/trạng thái/thời gian, sắp xếp mặc định theo Priority giảm dần.

### 2.2. Xem chi tiết ticket

Hiển thị đầy đủ ảnh gốc, text gốc, vị trí, và điểm số tổng — không hiển thị breakdown chi tiết từng thành phần điểm số.

### 2.3. Duyệt ticket chờ xử lý thủ công

1. Ticket rơi vào trạng thái này khi: Category từ ảnh và text không khớp nhau, hoặc AI đã tra cứu/hỏi cư dân tới giới hạn cho phép mà vẫn chưa đủ tự tin kết luận.
2. Điều phối viên đọc lại text/ảnh gốc (và các câu hỏi AI đã hỏi cư dân, nếu có).
3. Chọn 1 trong 2 hướng:
   - **Xác nhận Category hợp lệ**: hệ thống tính lại Density và điểm số bình thường theo Category đã xác nhận, ticket thoát trạng thái chờ duyệt.
   - **Xác nhận ticket không hợp lệ**: ticket bị loại bỏ, hệ thống gửi thông báo lý do cho cư dân.

### 2.4. Ghi đè (override) Priority/Category thủ công

Điều phối viên có thể sửa trực tiếp Category hoặc Priority của bất kỳ ticket nào nếu thấy AI phân loại/tính điểm không hợp lý. Mỗi lần override ghi lại ai, thời gian, giá trị cũ → giá trị mới, phục vụ audit (mục 2.9).

### 2.5. Duyệt ticket (APPROVE)

Với ticket đã có Category và Priority hợp lệ, Điều phối viên bấm duyệt để xác nhận ticket sẵn sàng gán việc.

### 2.6. Gán ticket cho Kỹ thuật viên

Điều phối viên chọn 1 Kỹ thuật viên phù hợp và gán việc. Kỹ thuật viên nhận được thông báo ngay khi được gán.

### 2.7. Xuất báo cáo/thống kê định kỳ

Báo cáo theo tuần/tháng: tổng số ticket theo Category, theo Priority, thời gian xử lý thực tế so với SLA cam kết.

### 2.8. Quản lý danh sách Kỹ thuật viên

Thêm/xóa/sửa thông tin Kỹ thuật viên: tên, chuyên môn, trạng thái đang hoạt động/đang rảnh.

### 2.9. Xem audit log

Lịch sử các hành động quan trọng: ai duyệt ticket nào, lúc nào; ai override Priority/Category nào, giá trị cũ/mới là gì.

---

## 3. NHÓM KỸ THUẬT VIÊN

### 3.1. Xem danh sách ticket được giao

Chỉ hiển thị ticket đã được gán cho chính mình, sắp xếp theo Priority.

### 3.2. Xem chi tiết ticket được giao

Hiển thị text, ảnh gốc, vị trí — không hiển thị điểm số hay thông tin không liên quan tới việc sửa chữa thực tế.

### 3.3. Cập nhật trạng thái xử lý

Chuyển trạng thái: Đã gán → Đã nhận việc → Đang xử lý → Hoàn thành, hoặc Không xử lý được (kèm bắt buộc nhập lý do).

### 3.4. Ghi chú xử lý

Trường văn bản tự do ghi nguyên nhân sự cố, vật liệu/linh kiện đã thay — bắt buộc khi chuyển sang Hoàn thành.

### 3.5. Ảnh xác nhận sau khi xử lý xong

Bắt buộc ít nhất 1 ảnh xác nhận hiện trạng khi chuyển sang Hoàn thành, dùng làm minh chứng khi cần đối chiếu hoặc có tranh chấp sau này.

### 3.6. Nhận thông báo khi có ticket mới được giao

Ngay khi Điều phối viên gán việc, hệ thống gửi thông báo tới đúng Kỹ thuật viên.

---

## 4. NHÓM HỆ THỐNG / AI (chạy ngầm)

### 4.1. AI Agent tự động phân loại

Agent nhận text và/hoặc ảnh của ticket, có trách nhiệm trích xuất dữ liệu có cấu trúc: Category (từ text và từ ảnh, độc lập với nhau), dấu hiệu nguy hiểm (từ text và từ ảnh), mức độ nghiêm trọng, và liệu ảnh có thực sự liên quan tới sự cố chung cư hay không. Agent không tự quyết định Priority cuối cùng, không tự so khớp Category để kết luận có cần duyệt thủ công hay không, không tự tính số liệu chính thức cho công thức điểm — những việc đó là logic Backend, chạy sau khi Agent trả kết quả.

Trước khi kết luận, Agent có thể tự quyết định dùng thêm một số công cụ hỗ trợ, tùy tình huống:

- **Tra cứu ticket liên quan gần đây** (cùng Category/tầng/vị trí, có thể mở rộng xem cả ticket đã xử lý xong trong quá khứ) để có thêm ngữ cảnh khi đánh giá mức độ nghiêm trọng.
- **Tự quyết định gộp hay không gộp** một nhóm ticket rò nước/chập điện thành 1 sự cố lan rộng — dựa trên xét đoán thực tế (ví dụ nhiều căn hộ cùng báo rò nước ở khu vực có khả năng chung 1 đường ống thì nên gộp; 2 ticket trùng ngày nhưng rõ ràng 2 nguyên nhân độc lập thì không nên gộp dù kỹ thuật khớp điều kiện thời gian/vị trí).
- **Hỏi lại cư dân** dạng trắc nghiệm hoặc để cư dân tự nhập câu trả lời (mục 1.3) khi chưa đủ tự tin về Category hoặc mức độ nghiêm trọng.

Tổng số lần dùng các công cụ trên trong 1 lượt phân tích không vượt quá 5 lần. Nếu chạm giới hạn này (hoặc hỏi cư dân đủ 3 lượt) mà vẫn chưa đủ tự tin kết luận → chuyển ticket cho Điều phối viên duyệt thủ công.

### 4.2. Red-flag override

Nếu phát hiện dấu hiệu nguy hiểm (khói, lửa, dây điện hở, nước tràn diện rộng, ngất xỉu, gây rối...) từ text hoặc ảnh — **ở bất kỳ thời điểm nào**, kể cả giữa lúc đang hỏi lại cư dân — hệ thống ép ngay Priority ở mức khẩn cấp nhất, ngắt toàn bộ phần xử lý còn lại, không chờ hết lượt hỏi hay hết thời gian.

### 4.3. Gộp ticket lan rộng theo vị trí liền kề

Chỉ áp dụng cho Category rò nước hoặc chập điện — 2 loại sự cố có khả năng lan vật lý thật qua kết cấu tòa nhà. Khi Agent quyết định gộp một nhóm ticket, số lượng căn hộ bị ảnh hưởng trong nhóm đó được dùng làm hệ số cộng thêm vào công thức tính điểm. Nếu Agent không gộp, hệ số này coi như không có (chỉ tính ticket hiện tại).

### 4.4. Đối chiếu Category ảnh vs text

So sánh Category rút ra từ ảnh và từ text. Nếu không khớp → ticket chuyển Điều phối viên duyệt thủ công (mục 2.3), không tính điểm tiếp cho tới khi được xác nhận lại.

### 4.5. Vô hiệu ticket do hết thời gian phản hồi

Áp dụng cho ticket không có dấu hiệu nguy hiểm: hết 5 phút mà cư dân không phản hồi kịp câu hỏi bổ sung (mục 1.3) → ticket tự động đóng, yêu cầu cư dân gửi lại.

### 4.6. Tự động gửi thông báo cập nhật tiến độ tới cư dân

Hệ thống tự động kích hoạt gửi thông báo mỗi khi trạng thái ticket thay đổi, tương ứng với phần cư dân nhận ở mục 1.6.
