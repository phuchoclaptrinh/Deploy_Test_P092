# Giải thích chi tiết các phần frontend/backend đã thêm

Ngày cập nhật: 20/08/2026

## Mục tiêu

Lần này bổ sung các phần còn thiếu ở **frontend** và **backend** theo Self Dev Docs V4, nhưng **chưa sửa code Agent**.

Mục tiêu chính:

- Chuẩn bị backend để hỗ trợ duplicate ticket.
- ~~Cho cư dân báo lại nếu ticket bị gộp nhầm.~~ (đã gỡ bỏ 23/08/2026)
- ~~Cho BQL xử lý yêu cầu xem lại duplicate.~~ (đã gỡ bỏ 23/08/2026)
- Cho KTV từ chối nhận việc đúng nghiệp vụ, không nhầm với “không xử lý được”.
- Chuẩn bị công tắc cấu hình phân việc tự động để sau này nối Agent/worker.
- Không phá flow cũ: tạo ticket, duyệt, phân công, KTV xử lý, hoàn thành vẫn giữ nguyên.

Không sửa:

- `src/agents`
- `.ai-log`
- `.codex`

## Vì Sao Cần

Theo V4, hệ thống cần xử lý các nghiệp vụ ngoài ticket đơn lẻ:

- Một phản ánh có thể trùng với phản ánh khác.
- ~~Cư dân có quyền báo “sự cố của tôi khác” nếu bị gộp nhầm.~~ (đã gỡ bỏ 23/08/2026)
- ~~BQL cần một nơi để xem và xử lý các yêu cầu xem lại duplicate.~~ (đã gỡ bỏ 23/08/2026)
- KTV có thể từ chối nhận việc trước khi xử lý để BQL phân lại.
- Auto assignment cần có cấu hình bật/tắt trước, dù Agent/worker chưa nối.

Nếu không có các phần này, sau này Agent V4 trả về duplicate hoặc auto assignment thì backend không có chỗ lưu, frontend không có chỗ thao tác.

## Backend

### Migration V4

File:

```text
alembic/versions/1a2b3c4d5e6f_add_v4_backend_shell.py
```

Dùng để cập nhật cấu trúc DB.

Đã chạy lên Supabase, DB hiện tại:

```text
1a2b3c4d5e6f (head)
```

Nếu dùng DB mới hoặc reset DB, cần chạy:

```powershell
python -m alembic upgrade head
```

Nếu không migrate, backend có thể lỗi 500 khi tạo ticket vì ORM đã có cột mới nhưng DB chưa có.

### Ticket `LINKED_DUPLICATE`

File:

```text
src/models/enums.py
```

Dùng để đánh dấu ticket đã được gộp vào một ticket gốc.

Ví dụ:

- Ticket A: cư dân A báo rò nước tầng 8.
- Ticket B: cư dân B cũng báo cùng sự cố.
- BQL chọn Ticket A là ticket gốc.
- Ticket B chuyển sang `LINKED_DUPLICATE`, lưu `duplicate_of_ticket_id = Ticket A`.

Ý nghĩa:

- Ticket duplicate không còn xử lý như ticket độc lập.
- Cư dân vẫn thấy phản ánh của mình.
- Cư dân biết việc xử lý đang theo ticket gốc.

### Assignment `REJECTED`

File:

```text
src/models/enums.py
src/domain/assignment_transitions.py
```

Dùng khi KTV từ chối nhận việc trước khi bắt đầu xử lý.

Khác với `UNABLE_TO_HANDLE`:

- `REJECTED`: KTV chưa xử lý, chỉ từ chối nhận việc.
- `UNABLE_TO_HANDLE`: KTV đã nhận/xử lý và kết luận không xử lý được.

Ví dụ:

- BQL gán ticket thang máy cho KTV điện nước.
- KTV bấm `Từ chối`, nhập lý do “không đúng chuyên môn”.
- Assignment cũ đóng lại.
- Ticket quay về để BQL phân lại.

### Cột Mới Trong `tickets`

File:

```text
src/database/models/ticket.py
```

Các cột mới:

```text
duplicate_of_ticket_id
invalid_reason
reassignment_count
auto_assignment_paused
auto_assignment_pause_reason
```

`duplicate_of_ticket_id`

- Lưu ticket gốc nếu ticket hiện tại bị gộp duplicate.

`invalid_reason`

- Chuẩn bị lưu lý do ticket không hợp lệ.

`reassignment_count`

- Đếm số lần ticket phải phân lại KTV.
- Tăng khi KTV từ chối.

`auto_assignment_paused`

- Đánh dấu ticket đang tạm dừng auto assignment.
- Ví dụ KTV từ chối, hệ thống tạm dừng tự phân để BQL xem lại.

`auto_assignment_pause_reason`

- Lưu lý do tạm dừng auto assignment.

### Cột Mới Trong `ticket_assignments`

File:

```text
src/database/models/ticket_assignment.py
```

Các cột mới:

```text
assignment_source
reject_reason
acceptance_warning_at
acceptance_reassign_at
```

`assignment_source`

- Cho biết assignment đến từ đâu.
- Hiện mặc định là `MANUAL`.
- Sau này có thể dùng `AI_DIRECT`, `AI_PROPOSAL`, `SYSTEM_REASSIGN`.

`reject_reason`

- Lưu lý do KTV từ chối nhận việc.

`acceptance_warning_at`

- Chuẩn bị cho rule cảnh báo nếu KTV không nhận việc sau một thời gian.

`acceptance_reassign_at`

- Chuẩn bị cho rule tự phân lại nếu KTV không nhận việc sau deadline.

Ngoài ra `assigned_by_user_id` được cho nullable để sau này hệ thống/Agent có thể tạo assignment mà không cần một user BQL cụ thể.

## Bảng Mới

### `duplicate_disputes`

> **ĐÃ GỠ BỎ (23/08/2026).** Phần dưới đây mô tả luồng kháng nghị duplicate của Cư dân/BQL, đã bị loại bỏ khỏi sản phẩm. Giữ lại vì đây là nhật ký thay đổi theo ngày; **không dùng làm hướng dẫn triển khai**. Xem mục "Cập nhật ngày 23/08/2026" ở cuối tài liệu.

File:

```text
src/database/models/duplicate_dispute.py  (đã xóa 23/08/2026)
```

Dùng để lưu yêu cầu của cư dân khi cư dân cho rằng ticket của họ bị gộp nhầm.

Flow:

1. Ticket của cư dân bị gộp vào ticket gốc.
2. Resident detail hiện banner `Phản ánh đã được gộp`.
3. Cư dân bấm `Sự cố của tôi khác`.
4. Backend tạo một dòng trong `duplicate_disputes`.
5. BQL vào trang `Tự động phân việc` để xử lý.

Trạng thái:

- `OPEN`: đang chờ BQL xem lại.
- `KEPT_LINKED`: BQL quyết định giữ gộp.
- `SPLIT_INDEPENDENT`: BQL tách thành ticket riêng.

### `ticket_relations`

File:

```text
src/database/models/ticket_relation.py
```

Dùng để chuẩn bị lưu quan hệ giữa các ticket.

Ví dụ sau này:

- Agent phát hiện ticket A liên quan ticket B vì cùng red-flag evidence.
- Agent phát hiện các ticket thuộc cùng một cụm.

Hiện tại:

- Bảng đã có.
- Chưa nối Agent nên chưa tự ghi quan hệ từ Agent.

### `auto_assignment_settings`

File:

```text
src/database/models/auto_assignment_setting.py
```

Dùng để lưu công tắc bật/tắt phân việc tự động.

Hiện tại:

- BQL có thể bật/tắt trong UI.
- Backend lưu cấu hình.
- Chưa có worker/Agent tự phân việc thật.

Nói ngắn gọn:

- Công tắc đã có.
- Máy tự phân việc chưa có.

### `resident_ticket_rate_limits`

File:

```text
src/database/models/resident_ticket_rate_limit.py
```

Dùng để chặn cư dân gửi phản ánh quá nhiều trong thời gian ngắn.

Rule theo `Self_Dev_Docs/agent_backend_contract_v4.md`, mục `8.2. Bộ đếm chống spam`:

- Mỗi lần tạo ticket thành công được tính vào ngưỡng `10 lần / 1 giờ`.
- Duplicate vẫn tính vào ngưỡng này vì vẫn là một lần tạo ticket hợp lệ.
- Khi chạm ngưỡng, hệ thống chặn tạo ticket mới trong `12 giờ`.
- Chỉ chặn tạo ticket mới, không khóa đăng nhập, không khóa xem ticket, không khóa nhận thông báo.

Bảng này lưu:

- `reporter_user_id`: cư dân bị theo dõi/chặn.
- `window_started_at`: mốc cửa sổ kiểm tra gần nhất.
- `window_ticket_count`: số ticket gần nhất trong cửa sổ 1 giờ.
- `ai_rejection_window_started_at`: mốc cửa sổ đếm ticket bị Agent từ chối vì thiếu dữ liệu.
- `ai_rejection_count`: số lần `INSUFFICIENT_INPUT / CONTENT_INSUFFICIENT` trong 1 ngày.
- `blocked_until`: thời điểm được phép tạo lại.
- `block_reason`: lý do khóa tạm.

Khi cư dân bị chặn, API tạo ticket trả:

```text
HTTP 429
code = TICKET_CREATE_RATE_LIMITED
```

Frontend sẽ nhận message:

```text
Bạn đã gửi quá nhiều phản ánh trong thời gian ngắn. Vui lòng thử lại sau.
```

Ngoài ngưỡng 10 lần/1 giờ, backend cũng ghi nhận ngưỡng V4:

- Nếu Agent kết luận `INSUFFICIENT_INPUT` vì nội dung không đủ rõ, backend tăng `ai_rejection_count`.
- Nếu đạt `3 lần / 1 ngày`, resident bị chặn tạo ticket mới trong `12 giờ`.
- Timeout do cư dân không trả lời câu hỏi Agent trong 5 phút không tính vào bộ đếm này.

### Timeout vận hành V4

File:

```text
src/services/operational_timeout_service.py
```

Dùng để quét các deadline không nên phụ thuộc vào request frontend:

1. **Cư dân không trả lời câu hỏi Agent trong 5 phút**
   - Gọi lại logic `AgentQuestionService.handle_timeouts`.
   - Đóng question đang mở.
   - Đưa ticket sang `INVALID`.
   - Ghi `invalid_reason = RESIDENT_RESPONSE_TIMEOUT`.

2. **KTV chưa nhận việc tới mốc cảnh báo**
   - Quét `ticket_assignments.acceptance_warning_at`.
   - Chỉ áp dụng assignment active, trạng thái `ASSIGNED`, chưa có `accepted_at`.
   - Gửi notification nhắc KTV xác nhận nhận việc.
   - Ghi `warning_sent_at` để không gửi lặp.

3. **KTV im lặng quá hạn nhận việc**
   - Quét `ticket_assignments.acceptance_reassign_at`.
   - Đóng assignment cũ bằng trạng thái `REASSIGNED`.
   - Tăng `tickets.reassignment_count`.
   - Pause auto assignment riêng ticket đó.
   - Gửi notification cho resident và Điều phối viên để BQL phân lại.

Endpoint test/quét thủ công:

```text
POST /api/v1/coordinator/operational-timeouts/run
```

Response:

```json
{
  "resident_question_timeouts": 0,
  "technician_acceptance_warnings": 0,
  "technician_acceptance_reassignments": 0
}
```

Lưu ý:

- Service này đã sẵn sàng để cron/worker gọi định kỳ.
- Hiện chưa gọi AI chọn KTV mới vì chưa sửa/nối phần Agent assignment V4.
- Khi quá hạn nhận việc, backend đưa ticket về trạng thái cần BQL phân lại an toàn thay vì tự đoán KTV.

## Service Mới

File:

```text
src/services/v4_workflow_service.py
```

### `DuplicateWorkflowService`

Dùng để BQL liên kết duplicate thủ công. Chỉ còn một method.

`link_duplicate`

- BQL đánh dấu ticket hiện tại là duplicate của ticket khác.
- Ticket bị link chuyển sang `LINKED_DUPLICATE`.
- Gửi thông báo cho cư dân.
- Chỉ chạm được vào ticket đã công bố; ticket đang trong giai đoạn AI riêng tư
  trả về not-found.

`request_dispute`, `list_disputes`, `resolve_dispute`,
`open_dispute_status_for_ticket` đã bị xóa cùng luồng kháng nghị (23/08/2026).

### `AutoAssignmentSettingsService`

Dùng để:

- Lấy cấu hình auto assignment.
- Bật/tắt auto assignment.
- Tăng version cấu hình.

Hiện tại chưa tự phân việc, chỉ lưu cấu hình.

## API Backend Mới

### API BQL

File:

```text
src/api/routes/coordinator_tickets.py
```

`POST /api/v1/coordinator/tickets/{ticket_id}/duplicate-link`

Dùng để BQL gộp ticket hiện tại vào ticket gốc.

Body:

```json
{
  "master_ticket_id": "uuid-ticket-goc",
  "reason": "Trùng với phản ánh đang xử lý"
}
```

Kết quả:

- Ticket hiện tại chuyển sang `LINKED_DUPLICATE`.
- Lưu `duplicate_of_ticket_id`.

`GET /api/v1/coordinator/duplicate-disputes` và
`POST /api/v1/coordinator/duplicate-disputes/{dispute_id}/resolve` **đã bị xóa**
(23/08/2026). Không còn alias, không còn deprecated route.

`GET /api/v1/coordinator/auto-assignment-settings`

- Lấy cấu hình phân việc tự động hiện tại.

`PATCH /api/v1/coordinator/auto-assignment-settings`

- Bật/tắt phân việc tự động.

Body:

```json
{
  "enabled": true,
  "activation_delay": "IMMEDIATE"
}
```

### API Cư Dân

File:

```text
src/api/routes/tickets.py
```

`POST /api/v1/tickets/{ticket_id}/duplicate-review` và
`POST /api/v1/tickets/{ticket_id}/duplicate-dispute` **đã bị xóa** (23/08/2026).

### API KTV

File:

```text
src/api/routes/technician_assignments.py
```

`POST /api/v1/technician/assignments/{assignment_id}/reject`

Dùng để KTV từ chối nhận việc.

Body:

```json
{
  "reason": "Không đúng chuyên môn"
}
```

Kết quả:

- Assignment chuyển sang `REJECTED`.
- Assignment không còn active.
- Ticket tăng `reassignment_count`.
- Ticket tạm dừng auto assignment.
- BQL cần phân lại KTV.

