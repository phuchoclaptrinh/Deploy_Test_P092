# Contract: AI Classification Agent → Backend

## Bối cảnh & ranh giới trách nhiệm (đọc trước khi implement)

Contract này mô tả **duy nhất** ranh giới giữa AI Agent (bước C→G trong pipeline) và Backend. Theo mục 4.1 (đặc tả) và mục C (logic v3):

> Agent trích xuất dữ liệu có cấu trúc — Category, red-flag, severity, is_relevant, density (nếu có gộp) — và **không** tự quyết định Priority cuối, **không** tự so khớp Category để kết luận có cần duyệt thủ công, **không** tự tính điểm chính thức. Toàn bộ phần đó (bước H/I/J: category_match check, scoring formula, priority ceiling) là logic Backend thuần, chạy sau, tra bảng — không gọi AI thêm.

Vì vậy contract có 2 phần:

1. **Agent → Backend (kết quả cuối 1 lượt phân tích)** — object `AgentAnalysisResult`, Backend dùng để chạy G→H→I→J.
2. **Backend → Agent (tool contracts)** — 3 tool mà Agent được gọi trong vòng E (`get_category_catalog`, `search_related_tickets`, `propose_case_grouping`) cộng `ask_resident` (nửa tool, nửa side-effect UI). Đây là API mà Backend **cung cấp cho** Agent gọi, nên chiều dữ liệu ngược lại so với phần 1.

Agent chạy trong 1 vòng lặp (E → F → quay lại E hoặc thoát), nên object ở phần 1 chỉ được Backend nhận **một lần duy nhất** — lúc Agent thoát vòng lặp (đủ tự tin, hoặc chạm giới hạn, hoặc red-flag lộ ra). Các lượt gọi tool ở giữa không tạo ra `AgentAnalysisResult` — chúng là tool call riêng, Backend chỉ log lại phục vụ audit/đếm ngân sách.

---

## Phần 1 — `AgentAnalysisResult` (Agent → Backend)

### 1.1. Khi nào Backend nhận object này

Đúng 1 lần mỗi lượt phân tích ticket, tại một trong các điểm thoát của vòng E/F/G:

| Điểm thoát | Điều kiện | `exit_reason` |
| --- | --- | --- |
| Red-flag lộ ra (D1) | `red_flag_text` hoặc `red_flag_signal` = true, ở bất kỳ vòng nào | `RED_FLAG` |
| Đủ tự tin, category khớp (G→H) | `is_confident=true` và text/image category khớp | `CONFIDENT_MATCH` |
| Đủ tự tin, category KHÔNG khớp (G→F1) | `is_confident=true` nhưng không khớp | `CATEGORY_MISMATCH` |
| Chạm giới hạn công cụ/hỏi, vẫn chưa tự tin (F1) | `tool_call_count=5` hoặc `ask_resident_rounds=3`, `is_confident=false` | `LIMIT_REACHED` |
| Ảnh/text không đủ để hiểu (C1) | ngay từ đầu, chưa từng vào vòng E | `INSUFFICIENT_INPUT` |

`INSUFFICIENT_INPUT` xảy ra ở bước C1 — **trước khi** ticket chính thức được tạo, nên đây thực chất là phản hồi đồng bộ ngay trong request đầu, không đi qua vòng E. Tôi để nó chung schema cho đơn giản, nhưng Backend cần biết: nếu `exit_reason=INSUFFICIENT_INPUT`, không tạo ticket, chỉ trả C2 message cho cư dân.

Timeout (F2 — hết 5 phút không phản hồi, không nghi ngờ nguy hiểm) **không** đi qua object này — đó là Backend tự phát hiện bằng đồng hồ chờ, không phải Agent trả về. Agent không có nghĩa vụ báo timeout.

### 1.2. Schema

