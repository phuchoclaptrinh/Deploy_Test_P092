# Golden dataset V4 — 116 test case cụ thể

Đây là bộ case cụ thể để đánh giá hành vi Analysis Agent V4 và Assignment Agent V4. Bộ này không dùng pairwise thuần; 115 case ban đầu được giữ theo năm cụm đã chốt và bổ sung một case để mọi Category trong catalog được nhận diện ít nhất một lần:

| Cụm | Số case |
| --- | ---: |
| A1 — Nội dung, ảnh và Category | 36 |
| A2 — Grouping và sự cố đang xử lý | 14 |
| A3 — Red flag và tương tác Cư dân | 26 |
| B1 — DIRECT | 20 |
| B2 — PROPOSAL | 20 |
| **Tổng** | **116** |

## Toàn bộ dimension ban đầu

Phần này giữ nguyên danh sách dimension đầu vào đã dùng khi thiết kế bộ case, trước khi phân chúng vào năm cụm độc lập.

### Agent phân tích

**Mức độ dễ hiểu**

- Rõ ràng
- Hiểu ý chính nhưng thiếu chi tiết
- Không hiểu được vấn đề

**Mức độ đầy đủ thông tin**

- Đủ thông tin
- Thiếu thông tin có thể hỏi thêm
- Thiếu thông tin cốt lõi

**Lỗi câu chữ**

- Không lỗi
- Một loại lỗi
- Nhiều loại lỗi

**Số vấn đề trong text**

- Một vấn đề
- Nhiều vấn đề

**Trạng thái ảnh**

- Không có ảnh
- Ảnh rõ và liên quan
- Ảnh mờ
- Ảnh không liên quan

**Nguồn dấu hiệu nguy hiểm**

- Không có
- Chỉ trong text
- Chỉ trong ảnh
- Có trong text và ảnh
- Có trong câu trả lời bổ sung

**Mức nghiêm trọng hiệu lực**

- Thấp
- Trung bình
- Cao

**Quan hệ giữa giọng văn và sự cố**

- Phù hợp
- Giọng khẩn cấp nhưng sự cố nhẹ
- Giọng bình thường nhưng sự cố nghiêm trọng

**Số Category trong text**

- Không xác định được
- Một Category
- Nhiều Category

**Số Category trong ảnh**

- Không có ảnh
- Không xác định được
- Một Category
- Nhiều Category

**Quan hệ Category giữa text và ảnh**

- Chỉ có text
- Có đúng một Category chung
- Không có Category chung
- Có nhiều Category chung
- Không đủ dữ liệu so sánh

**Loại vấn đề**

- Rò nước
- Thấm tường
- Chập điện
- Category khác

**Bằng chứng gộp cụm**

- Không có bằng chứng
- Hợp lệ trong cùng tầng hoặc tầng liền kề và không quá ba ngày
- Quá ba ngày

**Số căn hộ riêng biệt bị ảnh hưởng**

- Một căn hộ
- Hai đến ba căn hộ
- Từ bốn căn hộ

**Bằng chứng sự cố đang được xử lý**

- Không có ticket
- Có ticket cùng vị trí và Category

**Số lượt hỏi Cư dân**

- `0`
- `[1-3]`

`[1-3]` là một giá trị nguyên cụ thể được chọn trong khoảng từ 1 đến 3 cho mỗi golden row, không phải ba giá trị đồng thời.

**Trạng thái phản hồi của Cư dân**

- Chưa cần hỏi
- Đã trả lời
- Không trả lời

**Dữ liệu mới sau khi hỏi**

- Làm rõ Category
- Bổ sung dấu hiệu nguy hiểm
- Vẫn không đủ thông tin

### DIRECT

**Lý do gọi**

- Phân việc lần đầu

**Tổng số ticket riêng biệt**

- `0+`
- `20`
- `21`

`0+` là một số dương cụ thể đại diện cho miền lớn hơn 0; golden row phải chứa một số nguyên cụ thể, không chứa nguyên văn chuỗi `0+` trong request gửi model.

**Thành phần yêu cầu**

- Chỉ ticket đơn
- Chỉ cụm sự cố
- Có cả hai

