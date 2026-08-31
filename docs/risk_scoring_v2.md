# Risk Scoring v2 — hợp đồng nghiệp vụ

**Trạng thái:** hiệu lực. Đây là **nguồn sự thật duy nhất** cho cách một phản ánh
được chấm điểm, xếp Priority và nhận hạn SLA.

Quy tắc của tài liệu này: **không một quyết định nghiệp vụ nào được phép chỉ tồn
tại trong prompt hoặc trong comment code.** Nếu một con số, một ngưỡng hay một
danh sách điều khiển hành vi runtime, nó phải có mặt ở đây trước. Code và prompt
là bản hiện thực của tài liệu này, không phải ngược lại.

Thay thế: `Logic_xử_lý_chính_v4` (base score + location bonus + density bonus +
severity + priority ceiling) và toàn bộ thang `Severity` LOW/MEDIUM/HIGH.

---

## 1. Ranh giới trách nhiệm

| Ai | Được quyết cái gì |
| --- | --- |
| **AI** | Năm điểm tiêu chí 0–4, danh sách blocker, bằng chứng cho từng điểm, các dữ kiện chưa biết. |
| **Backend** | `risk_score`, `Priority`, blocker floor, phạm vi đã xác nhận, `sla_started_at`/`sla_due_at`. |
| **Con người (BQL)** | Xác nhận hoặc hạ cấp P5, xử lý P5 thủ công, ghi lý do. |

Ba hệ quả không được vi phạm:

* **AI không bao giờ trả điểm tổng, Priority, Severity hay density.** Nó trả năm
  con số nguyên 0–4 và bằng chứng. Backend cộng.
* **Backend không bao giờ nhận điểm tổng từ AI.** Nếu payload có trường điểm
  tổng, đó là payload sai schema.
* **Category không tham gia tính điểm.** Category giữ rộng, dùng để định tuyến
  kỹ năng và thống kê. Nó không cộng, không trừ, không chặn trần.

---

## 2. Công thức

```
risk_score =
    human_safety        / 4 × 35
  + essential_function  / 4 × 35
  + affected_scope      / 4 × 20
  + property_spread     / 4 × 5
  + deterioration_speed / 4 × 5
```

Thứ tự viết ở đây là thứ tự **trọng số**, để đọc công thức. Thứ tự **trường**
trong payload, trong cột `ticket_risk_assessments` và trong bảng audit vẫn là
thứ tự khai báo ở `src/domain/risk_scoring.py`, và hai thứ tự này được phép
khác nhau: chỉnh lại một trọng số không nên kéo theo việc đổi thứ tự cột.

* Mỗi tiêu chí là số nguyên trong `[0, 4]`. Ngoài khoảng đó là lỗi, không phải
  giá trị cần kẹp.
* Tổng trọng số bằng 100, nên `risk_score` luôn nằm trong `[0, 100]`.
* Tính bằng `Decimal`. **Không làm tròn giữa chừng** — chỉ lượng tử hoá một lần
  ở bước cuối về hai chữ số thập phân (`NUMERIC(5,2)`).

Những thứ **không** có trong công thức, và sẽ không được thêm lại:

* `base_score` theo Category.
* `location_bonus` — vị trí không tự cộng điểm. Vị trí chỉ là *bằng chứng* để AI
  chấm `human_safety` hoặc `affected_scope`.
* `density_bonus` — phạm vi đã là một tiêu chí có trọng số, không cộng thêm lần
  hai.
* `priority_ceiling` — Category không chặn trần Priority.
* `severity` — thang LOW/MEDIUM/HIGH đã bị xoá khỏi hệ.

### 2.1 Ngưỡng Priority

| `risk_score` | Priority |
| --- | --- |
| `[0, 20)` | **P1** |
| `[20, 40)` | **P2** |
| `[40, 60)` | **P3** |
| `[60, 80)` | **P4** |
| `[80, 100]` | **P5** |

Biên là **trái đóng, phải mở**, trừ cận trên 100. `19.99 → P1`, `20.00 → P2`,
`79.99 → P4`, `80.00 → P5`, `100.00 → P5`.

