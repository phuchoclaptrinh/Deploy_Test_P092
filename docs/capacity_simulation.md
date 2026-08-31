# Mô phỏng công suất & SLA

Một màn hình chỉ đọc dành cho Ban quản lý. Một câu hỏi, hỏi từ một màn hình:
**với đúng tập phản ánh này và đúng đội kỹ thuật viên này, quy trình thủ công cũ
đã làm gì, và một luồng tự động sẽ làm gì?**

Không có gì trong nó chạm vào production. Nó không tạo ticket, không tạo phân
công, không tạo dispatch event; không gọi mô hình và không gọi agent; không giữ
trạng thái giữa các request; và không thay đổi bất kỳ cam kết SLA nào. Một lần
chạy là một kịch bản JSON vào và một tài liệu JSON ra.

---

## 1. Hai luồng, và không luồng nào là production

| Luồng | Là gì | Xếp hàng theo | Chọn kỹ thuật viên theo |
| --- | --- | --- | --- |
| `OLD_APP` | Nền thủ công: một người đọc, một người điều phối từng phản ánh. | Thứ tự đến | Ai rảnh sớm nhất |
| `NEW_APP` | **Mô phỏng một chính sách giả định.** Chưa áp dụng production. | P2 → hạn SLA → điểm → thời gian gửi | Ai bắt đầu được đúng hạn, rồi tới ai gần hơn |

**`NEW_APP` không phải là hành vi hiện tại của hệ thống.** Bộ điều phối đang
chạy (`src/dispatch/service.py`) xếp hàng đợi của một kỹ thuật viên theo *slack
còn lại* và có thể để P1 lên trước P2; nó không có khái niệm tầng và không chọn
người theo thời điểm bắt đầu. Gói `src/simulation/` **không import**
`src.dispatch.service`, `src.dispatch.scheduler` hay `src.dispatch.eligibility`
— nó chỉ mượn lịch ca làm và bảng ước lượng P80. Không có cờ parity nào trong
payload, không có huy hiệu "Production" nào trên màn hình, và mọi lần chạy đều
trả về cảnh báo nói ra điều đó.

### Chính sách `NEW_APP`, đầy đủ

> *"P2 đứng trước mọi P1 chưa bắt đầu. Nếu có cách bắt đầu ticket đúng SLA, hệ
> thống ưu tiên cách đó. Nếu không có cách nào đúng SLA, AI chọn phương án phù
> hợp nhất; nếu AI thất bại, scheduler chọn phương án dự kiến trễ ít nhất. Mọi
> trường hợp không bảo đảm SLA đều được phân công nhưng đồng thời thông báo BQL
> và ghi audit."*

Cụ thể:

1. **Việc đã bắt đầu không bị dừng hay chen ngang.** Đây là quy tắc, không phải
   hệ quả: một khi công việc rời khỏi hàng đợi thì không phản ánh nào đến sau
   sắp xếp lại được nó. Thiếu quy tắc này, "P2 trước P1" sẽ có nghĩa là kéo một
   kỹ thuật viên ra khỏi căn hộ đang sửa dở.
2. **Mức khẩn cấp vẫn chuyển Ban quản lý xử lý tay**, không tự động phân kỹ thuật viên.
   Mức đó là P3 ở hai chính sách V1 và P5 ở `SERVICE_HOURS_RISK_V2`.
3. **P2 đứng trước tất cả P1 chưa bắt đầu.**
4. **Trong cùng một mức ưu tiên**: hạn SLA sớm hơn trước, rồi điểm cao hơn, rồi
   `created_at`, rồi `ticket_id` để kết quả ổn định.
5. **Chỉ xét kỹ thuật viên thỏa toàn bộ ràng buộc cứng**: có kỹ năng phù hợp,
   đang khả dụng, và không bị `excluded_technician_ids` của ticket loại trừ. Một
   người bị loại ở đây không được cân nhắc lại vì không còn ai khác — đó là ý
   nghĩa của từ *cứng*.
6. **Với từng người hợp lệ, mô phỏng dự kiến**: khi nào họ rời việc trước, mất
   bao lâu di chuyển, khi nào tới nơi và thực sự bắt đầu, và trễ hạn bao nhiêu
   phút.

## 2. SLA được tính tại thời điểm **bắt đầu sửa**