```jsonc
{
  "ticket_id": "string (uuid)",
  "analysis_session_id": "string (uuid)",   // 1 session = 1 lượt phân tích, xuyên suốt cả vòng E lặp lại nhiều lần
  "exit_reason": "RED_FLAG | CONFIDENT_MATCH | CATEGORY_MISMATCH | LIMIT_REACHED | INSUFFICIENT_INPUT",

  // ---- Trích xuất từ text (luôn có, vì text bắt buộc) ----
  "text_categories": ["string"],            // 1 hoặc nhiều category_id, theo danh mục get_category_catalog() đã fetch lúc bắt đầu
  "red_flag_text": "boolean",

  // ---- Trích xuất từ ảnh (null nếu không có ảnh) ----
  "image_categories": ["string"] | null,
  "red_flag_signal": "boolean | null",
  "is_relevant": "boolean | null",          // ảnh có liên quan tới sự cố chung cư không; null nếu không có ảnh

  // ---- Severity (luôn có 1 giá trị nếu exit_reason không phải INSUFFICIENT_INPUT) ----
  "severity": "LOW | MEDIUM | HIGH",
  "severity_source": "IMAGE | TEXT",        // ảnh nếu có, text nếu không — theo mục C bảng trường dữ liệu

  // ---- Kết quả suy luận của Agent (KHÔNG phải Priority, chỉ là niềm tin nội bộ) ----
  "is_confident": "boolean",                // false khi exit_reason = LIMIT_REACHED; luôn true khi RED_FLAG/CONFIDENT_MATCH/CATEGORY_MISMATCH
  "confidence_notes": "string | null",      // Agent giải thích ngắn tại sao chưa tự tin — hiển thị cho Điều phối viên ở F1, không hiển thị cho cư dân

  // ---- Gộp ticket (chỉ có giá trị khi Agent đã gọi propose_case_grouping ở E2 và quyết định gộp) ----
  "grouping": {
    "grouped": "boolean",
    "density": "integer",                  // số căn hộ bị ảnh hưởng, bao gồm ticket hiện tại; chỉ có ý nghĩa khi grouped=true
    "related_ticket_ids": ["string"],       // các ticket được gộp cùng, rỗng nếu grouped=false
    "reason": "string"                      // lý do ngắn Agent đưa ra khi gọi tool, bắt buộc nếu grouped=true
  } | null,                                  // null nếu category không phải rò nước/chập điện, hoặc Agent không gọi E2

  // ---- Vết audit trong session (để Backend biết Agent đã dùng bao nhiêu ngân sách) ----
  "tool_usage": {
    "total_tool_calls": "integer",          // tổng E1+E2+E3, tối đa 5 theo mục 4.1
    "ask_resident_rounds": "integer",        // tối đa 3
    "ask_resident_elapsed_seconds": "integer", // tổng thời gian chờ trả lời cộng dồn qua các vòng, tối đa 300
    "search_related_tickets_called": "boolean",
    "propose_case_grouping_called": "boolean"
  },

  "category_catalog_version": "string",     // version/hash của danh mục category Agent đã dùng, để Backend audit khớp đúng danh mục hiệu lực lúc đó
  "model_version": "string",                // định danh model đã chạy (vd. "gpt-5.6-terra-2026-06"), phục vụ audit/debug
  "analyzed_at": "string (ISO 8601 timestamp)"
}
```

### 1.3. Ràng buộc & bất biến (Backend nên validate, không tin tưởng mù)