> **Đảo chiều so với hệ cũ.** Trước đây P3 là mức khẩn cấp nhất và P1 là mức
> thường. Từ v2, **P5 là mức khẩn cấp nhất và P1 là mức thường**. Mọi so sánh
> "cao hơn/thấp hơn" trong tài liệu này đọc theo chiều mới.

---

## 3. Năm tiêu chí và anchor 0–4

Anchor là bắt buộc trong prompt. Chúng ở đây để prompt trích dẫn, không phải để
prompt tự phát minh.

### 3.0 Trọng số nói gì

| Tiêu chí | Trọng số | Một điểm đáng bao nhiêu |
| --- | ---: | ---: |
| `human_safety` | 35 | 8.75 |
| `essential_function` | 35 | 8.75 |
| `affected_scope` | 20 | 5.00 |
| `property_spread` | 5 | 1.25 |
| `deterioration_speed` | 5 | 1.25 |

Bảng này quyết định hệ thống ưu tiên cái gì, nên nó đáng đọc kỹ hơn công thức:

* **An toàn con người và chức năng thiết yếu là hai trục nặng nhất.** Mỗi tiêu
  chí 4/4 đóng 35 điểm, đủ đưa ticket lên P2 nhưng chưa tự một mình vượt sang
  P3. Muốn thành P3 cần thêm dấu hiệu phạm vi hoặc tốc độ/phạm vi thiệt hại.
* **Phạm vi ảnh hưởng là trục cân bằng thứ ba.** `affected_scope` 4/4 đóng 20
  điểm, tức một sự cố lan nhiều căn đã tự chạm ngưỡng P2 trước khi cộng các
  yếu tố khác.
* **Lan tài sản và tốc độ xấu đi là tín hiệu phụ.** `property_spread` và
  `deterioration_speed` mỗi tiêu chí tối đa 5 điểm; chúng dùng để phân biệt các
  ca sát ngưỡng, không thay thế blocker.
* **Nguy hiểm tính mạng vẫn đi qua blocker.** Blocker nâng sàn thẳng lên P5
  mà không cần điểm nào đứng sau (§5). Trọng số 35 của `human_safety` là để
  chấm những mức nguy hiểm nghiêm trọng nhưng chưa rơi vào một blocker có tên.

Đổi một trọng số là đổi rubric: phải sửa `src/domain/risk_scoring.py`, tài liệu
này, `frontend/lib/risk.ts`, sheet `Rubric` trong bộ 260 case, và nâng
`RUBRIC_VERSION` (§9). `tests/test_evals/` giữ bốn chỗ đó không lệch nhau.

### 3.1 `human_safety` — trọng số 35

Nguy hiểm trực tiếp cho thân thể người.

| Điểm | Anchor |
| --- | --- |
| 0 | Không có yếu tố an toàn. Phiền toái, thẩm mỹ, tiện nghi. |
| 1 | Rủi ro gián tiếp, cần trùng hợp mới gây thương tích. Sàn ẩm ở khu ít qua lại. |
| 2 | Rủi ro thật nhưng tránh được. Sàn trơn lối đi chung, cạnh sắc trong tầm với. |
| 3 | Nguy hiểm cao, người thường không tự tránh được. Ổ điện hở trong tầm trẻ em, lan can lung lay. |
| 4 | Đang đe doạ tính mạng hoặc đã có người bị thương. Cháy, điện giật, ngạt khí, người mắc kẹt. |

### 3.2 `property_spread` — trọng số 5

Mức lan của thiệt hại tài sản nếu không xử lý.

| Điểm | Anchor |
| --- | --- |
| 0 | Không lan. Hỏng cục bộ, đứng yên. |
| 1 | Lan chậm trong phạm vi một căn. Vết ẩm mở rộng theo tuần. |
| 2 | Lan rõ trong một căn hoặc chớm sang kết cấu chung. |
| 3 | Đang lan sang căn khác hoặc sang hệ thống dùng chung. |
| 4 | Lan nhanh, diện rộng, không tự dừng. Vỡ ống trục, ngập tầng. |

### 3.3 `essential_function` — trọng số 35

Chức năng sống thiết yếu của căn hộ: điện, nước, vệ sinh, lối ra vào.