## Frontend

### API Client

File:

```text
frontend/api/backend.api.ts
```

Thêm:

```text
linkCoordinatorDuplicateTicket
(đã xóa 23/08/2026: listCoordinatorDuplicateDisputes, resolveCoordinatorDuplicateDispute)
getAutoAssignmentSettings
updateAutoAssignmentSettings
rejectTechnicianAssignment
(đã xóa 23/08/2026: requestResidentDuplicateDispute)
```

Dùng để frontend gọi các API backend mới.

### Types

File:

```text
frontend/types/api.ts
```

Thêm/mở rộng:

- `ResidentTicket` có duplicate info.
- `CoordinatorTicket` có duplicate info, reassignment info, auto assignment pause info.
- `TechnicianAssignment` có status `REJECTED`.
- ~~Type `DuplicateDispute`.~~ (đã xóa 23/08/2026)
- Type `AutoAssignmentSettings`.

### Trang BQL `/manager/automation`

File:

```text
frontend/app/manager/automation/page.tsx
```

Trang này dùng để:

- Bật/tắt cấu hình phân việc tự động.
- Xem danh sách yêu cầu cư dân báo duplicate bị gộp nhầm.
- BQL quyết định giữ gộp hoặc tách ticket.

Các phần trên UI:

`Phân việc tự động`

- Hiện đang bật/tắt.
- Có nút `Bật` hoặc `Tắt`.
- Hiện tại chỉ lưu cấu hình, chưa tự gán KTV.

~~`Yêu cầu xem lại duplicate`~~ (panel đã gỡ khỏi `/manager/automation` ngày 23/08/2026)

- Hiện các dispute đang `OPEN`.
- Mỗi item có:
  - Mã ticket.
  - Thời gian gửi.
  - Mô tả ticket.
  - Ticket gốc đang bị gộp vào.
  - Nút `Giữ gộp`.
  - Nút `Tách ticket`.

Khi bấm `Giữ gộp`:

- Dispute chuyển sang `KEPT_LINKED`.
- Ticket vẫn là duplicate.

Khi bấm `Tách ticket`:

- Dispute chuyển sang `SPLIT_INDEPENDENT`.
- Ticket được đưa về `NEW`.
- `classification_status` chuyển sang `MANUAL_REVIEW`.
- BQL cần duyệt lại như ticket riêng.

### Sidebar BQL

File:

```text
frontend/components/ManagerNav.tsx
```

Thêm tab:

```text
Tự động phân việc
```

Dẫn tới:

```text
/manager/automation
```

### Màn Chi Tiết Resident

File:

```text
frontend/app/resident/tickets/[id]/page.tsx
```

Đã thêm:

- Banner khi ticket bị gộp duplicate.
- ~~Nút `Sự cố của tôi khác`.~~ (đã gỡ 23/08/2026; thẻ liên kết chỉ còn thông tin)

Khi nào hiện:

- Khi backend trả `duplicate_of_ticket_id`.

Flow:

1. Resident mở chi tiết ticket.
2. Nếu ticket là duplicate, thấy banner.
3. Resident bấm `Sự cố của tôi khác`.
4. Frontend gọi `requestResidentDuplicateDispute`.
5. Nút đổi thành `Đang chờ BQL xem lại`.

### Màn KTV

File:

```text
frontend/app/technician/tickets/[id]/page.tsx
frontend/app/technician/page.tsx
```

Đã thêm:

- Nút `Từ chối` khi assignment đang `ASSIGNED`.
- Nút `Từ chối` khi assignment đang `ACCEPTED`.
- Form nhập lý do từ chối.
- Gọi API `rejectTechnicianAssignment`.
- Danh sách KTV hiểu thêm status `REJECTED`.

Khi dùng:

- KTV được gán nhầm chuyên môn.
- KTV đang quá tải.
- KTV không thể nhận việc nhưng chưa bắt đầu xử lý.

Không dùng khi:

- KTV đã bắt đầu xử lý và phát hiện không xử lý được. Khi đó dùng `Không xử lý được`.

### CSS

File:

```text
frontend/app/globals.css
```

Thêm style cho:

- Card cấu hình auto assignment.
- Danh sách dispute duplicate.
- Banner duplicate ở resident detail.

## Flow Nghiệp Vụ Mới

### Cư dân báo ticket bị gộp nhầm

> **ĐÃ GỠ BỎ (23/08/2026).** Phần dưới đây mô tả luồng kháng nghị duplicate của Cư dân/BQL, đã bị loại bỏ khỏi sản phẩm. Giữ lại vì đây là nhật ký thay đổi theo ngày; **không dùng làm hướng dẫn triển khai**. Xem mục "Cập nhật ngày 23/08/2026" ở cuối tài liệu.

1. Ticket A bị gộp vào Ticket B.
2. Ticket A có status `LINKED_DUPLICATE`.
3. Resident mở Ticket A.
4. UI hiện `Phản ánh đã được gộp`.
5. Resident bấm `Sự cố của tôi khác`.
6. Backend tạo `duplicate_disputes` với status `OPEN`.
7. BQL vào `/manager/automation`.
8. BQL chọn `Giữ gộp` hoặc `Tách ticket`.