Ba mốc được tách rời và không mốc nào suy ra được từ mốc khác:

| Trường | Nghĩa |
| --- | --- |
| `departed_at` | Kỹ thuật viên rời công việc trước và lên đường. |
| `work_started_at` | Tới nơi và thực sự bắt đầu. **Đây là mốc SLA.** |
| `completed_at` | Sửa xong. Chỉ dùng cho công suất và lịch kỹ thuật viên. |

```
work_started_at = departed_at + thời gian di chuyển
đúng hạn khi    work_started_at <= sla_due_at
```

Ví dụ có thật, ghim trong `test_sla_metrics.py`: kỹ thuật viên rời việc trước
lúc **12:10**, đi mất **10 phút**, hạn là **12:15**. `work_started_at` là
**12:20**, tức **trễ 5 phút**. Đo theo lúc *rời đi* sẽ báo đúng hạn; đo theo lúc
*hoàn tất* sẽ báo trễ hơn nhiều. Cả hai đều trả lời một câu hỏi khác với câu đã
hứa với cư dân — lời hứa là **có người tới xử lý**, không phải sửa xong trong
bao lâu. Một công việc khó kéo dài bốn tiếng vẫn là `ON_TIME`.

### Năm trạng thái, và mẫu số

```
ON_TIME        đã bắt đầu, work_started_at <= sla_due_at      ┐
LATE_STARTED   đã bắt đầu, work_started_at >  sla_due_at      ├ mẫu số
OPEN_OVERDUE   chưa bắt đầu, đã qua hạn                       ┘
OPEN_NOT_DUE   chưa bắt đầu, chưa tới hạn
NOT_EVALUABLE  Mức khẩn cấp chờ Ban quản lý, hoặc không có hạn hợp lệ
```

```
sla_evaluable_tickets = ON_TIME + LATE_STARTED + OPEN_OVERDUE
compliance_rate       = sla_on_time_tickets / sla_evaluable_tickets
```

**`OPEN_OVERDUE` nằm trong mẫu số.** Một ticket đã qua hạn mà chưa ai chạm tới
là vi phạm rõ ràng nhất trong cả bảng; cho nó ra ngoài sẽ là cải thiện tỷ lệ
bằng cách đánh rơi đúng những ticket tệ nhất. `OPEN_NOT_DUE` thì đứng ngoài —
chưa phải vi phạm, và cũng chưa phải thành công. Cả hai nhóm ngoài mẫu số được
in ngay bên cạnh tỷ lệ, không bao giờ bị giấu.

Một phản ánh **không ai đủ điều kiện nhận** cũng vẫn bị đối chiếu với hạn của
nó: không ai nhận không làm cho lời hứa với cư dân biến mất. Nó giữ
`outcome = NO_ELIGIBLE_TECHNICIAN`, hiển thị ở bảng riêng, và trạng thái SLA của
nó là `OPEN_OVERDUE` hay `OPEN_NOT_DUE` tùy vào hạn.

### Phút trễ

| Trạng thái | `start_late_minutes` |
| --- | --- |
| `LATE_STARTED` | Từ `sla_due_at` tới `work_started_at`. |
| `OPEN_OVERDUE` | Từ `sla_due_at` tới `horizon_end`. |
| Ba trạng thái còn lại | 0. |

Đếm bằng đồng hồ của chính sách đang chạy: `SERVICE_HOURS_DRAFT_V1` đếm phút
trong giờ phục vụ, `WALL_CLOCK_V1` đếm treo tường. Cùng một khoảng — hạn 17:50,
bắt đầu 08:10 sáng hôm sau — là **20 phút** dưới đồng hồ giờ phục vụ và **860
phút** dưới đồng hồ treo tường.

## 3. Không bảo đảm được SLA thì vẫn phân công

Khi **không kỹ thuật viên hợp lệ nào** bắt đầu được trước hạn:

* Ticket **vẫn được phân công** — bỏ trống một phản ánh không làm nó biến mất
  khỏi tòa nhà.
* Đánh dấu `risk_state = AT_RISK`, `risk_reason = START_SLA_RISK`.
* Chọn phương án **trễ ít phút nhất**; bằng nhau thì tới di chuyển thấp hơn, rồi
  tải nhẹ hơn, rồi `technician_id` để kết quả ổn định.
* Trả về `would_notify_bql = true`, `would_write_audit = true`, và
  `projected_start_late_minutes`.