| Điểm | Anchor |
| --- | --- |
| 0 | Không đụng tới chức năng thiết yếu. |
| 1 | Suy giảm nhẹ, vẫn dùng được. Nước yếu, một ổ cắm chết. |
| 2 | Mất một chức năng phụ hoặc có đường thay thế. Toilet phụ hỏng, một nhánh điện mất. |
| 3 | Mất một chức năng thiết yếu, không có đường thay thế. Toilet duy nhất không dùng được. |
| 4 | Căn hộ không ở được. Mất hoàn toàn điện **và** nước, hoặc không vào được nhà. |

### 3.4 `affected_scope` — trọng số 20

Số **căn hộ** bị ảnh hưởng. Xem §4 để biết ai chấm điểm này và khi nào.

| Điểm | Anchor | Số căn |
| --- | --- | --- |
| 0 | Một căn duy nhất. | 1 |
| 1 | Hai căn. | 2 |
| 2 | Ba căn. | 3 |
| 3 | Bốn căn. | 4 |
| 4 | Năm căn trở lên. | ≥ 5 |

### 3.5 `deterioration_speed` — trọng số 5

Tốc độ xấu đi nếu để nguyên.

| Điểm | Anchor |
| --- | --- |
| 0 | Ổn định. Để một tuần cũng như vậy. |
| 1 | Xấu đi theo tuần. |
| 2 | Xấu đi theo ngày. |
| 3 | Xấu đi theo giờ. |
| 4 | Xấu đi theo phút. |

---

## 4. Phạm vi: AI ước lượng, Backend xác nhận

Ba trường, ba nguồn khác nhau, ghi cả ba:

| Trường | Nguồn | Ý nghĩa |
| --- | --- | --- |
| `ai_scope_score` | AI | Điều AI suy ra được từ một phản ánh duy nhất. |
| `backend_scope_score` | Backend | Điều đếm được từ IncidentCase. `NULL` khi ticket chưa thuộc case nào. |
| `effective_scope_score` | Backend | Điểm thực sự vào công thức. |

Quy tắc chọn:

```
effective_scope_score = backend_scope_score nếu backend_scope_score IS NOT NULL
                        ngược lại ai_scope_score
```

Bằng chứng đếm được luôn thắng ước lượng. Một cư dân nói "chắc cả tầng bị" không
nâng phạm vi; năm ticket từ năm căn thì có.

### 4.1 Công thức phạm vi backend

```
confirmed_affected_unit_count = case.density_value
backend_scope_score           = clamp(case.density_value - 1, 0, 4)
```

`case.density_value = COUNT(DISTINCT source_unit_id)` trong các member đang hoạt
động của **một** case.

Một lần ghi grouping có thể chạm **nhiều case**: unit thứ sáu mở case kế tiếp,
nên case vừa đầy và case vừa mở đều có member có phạm vi vừa thay đổi. Phải tính
lại density cho **mọi** case đã bị chạm, không phải case cuối cùng vòng lặp đang
giữ. Tính lại đúng case cuối để lại case đầy ở `density_value = 1` mà nó được tạo
ra: năm ticket cùng nhau xác nhận năm căn, mỗi cái tự chấm phạm vi như thể chỉ có
một căn, và không cái nào được rescore.

### 4.2 Bốn quy tắc phạm vi

1. **Một căn hộ là một affected unit.** Một căn gửi mười ticket vẫn là một unit.
   Đếm theo `DISTINCT source_unit_id`, không theo số ticket.
2. **Một IncidentCase tối đa năm unit.** Unit thứ sáu mở case kế tiếp trong cùng
   series, với phạm vi tính riêng từ đầu.
3. **Không cộng phạm vi trên toàn `series_id`.** Series là thứ tự hành chính để
   case tràn có chỗ đi tiếp, không phải một sự cố lớn hơn. Hai case đầy trong
   cùng series là 5 và 5, không phải 10.
4. **Khu vực chung không tự động thành điểm 4.** Một bóng đèn hành lang cháy vẫn
   là phạm vi nhỏ. Khu vực chung là bằng chứng để AI cân nhắc, không phải một
   luật cộng điểm.