- Nếu `red_flag_text=true` hoặc `red_flag_signal=true` → `exit_reason` **phải** là `RED_FLAG`, bất kể các field khác. Backend nên tự kiểm tra lại field này độc lập (không chỉ tin `exit_reason`), vì mục 4.2 gọi đây là "luật cứng" — không thuộc phạm vi Agent tự do quyết định.
- `exit_reason=RED_FLAG` → Backend set Priority=P3 ngay, **không** cần `is_confident`, `text_categories`/`image_categories` khớp hay không, hay `grouping` — bỏ qua toàn bộ, đi thẳng tới K. Field category vẫn nên được lưu lại (nếu có) để thống kê, nhưng không dùng để tính điểm.
- `exit_reason=LIMIT_REACHED` → `is_confident` phải là `false`. `tool_usage.total_tool_calls=5` HOẶC `ask_resident_rounds=3` (ít nhất 1 trong 2 chạm mức trần).
- `exit_reason=INSUFFICIENT_INPUT` → toàn bộ các field severity/category/grouping có thể là `null`; Backend không tạo ticket chính thức, chỉ trả về message C2.
- `grouping` chỉ hợp lệ khi category (từ `text_categories` hoặc `image_categories`) thuộc {Rò nước, Chập điện} — theo mục 4.3/E2. Nếu Agent trả `grouping` cho category khác, Backend nên coi là lỗi và bỏ qua field này (log warning), không chặn pipeline.
- `image_categories`, `red_flag_signal`, `is_relevant` là `null` khi và chỉ khi ticket không có ảnh — không dùng `false`/`[]` để biểu diễn "không có ảnh", tránh nhầm với "có ảnh nhưng không phát hiện gì".

### 1.4. Điều Backend **không** được suy ra từ object này (phải tự tính, không xin Agent)

Theo đúng nguyên tắc "Agent không tự quyết định" ở mục 4.1/C, các việc sau **luôn thuộc Backend**, contract này không cấp field cho nó:

- `category_match` (so khớp `text_categories` vs `image_categories`) — Backend tự so sánh ở bước G.
- Điểm số thô (Category base + Vị trí×Category + Density + Severity) — Backend tự tra bảng mục I.
- Priority cuối cùng (P1/P2/P3) và áp Priority Ceiling — Backend tự tính mục J.
- Ticket có cần chuyển P0 (chờ duyệt thủ công) hay không — Backend tự suy từ `exit_reason` (`CATEGORY_MISMATCH` hoặc `LIMIT_REACHED` → P0), không đọc field boolean nào từ Agent nói "cần duyệt thủ công".

---

## Phần 2 — Tool contracts (Backend → Agent, Agent gọi trong vòng E)

### 2.1. `get_category_catalog()`

Gọi 1 lần đầu bước C, trước khi phân loại — đảm bảo dùng đúng danh mục category đang hiệu lực (mục C, do BQL có thể sửa qua màn hình quản trị).

**Request:** không tham số.

**Response:**
```jsonc
{
  "catalog_version": "string",
  "categories": [
    {
      "category_id": "string",
      "display_name": "string",
      "priority_ceiling": "P1 | P2 | P3 | UNLIMITED",
      "base_score": "integer"
    }
  ]
}
```

Không tính vào `tool_usage.total_tool_calls` (ngân sách 5 lần chỉ áp dụng cho E1/E2/E3 theo mục 4.1 — catalog fetch là bước bắt buộc ở C, không thuộc vòng E).

### 2.2. `search_related_tickets` (E1)

**Request:**
```jsonc
{
  "ticket_id": "string",
  "category_ids": ["string"],
  "floor": "string",
  "location": "string",
  "include_resolved": "boolean",   // mở rộng xem cả ticket đã xử lý xong trong quá khứ
  "lookback_days": "integer"       // Agent tự chọn khoảng thời gian tra cứu
}
```

**Response:**
```jsonc
{
  "related_tickets": [
    {
      "ticket_id": "string",
      "category_ids": ["string"],
      "floor": "string",
      "location": "string",
      "status": "string",
      "summary": "string",          // tóm tắt ngắn, KHÔNG trả nguyên văn mô tả gốc của cư dân khác (mục E1 — tránh rò dữ liệu cá nhân)
      "created_at": "string (ISO 8601)"
    }
  ]
}
```

### 2.3. `propose_case_grouping` (E2)

Chỉ hợp lệ khi category hiện tại là Rò nước hoặc Chập điện.

**Request:**
```jsonc
{
  "ticket_id": "string",
  "related_ticket_ids": ["string"],  // danh sách Agent tự chọn từ kết quả E1, cho là cùng 1 sự cố lan rộng
  "reason": "string"                  // lý do ngắn, bắt buộc
}
```

