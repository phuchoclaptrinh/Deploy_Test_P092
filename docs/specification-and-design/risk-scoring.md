# Tính điểm rủi ro

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Mã thang đánh giá:** `risk-v2.1`

## 1. Ranh giới trách nhiệm

Agent phân tích chấm năm tiêu chí và nêu các blocker được hỗ trợ kèm bằng chứng. Backend quản lý trọng số, phép tính, ngưỡng, hiệu chỉnh phạm vi và mức ưu tiên cuối cùng. Nhãn danh mục và vị trí không cộng thêm điểm ẩn.

Agent không được phép trả về `risk_score`, `priority` hoặc một trường mức độ nghiêm trọng riêng.

## 2. Các tiêu chí

Mỗi tiêu chí là một số nguyên từ 0 đến 4.

| Tiêu chí | Trọng số | Câu hỏi cần trả lời |
| --- | ---: | --- |
| `human_safety` | 35 | Con người tiếp xúc trực tiếp với thương tích hoặc nguy hiểm tức thời ở mức nào? |
| `property_spread` | 5 | Thiệt hại vật chất có khả năng lan ra ngoài phạm vi hiện tại đến mức nào? |
| `essential_function` | 35 | Một chức năng thiết yếu của tòa nhà hoặc hộ gia đình bị ảnh hưởng nghiêm trọng đến mức nào? |
| `affected_scope` | 20 | Có bao nhiêu căn hộ riêng biệt hoặc người dùng khu vực chung được xác nhận chịu ảnh hưởng? |
| `deterioration_speed` | 5 | Sự cố xấu đi nhanh đến mức nào nếu không can thiệp? |

Thang điểm có ý nghĩa nhất quán:

- `0`: không có tác động được chứng minh đối với tiêu chí;
- `1`: tác động hạn chế;
- `2`: tác động đáng kể nhưng đã được giới hạn;
- `3`: tác động lớn, cần can thiệp sớm;
- `4`: tác động tối đa được bằng chứng chứng minh.

Nếu bằng chứng hiện có không thể xác lập một tiêu chí, Agent phải nêu tên tiêu chí đó trong `unknown_facts`. Không được biểu diễn kiến thức còn thiếu bằng giá trị 0 ngầm định.

## 3. Công thức

Với mỗi tiêu chí:

```text
contribution = criterion_score / 4 × criterion_weight
```

Điểm số cuối cùng:

```text
risk_score = Σ contribution
```

Phép tính dùng số thập phân và chỉ làm tròn lượng tử một lần đến hai chữ số thập phân sau khi tính tổng.

## 4. Các dải ưu tiên

| Điểm | Mức ưu tiên | Ý nghĩa vận hành |
| ---: | :---: | --- |
| `0 ≤ score < 20` | P1 | Thông thường |
| `20 ≤ score < 40` | P2 | Cần lưu ý |
| `40 ≤ score < 60` | P3 | Quan trọng |
| `60 ≤ score < 80` | P4 | Khẩn |
| `80 ≤ score ≤ 100` | P5 | Khẩn cấp, chỉ xử lý thủ công |

Thứ hạng ưu tiên tăng từ P1 đến P5. Xem xét thủ công là một trạng thái phân loại và không bao giờ là giá trị ưu tiên.

## 5. Sàn ưu tiên do blocker

Blocker thiết lập mức ưu tiên tối thiểu. Blocker không bao giờ trừ điểm hoặc hạ kết quả được suy ra từ điểm.

### Sàn P5

- `FIRE_OR_SMOKE`
- `ELECTRIC_SHOCK_OR_LIVE_WIRE`
- `GAS_LEAK_OR_ASPHYXIATION`
- `SERIOUS_INJURY`
- `PERSON_TRAPPED_IN_ELEVATOR`
- `SOLE_ESCAPE_ROUTE_BLOCKED`
- `ONGOING_VIOLENCE`

### Sàn P4

- `SEWAGE_OVERFLOW`
- `HEAVY_WATER_FLOW_SPREAD_RISK`
- `TOTAL_UNPLANNED_UTILITY_LOSS`
- `SOLE_TOILET_UNUSABLE`

Mỗi blocker cần có bằng chứng được lưu dưới mã tương ứng. Mã blocker không xác định sẽ làm xác thực thất bại.

## 6. Phạm vi ảnh hưởng thực tế

Agent ước tính phạm vi từ một báo cáo. Khi Backend có số lượng đã xác nhận trong nhóm sự cố, số lượng đó thay thế ước tính theo cả hai hướng.

```text
backend_scope_score = clamp(distinct_affected_units - 1, 0, 4)
```

Ví dụ:

| Số căn hộ đã xác nhận | Điểm phạm vi |
| ---: | ---: |
| 1 | 0 |
| 2 | 1 |
| 3 | 2 |
| 4 | 3 |
| 5 trở lên | 4 |

Đánh giá lưu phạm vi do Agent xác định, phạm vi do Backend xác định và phạm vi thực tế để người vận hành thấy được lý do kết quả thay đổi sau khi tính lại.

## 7. Cổng kiểm soát khẩn cấp

P5 không bao giờ đi vào quy trình điều phối kỹ thuật viên tự động hoặc trực quan.

1. Phân tích phát cảnh báo tức thời khi phát hiện nguy hiểm có bằng chứng.
2. Kết quả được lưu với trạng thái chờ xem xét khẩn cấp.
3. Điều phối viên xác nhận P5 hoặc hạ mức kèm lý do.
4. Xác nhận sẽ giữ quy trình xử lý thủ công và nằm ngoài hàng đợi kỹ thuật viên.
5. Hạ mức sẽ ghi nối tiếp một phiên bản rủi ro mới và có thể tiếp tục các bước phân tích/điều phối thông thường.

## 8. Chính sách SLA

Giờ phục vụ là 08:00–18:00 theo múi giờ Asia/Saigon mỗi ngày. Đồng hồ P1–P4 tạm dừng ngoài khung giờ này. P5 dùng cổng phản hồi năm phút theo thời gian thực cho ban quản lý tòa nhà và không nằm trong phép đo tuân thủ SLA của kỹ thuật viên.

| Mức ưu tiên | Thời lượng | Loại đồng hồ |
| :---: | ---: | --- |
| P1 | 1.800 phút phục vụ | Giờ phục vụ |
| P2 | 1.200 phút phục vụ | Giờ phục vụ |
| P3 | 600 phút phục vụ | Giờ phục vụ |
| P4 | 180 phút phục vụ | Giờ phục vụ |
| P5 | 5 phút | Thời gian thực, phản hồi thủ công |

Trạng thái sẵn sàng của kỹ thuật viên không làm thay đổi cam kết SLA với cư dân.



## 9. Vị trí triển khai

- Tính điểm: `src/domain/risk_scoring.py`
- Đồng hồ SLA: `src/domain/sla_clock.py`
- Lưu trữ bền vững: `src/database/models/ticket_risk_assessment.py`
- Dịch vụ ứng dụng: `src/services/risk_assessment_service.py`
