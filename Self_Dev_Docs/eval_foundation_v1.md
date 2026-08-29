# Eval Foundation v1

## Hệ thống phân tích, phân loại và phân việc phản ánh chung cư

> **Trạng thái:** FOUNDATION DRAFT — chưa chạy Vibe Check, chưa tạo reference dataset, chưa chạy Offline Eval, chưa calibrate Judge và chưa có quyết định Ship/Hold.
>
> Tài liệu này chỉ chốt nền móng đánh giá. Không được diễn giải các ví dụ hoặc taxonomy ban đầu dưới đây như kết quả chất lượng thực tế của hệ thống.

---

## 1. Mục tiêu của Foundation

Foundation trả lời sáu câu hỏi trước khi chạy bất kỳ eval nào:

1. Hệ thống hứa làm đúng điều gì?
2. Thành phần nào chịu trách nhiệm cho từng quyết định?
3. Một đơn vị được chấm là gì?
4. Cần lưu trace nào để tái dựng được quyết định?
5. Lỗi được phân loại theo taxonomy nào?
6. Tiêu chí nào phải hoàn tất trước khi chuyển sang Vibe Check?

Foundation **không** làm các việc sau:

- Không sinh 40–50 test input.
- Không tạo golden label.
- Không chạy Agent, LLM, Backend worker hoặc simulator.
- Không đặt ngưỡng Ship/Hold cuối cùng.
- Không khẳng định model hiện tại đạt hay không đạt chất lượng.

---

## 2. Nguồn nghiệp vụ chuẩn

Eval phải bám theo thứ tự:

1. `dac_ta_tinh_nang_luong_nghiep_vu_v4.md` — quyết định nghiệp vụ.
2. `agent_backend_contract_v4.md` — schema và invariant tại ranh giới AI/Backend.
3. `Logic_xử_lý_chính_v4.md` — pipeline kỹ thuật hợp nhất.
4. Foundation này — quy ước tổ chức eval.
5. Code hiện tại — đối tượng được đánh giá, không được dùng để âm thầm thay đổi expected behavior.

Hai file `ai_evaluation_metrics_v1.md` và `ai_evaluation_metrics_v2.md` là tài liệu eval cũ, chỉ dùng để tham khảo ý tưởng metric. Chúng chưa phải nguồn chuẩn cho V4 vì còn các khác biệt:

- V2 mô tả `is_relevant` như đánh giá chung toàn ticket; contract V4 định nghĩa field này cho nguồn ảnh.
- V2 chưa bao phủ đầy đủ `DUPLICATE_EXISTING`, `DUPLICATE_UNCERTAIN` và `RED_FLAG_EVIDENCE`.
- V2 chưa có LLM chọn Kỹ thuật viên theo DIRECT/PROPOSAL.
- V2 chưa có DIRECT nhiều đơn vị, tối đa 5 ticket/cụm, tách chuỗi case và hệ số SLA cụm.
- V2 có một số expected behavior thuộc Backend nhưng đặt lẫn vào đánh giá Agent.

Không được lấy nguyên ma trận 32 case của V2 làm reference dataset V4 nếu chưa rà và gán nhãn lại.

---

## 3. Vị trí trong vòng đời eval

```text
Pha 0 — Foundation hiện tại
    ↓
Pha 1 — Vibe Check: chạy ít case, đọc output/trace bằng tay
    ↓
Pha 2 — Offline Eval: rubric + reference dataset + grader + report
    ↓
Pha 3 — Production Monitoring: quality/cost/drift/incident
    ↓
Continuous Improvement: lỗi thật quay lại dataset và regression suite
```

Tài liệu này chỉ hoàn thành **Pha 0**. Chưa có bằng chứng thực nghiệm để khóa failure taxonomy hoặc rubric cuối cùng.

---

## 4. Ranh giới hệ thống cần đánh giá

Phải tách ba chủ thể, không gộp điểm:

| Chủ thể | Trách nhiệm cần đánh giá |
| --- | --- |
| **Agent phân tích** | Đọc text/ảnh; trích xuất Category theo nguồn, red-flag, severity; dùng tool; hỏi Cư dân; nhận diện duplicate/grouping và kết thúc bằng đúng exit reason. |
| **Backend xác định nghiệp vụ** | Validate contract; đối chiếu Category; tính Density/điểm/Ceiling/Priority; ghi trạng thái; chia case; tính SLA; bảo vệ transaction/idempotency. |
| **LLM chọn Kỹ thuật viên** | Với DIRECT hoặc PROPOSAL, chọn KTV trong candidate snapshot dựa trên kỹ năng và tải; trả đúng một decision cho mỗi đơn vị. Không dùng tool. |