### BQL tách ticket duplicate

> **ĐÃ GỠ BỎ (23/08/2026).** Phần dưới đây mô tả luồng kháng nghị duplicate của Cư dân/BQL, đã bị loại bỏ khỏi sản phẩm. Giữ lại vì đây là nhật ký thay đổi theo ngày; **không dùng làm hướng dẫn triển khai**. Xem mục "Cập nhật ngày 23/08/2026" ở cuối tài liệu.

1. BQL bấm `Tách ticket`.
2. Backend đổi dispute sang `SPLIT_INDEPENDENT`.
3. Ticket được xóa `duplicate_of_ticket_id`.
4. Ticket chuyển về `NEW`.
5. Ticket chuyển `classification_status = MANUAL_REVIEW`.
6. BQL xử lý như ticket cần duyệt thủ công.

### KTV từ chối nhận việc

1. BQL phân công ticket cho KTV.
2. KTV mở chi tiết công việc.
3. KTV bấm `Từ chối`.
4. KTV nhập lý do.
5. Backend chuyển assignment sang `REJECTED`.
6. Assignment không còn active.
7. Ticket tăng `reassignment_count`.
8. BQL phân lại KTV khác.

## Phần Đã Chuẩn Bị Nhưng Chưa Chạy Thật

### Auto Assignment

Đã có:

- DB settings.
- API bật/tắt.
- UI bật/tắt.

Chưa có:

- Worker tự động chọn KTV.
- Agent tự đề xuất KTV.
- Queue chạy nền để tự gán.

### Ticket Relations

Đã có:

- Bảng `ticket_relations`.

Chưa có:

- Agent ghi quan hệ.
- UI hiển thị quan hệ chi tiết giữa ticket.

### Agent V4

Chưa sửa:

- `src/agents/llm_client.py`
- `src/agents/nodes.py`
- `src/agents/graph.py`
- `src/agents/state.py`

Nghĩa là:

- Agent hiện vẫn chạy logic cũ.
- Các phần vừa thêm là nền backend/frontend để sau này nối Agent V4.

## Kiểm Tra Đã Chạy

Backend import:

```powershell
.venv\Scripts\python.exe -c "import src.database.models; import src.main; print('backend ok')"
```

TypeScript:

```powershell
node node_modules\typescript\bin\tsc --noEmit
```

Migration current:

```powershell
.venv\Scripts\python.exe -m alembic current
```

Kết quả:

```text
3c4d5e6f7a8b (head)
```

## File Đã Thêm Hoặc Sửa

Backend thêm:

```text
alembic/versions/1a2b3c4d5e6f_add_v4_backend_shell.py
alembic/versions/2b3c4d5e6f7a_add_resident_ticket_rate_limits.py
alembic/versions/3c4d5e6f7a8b_add_v4_timeout_support.py
src/database/models/auto_assignment_setting.py
src/database/models/duplicate_dispute.py  (đã xóa 23/08/2026)
src/database/models/resident_ticket_rate_limit.py
src/database/models/ticket_relation.py
src/services/operational_timeout_service.py
src/services/v4_workflow_service.py
```

Backend sửa:

```text
src/api/routes/coordinator_tickets.py
src/api/routes/technician_assignments.py
src/api/routes/tickets.py
src/database/models/__init__.py
src/database/models/ticket.py
src/database/models/ticket_assignment.py
src/domain/assignment_transitions.py
src/models/api/coordinator.py
src/models/api/technician.py
src/models/api/tickets.py
src/models/api/errors.py
src/models/enums.py
src/repositories/assignment_repository.py
src/repositories/ticket_repository.py
src/services/agent_question_service.py
src/services/agent_result_service.py
src/services/assignment_service.py
src/services/scoring_service.py
src/services/ticket_service.py
```

Frontend thêm:

```text
frontend/app/manager/automation/page.tsx
```

Frontend sửa:

```text
frontend/api/backend.api.ts
frontend/app/globals.css
frontend/app/resident/tickets/[id]/page.tsx
frontend/app/technician/page.tsx
frontend/app/technician/tickets/[id]/page.tsx
frontend/components/ManagerNav.tsx
frontend/types/api.ts
```

Docs thêm:

```text
V4_frontend_backend_changes.md
```

## Lưu Ý Khi Test

### Test KTV từ chối

1. Resident tạo ticket.
2. BQL duyệt ticket.
3. BQL phân công KTV.
4. Đăng nhập KTV.
5. Mở chi tiết công việc.
6. Bấm `Từ chối`.
7. Nhập lý do.
8. Quay lại BQL, ticket cần được phân lại.

### Test duplicate dispute