**Đây là mô phỏng nhánh dự phòng bảo thủ, không phải mô phỏng AI.** Trong đời
thật nhánh này hỏi AI trước; bản mô phỏng deterministic **không gọi mô hình
nào** và đi thẳng vào phương án dự phòng, nên nhãn nguồn quyết định là
`SCHEDULER_FALLBACK_SIMULATED` chứ không phải một nhãn nào gợi ý AI đã quyết
định. Chất lượng riêng của lựa chọn AI **không được tính vào kết quả** khi đầu
vào không có lịch sử kỹ thuật viên và không có kết quả AI ghi sẵn — mọi con số
trong bảng vì thế là cận dưới của những gì hệ thống thật làm được, không phải
ước lượng của nó.

`would_notify_bql` và `would_write_audit` là những gì hệ thống thật *sẽ* làm.
Bản mô phỏng không gửi gì và không ghi gì.

## 4. Micro-batch

Bộ điều phối không quyết định từng ticket một theo thứ tự cư dân bấm gửi. Nó
thức dậy theo nhịp cố định (mặc định 750ms, lấy từ `src/config.py`), **gom**
những sự kiện đã sẵn sàng, rồi mới **sắp xếp** cái vừa gom và xử lý. Hai quy tắc
đó khác nhau:

* **Thành viên lượt gom**: `available_at`, rồi `enqueued_at`, rồi `id`. Tối đa
  `micro_batch_size` (mặc định 20) cái một lượt; cái thứ hai mươi mốt chờ nhịp
  sau, khẩn cấp đến mấy cũng vậy.
* **Thứ tự xử lý bên trong lượt gom**: theo chính sách của luồng (§1).

Quy tắc thứ nhất từng dùng `ticket_id` thay cho `enqueued_at`, và đó là một lỗi
thật: mọi phản ánh gửi trước 08:00 đều bị đẩy `available_at` về đúng giờ mở cửa,
nên cả một đêm tồn đọng có chung `available_at` — và khi đó xếp theo id sẽ cho
`T001` chen lên trước một phản ánh gửi sớm hơn nó nhiều tiếng, chỉ vì tên nó nhỏ
hơn. Đã sửa, và ghim bằng một test 21 ticket trong `test_micro_batch.py`.

**Mô phỏng đúng cơ chế gom không phải là tuyên bố parity với production.** Cơ
chế gom là thật; chính sách sắp xếp bên trong là giả định.

## 5. Hợp đồng JSON

`POST /api/v1/coordinator/simulation/run`, chỉ Điều phối viên. Body là
`{"scenario": { … }}` — **một tài liệu**, không phải nhiều nguồn dán riêng.

```jsonc
{
  "scenario_name": "Tòa nhà 30 tầng — một ngày mẫu",
  "building":   { "floor_count": 30, "units_per_floor": 7 },
  "sla_policy": { "mode": "SERVICE_HOURS_RISK_V2" },       // mặc định: chính sách production đang chạy
  "settings": {
    "travel_base_minutes": 3,
    "travel_per_floor_minutes": 1,
    "micro_batch_interval_ms": 750,   // mặc định lấy từ cấu hình bộ điều phối
    "micro_batch_size": 20,
    "simulation_horizon_days": 14,
    "old_app": { "manual_category_minutes": 10, "manual_dispatch_minutes": 8 },
    "new_app": { "ai_classification_minutes": 1, "manual_review_minutes": 10 }
  },
  "technicians": [
    { "technician_id": "KTV_01", "skills": ["plumbing", "electrical"],
      "start_floor": 1, "is_active": true, "is_available": true }
  ],
  "tickets": [
    { "ticket_id": "T001", "created_at": "2026-09-01T17:00:00+07:00",
      "floor": 8, "unit": "0801", "issue_type": "WATER", "priority": "P2",
      "repair_minutes": 90, "required_skill": "plumbing",
      "need_hand_categorized": false, "score_total": 45,
      "excluded_technician_ids": [] }
      // không ghi sla_minutes: chính sách cấp hạn
  ]
}
```