Điểm tổng hợp toàn hệ thống chỉ được lập sau khi đã có điểm riêng cho ba chủ thể. Nếu không tách, lỗi Backend có thể bị ghi nhầm thành lỗi model hoặc ngược lại.

---

## 5. Product promises

### 5.1. Agent phân tích ticket

Agent phải:

- Xử lý text bắt buộc và ảnh tùy chọn.
- Trích xuất `text_categories` và `image_categories` độc lập theo catalog được ghim.
- Phát hiện red-flag ở text, ảnh và dữ liệu bổ sung; red-flag phải thắng duplicate/grouping.
- Trả severity cùng nguồn `TEXT` hoặc `IMAGE` đúng contract.
- Không tự chốt Category chính thức, điểm hoặc Priority cuối.
- Dùng tối đa 5 tool call tính phí, tối đa 3 lượt hỏi và tổng 300 giây chờ Cư dân.
- Trả đúng một trong sáu exit reason V4.
- Không gửi field nội bộ hoặc field ngoài schema `extra = forbid`.

### 5.2. Duplicate

Hệ thống phải:

- Chỉ auto-link khi cùng sự cố, cùng chính xác `location_id`, ticket gốc còn hoạt động và không có red-flag/thông tin cần xử lý riêng.
- Chuẩn hóa nhiều candidate về một `master_ticket_id` cuối cùng.
- Không auto-link khi chưa đủ chắc chắn; đưa sang duyệt thủ công.
- Không đóng ticket P3 mới như duplicate; giữ xử lý khẩn cấp và có thể tạo quan hệ bằng chứng tới master.
- Không tạo assignment riêng cho `LINKED_DUPLICATE`.

### 5.3. Grouping và Density

Hệ thống phải:

- Chỉ grouping rò nước hoặc chập điện theo quan hệ tòa/tầng/thời gian đã chốt.
- Tính Density theo căn hộ riêng biệt, không theo tài khoản hoặc số ticket.
- Giữ mỗi ticket là ticket nghiệp vụ độc lập.
- Giới hạn mỗi `INCIDENT_CASE` tối đa 5 ticket.
- Khi case đầy, tạo case kế tiếp trong cùng chuỗi; không commit member thứ sáu và không di chuyển member đã phân việc.

### 5.4. Backend Category/Priority

Backend phải:

- Tự đối chiếu tập Category text/ảnh; Agent không trả kết luận match/mismatch.
- Dùng đúng catalog và scoring-rule version đã ghim.
- Tính `Base + Location×Category + Density + Severity`, sau đó áp Ceiling.
- Red-flag ép P3 và bỏ qua scoring/Ceiling.
- P0 chỉ là trạng thái duyệt thủ công, không phải Priority.

### 5.5. LLM chọn Kỹ thuật viên — DIRECT

Hệ thống phải:

- Cho phép một request DIRECT chứa nhiều đơn vị, tối đa 20 UUID ticket riêng biệt.
- Mỗi đơn vị là một ticket hoặc một case; mỗi decision/job/transaction độc lập.
- Chỉ cho model chọn KTV thuộc candidate snapshot của chính decision.
- Gán ngay decision hợp lệ, không thêm bước Điều phối viên duyệt.
- Giữ decision hợp lệ khi decision khác cần fallback.
- Để phân tay thắng race trên từng ticket.

### 5.6. LLM chọn Kỹ thuật viên — PROPOSAL

Hệ thống phải:

- Lập bảng đề xuất tối đa 20 ticket riêng biệt cho hàng chờ hiện tại.
- Không tạo assignment trước khi Điều phối viên bấm OK.
- Giữ các dòng hợp lệ khi dòng khác lỗi/không có người.
- Không tách một case để lấp chỗ còn lại của batch.
- Hết hạn, đóng hoặc hủy bảng thì không tạo assignment và không tự bật công tắc.

### 5.7. SLA cụm

Theo tài liệu V4 hiện tại:

- Case có 1–5 ticket được giao cùng KTV trong một decision.
- P1/P2 tăng 25% SLA hoàn thành cho mỗi ticket bổ sung, tối đa 2 lần.
- P3 giữ 5 phút.
- Deadline nhận việc/cảnh báo/đổi người không kéo dài.
- Member được thêm sau dùng decision/cycle mới và không hồi tố assignment cũ.

Quy tắc này là đề xuất nghiệp vụ mới và phải được Product Owner xác nhận trước khi dùng làm golden label khóa cứng.

---

## 6. Unit of evaluation

Không dùng một khái niệm “test case” chung cho mọi lớp.

