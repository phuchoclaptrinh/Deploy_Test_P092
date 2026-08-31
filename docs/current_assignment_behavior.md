# Hành vi phân việc hiện tại

Tài liệu này tóm tắt hành vi phân việc đang có trong code hiện tại, gồm phân việc thủ công, phân việc tự động và các trường hợp bị chặn.

## Điều kiện để vào tự phân việc

Một ticket chỉ được đưa vào luồng tự phân việc khi thỏa các điều kiện sau:

- Ticket đã được duyệt: `status = APPROVED`.
- Agent đã phân loại xong: `classification_status = RESOLVED`.
- Có `category_id` và `priority`.
- Không phải ticket trùng.
- Không phải priority `P5`.
- Không đang chờ BQL duyệt mức khẩn cấp.
- Chưa có assignment active.
- Công tắc tự phân việc đang bật.

Nếu thiếu một trong các điều kiện trên, ticket sẽ không được worker tự gán KTV.

## Khi đủ điều kiện

Backend tạo một bản ghi trong `dispatch_events`.

`dispatch_events` chỉ là hàng đợi phân việc, chưa phải assignment thật. Assignment thật chỉ được tạo khi worker xử lý thành công.

## Worker phân việc

Worker `assignment-worker` xử lý các `dispatch_events` theo từng batch.

Mỗi batch:

- Chỉ chạy trong ca làm việc.
- Nếu ngoài ca, event được dời sang đầu ca tiếp theo.
- Lấy tối đa `dispatch_micro_batch_size` event.
- Sort ticket theo priority, điểm và thời gian gửi:
  - `P2` trước `P1`.
  - Điểm cao trước.
  - Ticket gửi sớm trước.

## Lọc kỹ thuật viên

Với mỗi ticket, worker chỉ xét KTV thỏa điều kiện:

- KTV active.
- KTV đang sẵn sàng nhận việc.
- Đang trong ca làm việc.
- Có kỹ năng phù hợp với danh mục ticket.
- Không bị loại do từng từ chối hoặc báo không xử lý được ticket đó.

Nếu không có KTV phù hợp, ticket không được tự gán và được đẩy về BQL xử lý.

## Cách chọn lịch

Scheduler mô phỏng queue của từng KTV.

Các nguyên tắc chính:

- Job đang `IN_PROGRESS` được giữ ở đầu queue.
- Job đã gán có `planned_finish_at` làm deadline cam kết.
- Ticket mới được tính thời lượng theo P80 của danh mục.
- Có safety buffer mặc định 30 phút.

Nếu thêm ticket mới mà không làm job cũ bị trễ deadline, phương án được xem là `SAFE`.

Nếu thêm ticket mới khiến job cũ âm slack, phương án được xem là `AT_RISK`.

## Khi có phương án SAFE

Worker tự gán ticket cho KTV tốt nhất.

KTV tốt nhất được chọn theo thứ tự:

- Phương án safe trước.
- Bắt đầu sớm nhất.
- Còn nhiều slack/headroom hơn.
- Ít job hơn.
- Id nhỏ hơn để kết quả ổn định.

## Khi tất cả phương án đều AT_RISK

Worker gọi dispatch agent một lần cho cả nhóm ticket rủi ro.

Nếu agent trả về lựa chọn hợp lệ, hệ thống gán theo agent.

Nếu agent lỗi, timeout hoặc trả về không hợp lệ, hệ thống fallback về scheduler và chọn KTV ít làm vỡ lịch nhất.

## Khi không thể tự gán

Ticket sẽ không được tự phân việc nếu:

- Không có KTV đủ kỹ năng.
- Không có phương án khả thi.
- Auto assignment bị tắt.
- Ticket không còn đủ điều kiện tại thời điểm worker xử lý.
- Ticket là P5 hoặc đang chờ duyệt mức khẩn cấp.

Các trường hợp này được đưa về BQL để xử lý thủ công.

## Luồng P5

P5 không được phân việc — không tự động, và cũng không thủ công.

Nếu Agent chấm ra P5, hệ thống cảnh báo ngay, vẫn chạy tra trùng, rồi ticket chờ BQL xác nhận hoặc hạ mức.

Sau khi BQL xác nhận P5:

- Ticket được chuyển từ `NEW` sang `APPROVED`.
- Giữ priority `P5`.
- Ghi lịch sử trạng thái.
- **Không** mở khóa phân việc. Xác nhận khẩn cấp không phải là bước tiền đề để
  giao cho kỹ thuật viên; nó chỉ ghi nhận rằng BQL đã đọc và đồng ý với mức.

P5 vẫn không đi vào auto dispatch, và cũng không lên bảng phân việc trực quan: Ban quản lý xử lý trực tiếp.

## Phân việc thủ công

BQL có thể phân công thủ công khi ticket đã được duyệt và chưa có assignment active.

Backend vẫn kiểm tra:

- Ticket có thể phân công.
- Không đang chờ duyệt mức khẩn cấp.
- KTV tồn tại.
- KTV active và available.
- KTV có kỹ năng phù hợp.
- Ticket chưa có assignment active.

Khi phân công thủ công thành công:

- Tạo `ticket_assignments`.
- Lên lịch cho assignment.
- Đóng dispatch event còn mở nếu có.
- Gửi thông báo cho KTV và cư dân.

## Phân việc theo cụm

Với cụm ticket, BQL có thể duyệt cả cụm và gán cả cụm cho một KTV.

Backend kiểm tra tất cả ticket trong cụm trước khi ghi assignment.

Nếu một ticket trong cụm không hợp lệ, toàn bộ thao tác gán cụm bị từ chối. Không có trạng thái gán một phần.

## Lưu ý về P2

Code hiện tại có ưu tiên `P2` trước `P1` khi worker xử lý batch.

Tuy nhiên chưa có rule riêng để ticket `P2` ngắn hoặc gần trễ được chen lịch mạnh hơn. Vì vậy một ticket `P2` vẫn có thể bị đặt muộn nếu scheduler thấy việc chen lên sẽ làm vỡ deadline của các job đã cam kết.

