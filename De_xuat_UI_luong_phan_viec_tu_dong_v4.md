# Đề xuất UI flow — Phân việc tự động V4

Ngày lập: 24/08/2026  
Phạm vi: giao diện Điều phối viên (BQL) cho phân việc tự động, bảng đề xuất, theo dõi gán thẳng và tái phân công.

## 1. Căn cứ và quyết định áp dụng

Luồng này bám theo thứ tự ưu tiên nêu trong `agent_backend_contract_v4.md`: đặc tả nghiệp vụ V4, contract V4, rồi mới tới code hiện hữu. Bản cập nhật ngày 23/08 trong `V4_frontend_backend_changes.md` được dùng để loại bỏ các nội dung lịch sử đã bị gỡ.

- Không có UI kháng nghị duplicate cho Cư dân/BQL. Thẻ duplicate chỉ hiển thị thông tin; BQL xử lý liên kết sai bằng thao tác duyệt/điều chỉnh thông thường.
- Tự động **bật** và **tắt** là trạng thái toàn cục. Việc lỗi/no-candidate chỉ đưa *ticket cụ thể* về hàng phân tay và pause riêng ticket, không được tự tắt công tắc.
- Khi công tắc đang tắt, hành động bật không được gán ngầm: phải mở bảng đề xuất tối đa 20 ticket/case, hết hạn sau 10 phút, và chỉ gán sau khi BQL xác nhận.
- Khi công tắc đã bật, ticket đủ điều kiện đi theo chế độ `DIRECT`: hệ thống gán thẳng sau delay đã cấu hình; P3 luôn bỏ qua delay. Không có bước BQL duyệt lại kết quả DIRECT.
- BQL luôn có quyền phân tay. Phân tay thắng race với AI.

## 2. Đích giao diện

Giữ một không gian điều phối duy nhất tại `/manager?view=assignment` (alias `/manager/automation` tiếp tục chuyển về đây), không tách người dùng qua một trang cấu hình riêng. Màn hình có ba vai trò rõ ràng: biết hệ thống đang ở chế độ nào, xử lý hàng cần con người, và duyệt một bảng AI đề xuất khi BQL chủ động bật tự động.

```text
Điều phối ticket
├─ Cảnh báo P3 bắt buộc xử lý (nếu có)
├─ Thanh trạng thái tự động [Đang tắt | Đang bật + delay] [hành động đúng ngữ cảnh]
├─ Hàng điều phối
│  ├─ Cần duyệt phân loại
│  ├─ Cần phân tay / auto bị pause
│  └─ DIRECT đang chờ hoặc đang chạy
├─ Bảng đề xuất đang mở (chỉ xuất hiện khi BQL khởi tạo lúc auto tắt)
└─ Lịch sử proposal và job gần đây
```

Không dùng màu sắc đơn lẻ để diễn tả trạng thái: mọi badge có chữ, thời gian đếm ngược và action được đặt cạnh đúng dòng ticket/job.

## 3. Luồng chính

### A. BQL bật auto khi đang tắt — `PROPOSAL`

1. Thanh trạng thái hiển thị `Tự động đang tắt`. CTA duy nhất là **Tạo bảng đề xuất để bật**; không có switch có thể chuyển ngay sang bật.
2. Sau khi bấm, giao diện khóa CTA tạo thêm đợt và hiện `Đang tạo đề xuất`. Batch có trạng thái `BUILDING`; không có assignment hoặc notification nào được tạo ở giai đoạn này.
3. Khi batch `READY`, hiển thị timer 10 phút, tổng số work item/ticket và danh sách theo KTV. Các dòng được sắp Priority giảm dần, rồi thời điểm gửi tăng dần.
4. Mỗi dòng hiển thị: mã ticket/case, các member của case (tối đa 5), vị trí, Category, Priority, KTV AI đề xuất, lý do ngắn, tải hiện tại và trạng thái (`PROPOSED`, `EMPTY`, `DESELECTED`, `SKIPPED_MANUAL_WON`). Một case là một work item và không bị tách để lấp chỗ trong batch.
5. BQL có thể bỏ chọn dòng hoặc chuyển dòng sang bất kỳ **KTV đang hoạt động** nào, kể cả người không xuất hiện trong snapshot/bảng đề xuất của AI. UI gắn nhãn **BQL bổ sung** cho lựa chọn này và yêu cầu xác nhận ngắn; không mô tả sai đây là đề xuất AI. KTV không hoạt động tuyệt đối không xuất hiện trong danh sách và Backend cũng phải từ chối nếu client gửi ID đó.
6. BQL chọn tùy chọn **Tiếp tục tự động phân việc cho ticket mới** và một delay hợp lệ chỉ khi muốn bật global toggle sau batch.
7. Bấm **Duyệt và phân công**: modal tóm tắt số ticket sẽ giao, số dòng bỏ trống/bỏ chọn/đã bị phân tay, và trạng thái auto sau xác nhận. Confirm mới tạo assignment. Audit actor là Điều phối viên.
8. Hủy, đóng, hoặc hết 10 phút: batch `CANCELLED`/`EXPIRED`, không tạo assignment và auto vẫn tắt. Hết hạn phải tạo batch mới, không dùng snapshot cũ.