| Eval suite | Đơn vị đánh giá chính | Đơn vị phụ |
| --- | --- | --- |
| Analysis Agent | Một `AIAnalysisSession` của một ticket từ lúc bắt đầu đến finalize/timeout | Từng model/tool attempt |
| Duplicate | Một ticket mới + candidate snapshot + quyết định liên kết | Commit validation tại Backend |
| Grouping | Một ticket mới + tập ticket liên quan + quyết định case | Case series và từng member |
| Backend scoring | Một analysis result hợp lệ + dữ liệu authoritative | Từng score component |
| DIRECT | Một `decision_id` cho một ticket/case | Toàn `request_id` để kiểm tra batching và cân bằng tải |
| PROPOSAL | Một proposal item/`decision_id` | Toàn `proposal_batch_id` để kiểm tra giới hạn, cân bằng và confirm |
| SLA case | Một decision case đã ghi assignment | Từng ticket member và mốc due tương ứng |

Quy ước:

- **Decision-level** là đơn vị chấm chất lượng chọn KTV.
- **Request/batch-level** là đơn vị chấm completeness, phân tải, partial fallback và giới hạn 20.
- Không kết luận toàn request Fail chỉ vì một decision Fail; report phải hiển thị cả hai cấp.

---

## 7. Input dimensions ban đầu

Dimension chỉ mô tả biến thể đầu vào. Expected output nằm ở golden label, không đặt trong dimension.

### 7.1. Analysis Agent

1. Chất lượng text: rõ / hiểu ý chính nhưng thiếu / không hiểu.
2. Trạng thái ảnh: không ảnh / rõ-liên quan / rõ-không liên quan / không đủ rõ.
3. Quan hệ Category text–ảnh: text-only / một giao rõ / không giao / nhiều giao chưa phân giải.
4. Nguồn red-flag: không có / text / ảnh / câu trả lời bổ sung.
5. Quan hệ giọng văn–mức nghiêm trọng: phù hợp / giọng gấp nhưng nhẹ / giọng bình thường nhưng nghiêm trọng.

### 7.2. Duplicate

1. Số ticket active cùng chính xác `location_id`: 0 / 1 / nhiều.
2. Quan hệ Category: cùng / nhóm tương đương / khác.
3. Quan hệ biểu hiện sự cố: cùng / khác.
4. Trạng thái candidate: chờ duyệt / đã duyệt / đã phân / đang xử lý / terminal.
5. Red-flag ở ticket mới: không / text / ảnh.

### 7.3. Grouping

1. Category: rò nước / chập điện / Category khác.
2. Phân bố tầng: cùng tầng / tầng liền trên / tầng liền dưới / ngoài phạm vi; các nhóm có thể đồng thời tồn tại.
3. Khoảng thời gian giữa các ticket liên quan.
4. Số căn hộ riêng biệt bị ảnh hưởng.
5. Trạng thái sức chứa case: còn chỗ / đúng 5 / có ticket tràn cần case kế tiếp.

### 7.4. Backend scoring

1. Exit reason đầu vào.
2. Giao của Category text/ảnh.
3. Tổ hợp score component: base / location bonus / density / severity.
4. Priority Ceiling: không giới hạn / P2 / P1.
5. Red-flag override: có / không.

### 7.5. DIRECT

1. Trigger: initial / reassign rejected / reassign silent.
2. Thành phần request: chỉ ticket / chỉ case / có cả hai.
3. Số candidate của từng decision: 0 / 1 / nhiều.
4. Phân bố kỹ năng phù hợp giữa candidates.
5. Phân bố tải hiện tại giữa candidates.

### 7.6. PROPOSAL

1. Thành phần batch: chỉ ticket / chỉ case / có cả hai.
2. Tổng số ticket riêng biệt: nhỏ / sát giới hạn / đúng 20 / vượt 20.
3. Số candidate của từng item: 0 / 1 / nhiều.
4. Phân bố kỹ năng phù hợp giữa candidates.
5. Phân bố tải hiện tại giữa candidates.

Giá trị “nhỏ”, “sát giới hạn” chỉ là bucket thiết kế test, không phải enum runtime. Giá trị cụ thể sẽ được chốt trong Eval Plan sau Vibe Check.

---

## 8. Trace contract phục vụ eval

### 8.1. Trường chung cho mọi model attempt

- `trace_id`, `request_id`, `decision_id` hoặc `analysis_session_id`.
- `contract_version`, `model_version`, prompt/config version.
- Input hash và sanitized input snapshot.
- Thời điểm bắt đầu/kết thúc, duration, timeout deadline.
- Kết quả parse/validate schema.
- Error code và error detail đã làm sạch.
- Attempt type: primary/fallback.
- Idempotency key.