5. **Mức khẩn cấp không bao giờ là member của case.** Một ticket P5 không xuất
   hiện trong danh sách ứng viên grouping, không được nêu trong proposal, và
   không được ghi membership. Lý do là số học chứ không phải chính sách: member
   là một số hạng trong `affected_scope` của mọi member khác, nên một P5 nằm
   trong case sẽ nâng điểm của bốn ticket khác mà không đóng góp việc nào có thể
   phân công. Xem `src/domain/grouping_guard.py`.

Quy tắc 5 được kiểm ở ba chỗ, và ba chỗ đó không thừa: giữa chúng có khoảng thời
gian thật, và một ticket đang là P4 lúc tìm ứng viên có thể đã là P5 lúc ghi
membership — một báo cáo trùng khẩn cấp nâng master của nó làm đúng việc đó.

| Chỗ kiểm | Ngăn điều gì |
| --- | --- |
| `AgentToolService._grouping_candidates` | Model không bao giờ nhìn thấy nó. |
| `AgentToolService._valid_grouping_related` | Model nêu tên nó, hoặc proposal cũ được phát lại. |
| `AgentResultService._can_join_case` | Nó đổi mức ngay trước lúc ghi. |

---

## 5. Blocker

Blocker là **sàn**, không phải điểm cộng. Nó nâng Priority lên tối thiểu một
mức, và **không bao giờ hạ** Priority đã cao hơn.

```
final_priority = max(score_priority, blocker_floor)   # theo chiều P1 < P2 < P3 < P4 < P5
```

Một ticket chấm ra P5 mà mang blocker mức P4 thì vẫn là P5.

### 5.1 Blocker sàn P5

| Mã | Nghĩa |
| --- | --- |
| `FIRE_OR_SMOKE` | Cháy hoặc khói. |
| `ELECTRIC_SHOCK_OR_LIVE_WIRE` | Điện giật, dây điện sống hở. |
| `GAS_LEAK_OR_ASPHYXIATION` | Rò gas, ngạt khí. |
| `SERIOUS_INJURY` | Chấn thương nghiêm trọng. |
| `PERSON_TRAPPED_IN_ELEVATOR` | Người kẹt trong thang máy. |
| `SOLE_ESCAPE_ROUTE_BLOCKED` | Lối thoát hiểm duy nhất bị chặn. |
| `ONGOING_VIOLENCE` | Bạo lực đang diễn ra. |

### 5.2 Blocker sàn P4

| Mã | Nghĩa |
| --- | --- |
| `SEWAGE_OVERFLOW` | Nước thải trào ngược. |
| `HEAVY_WATER_FLOW_SPREAD_RISK` | Nước chảy mạnh, có nguy cơ lan sang căn khác. |
| `TOTAL_UNPLANNED_UTILITY_LOSS` | Mất hoàn toàn điện hoặc nước ngoài kế hoạch. |
| `SOLE_TOILET_UNUSABLE` | Toilet duy nhất không dùng được. |

Không có blocker nào ngoài mười một mã trên. Thêm mã mới là sửa tài liệu này
trước.

### 5.3 Mỗi blocker mang bằng chứng riêng

Blocker nâng sàn mà không có điểm nào đứng sau, nên thứ duy nhất người duyệt kiểm
lại được là AI đã thấy gì. Bằng chứng vì thế được khoá theo mã:

```json
"evidence": {
  "human_safety": ["..."],
  "property_spread": [],
  "essential_function": [],
  "affected_scope": [],
  "deterioration_speed": [],
  "blockers": { "FIRE_OR_SMOKE": ["khói bốc ra từ hộp kỹ thuật"] }
}
```

Backend từ chối payload khi:

* một mã được nêu mà không có dòng bằng chứng nào của riêng nó;
* `evidence.blockers` chứa một mã không nằm trong `blockers` — nó sẽ hiện dưới
  một tiêu đề khẩn cấp cho một sàn không ai áp dụng;
* một mã lặp lại.

Kiểm theo từng mã chứ không kiểm tổng: ba mã dựa trên một dòng bằng chứng đọc
như là có bằng chứng, và không phải.

### 5.4 Bằng chứng theo tiêu chí