> **ĐÃ GỠ BỎ (23/08/2026).** Phần dưới đây mô tả luồng kháng nghị duplicate của Cư dân/BQL, đã bị loại bỏ khỏi sản phẩm. Giữ lại vì đây là nhật ký thay đổi theo ngày; **không dùng làm hướng dẫn triển khai**. Xem mục "Cập nhật ngày 23/08/2026" ở cuối tài liệu.

Hiện tại UI chưa có nút link duplicate trực tiếp trong manager detail.

Để test đủ flow cần:

- Gọi API link duplicate bằng tool/API client.
- Sau đó mở resident detail để bấm `Sự cố của tôi khác`.
- BQL xử lý ở `/manager/automation`.

### Test chặn spam tạo phản ánh

1. Đăng nhập một tài khoản resident.
2. Tạo 10 phản ánh hợp lệ trong vòng 1 giờ.
3. Phản ánh thứ 10 vẫn được tạo.
4. Tạo phản ánh tiếp theo.
5. Backend phải trả:

```text
HTTP 429
code = TICKET_CREATE_RATE_LIMITED
```

6. Resident vẫn đăng nhập, xem ticket và nhận thông báo bình thường.
7. Sau thời điểm `blocked_until`, resident có thể tạo phản ánh mới.

### Test timeout cư dân trả lời Agent

1. Tạo ticket khiến Agent hỏi lại cư dân.
2. Không trả lời tới sau `expires_at`.
3. Gọi:

```text
POST /api/v1/coordinator/operational-timeouts/run
```

4. Ticket phải chuyển sang `INVALID`.
5. `invalid_reason` phải là `RESIDENT_RESPONSE_TIMEOUT`.

### Test KTV im lặng quá hạn nhận việc

1. BQL gán ticket đã duyệt cho KTV.
2. Assignment được sinh `acceptance_warning_at` và `acceptance_reassign_at`.
3. Khi tới mốc cảnh báo, gọi endpoint sweep.
4. KTV nhận notification nhắc xác nhận nhận việc.
5. Khi tới mốc đổi người mà KTV vẫn chưa bấm nhận, gọi endpoint sweep.
6. Assignment cũ chuyển sang `REASSIGNED`.
7. Ticket tăng `reassignment_count` và hiện lại cần BQL phân công.

## Tóm Tắt Ngắn

Đã thêm nền V4 cho frontend/backend:

- Duplicate ticket.
- Cư dân khiếu nại gộp nhầm.
- BQL xử lý duplicate dispute.
- KTV từ chối nhận việc.
- Cấu hình phân việc tự động.

Chưa làm:

- Chưa sửa Agent.
- Chưa có worker auto assignment thật.
- Chưa có Agent V4 tự quyết duplicate/assignment.

---

## Cập nhật tiếp theo ngày 20/08/2026: Assignment Proposal / Job Shell

Phần này bổ sung tiếp frontend/backend để gần hơn với tài liệu V4, vẫn **không sửa code Agent**.

### Migration mới

File:

```text
alembic/versions/4d5e6f7a8b9c_add_assignment_proposal_shell.py
```

Đã chạy lên DB Supabase. Revision hiện tại:

```text
4d5e6f7a8b9c (head)
```

Migration này thêm metadata duplicate, bảng job phân việc, bảng batch đề xuất, item đề xuất, member của item/job, và metadata assignment để truy ngược assignment được tạo từ proposal/job nào.

### Metadata duplicate mới trong `tickets`

File:

```text
src/database/models/ticket.py
```

Các cột mới:

```text
duplicate_linked_at
duplicate_reason
duplicate_analysis_run_id
duplicate_disputed_at  (đã xóa 23/08/2026)
```

Dùng để lưu ticket được gộp duplicate lúc nào, vì lý do gì, do analysis run nào đề xuất, và cư dân đã khiếu nại gộp nhầm lúc nào. Vì chưa sửa Agent nên `duplicate_analysis_run_id` hiện là chỗ chờ nối sau; luồng BQL gộp thủ công đã ghi `duplicate_linked_at` và `duplicate_reason`.

### Metadata assignment mới

File:

```text
src/database/models/ticket_assignment.py
src/repositories/assignment_repository.py
src/services/assignment_service.py
src/services/operational_timeout_service.py
```

Các cột mới:

```text
assignment_job_id
rejected_at
end_reason
```

Dùng để:

- `assignment_job_id`: truy ngược assignment được tạo từ job/proposal nào.
- `rejected_at`: lưu thời điểm KTV từ chối.
- `end_reason`: chuẩn hóa lý do assignment kết thúc như `COMPLETED`, `REJECTED_BY_TECHNICIAN`, `UNABLE_TO_HANDLE`, `ACCEPTANCE_TIMEOUT`.

Luồng assign thủ công vẫn là `MANUAL`. Khi duyệt bảng đề xuất, assignment được tạo với `assignment_source = AI_PROPOSAL_CONFIRMED`.

### Bảng proposal/job mới

File:

```text
src/database/models/assignment_proposal.py
```

Các bảng:

```text
ai_assignment_jobs
ai_assignment_job_members
assignment_proposal_batches
assignment_proposal_items
assignment_proposal_item_members
```

Ý nghĩa:

- `ai_assignment_jobs`: lưu job phân việc theo shape V4, gồm mode, trạng thái, ticket/cụm, KTV được chọn, deadline, fallback và lỗi nếu có.
- `ai_assignment_job_members`: lưu các ticket thuộc một job. Hiện ticket đơn có 1 member; cụm sau này có nhiều member.
- `assignment_proposal_batches`: một bảng đề xuất để BQL xem và duyệt.
- `assignment_proposal_items`: từng dòng đề xuất trong batch, gồm ticket/cụm, KTV đề xuất, lý do và trạng thái.
- `assignment_proposal_item_members`: các ticket thuộc một item, chuẩn bị cho item dạng cụm.

### Service mới

File:

```text
src/services/assignment_proposal_service.py
```

Service này làm 4 việc:

1. `create_batch`: lấy các ticket đủ điều kiện (`APPROVED`, `RESOLVED`, có category, chưa duplicate, không pause auto assignment, chưa có assignment active), sau đó chọn KTV active/available có skill phù hợp. Nếu nhiều KTV phù hợp thì chọn người có ít assignment active nhất. Đây là logic backend deterministic để test flow khi chưa sửa Agent.
2. `list_batches`: trả danh sách bảng đề xuất, ưu tiên batch `READY` lên trước và mới nhất lên trên.
3. `confirm_batch`: duyệt cả bảng đề xuất, tạo assignment, gắn `assignment_job_id`, set deadline nhận việc theo P1/P2/P3, gửi thông báo cho KTV và cư dân. Nếu ticket đã được assign thủ công trước khi duyệt batch thì item chuyển thành `SKIPPED_MANUAL_WON`.
4. `cancel_batch`: hủy bảng đề xuất đang chờ.

### API mới cho Manager

File:

```text
src/api/routes/coordinator_tickets.py
src/models/api/coordinator.py
```

Endpoint mới:

```text
GET  /api/v1/coordinator/assignment-proposals
POST /api/v1/coordinator/assignment-proposals
GET  /api/v1/coordinator/assignment-proposals/{batch_id}
POST /api/v1/coordinator/assignment-proposals/{batch_id}/confirm
POST /api/v1/coordinator/assignment-proposals/{batch_id}/cancel
```

Manager có thể tạo bảng đề xuất, xem danh sách, duyệt cả bảng để phân công KTV, hoặc hủy bảng.

### Frontend mới trên màn Automation

File:

```text
frontend/app/manager/automation/page.tsx
frontend/app/globals.css
frontend/api/backend.api.ts
frontend/types/api.ts
```

Đã thêm dropdown chọn thời điểm auto assignment (`IMMEDIATE`, `2H`, `5H`, `1D`, `3D`), checkbox `Tiếp tục tự động sau duyệt`, nút `Tạo đề xuất`, danh sách bảng đề xuất, nút `Duyệt và phân công`, nút `Hủy`.

Mỗi bảng hiển thị ticket, ưu tiên, vị trí, KTV được đề xuất và trạng thái item. UI dùng lại phong cách dashboard hiện có: `ManagerSurface`, `tableAction`, `button`, `badge`, border/card nhẹ; không đổi cấu trúc layout chính.

### Phần vẫn chưa đủ nếu không sửa Agent

Các phần sau đã có chỗ lưu và API/UI để thao tác, nhưng chưa thể đúng hoàn toàn tài liệu nếu không nối Agent/worker:

- Agent tự sinh assignment proposal thật từ reasoning.
- Agent tự ranking KTV theo thuật toán đầy đủ.
- Job `DIRECT` tự chạy nền sau khi hết delay.
- Fallback assignment tự chạy sau primary deadline.
- Proposal cho cụm ticket do Agent tạo trực tiếp.
- `duplicate_analysis_run_id` tự gắn từ AI analysis run.

Nói ngắn gọn: frontend/backend đã có đường ray để lưu, xem và duyệt proposal; hiện proposal được backend tạo deterministic từ dữ liệu DB để test flow; muốn đúng 100% V4 thì bước sau cần nối Agent/worker vào các bảng này.

### Kiểm tra đã chạy

```powershell
.venv\Scripts\python.exe -c "import src.database.models; import src.main; print('backend ok')"
node node_modules\typescript\bin\tsc --noEmit
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current
```

Kết quả:

- Backend import OK.
- TypeScript OK.
- Alembic đang ở `4d5e6f7a8b9c (head)`.

## Cập nhật ngày 23/08/2026: gỡ kháng nghị duplicate và siết phạm vi hiển thị

Bản cập nhật này làm hai việc, và **giữ nguyên toàn bộ phần phát hiện/liên kết
duplicate**.

### 1. Gỡ bỏ luồng kháng nghị duplicate

Đã xóa khỏi code, contract, tài liệu và test:

- Endpoint Cư dân `POST /api/v1/tickets/{id}/duplicate-review` và
  `POST /api/v1/tickets/{id}/duplicate-dispute` — xóa hẳn, không giữ alias
  deprecated.
- Endpoint BQL `GET /api/v1/coordinator/duplicate-disputes` và
  `POST /api/v1/coordinator/duplicate-disputes/{id}/resolve`.
- Schema `DuplicateDisputeRequest`, `DuplicateDisputeCreatedResponse`,
  `DuplicateDisputeResponse`, `DuplicateDisputeResolveRequest`.
- Action `DISPUTE_DUPLICATE` trong `available_actions`, và field
  `duplicate_dispute_status` trong response Cư dân lẫn Điều phối viên.
- `DuplicateWorkflowService.request_dispute` / `list_disputes` /
  `resolve_dispute` / `open_dispute_status_for_ticket`.
- Enum `DuplicateDisputeStatus`, model `DuplicateDispute`, quan hệ
  `Ticket.duplicate_disputes`, cột `tickets.duplicate_disputed_at`.
- Frontend: `requestResidentDuplicateDispute`,
  `listCoordinatorDuplicateDisputes`, `resolveCoordinatorDuplicateDispute`,
  type `DuplicateDispute`, nút `Sự cố của tôi khác`, và panel
  `Yêu cầu xem lại duplicate` trên `/manager/automation`. Cấu hình auto
  assignment và bảng đề xuất phân việc trên trang đó giữ nguyên; các class CSS
  dùng chung được đổi tên thành `managerProposalList` / `managerProposalCard`.

**Giữ nguyên:** tra cứu và phán đoán duplicate của `src/agents/v4`, nhánh
`DUPLICATE_EXISTING` và `DUPLICATE_UNCERTAIN`, trạng thái `LINKED_DUPLICATE`,
`duplicate_of_ticket_id` / `duplicate_linked_at` / `duplicate_reason` /
`duplicate_analysis_run_id`, hai check constraint duplicate, chuẩn hóa master
canonical, dữ liệu master rút gọn, ticket liên kết đi theo vòng đời của master,
thông báo `TICKET_LINKED_AS_DUPLICATE`, và endpoint liên kết duplicate thủ công
`POST /api/v1/coordinator/tickets/{id}/duplicate-link` — đó là *liên kết*, không
phải kháng nghị.

### 2. Giai đoạn AI riêng tư

Ticket chỉ người gửi thấy khi:

```text
classification_status IN (PENDING, PROCESSING)
```

Khoảng này gồm cả lúc Agent đang phân tích lẫn lúc đang chờ Cư dân trả lời câu
hỏi bổ sung. Khi phân loại kết thúc (`RESOLVED`, `MANUAL_REVIEW`, `FAILED`, hoặc
kết thúc invalid), ticket được công bố: chia sẻ cho các tài khoản còn hoạt động
trong cùng căn hộ và bàn giao cho BQL. Không thêm cờ "published" riêng — công bố
được suy ra từ `classification_status`.

Thực thi:

- `src/services/ticket_visibility.py` giữ quy tắc một chỗ: một vị từ SQL cho
  truy vấn danh sách và một hàm Python cho một dòng đã load.
- Danh sách Cư dân nhận `actor.user.user_id` chứ không chỉ `unit_id`; vị từ chạy
  cùng các filter khác **trước** `count`, `offset`, `limit`.
- `list_coordinator_tickets` loại `PENDING`/`PROCESSING` trước count và phân
  trang.
- Chi tiết ticket, signed URL ảnh (Cư dân và BQL) và endpoint câu hỏi AI dùng
  đúng quy tắc đó, nên không đi vòng bằng URL trực tiếp được.
- Hủy ticket và cả hai endpoint câu hỏi AI chỉ dành cho người gửi, kiểm tra ở
  backend. `available_actions` nhận biết người gọi nhưng chỉ là gợi ý UI.
- Đọc không được phép trả về not-found (không phải forbidden).
- Đọc nội bộ của Agent/worker không bị lọc: `get_coordinator_ticket` và
  `get_resident_ticket` giữ nguyên phạm vi, các biến thể `*_visible_*` mới là
  đường dành cho API người dùng.

### Migration mới

| Revision | Nội dung |
| --- | --- |
| `7a8b9c0d1e2f` | Xóa bảng `duplicate_disputes` và cột `tickets.duplicate_disputed_at`. Downgrade dựng lại cấu trúc (không khôi phục dữ liệu). |
| `8b9c0d1e2f3a` | Đồng bộ RLS: policy SELECT của `tickets`, `ticket_attachments`, `ticket_status_history` theo giai đoạn AI riêng tư; `ai_agent_questions` vẫn chặn truy cập client trực tiếp. |

Các revision v4 cũ (`1a2b3c4d5e6f`, `4d5e6f7a8b9c`, `5e6f7a8b9c0d`) và các
migration policy cũ là bất biến, không sửa; hai revision trên là migration tiến
để sửa.