### 8.2. Trace Analysis Agent

- Catalog version đã ghim.
- Category theo từng nguồn.
- Red-flag theo từng nguồn và thời điểm phát hiện.
- Severity và severity source.
- Trạng thái ảnh/relevance.
- Danh sách tool call theo thứ tự, input/output đã làm sạch và duration.
- Số lượt hỏi, câu hỏi, loại câu hỏi, câu trả lời và thời gian chờ.
- Candidate duplicate/grouping do Backend trả.
- Đề xuất grouping và kết quả Backend chấp nhận/từ chối.
- Exit reason, confidence fields và payload finalize.

### 8.3. Trace Backend classification/scoring

- Contract validation result.
- Category intersection và Category chính thức.
- Density theo căn hộ riêng biệt.
- Base, location bonus, density score, severity score.
- Raw score, Ceiling, final Priority và rule version.
- State transition, transaction result và audit actor.
- Duplicate master validation/stale result.
- Case `series_id`, `sequence_no`, member count trước/sau.

### 8.4. Trace LLM chọn KTV

- Mode DIRECT/PROPOSAL.
- Request/batch ID và danh sách decision ID.
- Candidate snapshot riêng từng decision.
- Required/matched skills và active assignment count.
- Excluded technician IDs do Backend tạo.
- Selected technician, reason và contract validation.
- Decision nào primary thành công, decision nào đi fallback.
- `NO_SUITABLE_CANDIDATE` khác lỗi kỹ thuật.
- Kết quả ghi assignment, manual-wins hoặc pause/manual-required.
- Case member count thực ghi và SLA extension factor.

### 8.5. Bảo mật trace

- Không log API key, system prompt nhạy cảm hoặc stack trace ra log thường.
- Không đưa PII của Cư dân vào dataset/LLM Judge nếu không cần thiết.
- Summary ticket dùng cho duplicate/assignment phải được làm sạch.
- Raw output nhạy cảm chỉ lưu vùng audit có quyền phù hợp.

---

## 9. Failure taxonomy ban đầu

Taxonomy này là giả thuyết để bắt đầu đọc trace; chỉ khóa sau Vibe Check.

### 9.1. Contract và orchestration

- `CONTRACT_SCHEMA_INVALID`
- `UNKNOWN_ENUM_OR_EXTRA_FIELD`
- `MISSING_OR_DUPLICATE_DECISION`
- `IDEMPOTENCY_BROKEN`
- `PRIMARY_FALLBACK_ROUTING_WRONG`
- `MANUAL_WINS_VIOLATED`

### 9.2. Analysis semantic

- `TEXT_CATEGORY_WRONG`
- `IMAGE_CATEGORY_WRONG`
- `RED_FLAG_MISSED`
- `FALSE_RED_FLAG`
- `SEVERITY_WRONG`
- `SEVERITY_SOURCE_WRONG`
- `IMAGE_RELEVANCE_WRONG`
- `WRONG_EXIT_REASON`

### 9.3. Tool behavior

- `UNNECESSARY_TOOL_CALL`
- `REQUIRED_TOOL_NOT_CALLED`
- `TOOL_BUDGET_EXCEEDED`
- `ASK_ROUND_EXCEEDED`
- `WAIT_BUDGET_EXCEEDED`
- `RED_FLAG_NOT_SHORT_CIRCUITED`

### 9.4. Duplicate và grouping

- `DUPLICATE_FALSE_POSITIVE`
- `DUPLICATE_FALSE_NEGATIVE`
- `DUPLICATE_UNCERTAIN_AUTO_LINKED`
- `RED_FLAG_CLOSED_AS_DUPLICATE`
- `GROUPING_FALSE_POSITIVE`
- `GROUPING_FALSE_NEGATIVE`
- `DENSITY_COUNT_WRONG`
- `CASE_MEMBER_LIMIT_EXCEEDED`
- `CASE_SPLIT_SERIES_WRONG`

### 9.5. Backend deterministic logic

- `CATEGORY_INTERSECTION_WRONG`
- `SCORING_COMPONENT_WRONG`
- `CEILING_WRONG`
- `PRIORITY_WRONG`
- `CATALOG_OR_RULE_VERSION_WRONG`
- `INVALID_STATE_TRANSITION`
- `SLA_EXTENSION_WRONG`

### 9.6. Assignment selection