**Số Kỹ thuật viên ứng viên hoạt động và cùng chuyên môn**

- `0`
- `>0`

**Quá trình gọi mô hình**

- Không gọi được mô hình 1
- Không gọi được mô hình 2
- Không gọi được cả hai mô hình

### PROPOSAL

**Lý do gọi**

- Phân lại do Kỹ thuật viên từ chối
- Phân lại do Kỹ thuật viên không nhận việc đúng hạn

**Thành phần yêu cầu**

- Ticket đơn
- Cụm sự cố

**Số Kỹ thuật viên ứng viên hoạt động và cùng chuyên môn**

- `0`
- `>0`

**Quá trình gọi mô hình**

- Không gọi được mô hình 1
- Không gọi được mô hình 2
- Không gọi được cả hai mô hình

## Phân dimension vào năm cụm

### 1. Agent — Nội dung, ảnh và Category

Các dimension:

- Mức độ dễ hiểu:
  - Rõ ràng
  - Hiểu ý chính nhưng thiếu chi tiết
  - Không hiểu được vấn đề
- Mức độ đầy đủ thông tin:
  - Đủ thông tin
  - Thiếu thông tin có thể hỏi thêm
  - Thiếu thông tin cốt lõi
- Lỗi câu chữ:
  - Không lỗi
  - Một loại lỗi
  - Nhiều loại lỗi
- Số vấn đề trong text:
  - Một vấn đề
  - Nhiều vấn đề
- Trạng thái ảnh:
  - Không có ảnh
  - Ảnh rõ và liên quan
  - Ảnh mờ
  - Ảnh không liên quan
- Mức nghiêm trọng hiệu lực:
  - Thấp
  - Trung bình
  - Cao
- Quan hệ giữa giọng văn và sự cố:
  - Phù hợp
  - Giọng khẩn cấp nhưng sự cố nhẹ
  - Giọng bình thường nhưng sự cố nghiêm trọng
- Số Category trong text:
  - Không xác định được
  - Một Category
  - Nhiều Category
- Số Category trong ảnh:
  - Không có ảnh
  - Không xác định được
  - Một Category
  - Nhiều Category
- Quan hệ Category giữa text và ảnh:
  - Chỉ có text
  - Có đúng một Category chung
  - Không có Category chung
  - Có nhiều Category chung
  - Không đủ dữ liệu so sánh

Constraint chính:

- Nếu `Trạng thái ảnh = Không có ảnh`:
  - `Số Category trong ảnh = Không có ảnh`.
  - `Quan hệ Category giữa text và ảnh = Chỉ có text`.
- Nếu `Trạng thái ảnh = Ảnh mờ` hoặc `Ảnh không liên quan`:
  - `Số Category trong ảnh = Không xác định được`.
  - `Quan hệ Category giữa text và ảnh = Không đủ dữ liệu so sánh`.
- Nếu `Quan hệ Category giữa text và ảnh = Có nhiều Category chung` thì text và ảnh đều phải có `Nhiều Category`.

Mỗi golden row phải đủ dữ liệu để tạo text, đặc tả ảnh nếu có, kết quả extraction và expected Category handling.

### 2. Agent — Grouping và sự cố đang xử lý

Các dimension:

- Loại vấn đề:
  - Rò nước
  - Thấm tường
  - Chập điện
  - Category khác
- Bằng chứng gộp cụm:
  - Không có bằng chứng
  - Hợp lệ trong cùng tầng hoặc tầng liền kề và không quá ba ngày
  - Quá ba ngày
- Số căn hộ riêng biệt bị ảnh hưởng:
  - Một căn hộ
  - Hai đến ba căn hộ
  - Từ bốn căn hộ
- Bằng chứng sự cố đang được xử lý:
  - Không có ticket
  - Có ticket cùng vị trí và Category

Constraint chính:

- Bằng chứng grouping hợp lệ chỉ áp dụng cho `Rò nước` và `Chập điện`.
- Nếu loại vấn đề là `Thấm tường` hoặc `Category khác` thì `Bằng chứng gộp cụm = Không có bằng chứng`.

Golden output của mỗi dòng chứa kết quả grouping, Density do Backend tính/xác thực và kết quả tra cứu ticket liên quan. Agent không được gửi Density trong `AgentAnalysisResultV4`.