### B. Auto đang bật — `DIRECT`

1. Thanh trạng thái hiển thị `Đang bật · <delay>` và CTA **Tắt tự động**. Không đưa ra nút tạo proposal vì proposal chỉ dành cho lúc bật từ trạng thái tắt.
2. Ticket đã duyệt xuất hiện trong vùng `Đang chờ tự động` với thời điểm dự kiến chạy. P3 hiển thị `Ưu tiên P3 · gán ngay`, không hiển thị delay cấu hình.
3. Khi job `DIRECT` chạy, UI chỉ hiển thị tiến độ (`Đang chọn KTV`/`Đang dùng phương án dự phòng`), không cho BQL sửa kết quả AI.
4. Khi gán thành công, ticket biến mất khỏi hàng này và hiện nguồn `AI gán tự động` trong chi tiết/timeline.
5. `NO_SUITABLE_CANDIDATE` hoặc lỗi sau fallback chuyển ticket vào `Cần phân tay`, kèm lý do đã làm sạch và badge `Tự động tạm dừng cho ticket này`. Công tắc toàn cục vẫn hiển thị bật.

### C. Từ chối hoặc im lặng — tái phân công

1. Ticket detail/timeline hiện lý do KTV từ chối, số lần đổi người và người vừa rời assignment. Không gộp “Từ chối” với “Không xử lý được”.
2. Nếu P1/P2, auto bật, và chưa vượt trần: tạo job `SCHEDULED_GRACE`; workspace hiển thị countdown 5 phút, lý do từ chối, nút **Hủy lượt AI và phân tay**. BQL có thể phân tay trực tiếp; thành công sẽ hiển thị `Phân tay đã thắng` và job thành `CANCELLED_MANUAL_WON`.
3. Với P3: nếu chưa chạm trần và auto bật, job chạy ngay; không có cửa sổ 5 phút.
4. Nếu KTV im lặng quá hạn, hoặc auto tắt, hoặc số lần đổi đã thành 4: ticket vào `Cần phân tay`. Khi đã chạm trần, badge/action phải có độ nổi bật cao vì đây là thao tác bắt buộc, không chỉ là số liệu.

## 4. Cấu trúc màn hình và nội dung từng vùng

| Vùng | Nội dung bắt buộc | Hành động |
| --- | --- | --- |
| Cảnh báo P3 | Số P3 đang chờ duyệt/xử lý, ticket ưu tiên nhất, lý do khóa thao tác | Mở ticket để duyệt/override; chặn action không liên quan theo đặc tả |
| Thanh trạng thái auto | Trạng thái global, delay, lần cập nhật gần nhất; không hiển thị pause riêng ticket như thể global đang tắt | Tạo proposal để bật (khi tắt), hoặc tắt auto (khi bật) |
| Cần duyệt phân loại | Ticket `MANUAL_REVIEW`, không trộn với hàng phân việc | Mở chi tiết để chốt Category/Severity rồi approve riêng |
| Cần phân tay | `APPROVED` chưa gán khi auto tắt, `MANUAL_REQUIRED`, ticket pause/no-candidate/fallback lỗi, đã chạm trần đổi người | Mở ticket → phân tay; lý do và job liên quan dễ thấy |
| DIRECT đang chờ/chạy | Ticket/job có `SCHEDULED_GRACE`, `PRIMARY_RUNNING`, `FALLBACK_RUNNING`; thời điểm chạy và trigger | Với grace P1/P2: hủy job để phân tay; ngoài grace chỉ xem trạng thái |
| Proposal board | Một active batch `BUILDING`/`READY`, từng work item/case, TTL, snapshot/AI reason, các trạng thái dòng | Bỏ/chọn lại, đổi KTV, xác nhận/hủy |
| Lịch sử | Batch/job `CONFIRMED`, `CANCELLED`, `EXPIRED`, `COMPLETED`, `MANUAL_REQUIRED` gần đây | Xem read-only để audit; không khôi phục batch hết hạn |

## 5. Điều chỉnh trên UI hiện có

Nền proposal hiện tại ở `frontend/components/manager/AssignmentWorkspace.tsx` đã có các phần đúng hướng: polling batch `BUILDING`, timer 10 phút, bỏ/chọn lại, kéo/đổi KTV, checkbox tiếp tục auto và optimistic version khi confirm. Đề xuất giữ lại cấu trúc này và bổ sung:

