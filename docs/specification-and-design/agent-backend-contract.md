# Hợp đồng Agent–Backend

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Mã hợp đồng:** `canonical`

## 1. Ranh giới

Agent suy luận trên bằng chứng. Backend nắm quyền quyết định.

| Agent được phép | Agent không được phép |
| --- | --- |
| Chọn một danh mục từ bản chụp danh mục được cố định. | Tạo danh mục hoặc tin vào tên danh mục dạng văn bản tự do. |
| Trích xuất dữ kiện sự cố có thể quan sát. | Ghi trực tiếp các dòng ticket, phân công hoặc kiểm toán. |
| Chấm năm tiêu chí rủi ro từ 0 đến 4. | Trả về điểm rủi ro hoặc mức ưu tiên cuối cùng. |
| Nêu blocker được hỗ trợ kèm bằng chứng. | Tự tạo mã blocker hoặc thay đổi sàn ưu tiên. |
| Đặt câu hỏi làm rõ có giới hạn. | Vượt ngân sách câu hỏi/công cụ. |
| Đánh giá ứng viên trùng lặp do Backend cung cấp. | Tìm tùy ý trong dữ liệu cư dân hoặc làm lộ báo cáo của cư dân khác. |

Mọi lược đồ đều cấm trường không xác định. Gói dữ liệu chứa trường chưa khai báo sẽ bị từ chối thay vì được chấp nhận một phần.

## 2. Gói đầu vào

Backend tập hợp trạng thái có thể tuần tự hóa thành JSON, bao gồm:

- ID ticket và phiên phân tích;
- mô tả của cư dân và đầu vào hình ảnh đã được cấp quyền;
- vị trí đã chọn và ngữ cảnh tầng;
- bản chụp danh mục được cố định và ID danh mục;
- cuộc hội thoại làm rõ trước đó;
- ID mô hình;
- bộ đếm ngân sách do Backend quản lý;
- ứng viên trùng lặp đã được làm sạch khi đã chạy tìm kiếm.

Đồ thị không giữ đối tượng ORM. Trạng thái có thể được ghi điểm kiểm tra và tiếp tục trong yêu cầu hoặc tiến trình khác.

## 3. Kết quả phân loại

Kết quả có cấu trúc:

```json
{
  "ticket_id": "uuid",
  "analysis_session_id": "uuid",
  "exit_reason": "ANALYSIS_COMPLETE",
  "category_id": "uuid",
  "text_category_id": "uuid-or-null",
  "image_category_id": "uuid-or-null",
  "criteria": {
    "human_safety": 0,
    "property_spread": 0,
    "essential_function": 0,
    "affected_scope": 0,
    "deterioration_speed": 0
  },
  "blockers": [],
  "evidence": {
    "human_safety": [],
    "property_spread": [],
    "essential_function": [],
    "affected_scope": [],
    "deterioration_speed": [],
    "blockers": {}
  },
  "unknown_facts": [],
  "ai_reason": "Giải thích có căn cứ",
  "location_id": "uuid",
  "duplicate": null,
  "duplicate_verdict": "DIFFERENT_INCIDENT",
  "duplicate_reason": "Giải thích có căn cứ",
  "duplicate_candidates": [],
  "tool_usage": {
    "total_tool_calls": 0,
    "ask_resident_rounds": 0,
    "ask_resident_elapsed_seconds": 0,
    "search_related_tickets_called": false,
    "propose_case_grouping_called": false
  },
  "category_catalog_version": "catalog-id",
  "model_version": "model-id",
  "analyzed_at": "ISO-8601"
}
```

`text_category_id` và `image_category_id` giải thích bằng chứng theo từng nguồn; chúng không ghi đè hoặc hợp nhất vào `category_id`.

## 4. Lý do kết thúc

| Lý do kết thúc | Ý nghĩa |
| --- | --- |
| `ANALYSIS_COMPLETE` | Danh mục và bằng chứng rủi ro đã đầy đủ. |
| `EMERGENCY_REVIEW_REQUIRED` | Rủi ro cuối cùng đạt ranh giới khẩn cấp và cần con người quyết định. |
| `DUPLICATE_EXISTING` | Được liên kết chắc chắn với một ticket gốc đang hoạt động. |
| `DUPLICATE_UNCERTAIN` | Quan hệ ứng viên cần điều phối viên quyết định. |
| `LIMIT_REACHED` | Hết ngân sách trước khi có kết quả nghiệp vụ đủ tin cậy. |
| `INSUFFICIENT_INPUT` | Báo cáo không thể trở thành công việc có thể xử lý từ bằng chứng hiện có. |

