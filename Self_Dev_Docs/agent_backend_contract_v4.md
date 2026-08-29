# Backend Contract: AI Agent ↔ Backend (v4 hoàn chỉnh)

> Tài liệu này là **contract đích cần triển khai**, được hợp nhất từ nghiệp vụ hiện hành và code base tại thời điểm rà soát. Nội dung nào ghi **Delta code** nghĩa là code hiện tại chưa có hoặc chưa đúng; đó là yêu cầu triển khai, không phải mô tả nhầm rằng hệ thống đã hỗ trợ.

## 0. Phạm vi và thứ tự ưu tiên

Contract bao phủ hai mặt gọi AI độc lập:

| | Mặt A — Phân tích ticket | Mặt B — Phân việc |
| --- | --- | --- |
| Thời điểm | Ngay sau khi Backend đã tạo bản ghi ticket | Sau khi ticket đã được duyệt và đủ điều kiện phân việc |
| AI làm gì | Trích xuất Category, red-flag, severity; tìm phản ánh trùng; đề xuất gộp case | Chọn một Kỹ thuật viên từ danh sách Backend đã lọc |
| Ai quyết định nghiệp vụ | Backend quyết định Category chính thức, điểm và Priority | AI được quyết định người nhận việc trong đúng danh sách đầu vào |
| Tool/session | Có session và tool, ngân sách tối đa 5 tool call | Không tool, không dùng session phân tích |
| Bộ máy quyết định | Luôn là LLM | Mặc định `RULE_ENGINE_V1` (rule-base, không gọi model); `AI` là tùy chọn dự phòng |
| Timeout | Tổng thời gian chờ Cư dân tối đa 300 giây | `RULE_ENGINE_V1`: tức thời. `AI`: model chính tối đa 300 giây, fallback tối đa 300 giây tiếp theo |

Thứ tự ưu tiên khi có khác biệt:

1. Tài liệu nghiệp vụ `dac_ta_tinh_nang_luong_nghiep_vu_v4.md` đã được chốt.
2. Contract này.
3. Code hiện tại. Code khác contract thì phải sửa code hoặc migration; không được âm thầm đổi nghiệp vụ để khớp code cũ.

Các nguyên tắc xuyên suốt:

- Text mô tả là bắt buộc; ảnh là tùy chọn.
- Mọi lượt gửi hợp lệ ở tầng API đều được tạo `Ticket` trước khi chạy Agent, kể cả sau đó bị xác định là phản ánh trùng hoặc không hợp lệ.
- Red-flag được xử lý trước duplicate và trước gộp case.
- AI phân tích không tự chốt Priority, điểm số hoặc Category chính thức.
- Bộ máy phân việc — dù là rule-base hay LLM — chỉ được chọn trong snapshot ứng viên do Backend gửi vào.
- Mọi schema ở ranh giới AI dùng `extra = forbid`; field thừa hoặc enum lạ là lỗi contract.
- Thời gian dùng UTC/ISO 8601; UUID dùng chuỗi chuẩn; mọi thao tác ghi phải có idempotency key.

---

## 1. Mặt A — vòng phân tích ticket

### 1.1. Session và ngân sách

Backend tạo một `AIAnalysisSession` cho một `ticket_id`. Session ghim `category_catalog_version` ngay lần đọc danh mục đầu tiên.

Ngân sách cứng:

- Tối đa `5` lần gọi tool có tính phí.
- Tối đa `3` lượt hỏi Cư dân.
- Tổng thời gian chờ câu trả lời của Cư dân là `300` giây cho cả session, không reset theo lượt hỏi.
- `get_category_catalog` không tính vào 5 lần; ba tool còn lại đều tính.
- Chỉ session `RUNNING` mới gọi tool hoặc finalize được.
- `ticket_id` trong request phải đúng ticket của session.

### 1.2. Sáu điểm thoát

`AgentExitReasonV4` không cho Agent tự kết luận Category ảnh/text có khớp hay không. V4 bỏ `CONFIDENT_MATCH` và `CATEGORY_MISMATCH` của V3, thay bằng `ANALYSIS_COMPLETE`; đồng thời bổ sung `DUPLICATE_EXISTING` và `DUPLICATE_UNCERTAIN`:

| `exit_reason` | Ý nghĩa | Backend xử lý |
| --- | --- | --- |
| `RED_FLAG` | Có dấu hiệu nguy hiểm ở text hoặc ảnh | Ép nhánh khẩn cấp/P3; không tự liên kết duplicate |
| `DUPLICATE_EXISTING` | Chắc chắn cao đây là cùng một sự cố chung đang hoạt động | Liên kết ticket mới với ticket gốc; không tạo assignment riêng |
| `DUPLICATE_UNCERTAIN` | Có ứng viên liên quan nhưng chưa đủ chắc chắn để tự liên kết hoặc loại trừ | `classification_status = MANUAL_REVIEW` |
| `ANALYSIS_COMPLETE` | Agent đã trích xuất đủ dữ liệu có cấu trúc và không thuộc các điểm thoát đặc biệt khác | Backend tự đối chiếu Category ảnh/text; hoặc chốt Category/tính điểm, hoặc chuyển `MANUAL_REVIEW` |
| `LIMIT_REACHED` | Chạm ngân sách mà vẫn chưa đủ tự tin | `classification_status = MANUAL_REVIEW` |
| `INSUFFICIENT_INPUT` | Text/ảnh còn lại không đủ hiểu vấn đề | Đóng ticket `INVALID`; tính vào bộ đếm AI từ chối theo §8.2 |

### 1.3. `AgentAnalysisResultV4` — Agent → Backend

Giữ nguyên các field phù hợp của `AgentAnalysisResultV3`, bổ sung object `duplicate`, `red_flag_relation` và hai exit mới; riêng `grouping.density` được loại khỏi payload cuối vì thuộc quyền tính của Backend:

| Field | Kiểu | Bắt buộc | Ràng buộc |
| --- | --- | --- | --- |
| `ticket_id` | uuid | Có | Ticket của session |
| `analysis_session_id` | uuid | Có | Session đang `RUNNING` |
| `exit_reason` | enum §1.2 | Có | |
| `text_categories` | uuid[]/null, tối đa 20 | Trừ `INSUFFICIENT_INPUT` | Độc lập với ảnh |
| `red_flag_text` | bool | Có | Mặc định `false` |
| `image_categories` | uuid[]/null, tối đa 20 | Theo việc có ảnh | Null khi không có ảnh |
| `red_flag_signal` | bool/null | Theo việc có ảnh | Null cùng nhóm ảnh |
| `is_relevant` | bool/null | Theo việc có ảnh | Null cùng nhóm ảnh |
| `severity` | `LOW\|MEDIUM\|HIGH`/null | Trừ `INSUFFICIENT_INPUT` | |
| `severity_source` | `IMAGE\|TEXT`/null | Trừ `INSUFFICIENT_INPUT` | |
| `is_confident` | bool | Có | Duplicate tự động bắt buộc `true` |
| `confidence_notes` | string/null, tối đa 500 | Có điều kiện | Bắt buộc với `DUPLICATE_UNCERTAIN`; các nhánh khác dùng cho audit, không dùng thay reason |
| `grouping` | object §1.4/null | Không | Không được đồng thời với duplicate |
| `duplicate` | object §1.5/null | Có điều kiện | Chỉ có với `DUPLICATE_EXISTING` |
| `red_flag_relation` | object §1.5a/null | Không | Chỉ dùng với `RED_FLAG` khi có cùng sự cố đang hoạt động |
| `tool_usage` | object §1.6 | Có | Backend đối chiếu với session thực |
| `category_catalog_version` | string 1–128 | Có | Phải đúng bản ghim |
| `model_version` | string 1–100 | Có | |
| `analyzed_at` | ISO 8601 | Có | Không được nằm quá xa thời điểm server nhận |

`text_understandable` có thể tồn tại trong `ExtractionResult` hoặc `AgentState` nội bộ để graph chọn nhánh `INSUFFICIENT_INPUT`, nhưng **không** phải field của `AgentAnalysisResultV4`. Vì payload cuối dùng `extra = forbid`, Agent không được gửi field nội bộ này sang Backend.

Ví dụ kết quả duplicate:

```json
{
  "ticket_id": "2c7ab4df-6b5e-4d83-9be0-36178f63983c",
  "analysis_session_id": "519f758a-367a-466e-a699-1f404987df23",
  "exit_reason": "DUPLICATE_EXISTING",
  "text_categories": ["9f37e2e9-c38d-4fa5-80cb-b51d59f951d6"],
  "red_flag_text": false,
  "image_categories": null,
  "red_flag_signal": null,
  "is_relevant": null,
  "severity": "HIGH",
  "severity_source": "TEXT",
  "is_confident": true,
  "confidence_notes": "Cùng thang máy và cùng hiện tượng dừng hoạt động.",
  "grouping": null,
  "duplicate": {
    "master_ticket_id": "5bf1677c-1698-4319-aeaf-69369e9164a9",
    "reason": "Cùng thang máy A, cùng hiện tượng dừng giữa tầng và ticket gốc đang xử lý."
  },
  "red_flag_relation": null,
  "tool_usage": {
    "total_tool_calls": 1,
    "ask_resident_rounds": 0,
    "ask_resident_elapsed_seconds": 0,
    "search_related_tickets_called": true,
    "propose_case_grouping_called": false
  },
  "category_catalog_version": "2026-08-19T00:00:00Z",
  "model_version": "analysis-model-v4",
  "analyzed_at": "2026-08-19T08:30:00Z"
}
```

### 1.4. `grouping`

```json
{
  "grouped": true,
  "related_ticket_ids": ["uuid"],
  "reason": "string 1..300"
}
```

- Chỉ áp dụng cho rò nước và chập điện.
- Ticket cùng tòa, tầng liền kề, trong tối đa 3 ngày.
- Agent không gửi `density`. Backend lấy Density từ kết quả `propose_case_grouping` đã ghi trong tool log và tính/xác thực lại theo **căn hộ riêng biệt**, không theo tài khoản hay số ticket.
- Mỗi ticket vẫn là ticket nghiệp vụ độc lập; đây không phải duplicate.

### 1.5. `duplicate`

```json
{
  "master_ticket_id": "uuid",
  "reason": "string 1..500"
}
```

Điều kiện bắt buộc:

1. `exit_reason = DUPLICATE_EXISTING`, `is_confident = true`, `grouping = null`.
2. Không có red-flag mới, không có dấu hiệu xấu đi đáng kể và không có bằng chứng cần xử lý riêng.
3. `master_ticket_id` phải xuất hiện trong kết quả `search_related_tickets(purpose=DUPLICATE)` của chính session.
4. Ticket gốc vẫn hoạt động: chờ duyệt, đã duyệt, đã phân người hoặc đang xử lý.
5. Cùng Category hoặc nhóm vấn đề tương đương; cùng tòa và **cùng chính xác tài sản/vị trí chung**.
6. Cùng hiện tượng. Chỉ cùng Category hoặc cùng tòa là không đủ.
7. Ticket gốc không được là một ticket duplicate khác. Nếu ứng viên là duplicate, Backend chuẩn hóa về master cuối cùng trước khi trả cho Agent.