Năm khoá tiêu chí giữ đúng những dòng AI quy cho tiêu chí đó. Danh sách rỗng là
hợp lệ và có nghĩa "phản ánh không nói gì về tiêu chí này" — một lý do chính đáng
cho điểm 0, và khác hẳn "không biết" (xem §11.1).

Trước đây backend sao chép toàn bộ `incident_facts` vào mọi tiêu chí có điểm lớn
hơn 0. Bảng audit khi đó nói rằng cùng một câu là lý do cho bốn con số khác nhau,
đúng vào lúc người duyệt đang cần biết tiêu chí nào thật sự có căn cứ.

---

## 6. SLA

Đồng hồ SLA đo **thời điểm bắt đầu xử lý**, không phải thời điểm hoàn thành:

```
đạt SLA  ⟺  assignment.started_at <= ticket.sla_due_at
```

Lý do: cái BQL cam kết với cư dân và điều phối được là *bao giờ có người tới*.
Thời gian sửa xong phụ thuộc vào bản thân sự cố và không phải là lời hứa của
quy trình điều phối.

### 6.1 Chính sách `SERVICE_HOURS_RISK_V2`

| Priority | Hạn bắt đầu xử lý | Đồng hồ |
| --- | --- | --- |
| P1 | 1.800 phút phục vụ | Tạm dừng ngoài 08:00–18:00 |
| P2 | 1.200 phút phục vụ | Tạm dừng ngoài 08:00–18:00 |
| P3 | 600 phút phục vụ | Tạm dừng ngoài 08:00–18:00 |
| P4 | 180 phút phục vụ | Tạm dừng ngoài 08:00–18:00 |
| P5 | 5 phút wall-clock | Chạy 24/7, xử lý thủ công |

"Phút phục vụ" là phút nằm trong cửa sổ 08:00–18:00. Không nghỉ trưa, không lịch
cuối tuần, không lịch lễ — giống hệt `WALL_CLOCK_V1` và `SERVICE_HOURS_DRAFT_V1`
ở điểm này.

### 6.2 P5 nằm ngoài mọi phép đo SLA kỹ thuật viên

* P5 **không** vào mẫu số của tỉ lệ tuân thủ SLA của kỹ thuật viên.
* P5 hiển thị riêng trong báo cáo dưới nhãn *emergency manual*.
* Compliance chỉ tính trên P1–P4.
* Ticket quá hạn mà còn mở vẫn là vi phạm, kể cả khi chưa ai bắt đầu.

### 6.3 Thứ tự hàng đợi điều phối

```
P4 → P3 → P2 → P1
```

P5 **không có thứ hạng** trong hàng đợi, vì nó không bao giờ được đưa vào hàng
đợi. Xem §8.

---

## 7. Luồng xử lý

```
classify
  → validate AI assessment
  → calculate provisional risk
  → cảnh báo khẩn cấp ngay nếu P5
  → duplicate
  → grouping (chỉ khi P1–P4 và đủ điều kiện gom)
  → calculate final risk
  → operational exit
```

Hai điểm hay bị làm sai:

* **Cảnh báo P5 phát trước duplicate, không phải sau.** Chờ duplicate xong mới
  báo cháy là mất thời gian không có lý do.
* **P5 vẫn chạy duplicate.** Cảnh báo đã phát rồi; duplicate chỉ quyết định
  ticket này đứng riêng hay nối vào master, không quyết định có báo động hay
  không.

### 7.1 P5 và duplicate

| Kết luận duplicate | Hành vi |
| --- | --- |
| **Confident** (SAME_INCIDENT) | Nối vào master. Không tạo thêm mục emergency-review. Tăng bộ đếm báo cáo trùng trên master. Nếu master đang thấp hơn P5 thì **nâng master lên P5**. Nếu master đang trong case thì **tách master khỏi case**. |
| **Uncertain** | Giữ ticket P5 độc lập trong khi chờ điều phối viên. **Không** tắt cảnh báo. |
| **Different** | Giữ P5 độc lập. |

### 7.2 Grouping (chỉ P1–P4)

* Tối đa năm member một case.
* Sau khi gom, **rescore lại toàn bộ member đang hoạt động** của **mọi case đã
  bị chạm** — vì `backend_scope_score` của tất cả vừa đổi. Khi lần ghi tràn sang
  case kế tiếp thì đó là hai case, và cả hai đều phải được tính lại.