### 3. Agent — Red flag và tương tác Cư dân

Các dimension:

- Mức độ đầy đủ thông tin:
  - Đủ thông tin
  - Thiếu thông tin có thể hỏi thêm
  - Thiếu thông tin cốt lõi
- Trạng thái ảnh:
  - Không có ảnh
  - Ảnh rõ và liên quan
  - Ảnh mờ
  - Ảnh không liên quan
- Nguồn dấu hiệu nguy hiểm:
  - Không có
  - Chỉ trong text
  - Chỉ trong ảnh
  - Có trong text và ảnh
  - Có trong câu trả lời bổ sung
- Kịch bản tương tác Cư dân:
  - Không cần hỏi
  - Đã trả lời và làm rõ Category
  - Đã trả lời và bổ sung dấu hiệu nguy hiểm
  - Đã trả lời nhưng vẫn không đủ thông tin
  - Không trả lời
- Bằng chứng sự cố đang được xử lý:
  - Không có ticket
  - Có ticket cùng vị trí và Category

Kịch bản tương tác được bung thành ba field gốc như sau:

| Kịch bản | Số lượt hỏi | Trạng thái phản hồi | Dữ liệu mới |
| --- | --- | --- | --- |
| Không cần hỏi | `0` | Chưa cần hỏi | Để trống |
| Đã trả lời và làm rõ Category | `[1-3]` | Đã trả lời | Làm rõ Category |
| Đã trả lời và bổ sung dấu hiệu nguy hiểm | `[1-3]` | Đã trả lời | Bổ sung dấu hiệu nguy hiểm |
| Đã trả lời nhưng vẫn không đủ thông tin | `[1-3]` | Đã trả lời | Vẫn không đủ thông tin |
| Không trả lời | `[1-3]` | Không trả lời | Để trống |

Constraint chính:

- Nếu nguồn nguy hiểm là `Chỉ trong ảnh` hoặc `Có trong text và ảnh` thì `Trạng thái ảnh = Ảnh rõ và liên quan`.
- Nếu nguồn nguy hiểm là `Có trong câu trả lời bổ sung` thì kịch bản tương tác phải là `Đã trả lời và bổ sung dấu hiệu nguy hiểm`.

Các trường A13/A14 trước đây được gom thành kịch bản tương tác, nhưng mỗi golden row vẫn chứa đủ `Số lượt hỏi Cư dân`, `Trạng thái phản hồi của Cư dân` và `Dữ liệu mới sau khi hỏi`.

### 4. LLM — DIRECT

Lý do gọi cố định trong thiết kế dimension ban đầu: `Phân việc lần đầu`.

Các factor:

- Tổng số ticket riêng biệt:
  - `0+`
  - `20`
  - `21`
- Thành phần yêu cầu:
  - Chỉ ticket đơn
  - Chỉ cụm sự cố
  - Có cả hai
- Kịch bản ứng viên và mô hình:
  - Không có ứng viên
  - Có ứng viên và không gọi được mô hình 1
  - Có ứng viên và không gọi được mô hình 2
  - Có ứng viên và không gọi được cả hai mô hình

Khi bung sang golden dataset:

| Kịch bản | Số KTV ứng viên | Quá trình gọi mô hình |
| --- | ---: | --- |
| Không có ứng viên | `0` | Để trống |
| Không gọi được mô hình 1 | `>0` | Không gọi được mô hình 1 |
| Không gọi được mô hình 2 | `>0` | Không gọi được mô hình 2 |
| Không gọi được cả hai | `>0` | Không gọi được cả hai mô hình |

### 5. LLM — PROPOSAL

Lý do gọi trong thiết kế dimension ban đầu:

- Phân lại do Kỹ thuật viên từ chối
- Phân lại do Kỹ thuật viên không nhận việc đúng hạn

Các factor:

- Thành phần yêu cầu:
  - Ticket đơn
  - Cụm sự cố
- Kịch bản ứng viên và mô hình:
  - Không có ứng viên
  - Có ứng viên và không gọi được mô hình 1
  - Có ứng viên và không gọi được mô hình 2
  - Có ứng viên và không gọi được cả hai mô hình