`master_ticket_id` cố ý là một UUID, không phải mảng: quan hệ duplicate là nhiều ticket phản ánh trùng cùng trỏ về một master chuẩn (N→1). Nếu search tìm thấy nhiều ticket cùng sự cố, toàn bộ candidate/evidence được giữ trong tool-call log; Backend chuẩn hóa chúng về master cuối cùng trước khi Agent kết luận. Không đưa `matched_ticket_ids` vào payload finalize để tránh tạo nhiều nguồn sự thật cho quan hệ duplicate.

`locations.id` hiện có được dùng làm khóa định danh tài sản/vị trí. Với tài sản có nhiều cá thể, ví dụ thang máy A và B, dữ liệu Location phải có record riêng. Nếu vị trí hiện tại chỉ là nhãn chung không phân biệt được tài sản, Backend **không cho auto-duplicate** và đưa ticket vào duyệt tay.

### 1.5a. `red_flag_relation`

Schema giống `duplicate` (`master_ticket_id`, `reason`) nhưng chỉ là quan hệ bằng chứng, **không phải lệnh đóng ticket mới**. Agent chỉ trả object này khi:

- `exit_reason = RED_FLAG`;
- ứng viên đến từ search `purpose=DUPLICATE` của chính session;
- AI chắc chắn đây vẫn là cùng sự cố/tài sản đang hoạt động, nhưng phản ánh mới có red-flag hoặc cho thấy tình trạng xấu đi.

Backend luôn tiếp tục xử lý ticket mới theo nhánh P3. Object này chỉ cho phép Backend liên kết bằng chứng với master và yêu cầu đánh giá khẩn cấp lại master theo §3.3.

### 1.6. `tool_usage`

```json
{
  "total_tool_calls": 0,
  "ask_resident_rounds": 0,
  "ask_resident_elapsed_seconds": 0,
  "search_related_tickets_called": false,
  "propose_case_grouping_called": false
}
```

Giới hạn lần lượt là `0..5`, `0..3`, `0..300`. Backend không tin số Agent tự khai; phải so với counters và tool-call log của session.

### 1.7. Invariant khi finalize

Backend validate trong một transaction:

1. Red-flag ở bất kỳ nguồn nào thì chỉ chấp nhận `RED_FLAG`.
2. `RED_FLAG` bắt buộc `duplicate = null`; có thể có `red_flag_relation` hợp lệ theo §1.5a, nhưng không được âm thầm đóng ticket mới.
3. `DUPLICATE_EXISTING` phải thỏa toàn bộ §1.5 và bắt buộc `red_flag_relation = null`.
4. `DUPLICATE_UNCERTAIN` bắt buộc `is_confident = false`, `duplicate = null`, đã gọi search `purpose=DUPLICATE` và phải có lý do trong `confidence_notes`; Backend lấy danh sách ứng viên từ tool log để hiển thị khi duyệt tay.
5. `LIMIT_REACHED` bắt buộc `is_confident = false` và đã chạm `5` tool calls hoặc `3` lượt hỏi.
6. Ba field ảnh null cùng nhau nếu không có ảnh; có ảnh thì không field nào trong nhóm được null.
7. Trừ `INSUFFICIENT_INPUT`, `text_categories`, `severity`, `severity_source` không được null. `ANALYSIS_COMPLETE` chỉ khẳng định vòng trích xuất đã hoàn tất; không khẳng định hai nguồn Category khớp nhau.
8. Category ID phải thuộc catalog đã ghim.
9. Một session chỉ finalize thành công một lần. Gửi lại cùng payload/idempotency key trả kết quả cũ; payload khác trả `409 ANALYSIS_ALREADY_FINALIZED`.

---

## 2. Tool contracts của vòng phân tích

Agent chỉ được cấp bốn tool sau:

| Tool | Tính vào ngân sách 5 |
| --- | --- |
| `get_category_catalog` | Không |
| `search_related_tickets` | Có |
| `propose_case_grouping` | Có |
| `ask_resident` | Có |

### 2.1. `get_category_catalog`

Request:

```json
{ "session_id": "uuid" }
```

Response giữ đúng schema hiện có: `catalog_version` và danh sách `category_id`, `display_name`, `priority_ceiling`, `base_score`. Lần gọi đầu ghim catalog vào session.

### 2.2. `search_related_tickets`

Request v4:

```json
{
  "session_id": "uuid",
  "ticket_id": "uuid",
  "purpose": "DUPLICATE | GROUPING",
  "category_ids": ["uuid"],
  "limit": 20
}
```

Backend tự lấy building, floor và `location_id` chuẩn từ ticket; Agent không được cung cấp các field đó để mở rộng phạm vi.

Response:

```json
{
  "purpose": "DUPLICATE",
  "related_tickets": [
    {
      "ticket_id": "uuid",
      "category_ids": ["uuid"],
      "location_id": "uuid",
      "location_label": "Thang máy A",
      "status": "IN_PROGRESS",
      "summary": "Thang máy dừng giữa tầng 5 và 6.",
      "status_history": [
        { "status": "APPROVED", "changed_at": "ISO 8601" }
      ],
      "current_due_at": "ISO 8601 | null",
      "created_at": "ISO 8601"
    }
  ]
}
```

Luật lọc theo `purpose`:

- `DUPLICATE`: mọi Category; chỉ ticket đang hoạt động; cùng tòa và cùng chính xác `location_id`; trả lịch sử trạng thái cần thiết và mốc dự kiến hiện hành; không giới hạn 3 ngày khi master còn hoạt động.
- `GROUPING`: chỉ rò nước/chập điện; cùng tòa, tầng liền kề; lookback cố định tối đa 3 ngày; không trả ticket đã hoàn thành/hủy/không hợp lệ.
- Tối đa 20 dòng, sắp theo mức liên quan rồi thời gian tạo giảm dần.
- Chỉ trả summary đã làm sạch. Không trả tên, số điện thoại, căn hộ nguồn, text đầy đủ hoặc ảnh của người gửi trước.
- `category_ids` rỗng hoặc ngoài catalog ghim trả `400 CATEGORY_REQUIRED`.

### 2.3. `propose_case_grouping`

Giữ request/response hiện có:

```json
{
  "session_id": "uuid",
  "ticket_id": "uuid",
  "related_ticket_ids": ["uuid"],
  "reason": "string"
}
```

```json
{
  "accepted": true,
  "density": 3,
  "category_id": "uuid",
  "related_ticket_ids": ["uuid"],
  "rejected_reason": null
}
```

ID liên quan phải đến từ search `purpose=GROUPING` của chính session. Backend tính `density` và chỉ thẩm định đề xuất; chưa tạo cụm trước khi kết quả phân tích được finalize.

### 2.4. `ask_resident`

Giữ schema hiện có với `MULTIPLE_CHOICE | FREE_TEXT`, `question_text`, `options`, `allow_free_text_fallback`. `expires_at` bằng thời điểm hiện tại cộng **phần còn lại** của tổng 300 giây.

Chỉ người tạo ticket trả lời. Chạm trần trả `409 AGENT_BUDGET_EXHAUSTED`.

---

## 3. Backend áp dụng kết quả phân tích

### 3.1. Nhánh duplicate

Khi finalize `DUPLICATE_EXISTING`, Backend khóa ticket mới và master cần thiết, kiểm tra invariant, rồi ghi atomically:

- Ticket mới: `status = LINKED_DUPLICATE`, `classification_status = RESOLVED`.
- `duplicate_of_ticket_id = master_ticket_id`.
- `duplicate_linked_at`, `duplicate_reason`, `duplicate_analysis_run_id`.
- Không tính điểm/Priority/SLA riêng; không tạo assignment; không tham gia hàng ticket hoạt động.
- Tăng số lượt phản ánh trùng và số căn hộ riêng biệt trên thống kê master bằng truy vấn/aggregate, không tăng Density.
- Ghi audit actor `SYSTEM`, action `TICKET_LINKED_AS_DUPLICATE`.
- Gửi thông báo tới mọi tài khoản trong căn hộ của ticket mới bằng dữ liệu rút gọn của master.

Nếu master đổi trạng thái hoặc mốc dự kiến, Backend fan-out thông báo rút gọn đến các căn hộ có ticket liên kết. Không sao chép PII, text hoặc ảnh của master sang ticket duplicate.

Nếu master vừa đóng hoặc vị trí/category đã đổi trước lúc ghi, trả `409 DUPLICATE_CANDIDATE_STALE`; không ghi một phần. Agent có thể tiếp tục nếu session còn ngân sách, nếu không thì ticket vào `MANUAL_REVIEW`.

### 3.2. Phạm vi hiển thị ticket: giai đoạn AI riêng tư

**Luồng “Sự cố của tôi khác” đã bị loại bỏ.** Không còn endpoint kháng nghị cho
Cư dân, không còn hàng chờ xử lý kháng nghị cho Ban quản lý, không còn bảng
`duplicate_disputes` và cột `tickets.duplicate_disputed_at`. Việc phát hiện và
liên kết duplicate (§3.1) giữ nguyên hoàn toàn. Nếu một liên kết bị sai, Ban
quản lý sửa bằng các thao tác duyệt/điều chỉnh thông thường.

Thay vào đó, phần này định nghĩa ai được thấy một ticket. Ticket là **riêng tư
với người gửi** trong suốt thời gian phân loại chưa kết thúc:

```text
riêng tư  <=>  classification_status IN (PENDING, PROCESSING)
```

Khoảng đó bao gồm cả lúc Agent đang phân tích lẫn lúc Agent đang chờ Cư dân trả
lời câu hỏi bổ sung. Mọi trạng thái còn lại là **đã công bố**: `RESOLVED`,
`MANUAL_REVIEW`, `FAILED`, và mọi kết thúc invalid — bất kể nhánh v3/v4 ghi
`classification_status` là `FAILED` hay `RESOLVED`. Không có cờ “published”
riêng: công bố được suy ra từ `classification_status`, nên các nhánh finalize
hiện có vẫn là nguồn sự thật duy nhất.

Khi phân loại kết thúc, ticket được chia sẻ cho các tài khoản còn hoạt động
trong cùng căn hộ và bàn giao cho Ban quản lý xem xét, hoặc kết thúc là invalid.

| Đối tượng | Giai đoạn AI riêng tư | Sau khi công bố |
| --- | --- | --- |
| Người gửi | Xem trong danh sách, xem chi tiết/ảnh/câu hỏi, trả lời câu hỏi, hủy khi trạng thái cho phép | Xem được; các thao tác dành riêng người gửi vẫn còn nếu hợp lệ |
| Thành viên khác cùng căn hộ | Không thấy trong danh sách, không tính vào `total`, không xem được chi tiết, ảnh hay câu hỏi AI | Xem được ticket và ảnh; không bao giờ được hủy hoặc trả lời câu hỏi AI |
| Cư dân căn hộ khác | Không có quyền | Không có quyền |
| Điều phối viên / Ban quản lý | Không thấy trong danh sách/`total`/chi tiết/ảnh và không thao tác được qua API người dùng | Theo đúng luồng Điều phối viên hiện hành |
| Agent và worker nội bộ | Truy cập bình thường | Không đổi |

Ràng buộc thực thi:

- Backend là nơi quyết định. Ẩn nút ở frontend không được tính là kiểm soát.
- Vị từ hiển thị phải chạy trong SQL **trước** `count`, `offset` và `limit`, để
  một dòng không được phép xem không bao giờ chiếm chỗ trên trang hay lọt vào
  `total`.
- Chi tiết ticket, signed URL của ảnh và endpoint câu hỏi AI áp dụng cùng một
  quy tắc, nên không thể đi vòng qua danh sách bằng URL trực tiếp.