* Ticket P5 không tham gia (§4.2 quy tắc 5).
* Visual board tiếp tục bị chặn cho tới khi grouping đóng.

### 7.3 Khi grouping đẩy một member lên P5

Rescore sau grouping có thể làm một member vượt ngưỡng 80. Trình tự bắt buộc,
đúng thứ tự này:

1. Ghi một revision risk-assessment mới **trước khi tách**, kèm snapshot
   `case_id_snapshot`, `case_density_snapshot`, và điểm phạm vi tại thời điểm đó.
2. Giữ nguyên Priority P5 **sau khi** tách. Tách không phải là lý do hạ điểm.
3. Xoá **riêng** membership của ticket P5 đó.
4. Tính lại density cho case còn lại.
5. Không vô hiệu hoá các member khác — case còn lại vẫn phân việc được.
6. Nếu không còn member nào, đóng case kèm lý do.
7. Case rỗng không được xuất hiện trên board.

Bước 1 và 2 tồn tại để chặn một vòng lặp cụ thể: *grouping nâng lên P5 → tách
khỏi case → mất phạm vi → hạ về P4 → gom lại → nâng lên P5*. Priority P5 đã ghi
là một sự kiện đã xảy ra, không phải một hàm của trạng thái case hiện tại.

---

## 8. Bất biến P5: chỉ thủ công

**Không một đường phân việc nào được tạo assignment cho ticket P5.** Đây là bất
biến, không phải mặc định có thể tắt.

Mọi đường phân việc đi qua cùng một guard: auto-dispatch enqueue, dispatch
worker, backlog requeue, manual assignment, case assignment, visual board pool,
visual board confirm, available actions, assignment API, reassignment/requeue.

Khi một ticket **đang được phân** bị nâng lên P5:

* Không xoá lịch sử assignment.
* Kết thúc assignment đang hoạt động với lý do `EMERGENCY_MANUAL_ESCALATION`.
* Lập lại kế hoạch hàng đợi của kỹ thuật viên đó.
* Supersede dispatch event đang mở.
* Không gửi assignment mới.
* Không ghi tên ai đó như người xử lý thủ công — việc đó do BQL làm ngoài hệ.

Khi con người **xác nhận** P5: chỉ ghi audit. Không mở khoá board, không chuyển
sang auto-dispatch.

Khi con người **hạ cấp** P5 xuống P1–P4: chạy lại duplicate/grouping nếu cần, và
chỉ mở phân việc **sau khi** grouping đóng. Hạ cấp bắt buộc có lý do.

Ba truy vấn bất biến, phải luôn trả về rỗng:

```sql
-- không có assignment đang hoạt động thuộc ticket P5
-- không có IncidentCaseMember đang hoạt động thuộc ticket P5
-- không có dispatch event đang mở thuộc ticket P5
```

---

## 9. Lưu vết

`ticket_risk_assessments` là bảng **append-only**. Không UPDATE, không DELETE.
Một lần chấm là một hàng, nối với hàng trước bằng `supersedes_id` và đánh số
bằng `revision_no` (duy nhất trong mỗi ticket).

Ticket chỉ giữ **cache** của trạng thái hiện hành: `current_risk_assessment_id`,
`risk_score`, `priority`, `sla_started_at`, `sla_due_at`. Cache có thể dựng lại
từ bảng revision; bảng revision không dựng lại được từ cache.

Những sự kiện bắt buộc phải có hàng audit:

* Đánh giá của AI.
* Điểm backend tính ra.
* Rescore sau grouping.
* Blocker nâng sàn.
* P5 duplicate nâng master.
* P5 tách khỏi case.
* Con người hạ cấp.
* Assignment bị kết thúc do leo thang P5.

`rubric_version` được ghi trên mọi hàng. Phiên bản hiện tại: **`risk-v2.1`**.

`risk-v2.0` là bản trọng số 30/25/20/15/10. Một hàng mang nhãn đó được chấm
bằng rubric khác với hàng `risk-v2.1` bên cạnh nó, và không so sánh trực tiếp
được. Đó là lý do trường này tồn tại: đổi trọng số mà không đổi nhãn sẽ làm
hai lần chấm khác nhau trông như một.

