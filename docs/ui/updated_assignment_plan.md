# Bản cập nhật kế hoạch phân việc

## 1. Mục tiêu

Thiết kế lại toàn bộ phân việc thành hai luồng độc lập:

- Phân việc trực quan: BQL tự kéo thả ticket vào KTV.
- Phân việc tự động: hệ thống tự duyệt và phân KTV cho ticket đủ điều kiện.

Mục tiêu vận hành:

- Giảm latency và tránh crash khi ticket tăng cao.
- Không tạo đề xuất phân việc theo kiến trúc cũ.
- Không gọi agent cho mọi ticket.
- Chỉ dùng agent khi scheduler không tìm được lịch phân công an toàn.
- P3 luôn do BQL xử lý tay.

## 2. Phân việc trực quan

Nút mới: Phân việc trực quan.

Luồng ticket:

Cư dân gửi ticket
→ AI phân loại
→ Kiểm tra duplicate
→ Grouping
→ Ticket/cụm ticket hợp lệ vào pool trực quan
→ BQL kéo thả vào KTV
→ BQL xác nhận phân việc

Quy tắc:

- Không chạy rule-base để đề xuất KTV.
- Không tạo proposal batch, proposal item hoặc chờ worker dựng đề xuất.
- Toàn bộ ticket hợp lệ nằm trong pool chờ BQL quyết định.
- Nếu grouping tạo cụm, cụm là một đơn vị kéo thả; không được tách ticket trong cùng cụm sang nhiều KTV.
- Xác nhận phân việc gửi tất cả thay đổi trong một request và một transaction.
- Board hiển thị tải hiện tại, ca làm việc, kỹ năng và cảnh báo rủi ro của KTV.

Quy tắc xác thực board:

BQL là người quyết định trong các phương án hợp lệ, nhưng backend phải chặn các vi phạm sau:

- KTV không hoạt động.
- KTV không sẵn sàng.
- KTV không có kỹ năng phù hợp.
- KTV ngoài ca.
- Ticket đã có assignment hoạt động.
- Một KTV đã có ticket IN_PROGRESS.
- Cụm ticket bị tách sang nhiều KTV.

UI có thể cảnh báo trước khi thả ticket; backend vẫn là lớp kiểm tra cuối cùng.

## 3. Phân việc tự động

Nút Phân việc tự động chỉ còn trạng thái Bật/Tắt.

Khi bật, popup thông báo:

> Ticket được AI xác nhận phân loại, không trùng và không thuộc diện khẩn cấp sẽ được tự động duyệt, bỏ qua grouping và phân công ngay. Ticket không đạt điều kiện sẽ chuyển BQL xử lý.

Luồng:

Cư dân gửi ticket
→ AI phân loại
→ đủ điều kiện phân loại?
→ không duplicate?
→ không phải P3?

├─ Không: vào hàng chờ BQL
└─ Có:
→ bỏ qua grouping
→ scheduler kiểm tra kỹ năng, ca trực, lịch và deadline
├─ SAFE: gán KTV ngay
└─ AT_RISK: agent đánh giá → gán có cờ rủi ro → báo BQL

Quy tắc:

- P3 không thuộc auto flow.
- P3 phải do BQL phân tay và xử lý khẩn.
- Ticket chưa phân loại, duplicate hoặc không hợp lệ đi vào hàng chờ BQL.
- Tắt auto chỉ ngăn ticket mới được tự động phân; không hoàn tác assignment đã có.
- Auto flow bỏ qua grouping; visual board vẫn dùng grouping.

## 4. Ràng buộc cứng

Không scheduler hay agent nào được phép vượt qua:

- KTV đang active.
- KTV đang available.
- KTV có kỹ năng đúng danh mục.
- KTV đang trong ca 08:00–18:00.
- Ca áp dụng tất cả các ngày trong tuần, theo giờ Việt Nam.
- Một KTV chỉ có một ticket IN_PROGRESS.
- Không có hai active assignment cho cùng một ticket.
- P3 không được tự động phân.
- Ticket grouped trong visual board không bị tách.

Nếu không có KTV nào qua được ràng buộc cứng, không được “chọn đại”. Ticket phải chuyển BQL.

## 5. SLA, lịch nội bộ và giao diện cư dân