- Đọc không được phép trả về **not-found**, không phải forbidden: đoán ID không
  được xác nhận ticket có tồn tại hay không.
- Hủy ticket và cả hai endpoint câu hỏi AI (`GET` và `POST .../answer`) chỉ dành
  cho người gửi, kiểm tra ở backend; endpoint trả lời còn phải khớp cả
  `ticket_id` lẫn `question_id`.
- `available_actions` nhận biết người gọi (`CANCEL` chỉ trả cho người gửi) nhưng
  chỉ là gợi ý cho UI.
- Không gửi thông báo toàn căn hộ khi ticket còn riêng tư. Thông báo toàn căn hộ
  sau khi có kết quả phân loại cuối — đặc biệt là thông báo liên kết duplicate —
  giữ nguyên.

### 3.3. Nhánh còn lại

- `RED_FLAG`: Backend chốt nhánh P3 theo rule hiện hành; không dùng kết quả duplicate.
- `ANALYSIS_COMPLETE`: Backend đối chiếu Category ảnh/text. Có đúng một kết quả chung rõ ràng, hoặc chỉ có nguồn text đủ rõ, thì Backend chốt Category, tra scoring rules, áp ceiling và chốt Priority; không có giao hoặc còn nhiều kết quả không thể phân giải thì `classification_status = MANUAL_REVIEW`.
- `DUPLICATE_UNCERTAIN`, `LIMIT_REACHED`: `classification_status = MANUAL_REVIEW`.
- `INSUFFICIENT_INPUT`: `status = INVALID`, `classification_status = RESOLVED`, `invalid_reason = CONTENT_INSUFFICIENT`, thông báo gửi lại.

Nếu `RED_FLAG` có `red_flag_relation`, Backend ưu tiên không làm chậm ticket P3 mới, đồng thời:

1. Tạo quan hệ `RED_FLAG_EVIDENCE` tới master trong transaction độc lập có retry/idempotency.
2. Đánh dấu master cần đánh giá lại khẩn cấp; đưa bằng chứng mới vào màn hình Điều phối viên và phát cảnh báo P3.
3. Nếu policy cho phép nâng Priority tự động thì áp rule P3; nếu master đang chờ quyết định con người thì bắt buộc Điều phối viên xử lý. Không hạ mức master.
4. Nếu không thể liên kết an toàn vì master đã đóng/thay đổi, vẫn giữ ticket mới theo pipeline P3 độc lập và ghi cảnh báo/audit; không được làm mất phản ánh.

### 3.3a. Điều phối viên chốt `MANUAL_REVIEW`

Endpoint chốt duyệt tay nhận `category_id`, `resolution_source` (`IMAGE`, `TEXT` hoặc `OTHER`), lý do audit và `severity` tùy chọn. Đây chỉ là bước chốt phân loại: ticket chuyển từ `MANUAL_REVIEW` sang `RESOLVED`, **không** đồng nghĩa `APPROVE` hay tạo assignment.

- `IMAGE` và `TEXT` là tuyên bố rằng Category được chọn dựa trên dự đoán của nguồn đó. Backend chỉ chấp nhận khi ticket có analysis run mới nhất và danh sách Category của đúng nguồn đó không rỗng; Category được chọn phải thuộc danh sách này. Với contract `v3`/`v4`, so UUID `category_id`; chỉ contract legacy `v2` mới so mã Category đã chuẩn hóa.
- Không có analysis run, danh sách dự đoán `null`/rỗng, hoặc Category không khớp phải trả `400 CATEGORY_REQUIRED`. Client phải hiển thị rõ nguồn không có kết quả để đối chiếu, không được ghi audit giả rằng AI đã dự đoán.
- `OTHER` là quyết định độc lập của Điều phối viên, nên hợp lệ dù có hoặc không có analysis run/prediction; bắt buộc lưu lý do và `resolution_source = OTHER` vào audit.
- Backend dùng Severity đã có trên ticket. Nếu ticket chưa có Severity (ví dụ run thất bại trước khi trả Severity), Điều phối viên phải gửi `severity`; Backend ghi `tickets.severity_source = COORDINATOR_MANUAL`, rồi mới tính điểm/Priority/SLA. Nếu cả hai cùng thiếu, trả `400 SEVERITY_REQUIRED`; không được mặc định Severity.
- Nhánh red-flag P3 hiện hành vẫn là short-circuit của `ScoringService`; việc chốt Category/Severity thủ công không được làm hạ hay bỏ qua rule P3.

### 3.4. Hết thời gian chờ Cư dân

Đây là transition do Backend scheduler thực hiện, không phải Agent tự tạo một kết quả giả:

1. Session đang chờ câu trả lời và tổng thời gian chờ đạt 300 giây.
2. Backend đóng question đang mở, kết thúc session và chuyển ticket sang `INVALID`.
3. Ghi `invalid_reason = RESIDENT_RESPONSE_TIMEOUT`, gửi thông báo cho căn hộ.
4. Trường hợp này vẫn tính vào ngưỡng 10 lượt gửi/1 giờ, nhưng **không** tính vào bộ đếm 3 ticket bị AI từ chối/1 ngày.

---

## 4. Mặt B — chọn Kỹ thuật viên

### 4.0. Hai bộ máy quyết định

Việc chọn Kỹ thuật viên chạy trên **một trong hai bộ máy**, chọn bằng cấu hình `ASSIGNMENT_DECISION_ENGINE`:

| | `RULE` — mặc định | `AI` — dự phòng |
| --- | --- | --- |
| Bộ máy | `RULE_ENGINE_V1` (`src/assignment_rules`) | Model chính + model fallback (`src/assignment_agent`) |
| Độ trễ | Tức thời, không gọi mạng | Tối đa 300 giây, cộng 300 giây nữa nếu phải fallback |
| Kết quả bất định | Không. Cùng đầu vào luôn ra cùng một người | Có |
| `MANUAL_REQUIRED` vì lỗi model | Không tồn tại | Có, theo §5.2 |

Toàn bộ phần còn lại của §4 và §5 — snapshot ứng viên, `decision_id`, batch tối đa 20 ticket, `INCIDENT_CASE` là một đơn vị, ghi assignment, manual-wins, job bền vững, audit — **giống hệt nhau ở cả hai bộ máy**. Request và result dùng chung một schema; chỉ phần “ai nghĩ ra câu trả lời” là khác. Vì vậy đổi bộ máy là đổi một biến môi trường và khởi động lại, không phải đổi code.

`RULE_ENGINE_V1` là mặc định vì độ trễ. Bài toán đầu vào là bốn số nguyên và một mốc thời gian trên mỗi ứng viên; trả tiền cho hai cửa sổ 300 giây để giải nó là không tương xứng, và mỗi lỗi model lại đẩy một ticket vào hàng phân tay.

`AI` được giữ nguyên vẹn để hoàn tác: nếu khóa xếp hạng ở §4.1a phân tải theo cách Ban quản lý không đồng ý, đặt `ASSIGNMENT_DECISION_ENGINE=AI` là quay lại hành vi cũ ngay, không cần revert code.

### 4.1. Ranh giới trách nhiệm

Đây là một quyết định độc lập, không tool và không dùng `AIAnalysisSession`. Backend lọc ứng viên trước theo đúng ba điều kiện:

1. Hồ sơ Kỹ thuật viên đang hoạt động.
2. Kỹ thuật viên đang bật sẵn sàng.
3. Kỹ năng phù hợp Category chính thức của ticket.

Bộ máy quyết định chỉ cân nhắc chuyên môn/kỹ năng và tải công việc hiện tại. Không dùng vị trí địa lý, ca trực, Priority để đổi người ngoài danh sách, hoặc dữ liệu cá nhân không cần thiết.

Theo quyết định nghiệp vụ đã chốt, Backend **không kiểm tra lại điều kiện sẵn sàng/kỹ năng** sau khi có kết quả. Backend chỉ giữ hai chặn toàn vẹn dữ liệu:

- `technician_id` phải tồn tại và có trong snapshot ứng viên của request đó.
- Một ticket chỉ có một assignment active; phân tay của con người thắng race và làm kết quả tự động bị bỏ qua.

### 4.1a. `RULE_ENGINE_V1` — quy tắc chọn người

Ba bước, chạy đúng theo thứ tự này.

**Bước 1 — lọc ứng viên bắt buộc.** Ba điều kiện §4.1 cộng danh sách loại trừ §4.3 đã do `AssignmentCandidateService` áp dụng trước; snapshot đưa vào engine đã sạch. Engine chỉ thêm đúng một tầng lọc mà snapshot không mô tả được: **giới hạn tải theo cấu hình**, đối chiếu với tải **dự kiến** chứ không phải tải trong snapshot, nên người vừa được chọn ở work item trước đã bị tính.

**Bước 2 — sắp work item.** P3 → P2 → P1; cùng Priority thì work item tạo sớm hơn xử lý trước; hòa tiếp thì theo `work_item_id`. `INCIDENT_CASE` là **một** work item và không bao giờ bị tách. Thứ tự này quyết định ai được người rảnh nhất, vì tải dự kiến tăng dần theo từng quyết định.

**Bước 3 — chọn theo khóa xếp hạng (lexicographic), không random.**

| Priority | Thứ tự ưu tiên chọn Kỹ thuật viên |
| --- | --- |
| P3 | Ít P3 dự kiến nhất → ít tổng việc dự kiến nhất → lâu chưa được giao nhất → `technician_id` |
| P2 | Ít tổng việc dự kiến nhất → ít P3 dự kiến nhất → lâu chưa được giao nhất → `technician_id` |
| P1 | Ít tổng việc dự kiến nhất → lâu chưa được giao nhất → `technician_id` |

P3 dẫn đầu bằng số P3 vì 5 phút không phải khối lượng công việc mà là một lần ngắt: người đang chạy một ca khẩn cấp là người tệ nhất để nhận ca thứ hai, bất kể tổng việc của họ. P1/P2 dẫn đầu bằng tổng tải; P2 giữ số P3 làm khóa thứ hai để việc thường trôi ra khỏi người đang xử lý khẩn cấp. `technician_id` đứng cuối làm khóa trở thành thứ tự toàn phần — hai lần chạy trên cùng đầu vào chọn cùng một người, đó là điều làm một quyết định giải thích được về sau.

**“Dự kiến”** nghĩa là sau mỗi quyết định, tải của người được chọn tăng thêm đúng số ticket trong work item đó. Đây chính là phần cân tải toàn đợt mà §4.3a từng yêu cầu model tự làm.

Pseudo-code:

```text
projected_total = active_assignment_count
projected_p3    = active_p3_count

for item in items_sorted_by_priority_then_created_at:
    candidates = hard_filter(item)          # đã lọc sẵn + giới hạn tải

    if not candidates:
        manual_required(item, "NO_CANDIDATES")
        continue

    winner = min(candidates, key=lambda t: rank(item.priority, t, projected_total, projected_p3))

    assign_or_propose(item, winner)
    projected_total[winner] += item.ticket_count
    if item.priority == P3:
        projected_p3[winner] += item.ticket_count
```

Cấu hình rule nằm ở `config/assignment_rules.yaml`, ghi đè từng khóa bằng biến môi trường `ASSIGNMENT_RULE_<KHÓA>`; không hard-code trong code:

| Khóa | Ý nghĩa | Mặc định |
| --- | --- | --- |
| `rule_version` | Ghi vào `completed_model`/`primary_model` và vào từng decision | `RULE_ENGINE_V1` |
| `max_active_assignments` | Trần tổng việc đang giữ | `null` — không giới hạn |
| `max_active_p1_assignments` | Trần riêng cho P1 | `null` |
| `max_active_p2_assignments` | Trần riêng cho P2 | `null` |
| `max_active_p3_assignments` | Trần riêng cho P3, tức trần công việc khẩn cấp | `null` |
| `allow_p3_overload_when_all_capped` | Cho phép quá tải P3 khi mọi ứng viên đều đã chạm ngưỡng | `true` |
| `tie_break_on_last_assigned_at` | Dùng “lâu chưa được giao nhất” làm khóa hòa | `true` |

Bốn quy ước bắt buộc của phần cấu hình:

1. Trần hỏi “người này đã đầy chưa”, không hỏi “việc này có vừa không”. Nhờ vậy `max_active_assignments = 2` không làm một case 5 member trở thành không phân được cho một Kỹ thuật viên đang rảnh.
2. Chỉ trần đúng Priority đang phân mới được xét, cộng với trần tổng.
3. Trần nào Backend không gửi được số đếm tương ứng thì không có hiệu lực; không suy đoán một con số rồi từ chối việc theo nó.
4. Nếu mọi ứng viên đều vượt ngưỡng: P1/P2 trả `NO_SUITABLE_CANDIDATE`; P3 vẫn được đặt kèm ghi rõ ngoại lệ quá tải trong `reason`, trừ khi `allow_p3_overload_when_all_capped = false`. P3 giữ cam kết 5 phút của §11.7 — thiếu người cho P3 là việc phải cảnh báo Điều phối viên, không phải lý do lùi SLA.

`reason` sinh cố định, ví dụ: `"Chọn theo RULE_ENGINE_V1: tải dự kiến 2, P3 dự kiến 0; ưu tiên thấp nhất trong nhóm ứng viên hợp lệ."`

Đổi bất kỳ khóa nào ở bảng trên theo hướng làm quyết định hôm qua ra kết quả khác thì phải tăng `rule_version`.

### 4.2. Khi nào tạo assignment job

| Trigger | Điều kiện | Chế độ |
| --- | --- | --- |
| Ticket vừa đủ điều kiện phân việc | Auto bật và tới `approved_at + configured_delay` | `DIRECT` |
| Ticket P3 vừa đủ điều kiện | Auto bật; bỏ qua configured delay | `DIRECT` ngay |
| Kỹ thuật viên từ chối | Theo cửa sổ và trần tại §6.2 | `DIRECT` |
| Kỹ thuật viên im lặng quá hạn | Auto bật và chưa vượt trần | `DIRECT` |
| Điều phối viên yêu cầu bật auto khi đang có hàng chờ | Tạo batch nháp, lấy tối đa 20 ticket đủ điều kiện; công tắc vẫn TẮT | `PROPOSAL` |

`configured_delay` chỉ nhận: ngay lập tức, 2 giờ, 5 giờ, 1 ngày hoặc 3 ngày. Auto tắt thì không tạo job AI `DIRECT`; ticket vào/ở hàng phân tay. Ticket đã có assignment active, bị liên kết duplicate, chưa duyệt hoặc đã pause auto không đủ điều kiện tạo job. `PROPOSAL` là đợt xem trước theo yêu cầu riêng và không làm công tắc chuyển BẬT trước khi Điều phối viên xác nhận.

`INCIDENT_CASE` được dùng ở cả `DIRECT` và `PROPOSAL`, nhưng chỉ khi case đã được Backend tạo chính thức, có một Category chung, có tối đa **5 member** và mọi member đưa vào đơn vị phân việc đã được duyệt/đủ điều kiện. AI chọn một Kỹ thuật viên cho từng đơn vị case; Backend tạo assignment riêng trên từng member. Member được thêm vào case sau quyết định đó không tự kế thừa Kỹ thuật viên mà phải đi qua một quyết định phân việc mới.

Nhiều job `DIRECT` cùng đủ điều kiện ở một thời điểm có thể được Backend gom vào **một lượt gọi model**, tối đa 20 UUID ticket riêng biệt trên toàn request. Mỗi đơn vị vẫn có `decision_id`, candidate snapshot, job và transaction ghi độc lập. Vì vậy hai đơn vị trong cùng request `DIRECT` là hợp lệ; batching không biến DIRECT thành PROPOSAL và không thêm bước Điều phối viên duyệt.

### 4.3. `DirectAssignmentBatchRequestV4` — Backend → AI

```json
{
  "request_id": "uuid",
  "assignment_mode": "DIRECT",
  "work_items": [
    {
      "decision_id": "uuid",
      "work_item": {
        "work_item_type": "TICKET | INCIDENT_CASE",
        "work_item_id": "uuid",
        "ticket_ids": ["uuid"],
        "category_id": "uuid",
        "priority": "P1 | P2 | P3",
        "location_labels": ["string"],
        "issue_summary": "string",
        "required_skills": ["string"],
        "current_due_at": "ISO 8601 | null",
        "created_at": "ISO 8601 | null"
      },
      "trigger": "INITIAL_AUTO | REASSIGN_REJECTED | REASSIGN_SILENT",
      "reassignment_count": 0,
      "excluded_technician_ids": ["uuid"],
      "candidates": [
        {
          "technician_id": "uuid",
          "display_name": "string",
          "matched_skills": ["string"],
          "active_assignment_count": 2,
          "active_p3_count": 0,
          "is_available_snapshot": true,
          "active_p1_count": 1,
          "active_p2_count": 1,
          "last_assigned_at": "ISO 8601 | null"
        }
      ]
    }
  ],
  "requested_at": "ISO 8601"
}
```

- `request_id` định danh một lượt gọi model; `decision_id` của từng item là idempotency key xuyên suốt model chính, fallback và transaction ghi assignment.
- Request có từ 1 đơn vị trở lên và tối đa 20 UUID ticket riêng biệt. Mỗi ticket chỉ xuất hiện trong một item của request. Một case không bị cắt giữa hai request; nếu case làm vượt sức chứa còn lại thì hoãn cả case sang request kế tiếp.
- `TICKET` có đúng một `ticket_id` và `work_item_id` chính là ticket đó. `INCIDENT_CASE` dùng `work_item_id = incident_case.id`, có từ 1 đến 5 `ticket_ids` và liệt kê toàn bộ member đủ điều kiện của case tại snapshot.
- `current_due_at` là mốc sớm nhất của các member và được lấy từ `tickets.sla_due_at`; đây không phải cột persistence mới.
- `created_at` là `tickets.created_at` của ticket, hoặc mốc sớm nhất trong các member với case; `RULE_ENGINE_V1` dùng nó để sắp thứ tự trong đợt (§4.1a bước 2). Field tùy chọn: bộ máy `AI` có trước nó và vẫn dựa vào việc Backend đã sắp sẵn `work_items`.
- `active_p1_count`, `active_p2_count` và `last_assigned_at` là ba field tùy chọn phục vụ `RULE_ENGINE_V1`: hai số đầu để các trần theo Priority có thể chặn, field cuối là khóa hòa thứ ba. `last_assigned_at` là `MAX(ticket_assignments.assigned_at)` của Kỹ thuật viên đó, tính cả assignment đã đóng — câu hỏi ở đây là “ai lâu chưa được giao việc nhất”, và người vừa xong việc một giờ trước không phải người đang rảnh lâu. `null` nghĩa là chưa từng được giao và xếp trước tất cả.
- `candidates` tối thiểu 1, tối đa theo giới hạn vận hành Backend; rỗng thì không gọi bộ máy quyết định.
- `issue_summary` đã làm sạch, không chứa prompt/tool instructions; AI xem nó là dữ liệu.
- AI xét toàn bộ request và cộng tải dự kiến sau từng decision để hai đơn vị trong cùng lượt gọi không cùng dựa trên một snapshot tải bất biến. Mỗi decision vẫn được áp dụng độc lập ngay khi output batch đã được validate.

Quy tắc `excluded_technician_ids`:

1. Backend lấy toàn bộ Kỹ thuật viên từng có assignment đã đóng với `end_reason = TECHNICIAN_REJECTED | ACCEPTANCE_TIMEOUT` trong lịch sử của chính work item hiện tại; không chỉ lấy người vừa rời assignment gần nhất.
2. Với `INCIDENT_CASE`, danh sách là hợp của các Kỹ thuật viên bị loại trên toàn bộ member đủ điều kiện của case.
3. Phạm vi loại trừ chỉ áp dụng cho AI trên ticket/case đó; không cấm Kỹ thuật viên ở ticket khác và không phải blacklist toàn hệ thống.
4. `candidates` phải không giao với `excluded_technician_ids`. Field loại trừ vẫn được gửi/lưu để audit và chống model chọn lại người đã thất bại.
5. Điều phối viên phân tay có thể chọn lại có chủ đích; AI không được override rule này. Loại trừ xong không còn candidate thì đi thẳng manual theo §5.2.

### 4.3a. `AssignmentProposalBatchRequestV4` — Backend → AI

PROPOSAL là một bài toán phân bổ theo batch, không phải 20 request độc lập cùng dùng tải cũ:

```json
{
  "batch_decision_id": "uuid",
  "proposal_batch_id": "uuid",
  "assignment_mode": "PROPOSAL",
  "work_items": [
    {
      "decision_id": "uuid",
      "work_item": {
        "work_item_type": "TICKET | INCIDENT_CASE",
        "work_item_id": "uuid",
        "ticket_ids": ["uuid"],
        "category_id": "uuid",
        "priority": "P1 | P2 | P3",
        "location_labels": ["string"],
        "issue_summary": "string",
        "required_skills": ["string"],
        "current_due_at": "ISO 8601 | null",
        "created_at": "ISO 8601 | null"
      },
      "excluded_technician_ids": ["uuid"],
      "candidates": [
        {
          "technician_id": "uuid",
          "display_name": "string",
          "matched_skills": ["string"],
          "active_assignment_count": 2,
          "active_p3_count": 0,
          "is_available_snapshot": true,
          "active_p1_count": 1,
          "active_p2_count": 1,
          "last_assigned_at": "ISO 8601 | null"
        }
      ]
    }
  ],
  "requested_at": "ISO 8601"
}
```

- Tối đa 20 UUID ticket riêng biệt trên toàn bộ `work_items`; mỗi case có tối đa 5 ticket và một case không bị tách giữa hai batch.
- Backend sắp `work_items` theo Priority giảm dần rồi thời gian gửi tăng dần trước khi gọi AI.
- AI phải xét toàn bộ batch và tải dự kiến `active_assignment_count + proposed_assignment_count_in_batch`. Sau mỗi quyết định dự kiến, số đề xuất thêm của Kỹ thuật viên đó tăng theo số `ticket_ids` thuộc work item.
- Một Kỹ thuật viên được xuất hiện trong nhiều quyết định nếu sau khi cộng tải dự kiến họ vẫn là lựa chọn phù hợp; không có quy tắc một Kỹ thuật viên chỉ nhận một ticket.
- Mỗi work item có candidate snapshot riêng vì Category/kỹ năng và danh sách loại trừ có thể khác nhau.

### 4.4. Kết quả AI → Backend

#### 4.4.1. `DirectAssignmentBatchResultV4` cho DIRECT