---

## 10. Khi AI trả payload sai

* Payload sai schema → **retry**.
* Retry vẫn thất bại → **manual review**.
* **Không** đánh ticket của cư dân là `INVALID` chỉ vì AI trả sai. Lỗi kỹ thuật
  của hệ không phải là kết luận nghiệp vụ về phản ánh.
* Ticket chỉ `INVALID` khi input thực sự không hợp lệ, sau khi đã đi hết quy
  trình hỏi xác nhận.

---

## 11. Câu hỏi cho cư dân

Thay `SEVERITY_CONFIRMATION` bằng năm câu hỏi có mục tiêu, mỗi câu nhắm đúng một
tiêu chí đang thiếu bằng chứng:

| Kind | Nhắm tiêu chí |
| --- | --- |
| `SAFETY_CONFIRMATION` | `human_safety` |
| `SPREAD_CONFIRMATION` | `property_spread` |
| `ESSENTIAL_FUNCTION_CONFIRMATION` | `essential_function` |
| `AFFECTED_SCOPE_CONFIRMATION` | `affected_scope` |
| `DETERIORATION_CONFIRMATION` | `deterioration_speed` |

Ngân sách hỏi giữ nguyên như hiện tại, và mỗi lượt chỉ hỏi **một** câu.

### 11.1 `unknown_facts` và năm điểm là một sự thật, viết hai cách

Một tiêu chí nằm ở đúng **một** trong hai chỗ:

* có điểm 0–4, và tên nó **không** nằm trong `unknown_facts`; hoặc
* điểm để trống, và tên nó **có** trong `unknown_facts`.

Hai chiều đều bị chặn ở schema. Payload nguy hiểm là chiều thứ nhất:

```json
{ "affected_scope": 0, "unknown_facts": ["affected_scope"], "question_kind": "NONE" }
```

Từng trường đều hợp lệ. Ghép lại thì đủ năm điểm nên vòng chạy kết thúc, ticket
được chấm và công bố trên phạm vi 0, câu hỏi mà AI vừa nói là nó cần thì không
bao giờ được hỏi — trong khi hàng assessment lưu lại rằng AI không biết tiêu chí
đó.

Còn tiêu chí chưa biết thì chỉ có hai lối ra hợp lệ:

1. Hỏi đúng một câu nhắm vào tiêu chí đó.
2. Hết ngân sách hỏi → `LIMIT_REACHED`, điều phối viên chốt tay.

**Không có lối thứ ba.** Điền một điểm mặc định — 0, 2, hay bất cứ số nào — cho
tiêu chí AI không có căn cứ là bịa ra chính cái phán đoán mà rubric tồn tại để
bắt phải nói rõ.

---

## 12. Simulator

Simulator V1 (`OLD_APP`/`NEW_APP`, `WALL_CLOCK_V1`, `SERVICE_HOURS_DRAFT_V1`)
**giữ nguyên hành vi**. V2 là phần thêm vào, không phải bản viết lại:

* V1 vẫn chỉ hiểu P1–P3, và P3 vẫn là mức thủ công như cũ.
* V2 hiểu P1–P5, xếp hàng P4→P1, và trả P5 về nhánh
  `REQUIRES_MANUAL_P5_REVIEW` / `P5_MANUAL_REVIEW` / `NOT_EVALUABLE` mà không
  chọn kỹ thuật viên.
* `score_total` trong input simulator vẫn là tie-break. Ở V2 có thể truyền
  `risk_score` vào đúng trường đó để không phá contract cũ.
* SLA trong simulator đo tại `work_started_at`, đúng như §6.

---

## 13. Bảng thuật ngữ đã bị xoá

Những cái tên sau không còn tồn tại trong runtime. Nếu `rg` tìm thấy chúng ngoài
migration hoặc tài liệu lịch sử, đó là lỗi:

`Severity.LOW` · `Severity.MEDIUM` · `Severity.HIGH` · `severity_source` ·
`red_flag` · `base_score` · `priority_ceiling` · `density_bonus` ·
`location_bonus` · `P3_REVIEW_REQUIRED` · `SEVERITY_CONFIRMATION`