**Chính sách sở hữu thời hạn, không phải đầu vào.** `sla_minutes` là tùy chọn;
bỏ trống thì lấy từ `POLICY_SLA_MINUTES[policy][priority]` và dòng được đánh dấu
`sla_duration_source: POLICY`. Đó là điều làm cho câu "chạy cùng tập dữ liệu
dưới hai đồng hồ" có nghĩa: đổi `sla_policy.mode` và mọi hạn P1 tự chuyển từ
1800 phút giờ phục vụ sang 4320 phút treo tường.

Kịch bản vẫn được phép ghi hạn riêng — *"nếu ta hứa P2 bốn tiếng thì sao?"* là
câu hỏi Ban quản lý thực sự đặt ra — nhưng khi đó dòng được đánh dấu
`INPUT_OVERRIDE`, lần chạy cảnh báo và nêu đích danh ticket, và màn hình gắn cờ
cả cột. Không đánh dấu thì đó chính là cách một P1 mang con số `4320` của
production bị đo thành 4320 phút *giờ phục vụ*, tức 7,2 ngày làm việc, dưới một
chính sách sinh ra để giữ nó ở ba ngày.

**Bộ phân tích nghiêm ngặt**, và mỗi lần từ chối là một cách mà lần chạy có thể
trả lời một câu hỏi không ai hỏi:

| Bị từ chối | Vì sao |
| --- | --- |
| CSV, dưới mọi hình thức | Một ô CSV luôn là chuỗi, nên `false` và `"false"` thành một giá trị và một boolean lật âm thầm. |
| `created_at` không có múi giờ | Đoán sai là dời mọi hạn đi cả một ca. |
| `"Yes"` / `1` cho boolean | Bên tạo dữ liệu không biết kiểu; đoán hộ là lật cờ. |
| `"plumbing;electrical"` | `skills` phải là mảng JSON. Tương tự với `excluded_technician_ids`. |
| Ưu tiên là `3` | Có thể là bậc, có thể là chỉ số; đọc sai là phân loại lại phản ánh. (`"p2"` **được** chấp nhận — chữ hoa thường không phải mơ hồ về kiểu.) |
| Khóa lạ bất kỳ | `travel_per_floor` sẽ chạy bằng mặc định trong khi màn hình hiển thị giá trị đã bị bỏ qua. `safety_buffer_minutes` và `current_app` đã bị gỡ khỏi hợp đồng và nay bị từ chối chứ không bị lờ đi. |
| Trùng `ticket_id` / `technician_id` | Mọi con số theo ticket trở nên mơ hồ. |
| Quá 500 ticket / 200 kỹ thuật viên | Bảo vệ API. |

Tùy chọn: `sla_minutes` (lấy từ chính sách), `repair_minutes` (lấy P80 theo loại
sự cố, và lần chạy nói ra trong `warnings`), `excluded_technician_ids` (rỗng),
`score_total` (0), `start_floor` (1), `is_active` / `is_available` (true),
`settings` và `building` (mặc định đã ghi tài liệu).

Dòng bị từ chối trả về **422 `SIMULATION_INPUT_INVALID`** kèm `details.field` và
`details.index`.

Dấu thời gian đi ra theo **giờ Việt Nam (+07:00)** — ngoại lệ của API này, vì
mọi con số ở đây là một phát biểu theo đồng hồ treo tường về một ngày làm việc.

## 6. Response, và quy ước dấu

```jsonc
{
  "old_app": { "scenario": "OLD_APP", "summary": { … }, "tickets": [ … ] },
  "new_app": { "scenario": "NEW_APP", "summary": { … }, "tickets": [ … ] },
  "comparison": {
    "bql_minutes_saved": 140,
    "bql_hours_saved": 2.33,
    "late_starts_avoided": 1,
    "start_late_minutes_avoided": 110,
    "average_response_minutes_saved": 10.6,
    "p95_response_minutes_saved": -13,
    "travel_minutes_saved": -10,
    "compliance_rate_gain": 0.1111
  },
  "warnings": [ … ]
}
```

**Dương luôn nghĩa là app mới tốt hơn app cũ.**

```
mọi trường _saved / _avoided  =  OLD_APP − NEW_APP
compliance_rate_gain          =  NEW_APP − OLD_APP     (ở đây nhiều hơn là tốt hơn)
```

Phép trừ xảy ra **đúng một lần, ở backend**. Frontend không đảo dấu ở bất kỳ
đâu, và một test khẳng định điều đó bằng cách đọc chính mã nguồn của trang: một
dấu trừ nằm rải rác trong JSX là chỗ một con số cuối cùng sẽ được đọc ngược.