```json
{
  "request_id": "uuid",
  "decisions": [
    {
      "decision_id": "uuid",
      "work_item_id": "uuid",
      "selected_technician_id": "uuid | null",
      "decision": "SELECTED | NO_SUITABLE_CANDIDATE",
      "reason": "string 1..500",
      "model_version": "string 1..100",
      "decided_at": "ISO 8601"
    }
  ],
  "completed_at": "ISO 8601"
}
```

#### 4.4.2. `AssignmentProposalBatchResultV4` cho PROPOSAL

```json
{
  "batch_decision_id": "uuid",
  "proposal_batch_id": "uuid",
  "decisions": [
    {
      "decision_id": "uuid",
      "work_item_id": "uuid",
      "selected_technician_id": "uuid | null",
      "decision": "SELECTED | NO_SUITABLE_CANDIDATE",
      "reason": "string 1..500",
      "model_version": "string 1..100",
      "decided_at": "ISO 8601"
    }
  ],
  "completed_at": "ISO 8601"
}
```

Invariant:

- `SELECTED` cần `selected_technician_id` thuộc snapshot.
- `NO_SUITABLE_CANDIDATE` cần ID null.
- Kết quả sai schema, sai `decision_id`, sai work item hoặc chọn ngoài snapshot được xem là lỗi kỹ thuật của model và kích hoạt fallback.
- `NO_SUITABLE_CANDIDATE` là kết quả nghiệp vụ hợp lệ và không gọi fallback; cách áp dụng phụ thuộc mode theo §5.2.
- Với cả DIRECT và PROPOSAL, mỗi `decision_id` xuất hiện đúng một lần. Backend validate từng decision độc lập: item thiếu/sai contract được đưa vào fallback cùng các item lỗi, không làm mất những item hợp lệ từ model chính.
- `model_version` là phiên bản bộ máy đã sinh ra decision đó: `RULE_ENGINE_V1` (hoặc `rule_version` đang cấu hình) với bộ máy `RULE`, tên model với bộ máy `AI`. Đọc như `decision_engine_version`; tên field giữ nguyên để không phải đổi cột persistence và payload API đang chạy.
- `model_version` và `decided_at` nằm trên từng decision vì batch cuối có thể trộn item hợp lệ từ model chính với item được cứu bởi fallback; không được gán một model chung sai cho toàn batch. Với `RULE_ENGINE_V1` mọi decision trong một batch luôn cùng một phiên bản.
- Một `selected_technician_id` có thể lặp lại ở nhiều decision. Backend không cân bằng lại kết quả; model phải thực hiện phân tải trong ngữ cảnh toàn request theo §4.3 hoặc §4.3a.

### 4.5. Ghi assignment

Với từng decision `DIRECT`, Backend ghi một transaction độc lập:

1. Khóa các ticket của work item theo thứ tự UUID để tránh deadlock hoặc dùng optimistic `version`.
2. Trên từng ticket, xác nhận chưa có assignment active. Không đánh giá lại ready/skills.
3. Tạo `TicketAssignment(status=ASSIGNED, is_active=true, source=AI_AUTO)` cho ticket đơn hoặc từng member hợp lệ của case.
4. Ghi `assigned_by_user_id = null`, audit actor `SYSTEM`.
5. Nếu work item là case, tính `member_count = 1..5` và áp hệ số mở rộng SLA hoàn thành `1 + 0,25 × (member_count - 1)` cho P1/P2, tối đa 2 lần. P3 giữ 5 phút. Không thay đổi deadline nhận việc/cảnh báo/đổi người. Sau đó cập nhật `sla_due_at` và gửi thông báo Backend.
6. Mark job `COMPLETED`.

Nếu Điều phối viên đã phân tay, unique constraint một-assignment-active làm lệnh AI thất bại an toàn trên member đó. Với ticket đơn, job chuyển `CANCELLED_MANUAL_WON`. Với case, Backend bỏ qua member đã có người và vẫn gán các member còn hợp lệ; SLA mở rộng phải tính lại theo đúng số member thực tế được ghi trong transaction, không dùng số snapshot ban đầu. Nếu không còn member nào thì job chuyển `CANCELLED_MANUAL_WON`. Không ghi đè và không retry.

Với `PROPOSAL`, không tạo assignment khi AI trả kết quả. Backend lưu từng dòng vào batch; Điều phối viên được bỏ dòng, đổi người và bấm OK. Assignment sau khi OK có `source=AI_PROPOSAL_CONFIRMED`, actor là Điều phối viên. Mỗi đợt chứa tối đa **20 ticket riêng biệt** tính trên toàn bộ work item. Một case không được tách đôi giữa hai batch: nếu toàn bộ member đủ điều kiện của case làm vượt số chỗ còn lại thì hoãn cả case sang đợt sau. Khi confirm case, Backend áp cùng hệ số SLA cụm ở bước 5 phía trên theo số member thực tế còn được chọn và ghi thành công; P3 cùng deadline nhận việc không kéo dài.

### 4.6. Vòng đời bảng proposal và công tắc

1. Điều phối viên bấm bật khi công tắc đang TẮT → Backend tạo `assignment_proposal_batch(status=BUILDING)`. Công tắc vẫn TẮT.
2. Backend gửi một request batch để AI cân bằng tải trên toàn bộ work item. Mỗi work item vẫn có kết quả độc lập: dòng lỗi hoặc không có người phù hợp được lưu `EMPTY` kèm lý do; các dòng hợp lệ khác vẫn tiếp tục.
3. Khi hoàn tất, batch thành `READY`, `expires_at = ready_at + 600 giây`.
4. Điều phối viên có thể bỏ dòng, đổi Kỹ thuật viên và chọn checkbox `continue_auto_assignment` cùng `activation_delay`.
5. Bấm OK trước hạn → Backend khóa batch và work items, bỏ qua ticket đã được phân tay, ghi các assignment còn hợp lệ rồi chuyển batch `CONFIRMED`.
6. Chỉ sau khi confirm thành công, nếu `continue_auto_assignment = true` thì công tắc chuyển BẬT với delay đã chọn. Nếu false, công tắc giữ TẮT và đây là đợt một lần.
7. Đóng bảng không bấm OK → batch `CANCELLED`, không assignment và công tắc vẫn TẮT.
8. Hết 10 phút → batch `EXPIRED`; endpoint confirm trả `409 PROPOSAL_EXPIRED`, không dùng snapshot cũ và công tắc vẫn TẮT.

---

## 5. Điều phối thời gian, lỗi model và fallback

Mỗi lượt phân việc phải là một job bền vững trong database/queue; không dùng `FastAPI BackgroundTasks` hoặc timer chỉ sống trong process cho cửa sổ 5–10 phút. Điều này đúng với cả hai bộ máy: job là nơi giữ cửa sổ chờ §6.2, khóa ticket §5.1 và audit, không phải nơi chờ model.

§5.2 dưới đây mô tả bộ máy `AI`. Với `RULE_ENGINE_V1`, các bước gọi model biến mất và chỉ còn hai kết cục nghiệp vụ: snapshot rỗng thì theo item 1, và mọi ứng viên vượt ngưỡng thì theo item 7 (`NO_SUITABLE_CANDIDATE`). Không có `PRIMARY_RUNNING` thực sự kéo dài, không có `FALLBACK_RUNNING`, không có `MANUAL_REQUIRED` vì lỗi kỹ thuật của model. Cấu hình `assignment_primary_model`/`assignment_fallback_model` không được đọc, và startup validation kiểm tra `config/assignment_rules.yaml` thay cho cặp model.

### 5.1. Trạng thái job

`AssignmentJobStatus`:

```text
SCHEDULED_GRACE
PRIMARY_RUNNING
FALLBACK_RUNNING
COMPLETED
FAILED
CANCELLED_BY_COORDINATOR
CANCELLED_MANUAL_WON
MANUAL_REQUIRED
```

`COMPLETED` nghĩa là model đã trả kết quả nghiệp vụ hợp lệ, gồm cả `NO_SUITABLE_CANDIDATE`; kết quả hiển thị của PROPOSAL nằm ở status của `assignment_proposal_items`, không mã hóa lại thành `PROPOSAL_READY/PROPOSAL_EMPTY` trong job. `FAILED` là lỗi kỹ thuật sau khi đã hết đường fallback; với `DIRECT`, orchestration chuyển tiếp sang `MANUAL_REQUIRED`, còn với `PROPOSAL`, item tương ứng thành `EMPTY`.

Ràng buộc đồng thời phải diễn đạt theo persistence, không dùng câu “cùng mode/batch” mơ hồ:

- `DIRECT`: unique partial index trên `ai_assignment_job_members(ticket_id) WHERE is_active` ngăn một ticket đồng thời thuộc hai job chưa kết thúc.
- `PROPOSAL`: không đặt `is_active` và không khóa ticket; trong chính một batch, mỗi ticket chỉ được xuất hiện trong một proposal item theo constraint §7.5.

### 5.2. Quy trình model

1. Nếu snapshot ứng viên rỗng: không gọi model. `DIRECT` → `MANUAL_REQUIRED`, pause riêng ticket/work item với lý do `NO_CANDIDATES`; `PROPOSAL` → item đó `EMPTY`, không pause ticket và không chặn batch. Các item còn candidate vẫn nằm trong batch request.
2. Gọi model chính đúng một request với deadline tối đa 300 giây. DIRECT gom các job cùng đủ điều kiện thành request 1–20 ticket theo §4.3; PROPOSAL gửi toàn bộ item còn candidate theo §4.3a. Không retry và không gia hạn deadline.
3. Với cả DIRECT và PROPOSAL, nếu envelope model chính lỗi hoàn toàn thì fallback nhận toàn bộ request; nếu chỉ một số decision thiếu/sai contract thì giữ nguyên item hợp lệ và fallback chỉ nhận tập item lỗi. DIRECT giữ nguyên `request_id`/`decision_id`; PROPOSAL giữ nguyên `batch_decision_id`/`decision_id`. Deadline fallback tối đa 300 giây.
4. Decision DIRECT hợp lệ từ model chính được giữ để ghi assignment; decision lỗi đang chờ fallback không được làm mất hoặc trì hoãn vô hạn decision hợp lệ khác. Mỗi decision có trạng thái job và transaction riêng.
5. Fallback thất bại: `DIRECT` → `MANUAL_REQUIRED`, đặt `ticket.auto_assignment_paused = true` cho các ticket liên quan và cảnh báo Điều phối viên; `PROPOSAL` → từng item còn lỗi chuyển `EMPTY` kèm lỗi đã làm sạch, không pause ticket và không chặn các item hợp lệ.
6. Không thay đổi công tắc tự động toàn hệ thống; ticket khác tiếp tục bình thường.
7. Model trả `NO_SUITABLE_CANDIDATE`: không gọi fallback. `DIRECT` → manual ngay; `PROPOSAL` → dòng `EMPTY`, không chặn bảng.

Config tối thiểu:

```text
assignment_decision_engine = RULE        # RULE | AI
assignment_primary_model                 # chỉ đọc khi engine = AI
assignment_fallback_model                # chỉ đọc khi engine = AI
assignment_model_timeout_seconds = 300   # chỉ đọc khi engine = AI
assignment_grace_seconds = 300
assignment_reassignment_cap = 3
direct_request_max_ticket_count = 20
proposal_ttl_seconds = 600
incident_case_max_ticket_count = 5
incident_case_sla_extension_per_extra_ticket = 0.25
acceptance_warning_p1_seconds = 172800
acceptance_reassign_p1_seconds = 176400
acceptance_warning_p2_seconds = 7200
acceptance_reassign_p2_seconds = 9000
acceptance_reassign_p3_seconds = 300
```