1. Tách rõ ba hàng `Cần duyệt phân loại` / `Cần phân tay` / `DIRECT đang chờ-chạy`; không dùng riêng danh sách `APPROVED chưa gán` như một hàng duy nhất.
2. Thêm client state/API hiển thị assignment jobs, job cancel, trigger, `execute_after`, lý do lỗi và ticket/case members. Chỉ hiển thị action cancel trong cửa sổ grace hợp lệ.
3. Proposal item dạng case cần hiển thị toàn bộ member, không chỉ `ticket_id` đầu tiên; tổng ticket member phải kiểm soát giới hạn 20.
4. Roster chọn KTV cần phân biệt `AI candidate snapshot` (đủ active/available/skill ở thời điểm AI xét) với nhóm **KTV đang hoạt động do BQL bổ sung**. BQL có thể chọn người ở nhóm thứ hai; không được làm người dùng tưởng mọi KTV trong select là AI đã xét phù hợp.
5. Bổ sung banner P3 khóa thao tác, màn hình/manual-required và nhãn action bắt buộc khi reassignment count vượt 3.
6. Ticket detail bổ sung lý do reject, trạng thái/direct job gần nhất, thời điểm AI sẽ chạy và nút hủy job khi còn grace. Các metadata nguồn gán, reassignment count và pause hiện đã có là nền tốt.
7. Tại dashboard, sắp ticket mặc định theo Priority giảm dần rồi thời gian gửi tăng dần theo đặc tả; hiện UI đang sort thời gian giảm dần.

## 6. Các contract/API cần UI tiêu thụ

Không tự suy luận trạng thái từ client. UI phải dùng dữ liệu backend có authority:

- Settings: `GET/PATCH /coordinator/auto-assignment-settings`; UI không gọi PATCH `enabled=true` để lách proposal flow.
- Proposal: list/create/get/patch item/confirm/cancel. Confirm gửi `expected_version`, `continue_auto_assignment` và `activation_delay`.
- Job: list và `POST /coordinator/assignment-jobs/{job_id}/cancel`; dữ liệu cần đủ `mode`, `status`, `trigger`, work item/member, `execute_after`, selected KTV, error/reason và deadline.
- Ticket detail/list: `auto_assignment_paused`, pause reason, `reassignment_count`, assignment active/source, lịch sử reject/timeout và case membership.

Mọi lỗi `PROPOSAL_EXPIRED`, `PROPOSAL_NOT_READY`, `ACTIVE_ASSIGNMENT_EXISTS`, `NO_CANDIDATES` phải được đổi thành chỉ dẫn có thể hành động trong UI, tuyệt đối không lộ raw model error/prompt/stack trace.

## 7. Tiêu chí nghiệm thu UI

1. Khi tắt auto và có hàng chờ, BQL không thể bật/gán trực tiếp; chỉ có proposal và confirm trong TTL mới tạo assignment.
2. Proposal có thể chứa ticket và case, tối đa 20 ticket, không cắt case; dòng lỗi/empty không chặn các dòng khác.
3. Confirm sau 10 phút bị chặn rõ ràng và không bật auto; cancel/close không phát notification và không đổi assignment.
4. Khi auto bật, P3 bỏ qua delay; BQL vẫn phân tay được trước lúc AI gán và quyết định tay không bị ghi đè.
5. P1/P2 từ chối có countdown grace + cancel/assign handoff; P3 không có grace; lần đổi thứ tư dẫn vào manual-required rõ ràng.
6. UI phân biệt đúng: hệ thống gán thẳng, AI đề xuất đã được BQL duyệt, và BQL phân tay.
7. Không có nút/panel kháng nghị duplicate.

## 8. Điểm cần chốt trước khi tạo prompt triển khai

1. **Luồng duplicate:** chốt dùng bản cập nhật mới hơn: không kháng nghị duplicate. Mục 2.3 của đặc tả nghiệp vụ còn một bullet cũ phải được xem là obsolete hoặc sửa tài liệu trước khi code.
2. **Bật/tắt toggle API:** xác nhận frontend sẽ chỉ bật global auto qua confirm proposal, còn `PATCH enabled=true` không được expose như một switch trực tiếp. Nếu cần giữ endpoint cho vận hành nội bộ, phải hạn chế bằng policy/backend guard.
3. **Override KTV trong proposal — ĐÃ CHỐT:** BQL được chọn bất kỳ KTV đang hoạt động, kể cả người không nằm trong snapshot của AI; UI đánh dấu đây là lựa chọn BQL bổ sung và Backend từ chối KTV không hoạt động.
4. **P3 blocking — NGOÀI PHẠM VI:** không triển khai trong đợt UI phân việc này.
5. **Hiển thị job/case — ĐÃ CHỐT:** bổ sung/tận dụng response job để UI có trigger, `execute_after`, member ticket/case, lý do và trạng thái cần thiết, trong phạm vi phân việc và không ảnh hưởng luồng khác.

Sau khi năm điểm trên được chấp thuận, prompt triển khai sẽ chia chính xác theo frontend/backend, migration/API cần thiết, trạng thái lỗi và test acceptance, không yêu cầu Agent tự diễn giải lại nghiệp vụ.