**Response:**
```jsonc
{
  "accepted": "boolean",     // Backend có thể tự chặn (vd. category không hợp lệ cho grouping) — Agent cần đọc lại field này
  "density": "integer",      // số căn hộ bị ảnh hưởng, Backend tính (= số related_ticket_ids hợp lệ + 1), không phải Agent tự đếm
  "rejected_reason": "string | null"
}
```

Nếu Agent không gọi tool này, mặc định không gộp (`grouping=null` trong `AgentAnalysisResult` phần 1) — không cần gọi để "xác nhận không gộp" tường minh.

### 2.4. `ask_resident` (E3)

**Request:**
```jsonc
{
  "ticket_id": "string",
  "question_type": "MULTIPLE_CHOICE | FREE_TEXT",
  "question_text": "string",
  "options": ["string"] | null,       // null nếu FREE_TEXT; bao gồm "Chụp lại ảnh khác" nếu câu hỏi liên quan tới ảnh
  "allow_free_text_fallback": "boolean" // true nếu MULTIPLE_CHOICE nhưng vẫn cho phép cư dân tự nhập (mục E3: "có phương án khác")
}
```

**Response:**
```jsonc
{
  "answered": "boolean",              // false nếu hết 5 phút tổng cộng mà chưa trả lời kịp
  "answer_type": "OPTION | FREE_TEXT | NEW_PHOTO | null",
  "answer_text": "string | null",
  "new_photo_ref": "string | null",   // nếu cư dân chọn "Chụp lại ảnh khác"
  "elapsed_seconds": "integer",       // thời gian chờ riêng lượt này, Backend cộng dồn vào ask_resident_elapsed_seconds
  "round_number": "integer"           // 1, 2, hoặc 3
}
```

Nếu `answered=false` (timeout tổng 5 phút), Backend tự phát hiện và xử lý F2 (vô hiệu hóa) — Agent nhận response này và nên dừng lại, không gọi thêm gì nữa; Agent **không** tự trả `AgentAnalysisResult` với `exit_reason` gì trong trường hợp này vì F2 không đi qua Phần 1 (xem mục 1.1).

Nếu response mới (`answer_text` hoặc `new_photo_ref`) chứa dấu hiệu nguy hiểm, Agent phải tự phát hiện ngay tại vòng này và thoát luôn với `exit_reason=RED_FLAG` trong `AgentAnalysisResult` — không gọi thêm `ask_resident` hay tool nào khác, đúng mục D/4.2 ("kiểm tra lại ở MỌI bước phía sau").

---

## Câu hỏi mở cần xác nhận thêm

1. **`confidence_notes`** — mình để field này optional/free-text cho Điều phối viên đọc ở F1. Nếu muốn structured hơn (vd. enum lý do: "category mơ hồ", "severity không rõ", "ảnh chất lượng thấp"), báo mình sửa lại thành enum + optional detail.
2. **Ràng buộc `severity` phải khác null khi `exit_reason=LIMIT_REACHED`** — mục 4.1 nói agent "chưa đủ tự tin về Category hoặc mức độ nghiêm trọng" mới hỏi thêm, nghĩa là severity **có thể** cũng chưa chắc lúc rơi vào P0. Hiện tại mình vẫn bắt buộc Agent trả 1 giá trị severity (best-guess) kể cả khi thoát ở LIMIT_REACHED/CATEGORY_MISMATCH, để Điều phối viên có điểm khởi đầu tham khảo. Xác nhận đây là hành vi mong muốn, hay muốn cho phép `severity=null` khi Agent thực sự không đoán được?
3. **`tool_usage`** — hiện đang gộp cả `get_category_catalog` là KHÔNG tính vào ngân sách 5 lần. Xác nhận lại theo đúng ý mục 4.1 (ngân sách chỉ áp dụng "các công cụ trên" = E1/E2/E3).