Khi `assignment_decision_engine = AI`, model fallback phải cấu hình độc lập; trùng tên model chính thì startup/config validation phải cảnh báo và coi là cấu hình không đạt yêu cầu failover. Khi bộ máy là `RULE`, kiểm tra này không áp dụng — không có gì để failover — và thay vào đó startup phải parse được `config/assignment_rules.yaml`: khóa sai chính tả hoặc giá trị trần không hợp lệ là lỗi cấu hình làm dừng khởi động, không phải cảnh báo bị bỏ qua để rồi mọi trần lặng lẽ biến mất.

Các giá trị cảnh báo/đổi người và trần ba lần là cấu hình kỹ thuật được version/control tập trung, không phải tùy chọn Điều phối viên được sửa trên UI.

---

## 6. Kỹ thuật viên từ chối và tái phân công

“Từ chối” khác hoàn toàn “Không xử lý được”:

- `REJECTED`: từ chối trước khi thực hiện; đóng assignment hiện tại và thử đổi người.
- `REASSIGNED`: assignment kết thúc vì hệ thống/Điều phối viên chuyển ticket sang người khác; lý do nằm ở `end_reason`.
- `UNABLE_TO_HANDLE`: đã nhận/đang xử lý nhưng kết luận không thể xử lý; giữ nghiệp vụ hiện có riêng.

### 6.1. API từ chối

```http
POST /api/v1/technician/assignments/{assignment_id}/reject
Idempotency-Key: <uuid>
```

```json
{ "reason": "string 1..500" }
```

Chỉ Kỹ thuật viên đang được gán, assignment active ở `ASSIGNED` hoặc `ACCEPTED` mới gọi được. Backend transaction:

1. `status = REJECTED`, `end_reason = TECHNICIAN_REJECTED`, `is_active = false`, `rejected_at = now`, `ended_at = now`, lưu reason.
2. Tăng `ticket.reassignment_count` thêm 1.
3. Ghi audit và thông báo Điều phối viên kèm lý do.
4. Nếu count vượt trần 3: pause auto cho ticket, vào hàng phân tay.
5. Nếu chưa vượt trần: áp §6.2.

### 6.2. Luồng sau từ chối

| Trường hợp | Xử lý |
| --- | --- |
| P3, auto đang bật, count ≤ 3 | Tạo job chạy ngay, không chờ 5 phút |
| P1/P2, auto đang bật, count ≤ 3 | Tạo `SCHEDULED_GRACE`, `execute_after = now + 300s` |
| Auto tắt hoặc count > 3 | `MANUAL_REQUIRED`, không gọi AI |

Trong cửa sổ P1/P2, Điều phối viên thấy lý do từ chối và thời điểm AI dự kiến chạy. Điều phối viên có thể:

- Hủy job để phân tay: `POST /api/v1/coordinator/assignment-jobs/{job_id}/cancel`.
- Phân tay trực tiếp; thành công thì Backend tự chuyển job thành `CANCELLED_MANUAL_WON`.

Hết 300 giây mà job chưa bị hủy và chưa có assignment active, AI chọn rồi gán thẳng. Không có bước Backend/Điều phối viên duyệt lại kết quả AI.

### 6.3. Đổi người do im lặng

Khi hết mốc cảnh báo và mốc gia hạn theo nghiệp vụ mà Kỹ thuật viên chưa nhận việc:

- Đóng assignment cũ với `status = REASSIGNED`, `end_reason = ACCEPTANCE_TIMEOUT`, `is_active = false`.
- Tăng cùng bộ đếm `reassignment_count`.
- Auto bật và count ≤ 3: AI chọn/gán thẳng; auto tắt hoặc vượt trần: manual.
- SLA người mới reset theo rule hiện hành; P3 giữ cam kết 5 phút như tài liệu nghiệp vụ.

### 6.4. Persistence và cách tính đồng hồ nhận việc

Backend dùng các cột đã có `tickets.sla_started_at` và `tickets.sla_due_at`; field API/AI `current_due_at` ánh xạ trực tiếp từ `sla_due_at`. Mỗi assignment chỉ được tạo deadline sau khi đã có `assigned_at`; deadline không bao giờ được phép nằm trong quá khứ ngay tại thời điểm giao việc.

| Priority | `acceptance_warning_at` | `acceptance_reassign_at` |
| --- | --- | --- |
| P1 | `MAX(cycle_started_at + 48 giờ, assigned_at)` | `MAX(cycle_started_at + 49 giờ, assigned_at + 1 giờ)` |
| P2 | `MAX(cycle_started_at + 2 giờ, assigned_at)` | `MAX(cycle_started_at + 2 giờ 30 phút, assigned_at + 30 phút)` |
| P3 | null | `MAX(cycle_started_at + 5 phút, assigned_at + 5 phút)` |

`cycle_started_at`:

- Lượt giao đầu của ticket thường: `ticket.sla_started_at`, được khởi tạo từ `ticket.created_at`; công thức `MAX` bảo đảm nếu ticket được gán muộn thì Kỹ thuật viên vẫn có tối thiểu 1 giờ với P1, 30 phút với P2 hoặc 5 phút với P3 trước khi bị đổi.
- Ticket qua duyệt thủ công: `assigned_at` của assignment đầu tiên.
- Mọi lượt tái phân công: `assigned_at` của assignment mới; đồng hồ cũ không được dùng tiếp.

“Được gán” nghĩa là đã có assignment active; “đã nhận việc” nghĩa là Kỹ thuật viên đã bấm nhận. Worker chỉ có thể đổi người sau khi assignment tồn tại, và phải dừng toàn bộ cảnh báo/đổi người của assignment đó ngay khi `accepted_at` được đặt.

Worker bền vững quét `acceptance_warning_at` và `acceptance_reassign_at` bằng khóa hàng/idempotency. `warning_sent_at` ngăn gửi cảnh báo lặp. Kỹ thuật viên bấm nhận việc thì đặt `accepted_at` và các job/quét quá hạn phải bỏ qua assignment đó.

---

## 7. Persistence contract và migration bắt buộc

### 7.1. `tickets`

Bổ sung tối thiểu:

```text
status: thêm LINKED_DUPLICATE
invalid_reason: CONTENT_INSUFFICIENT | RESIDENT_RESPONSE_TIMEOUT | null
duplicate_of_ticket_id uuid null FK tickets.id
duplicate_linked_at timestamptz null
duplicate_reason varchar(500) null
duplicate_analysis_run_id uuid null FK ai_analysis_runs.id
severity_source: IMAGE | TEXT | COORDINATOR_MANUAL | null
reassignment_count int not null default 0
auto_assignment_paused bool not null default false
auto_assignment_pause_reason varchar(100) null
```

Constraint: `duplicate_of_ticket_id != id`; ticket `LINKED_DUPLICATE` bắt buộc có master và không được có assignment active.

Hai cột hiện có `sla_started_at` và `sla_due_at` tiếp tục là nguồn đúng cho SLA/mốc dự kiến. Không tạo thêm cột `current_due_at`; đây chỉ là tên field trao đổi ánh xạ từ `sla_due_at`.

### 7.2. `ai_analysis_runs`

- `contract_version = v4`.
- Bổ sung JSONB `duplicate` hoặc các cột tương đương để lưu đúng payload đã ký/validate.
- `exit_reason` đang là string nên lưu được enum mới, nhưng Pydantic enum và service dispatch phải cập nhật.

### 7.3. `ticket_assignments`

Bổ sung:

```text
AssignmentStatus: thêm REJECTED | REASSIGNED
assignment_source: COORDINATOR_MANUAL | AI_AUTO | AI_PROPOSAL_CONFIRMED
assigned_by_user_id: nullable với source = AI_AUTO
rejection_reason varchar(500) null
rejected_at timestamptz null
end_reason: TECHNICIAN_REJECTED | ACCEPTANCE_TIMEOUT | COORDINATOR_REASSIGNED | null
assignment_job_id uuid null
cycle_started_at timestamptz not null
acceptance_warning_at timestamptz null
acceptance_reassign_at timestamptz not null
warning_sent_at timestamptz null
case_member_count_snapshot int null check 1..5
completion_sla_extension_factor numeric(3,2) not null default 1.00 check 1.00..2.00
```

Giữ unique index hiện có “một assignment active trên một ticket”. Với source do người thực hiện, `assigned_by_user_id` bắt buộc; với `AI_AUTO`, field này null và audit actor là `SYSTEM`.

### 7.4. `ai_assignment_jobs`

Bảng/job store tối thiểu:

```text
id uuid PK
decision_id uuid null unique
batch_decision_id uuid null unique
model_request_id uuid null
work_item_type: TICKET | INCIDENT_CASE | null
work_item_id uuid null
ticket_id uuid null FK
incident_case_id uuid null FK
proposal_batch_id uuid null FK
trigger, mode, status
previous_assignment_id uuid null
reassignment_count_snapshot int
execute_after, primary_deadline_at, fallback_deadline_at
candidate_snapshot jsonb
selected_technician_id uuid null
primary_model, fallback_model, completed_model
decision_reason, error_code, error_detail
cancelled_by_user_id uuid null
created_at, started_at, completed_at

ai_assignment_job_members
- job_id uuid FK ai_assignment_jobs.id
- ticket_id uuid FK tickets.id
- is_active bool
```

Check constraint theo mode:

- `DIRECT`: `decision_id` có giá trị, `batch_decision_id/proposal_batch_id` null; `model_request_id` cho phép nhiều job độc lập được gửi trong cùng một lượt model; đúng một trong `ticket_id`/`incident_case_id` khớp `work_item_type`.
- `PROPOSAL`: `batch_decision_id/proposal_batch_id` có giá trị, `decision_id`, `work_item_type`, `work_item_id`, `ticket_id`, `incident_case_id` null; một job đại diện cho một lần gọi model trên toàn batch.

Chỉ job `DIRECT` tạo member `is_active=true`; unique partial index trên `ai_assignment_job_members(ticket_id) WHERE is_active` ngăn cùng một ticket nằm trong hai job `DIRECT` chưa kết thúc, kể cả khi một job đại diện case. Job `PROPOSAL` không khóa ticket, vì phân tay phải tiếp tục được trong lúc bảng mở. Khi job `DIRECT` terminal, Backend đặt các member `is_active=false`. Candidate snapshot và raw model output của DIRECT hoặc toàn batch PROPOSAL phải được lưu để audit.

### 7.5. `assignment_proposal_batches` và `assignment_proposal_items`

```text
assignment_proposal_batches
- id uuid PK
- status: BUILDING | READY | CONFIRMED | EXPIRED | CANCELLED
- requested_by_user_id uuid
- continue_auto_assignment bool null
- activation_delay: IMMEDIATE | 2_HOURS | 5_HOURS | 1_DAY | 3_DAYS | null
- ready_at, expires_at, confirmed_at, cancelled_at
- version int
- created_at, updated_at

assignment_proposal_items
- id uuid PK
- batch_id uuid FK
- work_item_type, work_item_id
- decision_id uuid unique
- decision_job_id uuid null FK ai_assignment_jobs.id
- proposed_technician_id uuid null
- final_technician_id uuid null
- completed_model varchar(100) null
- decided_at timestamptz null
- status: PENDING | PROPOSED | EMPTY | DESELECTED | ASSIGNED | SKIPPED_MANUAL_WON
- reason varchar(500) null
- created_at, updated_at

assignment_proposal_item_members
- batch_id uuid FK assignment_proposal_batches.id
- item_id uuid FK assignment_proposal_items.id
- ticket_id uuid FK tickets.id
- PK (item_id, ticket_id)
- UNIQUE (batch_id, ticket_id)
```