SLA chính thức chuyển sang thời gian KTV tiếp nhận ticket.

| Trường              | Mục đích                                        |
| ------------------- | ----------------------------------------------- |
| `acceptance_due_at` | Hạn KTV phải bấm tiếp nhận                      |
| `planned_start_at`  | Dự kiến KTV bắt đầu xử lý; cư dân được xem      |
| `planned_finish_at` | Chỉ dùng nội bộ để xếp lịch và phát hiện rủi ro |

Cư dân thấy:

- Chưa được phân: Đang chờ điều phối.
- Đã phân nhưng chưa nhận: Đang chờ KTV tiếp nhận.
- Đã nhận: Dự kiến KTV bắt đầu xử lý: ....
- Đang xử lý: KTV đang xử lý.
- Hoàn thành: ghi chú và ảnh của KTV.

Cư dân không còn thấy “dự kiến hoàn thành” như một cam kết SLA.

`planned_finish_at` vẫn bắt buộc tồn tại trong nội bộ. Nếu không giữ mốc này, KTV có thể tiếp nhận nhiều ticket nhưng hệ thống không biết lịch thực tế đã quá tải.

Trạng thái SLA hiện có:

Hành vi tiếp nhận hiện có gần với P1 = 49 giờ, P2 = 2 giờ 30 phút từ chu kỳ ticket.

## 6. Thời gian P80 nội bộ theo danh mục

Dùng để tính sức chứa và lịch KTV, không hiển thị như lời hứa hoàn thành:

| Danh mục              | P80 nội bộ |
| --------------------- | ---------: |
| Nước                  |      4 giờ |
| Tường ẩm, thấm        |      6 giờ |
| Thang máy             |    4.5 giờ |
| Mất điện              |      3 giờ |
| An ninh, an toàn      |      1 giờ |
| Tiếng ồn              |      3 giờ |
| Khóa, cửa             |      2 giờ |
| Điều hòa              |      5 giờ |
| Mùi hôi, vệ sinh      |      3 giờ |
| Internet, TV          |      3 giờ |
| Hư hỏng khu vực chung |      2 giờ |

Các số này là cấu hình khởi tạo. Sau khi có dữ liệu vận hành, cần tính lại P50/P80 từ lịch sử hoàn thành theo từng danh mục và KTV.

## 7. Thuật toán scheduler

Scheduler mô phỏng lịch từng KTV dựa trên:

- Ca 08:00–18:00 mỗi ngày.
- Ticket đang IN_PROGRESS.
- Ticket đã nhận/chờ nhận.
- `planned_start_at`, `planned_finish_at`.
- P80 theo danh mục.
- Deadline tiếp nhận.
- Đệm vận hành.

Thứ tự xử lý trong lịch KTV:

1. Ticket có độ đệm thấp nhất.
2. Nếu cùng độ đệm, ticket có điểm cao hơn.
3. Nếu cùng điểm, ticket gửi sớm hơn.
4. Nếu vẫn bằng nhau, thứ tự bất kỳ.

Độ đệm =

`planned_finish_at - thời điểm hiện tại - thời gian các ticket đứng trước - P80 ticket - đệm vận hành`

Một ticket là SAFE nếu chèn vào lịch KTV mà không làm độ đệm của bất kỳ ticket nào âm.

Một ticket là AT_RISK nếu mọi phương án hợp lệ đều làm lịch có độ đệm âm.

## 8. Agent cho ticket AT_RISK

Agent không được gọi cho ticket SAFE.

Agent chỉ chạy khi scheduler xác định ticket AT_RISK.

Agent được cung cấp danh sách KTV đã qua ràng buộc cứng. Agent không được chọn KTV ngoài danh sách này.

Agent có một tool chuyên biệt, ví dụ:

```python
get_candidate_dispatch_history(
  candidate_technician_ids,
  category_id,
  current_time,
)
```

Tool trả về dữ liệu tổng hợp, không trả nội dung phản ánh hoặc thông tin cá nhân cư dân:

- Lịch hiện tại và tải hiện tại của từng KTV.
- Độ đệm/rủi ro của từng lịch.
- Ticket đã hoàn thành.
- P50/P80 xử lý theo danh mục.
- Tỷ lệ tiếp nhận đúng hạn.
- Lịch sử bị phân lại.
- Lịch sử từ chối.
- Lịch sử không xử lý được.
- Dữ liệu 30, 60 và 90 ngày.