- `SELECTED_OUTSIDE_SNAPSHOT`
- `SKILL_MATCH_IGNORED`
- `LOAD_NOT_CONSIDERED`
- `DIRECT_DECISION_NOT_APPLIED`
- `PROPOSAL_APPLIED_BEFORE_CONFIRM`
- `BATCH_LIMIT_EXCEEDED`
- `CASE_SPLIT_ACROSS_BATCH`
- `NO_CANDIDATE_MODEL_STILL_CALLED`

---

## 10. Nguyên tắc rubric và grader

### 10.1. Rubric

- Mỗi rubric chỉ chấm một tiêu chí quan sát được.
- Ưu tiên Pass/Fail với điều kiện rõ; không dùng mô tả cảm tính như “khá hợp lý”.
- Dimension đầu vào và golden label phải nằm ở hai trường riêng.
- Một test có thể được chấm bởi nhiều rubric độc lập.
- Rubric chưa được khóa trước khi đạt đồng thuận annotator theo ngưỡng sẽ được chốt trong Eval Plan.

### 10.2. Route grader

| Tiêu chí | Grader ưu tiên |
| --- | --- |
| Schema, enum, null, ID, số decision | Code |
| Score, Ceiling, Priority, Density, case max 5, SLA formula | Code |
| Candidate membership, max 20, manual-wins | Code |
| Category/biểu hiện sự cố/severity theo ngữ nghĩa | LLM Judge đã calibrate |
| Ca mơ hồ hoặc hậu quả nghiệp vụ cao | Human expert |

Không dùng LLM Judge để chấm điều kiện mà Code có thể xác định chính xác.

---

## 11. Các quyết định còn mở

Các điểm sau phải được giải quyết trước khi khóa reference dataset:

1. **Cửa sổ grouping:** tài liệu V4 đang dùng tối đa 3 ngày; thảo luận eval có đề cập điều kiện mọi cặp dưới 2 ngày. Hai cách này không tương đương.
2. **Severity rubric:** chưa có định nghĩa Category-specific đủ rõ cho LOW/MEDIUM/HIGH.
3. **`is_confident`:** chưa chốt confidence đang áp cho toàn analysis, Category hay duplicate.
4. **Category tương đương cho duplicate:** cần bảng mapping hoặc rule versioned, không để annotator tự đoán.
5. **Priority của case trong payload assignment:** case có thể chứa member khác Priority nhưng contract đang có một `priority`; cần rule lấy giá trị.
6. **SLA cụm:** công thức +25% mỗi ticket bổ sung và P3 không kéo dài cần Product Owner xác nhận.
7. **Biên tải KTV:** chưa có tải tối đa, nên hiện chỉ có bucket đại diện chứ chưa có boundary test `max-1/max/max+1`.

Mọi quyết định phải cập nhật đồng thời ba tài liệu V4, Foundation và sau này là golden labels.

---

## 12. Điều kiện sẵn sàng chuyển sang Vibe Check

Chỉ chuyển sang Pha 1 khi:

- [ ] Product Owner xác nhận các product promise.
- [ ] Các quyết định mở ảnh hưởng expected behavior đã được chốt hoặc đánh dấu rõ case không được đưa vào golden set.
- [ ] Unit of evaluation được nhóm Backend/AI/QA cùng đồng ý.
- [ ] Trace contract tối thiểu đã instrument hoặc có kế hoạch mock rõ ràng.
- [ ] Có thể tái dựng một analysis decision và một assignment decision từ trace.
- [ ] Có danh sách 5–10 seed scenario nhỏ, chưa cần cân bằng thống kê.
- [ ] Có người chịu trách nhiệm đọc trace và ghi failure mode.
- [ ] Dữ liệu test không chứa PII không cần thiết.

---

## 13. Artifact dự kiến sau Foundation

Không artifact nào dưới đây được tạo/chạy trong bước hiện tại:

1. `eval_vibe_check_plan_v1.md` — 5–10 seed scenarios và checklist đọc trace.
2. `eval_trace_schema_v1.json` — schema máy đọc cho trace tối thiểu.
3. `eval_failure_taxonomy_v1.yaml` — taxonomy sau khi Vibe Check hiệu chỉnh.
4. `eval_rubrics_v1.yaml` — rubric nhị phân theo referent.
5. `eval_reference_dataset_v1.jsonl` — 40–50 input cùng golden labels.
6. `eval_report_v1.md` — so sánh candidate/baseline và quyết định Ship/Limited/Hold.

---

## 14. Execution record

```text
Foundation version: v1
Eval run count: 0
Vibe Check run count: 0
Offline Eval run count: 0
Production sample reviewed: 0
Judge calibration status: NOT_STARTED
Reference dataset status: NOT_CREATED
Ship decision: NOT_APPLICABLE
```