`assignment_proposal_items` cần unique `(id, batch_id)` để `assignment_proposal_item_members(item_id, batch_id)` dùng composite FK, ngăn truyền `batch_id` không khớp item. Batch `READY` bắt buộc có `expires_at = ready_at + 600 giây`. Confirm dùng optimistic `version`; batch hết hạn không được hồi sinh. `UNIQUE (batch_id, ticket_id)` bảo đảm một ticket chỉ xuất hiện trong một item của batch; tổng số member trong batch không vượt 20. Không dùng `ticket_ids jsonb` làm nguồn ràng buộc chính vì database không thể bảo vệ uniqueness xuyên các item một cách tin cậy.

### 7.6. `auto_assignment_settings`

Bảng singleton do Điều phối viên quản lý:

```text
id = 1
enabled bool not null default false
activation_delay: IMMEDIATE | 2_HOURS | 5_HOURS | 1_DAY | 3_DAYS
version int not null
updated_by_user_id uuid null
updated_at
```

Mở proposal không đổi row này. Chỉ confirm batch với `continue_auto_assignment=true` mới bật và cập nhật delay; confirm đợt một lần, cancel hoặc expire đều giữ `enabled=false`.

### 7.7. `ticket_relations`

Để không lạm dụng `duplicate_of_ticket_id`, quan hệ red-flag dùng bảng riêng:

```text
id uuid PK
source_ticket_id uuid FK tickets.id
target_ticket_id uuid FK tickets.id
relation_type: RED_FLAG_EVIDENCE
analysis_run_id uuid null
reason varchar(500)
created_at
```

Unique theo `(source_ticket_id, target_ticket_id, relation_type)`. Relation này không làm ticket nguồn biến thành duplicate và không loại nó khỏi hàng xử lý.

### 7.8. `duplicate_disputes` — đã loại bỏ

Bảng này và cột `tickets.duplicate_disputed_at` phục vụ luồng kháng nghị
duplicate của Cư dân, và đã bị xóa bằng migration tiến `7a8b9c0d1e2f`. Các
revision v4 tạo ra chúng (`1a2b3c4d5e6f`, `4d5e6f7a8b9c`, `5e6f7a8b9c0d`) là bất
biến nên vẫn còn nhắc tới; code, contract, tài liệu và test đang hoạt động thì
không.

Phần liên kết duplicate ở §3.1 và §7.1 không đổi: `duplicate_of_ticket_id`,
`duplicate_linked_at`, `duplicate_reason`, `duplicate_analysis_run_id`, trạng
thái `LINKED_DUPLICATE` và cả hai check constraint đều giữ nguyên.

### 7.9. Persistence case gom hiện có

Tiếp tục dùng `incident_cases` và `incident_case_members` hiện có. Finalize grouping tạo/cập nhật case sau khi Category chính thức đã chốt; `density_value` luôn được Backend tính bằng `COUNT(DISTINCT source_unit_id)`. Mỗi case có tối đa 5 member. Backend khóa case/chuỗi case khi thêm member; case hiện hành đủ 5 thì tạo case kế tiếp trong cùng chuỗi, không di chuyển member đã có và không bao giờ commit member thứ sáu vào cùng case.

Persistence phải có định danh chuỗi và thứ tự case tối thiểu tương đương:

```text
incident_cases
- series_id uuid not null
- sequence_no int not null
- UNIQUE (series_id, sequence_no)

incident_case_members
- UNIQUE (incident_case_id, ticket_id)
```

Giới hạn 5 member được bảo vệ trong transaction/service bằng khóa hàng theo `series_id`; constraint đơn thuần trên từng row member không đủ chống race. Cả DIRECT và PROPOSAL dùng `incident_case.id` làm `work_item_id` và toàn bộ member đủ điều kiện, tối đa 5, làm `ticket_ids`.

### 7.10. Phiên bản scoring rule hiện có

Tiếp tục dùng `scoring_rule_versions` và `ai_analysis_runs.rule_version_id`. Backend ghim đúng một rule version khi finalize tính điểm, lưu ID cùng các score components; thay đổi rule sau đó không được làm thay đổi audit của analysis run cũ.

---

## 8. Thông báo, audit và chống spam

### 8.1. Backend chịu trách nhiệm

AI không gửi notification. Backend tạo `Notification` cho mọi tài khoản trong căn hộ tại các mốc: duyệt, phân người, đổi người, đang xử lý, hoàn thành, bị liên kết duplicate và cập nhật rút gọn từ master.

Không có thông báo toàn căn hộ khi ticket còn trong giai đoạn AI riêng tư (§3.2): mọi mốc trên đều xảy ra sau khi phân loại đã kết thúc.

Thông báo đổi Kỹ thuật viên phải có mốc dự kiến mới. Thông báo duplicate chỉ có mã tham chiếu, Category, trạng thái và mốc dự kiến; không có dữ liệu nhận dạng người gửi master.

Audit tối thiểu:

- Input hash, output, model, duration/error của mỗi model attempt.
- Candidate snapshot và lý do chọn người.
- Link/giữ/tách duplicate.
- Tạo/confirm/cancel/expire proposal batch và lựa chọn bật công tắc sau batch.
- Kỹ thuật viên từ chối và lý do.
- Job được tạo, hủy, fallback, manual-required.
- Actor phân biệt `SYSTEM` với user cụ thể.

### 8.2. Bộ đếm chống spam

- Mọi lần tạo ticket tính vào ngưỡng `10 lần/1 giờ`, kể cả duplicate.
- `INSUFFICIENT_INPUT` với `invalid_reason = CONTENT_INSUFFICIENT` được tính vào `3 AI rejection/1 ngày`.
- Không tính duplicate và không tính timeout chờ Cư dân vào bộ đếm AI rejection.
- Chạm ngưỡng chỉ chặn tạo ticket mới trong 12 giờ; không khóa đăng nhập/xem ticket/nhận thông báo/gọi BQL.

---

## 9. Error contract

| HTTP/code | Khi nào |
| --- | --- |
| `400 CONTRACT_VALIDATION_ERROR` | Payload AI/tool sai schema |
| `400 CATEGORY_REQUIRED` | Category rỗng/ngoài catalog ghim, hoặc chốt `IMAGE`/`TEXT` khi không có prediction tương ứng/Category không thuộc prediction |
| `400 SEVERITY_REQUIRED` | Chốt `MANUAL_REVIEW` khi ticket chưa có Severity nhưng Điều phối viên không gửi Severity |
| `403 ASSIGNMENT_NOT_OWNED` | Kỹ thuật viên từ chối assignment của người khác |
| `409 INVALID_STATUS_TRANSITION` | Session/ticket/assignment sai trạng thái |
| `409 AGENT_BUDGET_EXHAUSTED` | Vượt tool/ask/time budget |
| `409 ANALYSIS_ALREADY_FINALIZED` | Finalize lần hai với payload khác |
| `409 DUPLICATE_CANDIDATE_STALE` | Master không còn đủ điều kiện lúc commit |
| `409 ACTIVE_ASSIGNMENT_EXISTS` | Race với phân tay/assignment khác |
| `409 ASSIGNMENT_JOB_ALREADY_ACTIVE` | Work item/ticket đã có job xung đột chưa kết thúc |
| `409 PROPOSAL_NOT_READY` | Batch chưa ở trạng thái có thể confirm |
| `409 PROPOSAL_EXPIRED` | Batch đã quá hạn 10 phút; phải tạo đợt mới |
| `422 NO_CANDIDATES` | Không có ứng viên cho mode `DIRECT`; Backend chuyển manual ngay. Với `PROPOSAL`, lưu dòng `EMPTY` thay vì trả lỗi làm hỏng batch |

Lỗi ra client không chứa prompt, stack trace, API key hoặc raw response nhạy cảm của model. Raw output chỉ lưu vùng audit có quyền hạn phù hợp.

---

## 10. Đối chiếu với code base hiện tại

| Khu vực code | Hiện trạng | Delta bắt buộc |
| --- | --- | --- |
| `src/models/agent_schemas.py` | Có 5 exit, `AgentAnalysisResultV3`; V3 còn `CONFIDENT_MATCH`/`CATEGORY_MISMATCH`; grouping còn nhận Density từ Agent | Tạo enum/schema V4 riêng với sáu exit §1.2, thay hai exit match/mismatch bằng `ANALYSIS_COMPLETE`, thêm object duplicate/red-flag relation; bỏ `density` khỏi grouping payload cuối và áp invariant §1.7. Giữ V3 cho session cũ |
| `src/models/enums.py` | Chưa có `LINKED_DUPLICATE`, `REJECTED`, `REASSIGNED` | Thêm enum/state mới; migration PostgreSQL tương ứng |
| `src/database/models/ticket.py` | Chưa có master link, invalid reason, dispute, reassign counter/pause; đã có `sla_started_at/sla_due_at` | Thêm cột/relationship/constraint §7.1, giữ SLA hiện có làm nguồn đúng và thêm relation/dispute §7.7–§7.8 |
| `src/database/models/ai_analysis.py` | Có grouping/tool usage, `exit_reason` dạng string | Thêm payload duplicate, contract v4 |
| `src/services/agent_tool_service.py` | Search chỉ phục vụ cùng tòa/tầng liền kề, tối đa 3 ngày | Thêm `purpose`; tách lọc DUPLICATE và GROUPING; trả lịch sử/mốc dự kiến đã làm sạch |
| `src/services/agent_result_service.py` | Chưa dispatch duplicate | Thêm validate + transaction link duplicate + notification/audit |
| `src/database/models/ticket_assignment.py` | `assigned_by_user_id` bắt buộc; chưa có reject/source/deadline nhận việc | Sửa theo §7.3; giữ unique active index và thêm các deadline §6.4 |
| `src/services/assignment_service.py` | Chỉ phân tay; `unable_to_handle` đóng ticket | Không dùng `unable_to_handle` thay reject; thêm reject/reassign orchestration |
| `src/api/routes/technician_assignments.py` | Chưa có endpoint từ chối | Thêm endpoint §6.1 |
| Assignment AI/job/proposal | Chưa có implementation | Thêm DIRECT request nhiều đơn vị và PROPOSAL request toàn batch, work item ticket/case, projected batch load, exclusion rule, primary/fallback cục bộ theo decision, durable worker, proposal 10 phút, settings toggle và coordinator cancel/confirm |
| `incident_cases`, `incident_case_members` | Đã có persistence grouping | Tái sử dụng theo §7.9, thêm `series_id/sequence_no`, bảo vệ tối đa 5 member/case và bổ sung đường phân AI cho cả case; không tạo case thứ sáu member |
| `scoring_rule_versions`, `ai_analysis_runs.rule_version_id` | Đã có nền tảng versioning | Bắt buộc ghim/lưu rule version khi finalize theo §7.10 |
| `src/config.py` | Chỉ có model chung | Thêm config §5.2 và `assignment_decision_engine` §4.0 |
| `src/assignment_rules/*` | Chưa có | Thêm `RULE_ENGINE_V1` §4.1a: cấu hình YAML, khóa xếp hạng thuần túy, `RuleBasedAssignmentService` cùng surface với `AssignmentAgentService` |
| `src/services/assignment_decision_engine.py` | Chưa có | Nơi duy nhất đọc `ASSIGNMENT_DECISION_ENGINE`, để API, worker và hai service không thể bất đồng về bộ máy đang chạy |
| `src/models/api/tickets.py` | `description` min 1, ảnh tùy chọn — đã đúng | Giữ nguyên; sửa mô tả route nào còn nói “text hoặc ảnh” |
| Notification/audit | Đã có nền tảng Backend | Mở rộng fan-out duplicate, system actor và toàn bộ event §8 |