Agent:

- Đánh giá cả micro-batch AT_RISK, không chỉ từng ticket riêng lẻ.
- Chọn KTV ít rủi ro nhất trong tập hợp hợp lệ.
- Trả lý do ngắn, có thể audit.
- Không được vượt kỹ năng, ca trực, trạng thái active/available hoặc uniqueness.
- Sau khi quyết định, ticket được gán cờ AT_RISK và BQL nhận thông báo.
- Nếu không có KTV hợp lệ về kỹ năng/ca trực, ticket chuyển BQL.

Hành vi khi agent timeout/lỗi kỹ thuật chưa được chốt: hoặc chuyển BQL ngay, hoặc dùng phương án ít rủi ro nhất đã được scheduler xếp sẵn.

## 9. Hiệu năng và giờ cao điểm

Yêu cầu kỹ thuật:

- Khi ticket đủ điều kiện auto flow, tạo durable dispatch event.
- Dispatcher gom micro-batch 0.5–1 giây, tối đa 20 ticket.
- Bulk-load ticket, KTV, kỹ năng, assignment đang mở, lịch và aggregate history.
- Không query candidate riêng cho từng ticket.
- Scheduler chạy trong bộ nhớ.
- Agent chỉ chạy cho tập AT_RISK.
- Agent có giới hạn đồng thời và timeout rõ ràng.
- Assignment được ghi theo batch transaction.
- Có idempotency và database locking để không gán trùng.
- Không còn proposal polling loop.

Lưu ý vận hành: database Supabase đã từng chạm giới hạn 15 session. Tổng pool kết nối của API và worker phải nằm dưới quota này; không tạo session mới vô hạn theo ticket hoặc theo agent tool call.

Mục tiêu:

- Ticket SAFE: phân trong khoảng dưới 2 giây sau khi AI phân loại xong.
- Ticket AT_RISK: agent đánh giá theo batch, không làm nghẽn ticket bình thường.
- Queue tăng cao: xử lý chậm có kiểm soát, không crash.

## 10. Kiến trúc cũ cần xóa

Có thể xóa trong môi trường test:

- Assignment proposal batch.
- Assignment proposal item.
- Proposal APIs.
- Proposal UI/workspace cũ.
- Proposal polling.
- Proposal worker stage.
- Lịch tạo proposal định kỳ.
- Rule-based candidate snapshot và engine proposal cũ.
- Logic “phải xác nhận proposal mới bật auto”.
- Assignment history chỉ phục vụ proposal batch.

Giữ lại:

- Ticket assignment lifecycle.
- Kỹ năng và availability KTV.
- Accept/start/complete/reject/reassign.
- Notification.
- Audit log.
- Ticket history.
- Duplicate detection.
- Grouping cho visual board.
- P3 manual flow.

## 11. Các màn hình cần cập nhật

### BQL

- Danh sách ticket: thêm nút Phân việc trực quan.
- Nút Phân việc tự động: chỉ Bật/Tắt.
- Popup bật auto: mô tả phạm vi, ngoại lệ P3/duplicate/chưa phân loại.
- Visual board: pool, cụm ticket, cột KTV, tải, cảnh báo và xác nhận batch.
- Thông báo AT_RISK: ticket, KTV agent chọn, lý do và mức rủi ro.

### KTV

- Danh sách công việc xếp theo scheduler.
- Ticket đầu: Làm ngay.
- Các ticket sau: Tiếp theo.
- Hiển thị dự kiến bắt đầu và cảnh báo rủi ro.
- Không dùng progress bar cũ làm chỉ dẫn chính.

### Cư dân

- Hiển thị chờ điều phối/chờ KTV tiếp nhận.
- Hiển thị dự kiến KTV bắt đầu xử lý.
- Hiển thị trạng thái KTV, ghi chú và ảnh hoàn thành.
- Không hiển thị SLA hoàn thành.

- Hành vi khi agent AT_RISK timeout hoặc lỗi kỹ thuật -> tự động gán người có rủi ro thấp nhất và báo với BQL.
git switch -c feature/redesign-dashboard