Giá trị âm được giữ nguyên chứ không làm tròn về không. Trên kịch bản mẫu,
`travel_minutes_saved` là **−10** và `p95_response_minutes_saved` là **−13**:
đưa P2 lên trước có giá của nó, và giấu cái giá đó đi là quảng cáo chứ không
phải đo lường.

**Breaking change có chủ ý.** Đã gỡ khỏi response: `proposed_optimized`,
`current_app`, danh sách `deltas` nhiều luồng, `planned_by_production`, `parity`,
và mọi trường SLA đặt tên theo việc *hoàn tất* (`sla_late_completed_tickets`,
`total_sla_late_minutes`, `sla_unresolved_tickets`, `simulated_completed_at`).

## 7. Thời gian mô phỏng

Một quy tắc và một cửa sổ, dùng chung cho cả hai luồng: **một công việc không
được *bắt đầu* sau `horizon_end`**; công việc đã bắt đầu trước đó thì chạy tới
khi xong. Cửa sổ neo vào `created_at` của phản ánh **đầu tiên**, không neo vào
thời điểm một luồng coi phản ánh đó là điều phối được — mỗi luồng trả chi phí
phân tích khác nhau trước `ready_at` (mười tám phút thủ công cho app cũ, một
phút cho app mới), nên neo theo `ready_at` sẽ kết thúc ngày của app cũ muộn hơn
mười bảy phút và âm thầm so sánh hai cửa sổ khác nhau.

Những gì còn lại sau mốc đó không biến mất: chúng là ticket **chưa bắt đầu**, và
được báo là `OPEN_OVERDUE` hoặc `OPEN_NOT_DUE` tùy hạn của chúng.

## 8. Mã nguồn ở đâu

| Đường dẫn | Nội dung |
| --- | --- |
| [`src/simulation/models.py`](../src/simulation/models.py) | Từ vựng: `ScenarioInput`, `TicketOutcome`, `ScenarioSummary`, `Comparison`, các enum. |
| [`src/simulation/validation.py`](../src/simulation/validation.py) | Một kịch bản JSON nghiêm ngặt vào; lỗi tiếng Việt có định vị dòng. |
| [`src/simulation/travel.py`](../src/simulation/travel.py) | `travel = base + abs(from - to) * per_floor`. |
| [`src/simulation/batching.py`](../src/simulation/batching.py) | Cơ chế gom micro-batch. |
| [`src/simulation/policies.py`](../src/simulation/policies.py) | `OLD_APP` và `NEW_APP`. |
| [`src/simulation/engine.py`](../src/simulation/engine.py) | Bản phát lại và các bản tổng hợp. |
| [`src/models/api/simulation.py`](../src/models/api/simulation.py) | Hợp đồng đường truyền. |
| [`src/api/routes/coordinator/simulation.py`](../src/api/routes/coordinator/simulation.py) | Endpoint. Không có database session trong chữ ký hàm. |
| [`frontend/lib/simulation.ts`](../frontend/lib/simulation.ts) | Phân tích, nhãn, định dạng, xuất file. |
| [`frontend/app/manager/simulation/page.tsx`](../frontend/app/manager/simulation/page.tsx) | Màn hình. |
| [`examples/simulation/scenario.json`](../examples/simulation/scenario.json) | Kịch bản mẫu, thấy khác biệt ngay lần bấm đầu tiên. |

**Bảo đảm mang tính cấu trúc**, do
[`test_no_database_writes.py`](../tests/test_simulation/test_no_database_writes.py)
thi hành bằng cách đọc đồ thị import chứ không bằng lời hứa của ai:

* không có `src.database`, `src.repositories`, `src.services`, `sqlalchemy` ở bất
  kỳ đâu trong gói;
* từ `src/dispatch/` chỉ có `shift` và `durations` — một cuốn lịch và một bảng
  ước lượng, đều là hàm thuần trên giá trị đơn giản. `src.dispatch.service`,
  `src.dispatch.scheduler`, `src.dispatch.eligibility` và
  `src.workers.dispatch_worker` đều **không** được import: trình mô phỏng không
  ghi qua chúng, và cũng không mượn quyết định của chúng;
* endpoint không nhận tham số `db`.

## 9. Chạy thử

