# PRD — Apartment Issue Triage System

### Hệ thống phân loại & ưu tiên phản ánh chung cư bằng AI Agent

---

## 1. Mục tiêu dự án (Objectives)

- Xây dựng và **deploy thành công 1 sản phẩm AI Agent V1 chạy được với dữ liệu thật** trước Demo Day, đáp ứng đủ 10 deliverables của AI20K Build Phase (Source code, README, Architecture Diagram, AI Logs, Live URL, Video Demo, Pitch Deck, Journal, Worklog, Evaluation Evidence).
- Giải quyết đúng bài toán thực tế: **tự động hóa việc phân loại (Category) và xếp mức ưu tiên (Priority)** cho phản ánh sự cố chung cư — thay thế cách làm thủ công hiện tại (điện thoại/ghi chép) vốn dễ phân loại sai bộ phận xử lý và xếp sai mức độ khẩn cấp.
- Đảm bảo **sự cố đe dọa tính mạng luôn được nhận diện và xử lý ngay lập tức** (cơ chế red-flag), đồng thời **mọi quyết định ưu tiên khác đều minh bạch, giải thích được** (không phải "AI nói vậy thì vậy") để BQL và mentor có thể kiểm chứng.
- Đạt điểm cao theo đúng 4 tiêu chí đánh giá cuối kỳ của BTC: Sản phẩm V1 chạy thật (40%), Mức hài lòng đối tác (25%), Chất lượng code & tài liệu (20%), Làm việc nhóm & trách nhiệm (15%).

## 2. Đối tượng người dùng (User Personas)

### Persona 1 — Cư dân chung cư

Người gửi phản ánh sự cố hàng ngày (ồn ào, thang máy lỗi, rò nước, hỏng đèn, chập điện...). Hiện tại phải gọi điện/ghi chép thủ công cho BQL, không biết tình trạng xử lý ra sao, và lo ngại rằng sự cố khẩn cấp (thang máy kẹt, dấu hiệu cháy) có thể bị xử lý chậm nếu thông tin ban đầu không đủ rõ ràng.

### Persona 2 — Điều phối viên BQL

Người tiếp nhận và điều phối toàn bộ phản ánh. Hiện phải tự phân loại và xếp ưu tiên hàng loạt ticket thủ công, dễ sai sót/chậm trễ khi lượng phản ánh lớn, khó theo dõi tổng thể tiến độ xử lý và khó phát hiện các sự cố đang lan rộng (ví dụ rò nước từ tầng trên xuống tầng dưới) nếu chỉ nhìn từng ticket riêng lẻ.

### Persona 3 — Kỹ thuật viên

Người trực tiếp xử lý sự cố tại hiện trường. Cần biết rõ: việc gì được giao, mức độ khẩn cấp ra sao (để ưu tiên đúng thứ tự trong ngày làm việc), và cần ghi nhận lại kết quả xử lý để có minh chứng khi cần.

## 3. Các tính năng chính (Features)

### Nhóm Cư dân

| #   | Feature                                                           |
| --- | ----------------------------------------------------------------- |
| 1   | Đăng ký/Đăng nhập (SĐT + OTP, gắn `unit_id`)                      |
| 2   | Gửi ticket mới (text + ảnh + chọn vị trí)                         |
| 3   | Xem danh sách ticket của mình + trạng thái                        |
| 4   | Xem chi tiết 1 ticket (Category/Priority/thời gian dự kiến xử lý) |
| 5   | Nhận thông báo khi ticket có cập nhật                             |
| 6   | Hủy ticket (gửi nhầm)                                             |
| 7   | Xem lịch sử ticket cũ                                             |

### Nhóm Điều phối viên BQL

| #   | Feature                                                            |
| --- | ------------------------------------------------------------------ |
| 8   | Dashboard tổng quan ticket (lọc theo Category/Priority/trạng thái) |
| 9   | Xem chi tiết ticket (ảnh gốc, text, vị trí, điểm số)               |
| 10  | Duyệt ticket P0 (category ảnh/text không khớp)                     |
| 11  | Ghi đè (override) Priority/Category thủ công                       |
| 12  | Gán ticket cho Kỹ thuật viên cụ thể                                |
| 13  | Xuất báo cáo/thống kê định kỳ                                      |
| 14  | Quản lý danh sách Kỹ thuật viên                                    |
| 15  | Xem audit log                                                      |

### Nhóm Kỹ thuật viên

| #   | Feature                                    |
| --- | ------------------------------------------ |
| 16  | Xem danh sách ticket được giao             |
| 17  | Xem chi tiết ticket được giao              |
| 18  | Cập nhật trạng thái xử lý                  |
| 19  | Ghi chú xử lý                              |
| 20  | Chụp ảnh xác nhận sau khi xử lý xong       |
| 21  | Nhận thông báo khi có ticket mới được giao |

### Nhóm Hệ thống/AI (chạy ngầm)

| #   | Feature                                           |
| --- | ------------------------------------------------- |
| 22  | AI Agent tự động phân loại                        |
| 23  | Red-flag override (ép P3 ngay)                    |
| 24  | Gộp ticket lan rộng theo vị trí liền kề           |
| 25  | Gộp ticket theo lịch xử lý                        |
| 26  | Đối chiếu Category ảnh vs text (nhánh P0)         |
| 27  | Tự động gửi thông báo cập nhật tiến độ tới cư dân |

## 4. Luồng nghiệp vụ (User Stories)