Khi bung sang golden dataset:

| Kịch bản | Số KTV ứng viên | Quá trình gọi mô hình |
| --- | ---: | --- |
| Không có ứng viên | `0` | Để trống |
| Không gọi được mô hình 1 | `>0` | Không gọi được mô hình 1 |
| Không gọi được mô hình 2 | `>0` | Không gọi được mô hình 2 |
| Không gọi được cả hai | `>0` | Không gọi được cả hai mô hình |

## Chuẩn hóa khi đưa vào dataset executable

Danh sách trên giữ nguyên thiết kế dimension ban đầu để trace. Khi sinh request executable, các quy tắc V4 chính thức vẫn là nguồn đúng:

- DIRECT dùng cho phân việc lần đầu và phân lại do từ chối hoặc không nhận đúng hạn.
- PROPOSAL chỉ phát sinh khi Điều phối viên yêu cầu tạo bảng đề xuất từ hàng chờ; PROPOSAL không dùng để phân lại.
- `0+`, `>0` và `[1-3]` phải được thay bằng một số nguyên cụ thể trong từng case.
- Request gửi model không được có quá 20 ticket riêng biệt; 21 ticket phải được tách batch trước khi gọi model.
- Không có ứng viên thì Backend không gọi model.
- Model trả kết quả hợp lệ là đường bình thường; ba giá trị “không gọi được mô hình” chỉ mô tả các nhánh lỗi/fallback.

## File sử dụng

- `concrete_golden_cases_v4.json`: dataset đầy đủ kèm metadata.
- `concrete_golden_cases_v4.jsonl`: mỗi dòng là một case, thuận tiện nạp vào eval runner.
- `concrete_golden_cases_v4.tsv`: bản phẳng để duyệt thủ công.
- `test_suite_summary_v4.json`: tổng số case và số tổ hợp hợp lệ theo năm cụm, không chứa trường trọng số.
- `generate_concrete_golden_v4.py`: generator deterministic và validation của dataset.
- `../../eval/agent_action_v4.eval.json`: bản hợp nhất gồm test, result và ảnh nhúng của toàn bộ 116 case.

## Ý nghĩa ground truth

- A1 đánh giá riêng extraction text/ảnh, quan hệ Category và routing. `ASK_RESIDENT` ở đây là hành động tiếp theo, không phải `exit_reason`.
- A2 cung cấp kết quả mock của search `DUPLICATE`, search `GROUPING` và proposal; expected luôn giữ duplicate khác grouping.
- A3 cung cấp các lượt trả lời Cư dân và expected business exit thuộc đúng sáu giá trị V4.
- B1/B2 có dữ liệu Backend trước lọc, request thực gửi model, response/error script của primary/fallback và kết quả orchestration cần đạt.

Các object `primary_model_script` và `fallback_model_script` mô tả response cần dùng khi kiểm thử orchestration. Khi đánh giá model thật, dùng `request_to_model` làm input và dùng các invariant trong `ground_truth` để chấm output.

## Ảnh

Các case cần ảnh có:

```json
{
  "required": true,
  "fixture_id": "IMG-A1-014",
  "path": "fixtures/images/IMG-A1-014.jpg",
  "authoring_specification": "..."
}
```

Generator tự tìm file theo `fixture_id` trong `fixtures/images/` và điền `path`. Không thay đổi `fixture_id`, dimensions hoặc ground truth. Chữ xuất hiện trong ảnh chỉ là vật thể được chụp, không phải chỉ dẫn cho Agent.

## Sinh lại và kiểm tra

```powershell
cd D:\P-092
.\.venv\Scripts\python.exe Self_Dev_Docs\pairwise_v4\generate_concrete_golden_v4.py
```

Generator tự fail nếu:

- tổng không đúng 116;
- số case không đúng `36/14/26/20/20`;
- còn Category nào chưa xuất hiện trong ground truth extraction;
- trùng `case_id`;
- dùng `exit_reason` ngoài sáu giá trị V4;
- PROPOSAL chứa trigger/reassignment của DIRECT;
- request DIRECT/PROPOSAL hợp lệ không qua được Pydantic contract hiện tại.