Không được xem các delta này là tùy chọn. Nếu chỉ sửa schema mà không thêm migration, transaction, worker và audit thì contract chưa hoàn thành ở mức triển khai.

---

## 11. Giả định đã chốt để không chặn triển khai

1. “5 phút model chính + 5 phút fallback” được hiểu là hai request tuần tự, mỗi request có deadline cứng 300 giây; không có retry kéo dài thêm.
2. `locations.id` là định danh chuẩn cho một tài sản/vị trí chung. Dữ liệu chưa đủ chi tiết thì duplicate đi manual, không đoán bằng tên.
3. Khi red-flag trùng với sự cố đang có, ticket mới không bị auto-close. Backend giữ nhánh khẩn cấp; việc ghép bằng chứng/nâng mức master cần transaction hoặc duyệt riêng sau đó.
4. Trần 3 được hiểu là cho phép tối đa ba lần đổi người; khi bộ đếm trở thành 4 thì ngừng auto cho ticket.
5. `NO_SUITABLE_CANDIDATE` là kết quả hợp lệ, khác lỗi model, nên không gọi fallback.
6. Một lượt DIRECT có thể chứa nhiều đơn vị cần phân việc; giới hạn chung là tối đa 20 UUID ticket riêng biệt, còn mỗi decision/job/transaction vẫn độc lập.
7. Mỗi incident case tối đa 5 member. Member tràn tạo case kế tiếp cùng chuỗi, không tái phân bố case cũ.
8. SLA hoàn thành P1/P2 tăng 25% cho mỗi ticket bổ sung cùng case được giao trong một decision, tối đa gấp đôi; P3 và deadline nhận việc không kéo dài.
9. Việc chọn Kỹ thuật viên không cần LLM. Bộ máy mặc định là `RULE_ENGINE_V1` §4.1a; giả định 1 và 5 chỉ còn hiệu lực khi `ASSIGNMENT_DECISION_ENGINE=AI`.
10. Trần tải mặc định là “không giới hạn”. Việc tắt LLM tự nó không được làm đổi ai nhận việc; đặt trần là một quyết định vận hành riêng, có phiên bản qua `rule_version`.

Các giả định trên bám đúng quyết định nghiệp vụ hiện tại và loại bỏ câu hỏi mở cũ. Nếu chủ sản phẩm muốn đổi một trong các điểm này thì cần sửa đồng thời tài liệu nghiệp vụ, contract, migration và test acceptance.

---

## 12. Acceptance scenarios tối thiểu

1. **Text không ảnh:** tạo ticket, ba field ảnh null cùng nhau, Agent vẫn phân tích được; không bị từ chối ở API vì thiếu ảnh.
2. **Trùng đúng thang máy A:** ticket gốc còn hoạt động, cùng `location_id`, cùng hiện tượng, không red-flag mới → ticket mới thành `LINKED_DUPLICATE`, không có assignment, master nhận thêm một lượt phản ánh.
3. **Cùng Category nhưng thang máy B:** `location_id` khác → không auto-duplicate.
4. **Cùng sự cố nhưng xấu đi/red-flag:** ticket mới đi P3 độc lập, tạo `RED_FLAG_EVIDENCE` tới master và master bị yêu cầu đánh giá lại; không biến ticket mới thành duplicate.
5. **Kỹ thuật viên từ chối P1/P2:** assignment cũ `REJECTED`, tăng counter, job chờ đúng 300 giây. Điều phối viên phân tay trong cửa sổ thì job AI bị hủy và không ghi đè.
6. **Kỹ thuật viên từ chối P3:** chưa vượt trần và auto bật → job chạy ngay, không có `SCHEDULED_GRACE`.
7. **Lỗi model (`ASSIGNMENT_DECISION_ENGINE=AI`):** primary hết cửa sổ 300 giây thì fallback chạy; fallback thành công thì chỉ tạo một assignment. Cả hai lỗi thì pause riêng ticket và manual-required, global toggle vẫn bật.
8. **DIRECT không có ứng viên/không chọn được ai:** vào manual ngay và pause riêng ticket, không chờ hai cửa sổ model.
9. **Lần đổi người thứ tư:** không chạy bộ máy quyết định, cảnh báo và phân tay bắt buộc.
10. **Bật auto khi có hàng chờ:** bảng proposal chỉ lấy tối đa 20 ticket; bấm OK mới ghi assignment và actor là Điều phối viên.
11. **Duplicate chưa chắc chắn:** có ứng viên nhưng AI không đủ tự tin → `DUPLICATE_UNCERTAIN`, không tự liên kết và vào `MANUAL_REVIEW`.
12. **Giai đoạn AI riêng tư (§3.2):** trong lúc `classification_status` là `PENDING`/`PROCESSING`, chỉ người gửi thấy ticket; thành viên khác cùng căn hộ và Ban quản lý đều nhận not-found ở danh sách, chi tiết, ảnh và câu hỏi AI. Sau khi phân loại kết thúc, ticket được chia sẻ cho căn hộ và bàn giao Ban quản lý; hủy và trả lời câu hỏi AI vẫn chỉ dành cho người gửi. Không còn luồng kháng nghị duplicate.
13. **Grouping/Density:** Agent không gửi Density trong kết quả cuối; Backend tính theo căn hộ riêng biệt, persist `incident_case/member`, và proposal có thể phân cả case cho cùng một Kỹ thuật viên.
14. **Timeout Cư dân:** hết tổng 300 giây → question/session đóng, ticket `INVALID` với `RESIDENT_RESPONSE_TIMEOUT`; tính 10 lần/giờ nhưng không tính 3 AI rejection/ngày.
15. **Catalog pin:** thay đổi catalog trong lúc session chạy không đổi category snapshot/version hoặc kết quả validate của session đó.
16. **Proposal hết hạn:** sau 600 giây, confirm trả `PROPOSAL_EXPIRED`, không assignment được tạo và công tắc vẫn TẮT.
17. **Proposal lỗi cục bộ:** một dòng primary/fallback lỗi hoặc không có ứng viên trở thành `EMPTY`; các dòng còn lại vẫn READY/confirm được và ticket lỗi không bị auto-pause.
18. **Đồng hồ nhận việc:** P1/P2/P3 tạo đúng `acceptance_warning_at`/`acceptance_reassign_at`; nhận việc trước hạn làm worker bỏ qua, còn tái phân công tạo một cycle mới và không dùng deadline cũ.
19. **Scoring version:** thay đổi scoring rule sau khi một analysis run hoàn tất không làm đổi `rule_version_id`, score components hoặc kết quả audit của run cũ.
20. **Backend mới quyết match:** Agent V4 trả `ANALYSIS_COMPLETE` cùng Category hai nguồn; trường hợp khớp được Backend chốt, trường hợp mâu thuẫn được Backend chuyển `MANUAL_REVIEW`; Agent không trả `CATEGORY_MISMATCH`.
21. **Proposal cân bằng toàn batch:** cùng một Kỹ thuật viên có thể nhận nhiều ticket, nhưng mỗi quyết định sau phải tính cả số ticket đã đề xuất thêm cho người đó trong chính batch; không dùng lại `active_assignment_count` ban đầu như tải bất biến.
22. **Loại trừ người đã thất bại:** Kỹ thuật viên từng từ chối hoặc im lặng quá hạn trên work item không xuất hiện trong candidate của lần đổi tiếp theo; hết người thì manual, Điều phối viên vẫn có thể override khi phân tay.
23. **Gán muộn không đổi người tức thì:** ticket P1/P2/P3 được duyệt/gán sau deadline gốc vẫn nhận đúng sàn 1 giờ/30 phút/5 phút tính từ `assigned_at` trước khi worker đổi người.
24. **DIRECT incident case:** case chính thức có nhiều member đủ điều kiện được AI chọn một Kỹ thuật viên và Backend tạo assignment trên từng member; member thêm sau phải có quyết định mới.
25. **DIRECT nhiều đơn vị:** hai hoặc nhiều đơn vị cùng đủ điều kiện được gửi trong một `DirectAssignmentBatchRequestV4`; mỗi decision được validate/fallback/ghi độc lập và phân tay vẫn thắng trên từng ticket.
26. **Case đầy:** năm ticket hợp lệ nằm trong case thứ nhất; ticket thứ sáu cùng chuỗi tạo case thứ hai, không di chuyển năm member cũ và không có thời điểm nào case thứ nhất chứa sáu member.
27a. **`RULE_ENGINE_V1` — khóa xếp hạng:** với cùng một snapshot, một work item P3 chọn người ít việc P3 nhất kể cả khi tổng việc của họ cao hơn; một work item P1/P2 chọn người ít tổng việc nhất; hòa tải thì chọn người lâu chưa được giao nhất, và người chưa từng được giao xếp trước tất cả. Chạy lại cùng đầu vào luôn ra cùng một người.
27b. **`RULE_ENGINE_V1` — cân tải trong một đợt:** n work item giống nhau và n Kỹ thuật viên đang rảnh thì mỗi người nhận một; một case n member tiêu thụ n đơn vị tải dự kiến nên work item kế tiếp đi sang người khác.
27c. **`RULE_ENGINE_V1` — trần cấu hình:** người đã chạm `max_active_assignments` bị loại khỏi ứng viên; trần theo Priority chỉ chặn đúng Priority đang phân; trần mà Backend không gửi số đếm thì không có hiệu lực; mọi ứng viên vượt ngưỡng thì P1/P2 trả `NO_SUITABLE_CANDIDATE` còn P3 vẫn được đặt kèm ghi rõ ngoại lệ quá tải; `max_active_assignments = 2` không làm case 5 member trở thành không phân được.
27d. **Không cần model:** với `ASSIGNMENT_DECISION_ENGINE=RULE`, DIRECT và PROPOSAL chạy trọn vẹn khi không cấu hình `ASSIGNMENT_PRIMARY_MODEL`/`ASSIGNMENT_FALLBACK_MODEL`; startup của API và worker không từ chối vì thiếu cặp model, nhưng phải từ chối nếu `config/assignment_rules.yaml` có khóa sai hoặc giá trị trần không hợp lệ.
27e. **Đổi bộ máy:** đặt `ASSIGNMENT_DECISION_ENGINE=AI` khôi phục nguyên hành vi §5.2, không cần đổi code hay migration; `model_version` trên từng decision cho biết bộ máy nào đã sinh ra nó.

28. **SLA cụm:** một decision giao 1–5 ticket P1/P2 của case áp đúng hệ số 1,00/1,25/1,50/1,75/2,00; P3 vẫn 5 phút, deadline nhận việc không đổi và race phân tay làm hệ số được tính lại theo số member thực ghi.