- **Là một cư dân**, tôi muốn đăng nhập bằng số điện thoại + OTP thay vì nhớ mật khẩu, để việc gửi phản ánh nhanh và đơn giản nhất có thể.
- **Là một cư dân**, tôi muốn gửi phản ánh kèm cả ảnh, mô tả bằng chữ và vị trí cụ thể, để hệ thống hiểu đúng vấn đề ngay từ lần báo cáo đầu tiên.
- **Là một cư dân**, tôi muốn biết ticket của mình dự kiến xử lý trong bao lâu (không cần hiểu mã kỹ thuật P1/P2/P3), để an tâm chờ đợi đúng mức.
- **Là một cư dân**, tôi muốn nhận thông báo mỗi khi ticket có cập nhật, để không phải tự vào app kiểm tra liên tục.
- **Là một Điều phối viên BQL**, tôi muốn xem toàn bộ ticket được xếp theo mức độ ưu tiên giảm dần, để luôn xử lý đúng việc khẩn cấp nhất trước.
- **Là một Điều phối viên BQL**, tôi muốn được cảnh báo riêng khi hệ thống không chắc chắn về loại sự cố (ảnh và text không khớp), để tự tay xác nhận trước khi giao việc, tránh giao nhầm bộ phận.
- **Là một Điều phối viên BQL**, tôi muốn ghi đè lại Category/Priority nếu thấy AI phân loại chưa hợp lý, để đảm bảo quyết định cuối cùng luôn chính xác và có thể truy vết lại lý do.
- **Là một Điều phối viên BQL**, tôi muốn xuất báo cáo định kỳ (số ticket theo loại, thời gian xử lý so với cam kết), để theo dõi hiệu suất vận hành theo thời gian.
- **Là một Kỹ thuật viên**, tôi muốn thấy danh sách việc được giao sắp xếp theo mức độ khẩn cấp, để biết chính xác nên làm việc gì trước trong ngày.
- **Là một Kỹ thuật viên**, tôi muốn cập nhật trạng thái xử lý kèm ảnh xác nhận khi hoàn thành, để có minh chứng rõ ràng cho công việc đã làm.
- **Là hệ thống AI**, cần tự động phát hiện dấu hiệu nguy hiểm (khói, lửa, mắc kẹt...) trong ảnh hoặc text ngay từ bước đầu tiên, để đảm bảo sự cố đe dọa tính mạng luôn được ép mức ưu tiên khẩn cấp nhất, không phụ thuộc vào kết quả của công thức tính điểm thông thường.

## 5. Yêu cầu kỹ thuật (Tech Stack)

| Layer               | Công nghệ                                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM                 | GPT-5.6 Terra (đa phương thức, hỗ trợ đọc ảnh sẵn, cân bằng hiệu năng/chi phí)                                                                             |
| Agent Orchestration | LangGraph (classify → prioritize → route → notify)                                                                                                         |
| Vision              | Tích hợp sẵn trong GPT-5.6 Terra cho phân tích ảnh sự cố (không cần model riêng)                                                                           |
| Backend             | FastAPI                                                                                                                                                    |
| Frontend            | Next.js                                                                                                                                                    |
| Database            | PostgreSQL (qua Supabase)                                                                                                                                  |
| Auth                | Supabase Auth — 3 vai trò: Cư dân (SĐT + OTP), Điều phối viên BQL, Kỹ thuật viên (email + password)                                                        |
| Task Queue          | Celery + Redis — xử lý bất đồng bộ (async) cho các lời gọi LLM/Vision chậm (đọc ảnh, phân loại), tránh chặn request chính khi có nhiều ticket gửi cùng lúc |
| Deploy              | Railway (backend + DB) + Vercel (frontend), gói miễn phí (free tier)                                                                                       |

_(Team ban đầu chọn "Kịch bản B" — quy mô đồ án/demo, không cần hạ tầng scale-up. Team hiện đang mở rộng thêm Celery + Redis để chuẩn bị khả năng scale khi deploy, dù các phần hạ tầng khác (Docker, monitoring, CI/CD, gói trả phí) vẫn chưa cần thiết ở giai đoạn này. Ngân sách API GPT và lựa chọn cụ thể cho Database vẫn đang chờ tư vấn từ ban tổ chức.)_

## 6. Tiêu chí thành công (Success Metrics)

### Theo đúng thang điểm đánh giá của BTC

| Tiêu chí                               | Trọng số | Cách đo                                                  |
| -------------------------------------- | -------- | -------------------------------------------------------- |
| Sản phẩm V1 chạy được với dữ liệu thật | 40%      | Live trên production, đối tác dùng được, không crash     |
| Mức hài lòng của đối tác               | 30%      | Điểm đánh giá từ đối tác sau Demo 2                      |
| Chất lượng code & tài liệu             | 20%      | Code review, test coverage, README, architecture diagram |
| Làm việc nhóm & trách nhiệm            | 10%      | Standup đều đặn, báo cáo đúng hạn, phân việc rõ ràng     |

### Chỉ số kỹ thuật riêng của hệ thống phân loại/ưu tiên

- **Không bỏ sót red-flag:** 0 trường hợp sự cố có dấu hiệu nguy hiểm (khói, lửa, mắc kẹt...) bị chấm sai xuống dưới P3 — đây là chỉ số an toàn quan trọng nhất, ưu tiên cao hơn độ chính xác phân loại tổng thể.
- **Tỷ lệ ticket rơi vào P0** (category ảnh/text không khớp) ở mức hợp lý — quá cao nghĩa là model phân loại kém, quá thấp có thể do không đủ nghiêm ngặt trong việc đối chiếu.
- **Tuân thủ thời gian phản hồi theo Priority:** tỷ lệ ticket được xử lý đúng hạn cam kết (P3 ≤ 5 phút, P2 ≤ 3 giờ, P1 ≤ 72 giờ).
- **Thời gian phản hồi của AI Agent:** từ lúc cư dân gửi ticket tới lúc có kết quả Category/Priority — mục tiêu vài giây, không để cư dân chờ lâu ở bước "Đang phân tích...".