**Nhanh nhất — không cần server, không cần đăng nhập:**

```powershell
.venv\Scripts\python.exe scripts\run_simulation.py
.venv\Scripts\python.exe scripts\run_simulation.py --input kich-ban.json --json ket-qua.json
```

Chạy kịch bản mẫu và in cả hai luồng ra terminal. `--json` xuất đúng payload API
trả về.

**Trên giao diện BQL:** backend (`python -m uvicorn src.main:app --reload --port
8000`) và frontend (`npm run dev`), đăng nhập tài khoản Điều phối viên, rồi vào
**Mô phỏng công suất** hoặc `http://localhost:3000/manager/simulation`. Màn hình
mở ra đã có sẵn kịch bản mẫu — bấm **Chạy mô phỏng**.

## 10. Kiểm chứng

```powershell
.venv\Scripts\python.exe -m pytest tests\test_simulation -q
.venv\Scripts\python.exe -m ruff check src\simulation src\models\api\simulation.py src\api\routes\coordinator\simulation.py tests\test_simulation
cd frontend
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
```

Và các suite production, để chứng minh không có hồi quy:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_dispatch tests\test_workflow\test_assignment_start.py -q
git diff -- src/dispatch src/services src/database
```

`git diff` phải rỗng, và `git status --short` trên ba thư mục đó cũng phải rỗng:
gói mô phỏng không sửa và không thêm gì trong bộ điều phối, tầng dịch vụ hay tầng
dữ liệu.

---

## Kịch bản không khai `sla_policy` chạy chính sách nào

Chạy **chính sách production đang dùng**, đọc từ `CURRENT_POLICY` trong
`src/domain/sla_clock.py`. Hiện tại là `SERVICE_HOURS_RISK_V2`.

Lấy từ hằng số đó chứ không ghi cứng tên chính sách vào bộ đọc kịch bản. Ghi
cứng chính là cách chỗ này từng sai: mặc định là `WALL_CLOCK_V1`, hệ thống
chuyển sang thang P1–P5, và mặc định ở lại thang cũ. Một kịch bản không khai
`sla_policy` khi đó chạy dưới rubric đã bỏ — P3 là mức khẩn cấp, ticket P4/P5 bị
từ chối — mà không có gì báo lỗi, vì không khai gì thì không có gì để kiểm.

Kịch bản muốn chạy chính sách V1 vẫn khai `mode` như cũ và nhận đúng kết quả cũ.
Hai chính sách V1 không đổi một dòng nào; chỉ mặc định đổi.

---

## Chính sách `SERVICE_HOURS_RISK_V2`

Chính sách thứ ba, thêm vào chứ không thay thế. `OLD_APP` và `NEW_APP` giữ
nguyên; một kịch bản V1 chạy lại vẫn cho đúng kết quả đã ghi.

Khác biệt so với hai chính sách V1, và cả ba đều là hệ quả của việc thang điểm
đã đảo:

| | V1 (`WALL_CLOCK_V1`, `SERVICE_HOURS_DRAFT_V1`) | `SERVICE_HOURS_RISK_V2` |
| --- | --- | --- |
| Mức nhận vào | P1–P3 | P1–P5 |
| Mức khẩn cấp | P3 | P5 |
| Thứ tự hàng đợi | P3 → P2 → P1 | P4 → P3 → P2 → P1 |
| Kết luận thủ công | `REQUIRES_MANUAL_P3_REVIEW` | `REQUIRES_MANUAL_P5_REVIEW` |
| Hạn SLA (phút) | 4320/180/5 hoặc 1800/180/5 | 1800/1200/600/180/5 |

Hai giá trị kết luận là hai giá trị riêng, không phải đổi tên một giá trị. Hai
lần chạy dưới hai thang điểm nói về hai mức khác nhau, và dùng chung một nhãn sẽ
khiến một bản so sánh cũ bị đọc như thể nó chạy dưới rubric mới.

Một kịch bản V1 mang mức P4 hoặc P5 bị từ chối kèm số dòng và tên trường: chính
sách V1 không có hạn SLA nào cho P4, và cho nó chạy sẽ lặng lẽ sinh ra một con
số so sánh vô nghĩa.

`score_total` vẫn là tie-break như cũ. Bản xuất V2 có thể đặt `risk_score` vào
đúng trường đó mà không phá hợp đồng đầu vào.