Ngoại lệ từ nhà cung cấp, công cụ hoặc lỗi lưu trữ bền vững là lỗi kỹ thuật và không trở thành lý do kết thúc nghiệp vụ.

## 5. Hợp đồng làm rõ

Các loại câu hỏi được hỗ trợ:

- xác nhận danh mục;
- xác nhận vị trí;
- xác nhận sự cố tái diễn sau lần hoàn thành gần đây;
- một câu hỏi tập trung cho từng tiêu chí rủi ro.

Giới hạn:

| Ngân sách | Giới hạn |
| --- | ---: |
| Lệnh gọi công cụ | 5 |
| Số vòng hỏi cư dân | 3 |
| Tổng thời gian chờ | 300 giây |
| Ứng viên trùng lặp | 10 |
| Ứng viên gom nhóm | 5 |

Câu hỏi rủi ro chỉ hợp lệ khi tiêu chí tương ứng xuất hiện trong `unknown_facts`. Không bao giờ giao cho cư dân đánh giá trùng lặp hoặc gom nhóm vì họ không thể xem các báo cáo khác.

## 6. Ranh giới công cụ

### Danh mục phân loại

Agent nhận ID và tên hiển thị của danh mục được cố định theo phiên. Khi hoàn tất, hệ thống xác minh ID trả về có trong bản chụp đó và vẫn hợp lệ đối với giao dịch.

### Tìm ticket liên quan

Backend lọc ứng viên theo mục đích:

- tìm bản trùng dùng cùng danh mục và chính xác cùng vị trí, bao gồm công việc đang hoạt động và công việc mới hoàn thành trong một khoảng hẹp;
- tìm nhóm dùng danh mục tương thích và cấu trúc vị trí trong tòa nhà.

Ứng viên chứa bản tóm tắt đã ẩn thông tin nhạy cảm và trường vận hành, không chứa danh tính người báo cáo, mô tả gốc hoặc tệp đính kèm.

### Tạo câu hỏi cho cư dân

Backend xác thực loại câu hỏi, lựa chọn, quyền sở hữu, tính duy nhất của câu hỏi đang mở và ngân sách trước khi lưu.

## 7. Kiểm tra khi hoàn tất

Trong một giao dịch, Backend xác minh:

- ID ticket/phiên và lần thực thi đang hoạt động;
- tính nhất quán của danh mục và vị trí;
- phạm vi tiêu chí, sự thống nhất với dữ kiện chưa biết và bằng chứng blocker;
- bộ đếm công cụ đã khai báo so với bộ đếm đã lưu;
- sự tồn tại và tính hợp lệ của ticket gốc khi trùng lặp;
- các bất biến khi kết thúc;
- điều kiện khẩn cấp và xem xét thủ công.

Chỉ sau khi xác thực, Backend mới ghi nối tiếp lần chạy phân tích, đánh giá rủi ro, lịch sử trạng thái, bản ghi kiểm toán và thông báo.

## 8. Quyền riêng tư và khả năng quan sát

- Đầu vào mô hình chỉ chứa bằng chứng cần thiết cho báo cáo hiện tại.
- Ứng viên ticket liên quan được ẩn thông tin nhạy cảm trước khi đi vào trạng thái Agent.
- Dữ liệu truy vết từ xa không chứa văn bản cư dân, tiêu đề xác thực và URL ký số.
- Dữ liệu truy vết cục bộ dùng gói dữ liệu có cấu trúc đã làm sạch và vùng lưu trữ do người vận hành kiểm soát.
- Lớp bọc truy vết không thay đổi node đồ thị, định tuyến hoặc kết quả mô hình.

## 9. Vị trí triển khai

- Lược đồ: `src/models/agent_schemas.py`
- Trạng thái đồ thị: `src/agents/state.py`
- Cấu trúc đồ thị: `src/agents/graph.py`
- Thực thi giới hạn công cụ: `src/services/agent_tool_service.py`
- Hoàn tất: `src/services/agent_result_service.py`
