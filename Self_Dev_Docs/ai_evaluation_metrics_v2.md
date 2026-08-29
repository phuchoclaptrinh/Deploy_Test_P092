# Quy trình Đánh giá AI (AI Evaluation)

## Hệ thống phân loại & ưu tiên phản ánh chung cư bằng AI

---

## 1. Hai lớp cần đánh giá riêng biệt

Vì Agent tự quyết định gọi công cụ (tra cứu, gộp case, hỏi lại cư dân) thay vì chạy 1 chuỗi bước cố định, việc đánh giá cần tách rõ 2 lớp, không gộp chung 1 bộ số:

- **Field-level**: đúng/sai của từng trường dữ liệu Agent trích xuất (category, red-flag, severity, is_relevant) — so với nhãn đúng trên tập test.
- **Hành vi agentic**: cách Agent đi tới kết luận đó (dùng bao nhiêu công cụ, hỏi mấy vòng, có ngắt kịp khi phát hiện nguy hiểm giữa chừng không) — không có trong pipeline cố định, cần đo riêng.

---

## 2. Field-level metrics

| Field                                     | Bản chất                    | Metric                                              | Ghi chú                                                                                                                                                                                                                          |
| ----------------------------------------- | --------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `text_categories` / `image_categories`    | Multi-label classification  | Precision, Recall, F1 theo từng nhãn                | 1 ticket đúng 1 phần vẫn phải tính là sai 1 phần, không chỉ đúng/sai cả câu                                                                                                                                                      |
| `red_flag_text` / `red_flag_signal`       | Binary, an toàn tính mạng   | **Recall là số 1**, Precision là phụ                | Bỏ sót 1 ca thật nguy hiểm hơn nhiều báo nhầm                                                                                                                                                                                    |
| `severity`                                | 3 lớp có thứ tự             | Accuracy + đo lệch bao xa (ordinal distance)        | Đoán Medium thay vì High đỡ tệ hơn đoán Low thay vì High                                                                                                                                                                         |
| `is_relevant`                             | Binary, chặn cứng cả ticket | **Cả Precision lẫn Recall**, không chỉ Recall       | 1 trường duy nhất, xét chung toàn bộ nội dung đã gửi (text và/hoặc ảnh), không tách riêng theo modality. Chặn nhầm ticket thật (Precision thấp) gây phiền cư dân thật sự — không được coi nhẹ hơn Recall như cách xử lý red-flag |
| `category_match` (khớp Category ảnh/text) | So khớp 2 tập nhãn          | Precision/Recall của cơ chế đẩy sang duyệt thủ công | Chỉ áp dụng khi ticket có ảnh — không có ảnh thì đi thẳng vào tính điểm bằng `text_categories`, không có bước so khớp                                                                                                            |

---

## 3. Hành vi agentic — metrics riêng, không có trong pipeline cố định

| Metric                                            | Cách đo                                                                                        | Vì sao cần                                                                                                   |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Tool-call efficiency                              | Số lần gọi tool trung bình/ticket, % ticket chạm trần 5 lần                                    | Chạm trần nhiều nghĩa là định hướng Agent trong prompt chưa tốt, tốn chi phí oan                             |
| Tỷ lệ giải quyết theo vòng hỏi cư dân             | % giải quyết được ở vòng 1/2/3/không giải quyết được                                           | Đa số phải hỏi tới vòng 3 mới xong → câu hỏi vòng đầu chưa hiệu quả                                          |
| **Độ trễ ngắt red-flag khi phát hiện giữa chừng** | Thời gian từ lúc dấu hiệu nguy hiểm xuất hiện tới lúc Priority được ép ở mức khẩn cấp nhất     | Phải gần như tức thời — đo riêng theo từng nguồn phát hiện (mục 6, nhóm D), không dùng chung 1 test đại diện |
| Tỷ lệ ticket chuyển "Không hợp lệ"                | Tách riêng theo 2 nguyên nhân: do `is_relevant = false`, và do hết thời gian chờ trả lời       | Trộn chung sẽ che mất nguyên nhân nào đang gây phiền cư dân nhiều hơn                                        |
| Tỷ lệ "chuyển duyệt thủ công" oan                 | % ticket chuyển duyệt thủ công mà Điều phối viên xác nhận đúng y hệt Category Agent đã đề xuất | Đo mức độ Agent quá thận trọng, đẩy việc không cần thiết cho Điều phối viên                                  |
| `get_category_catalog()`                          | Kiểm tra Agent luôn phân loại theo danh mục đang hiệu lực tại thời điểm chạy                   | Không phải ML metric — test dạng có/không, đảm bảo không dùng danh mục lỗi thời                              |
| **Quyết định gộp case** (`propose_case_grouping`) | Precision/Recall so với nhãn "thực sự cùng 1 sự cố" do người gán tay                           | Sai ở đây trực tiếp làm sai Density → sai Priority                                                           |

---

## 4. Người test giả lập

Vì Agent có thể dừng giữa chừng chờ cư dân trả lời, cần thủ công đánh để agent không treo chờ người thật:

- **Trả lời trắc nghiệm**: chọn option khớp nhất với nhãn đúng đã gán sẵn cho ticket.
- **Trả lời tự nhập (free-text)**: sinh câu trả lời theo mẫu dựa trên nhãn đúng (rule-based, không cần LLM riêng) — đủ để giữ vòng lặp chạy tiếp, và đủ để chèn từ khóa nguy hiểm khi cần test riêng nhánh "red-flag lộ ra qua câu trả lời tự nhập".

---

## 5. Xây dựng tập dữ liệu có nhãn

- Tách tập nhỏ để thử prompt lúc dev, và 1 tập giữ kín chỉ chạy 1 lần cuối để báo cáo — không dùng chung 1 tập.
- Chủ động đưa đủ số lượng ca red-flag thật vào tập test (ví dụ 20–30 ca) — lấy mẫu ngẫu nhiên sẽ khiến red-flag quá hiếm để Recall có ý nghĩa thống kê.
- Tương tự, chủ động đưa đủ ca ngay từ đầu đã không liên quan và ca cần hỏi lại cư dân (nhiều vòng, có/không trả lời kịp) — đây là các nhánh hiếm gặp tự nhiên nếu chỉ lấy mẫu ngẫu nhiên.
- Người gán nhãn cần được hướng dẫn phân biệt rõ **"không liên quan"** (sai chủ đề hoàn toàn) với **"liên quan nhưng sơ sài"** (đúng chủ đề, thiếu chi tiết) — 2 khái niệm dễ lẫn, ảnh hưởng trực tiếp độ chính xác nhãn `is_relevant`.

---

## 6. Ma trận test case đầy đủ

### Nhóm C — ảnh và text có liên quan ? (7 case)

| #   | Text                     | Ảnh                                           | `is_relevant` | Kết quả mong đợi                                           |
| --- | ------------------------ | --------------------------------------------- | ------------- | ---------------------------------------------------------- |
| 1   | Đúng chủ đề, đủ chi tiết | Không có                                      | true          | Xử lý tiếp                                                 |
| 2   | Đúng chủ đề, đủ chi tiết | Đúng chủ đề                                   | true          | Xử lý tiếp                                                 |
| 3   | Đúng chủ đề, đủ chi tiết | Tào lao                                       | false         | Ticket tạo xong → chuyển ngay "Không hợp lệ"               |
| 4   | Tào lao                  | Không có                                      | false         | "Không hợp lệ"                                             |
| 5   | Tào lao                  | Đúng chủ đề                                   | false         | "Không hợp lệ"                                             |
| 6   | Tào lao                  | Tào lao                                       | false         | "Không hợp lệ"                                             |
| 7   | Đúng chủ đề, đủ chi tiết | Không đọc được (lỗi kỹ thuật, khác "tào lao") | true          | Rẽ nhánh lỗi đọc ảnh riêng, không phải nhánh `is_relevant` |

Ghi chú: "Text không có" không còn là ô cần test ở tầng Agent — text luôn bắt buộc, việc thiếu text bị chặn ở tầng validate form, tách khỏi phạm vi eval này.

### Nhóm D — Red-flag theo nguồn phát hiện (6 case)

| #   | Nguồn phát hiện                                         | Hành vi mong đợi                                                                                                                   |
| --- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Ngay từ text ban đầu                                    | Ép Priority khẩn cấp nhất trước khi vào vòng hỏi thêm                                                                              |
| 2   | Ngay từ ảnh ban đầu                                     | Tương tự                                                                                                                           |
| 3   | Giữa vòng, qua kết quả tra cứu ticket liên quan         | Trường hợp hiếm nhưng cần test — ngữ cảnh từ ticket khác khiến Agent nhận ra mức nghiêm trọng cao hơn                              |
| 4   | Giữa vòng, qua trả lời trắc nghiệm                      | Cư dân chọn đúng option lộ dấu hiệu nguy hiểm                                                                                      |
| 5   | Giữa vòng, qua câu trả lời tự nhập                      | Cư dân gõ chữ chứa từ khóa nguy hiểm — cơ chế nhận diện khác case #4 (hiểu văn bản tự do thay vì chọn nút cố định), cần test riêng |
| 6   | Giữa vòng, qua ảnh mới sau khi chọn "chụp lại ảnh khác" | Ảnh mới cho thấy dấu hiệu nguy hiểm mà ảnh cũ không có                                                                             |

Với cả 6 case: đo độ trễ từ lúc dấu hiệu xuất hiện tới lúc Priority được ép mức khẩn cấp — không được chờ hết vòng hỏi hay hết thời gian ở bất kỳ case nào.

### Nhóm E/F — Vòng lặp công cụ và giới hạn (9 case)

| #   | Tình huống                                                                 | Hành vi mong đợi                                           |
| --- | -------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1   | Đủ tự tin ngay, không cần dùng công cụ nào                                 | Kết luận thẳng, không vào vòng hỏi thêm                    |
| 2   | Đủ tự tin sau đúng 1 vòng hỏi                                              | Giải quyết ở vòng 1                                        |
| 3   | Đủ tự tin sau đúng 2 vòng hỏi                                              | Giải quyết ở vòng 2                                        |
| 4   | Đủ tự tin sau đúng 3 vòng hỏi                                              | Giải quyết ở vòng 3                                        |
| 5   | Hỏi hết 3 vòng vẫn chưa đủ tự tin                                          | Chuyển duyệt thủ công                                      |
| 6   | Chạm trần 5 lần gọi công cụ (không phải do hỏi hết vòng)                   | Chuyển duyệt thủ công                                      |
| 7   | Hết 5 phút không có phản hồi nào                                           | "Không hợp lệ"                                             |
| 8   | Trả lời trễ, sau khi đã hết 5 phút                                         | Bị từ chối, ticket vẫn giữ "Không hợp lệ", không khôi phục |
| 9   | Trộn kiểu trả lời trong cùng 1 ticket (vòng 1 bấm nút, vòng 2 tự nhập chữ) | Agent xử lý nhất quán cả 2 kiểu, không lỗi giữa chừng      |

### Nhóm E2 — Quyết định gộp case (4 case)

| #   | Tình huống                                                                                       | Hành vi mong đợi                           |
| --- | ------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| 1   | Có ticket ứng viên thỏa điều kiện (cùng category/tầng/thời gian) nhưng thực chất không liên quan | Agent không gộp                            |
| 2   | Có ticket ứng viên thỏa điều kiện và thực sự cùng 1 sự cố                                        | Agent gộp, Density tính đúng số căn hộ     |
| 3   | Không có ticket ứng viên nào                                                                     | Density mặc định, không gộp                |
| 4   | Category không phải rò nước/chập điện                                                            | Không chạy bước gộp, Density không áp dụng |

### Nhóm G — Đối chiếu category (3 case)

| #   | Tình huống                  | Hành vi mong đợi                                              |
| --- | --------------------------- | ------------------------------------------------------------- |
| 1   | Không có ảnh                | Bỏ qua bước đối chiếu, tính điểm thẳng bằng `text_categories` |
| 2   | Có ảnh, khớp category       | Tiếp tục tính điểm                                            |
| 3   | Có ảnh, không khớp category | Chuyển duyệt thủ công                                         |

### Nhóm xử lý duyệt thủ công (3 case)

| #   | Tình huống                                             | Hành vi mong đợi                                          |
| --- | ------------------------------------------------------ | --------------------------------------------------------- |
| 1   | Điều phối viên xác nhận hợp lệ, điều chỉnh category    | Tính lại Density/điểm theo category mới                   |
| 2   | Điều phối viên xác nhận không hợp lệ                   | Loại bỏ ticket, cư dân nhận thông báo lý do               |
| 3   | Điều phối viên chọn yêu cầu bổ sung thay vì quyết ngay | Ticket chờ, không giới hạn thời gian như vòng hỏi tự động |

**Tổng cộng 32 case**, phủ toàn bộ nhánh rẽ trong sơ đồ xử lý.

---

## 7. Ngưỡng chấp nhận

| Metric                                                        | Ngưỡng đề xuất                                       | Lý do                                                                             |
| ------------------------------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------- |
| Category (F1 trung bình)                                      | ≥ 85–90%                                             |                                                                                   |
| Red-flag Recall                                               | Càng gần 100% càng tốt, chấp nhận Precision thấp hơn | An toàn tính mạng — thà báo nhầm còn hơn bỏ sót                                   |
| Severity Accuracy                                             | ≥ 75%, không lệch quá 1 bậc                          | Sai 1 bậc còn cứu được bằng Density/Ceiling, sai 2 bậc nguy hiểm                  |
| `is_relevant` Precision **và** Recall                         | Cả hai ≥ 90%                                         | Cả chặn nhầm lẫn bỏ sót đều gây hậu quả thật — không ưu tiên 1 chiều như red-flag |
| Độ trễ ngắt red-flag giữa vòng (mọi nguồn ở nhóm D)           | < 2 giây xử lý nội bộ                                | Phải coi như tức thời                                                             |
| Tỷ lệ chạm trần 5 lần gọi công cụ                             | < 10%                                                | Chạm trần thường xuyên nghĩa là định hướng Agent trong prompt chưa tốt            |
| Tỷ lệ "Không hợp lệ" do timeout trên tổng ticket thường       | < 5%                                                 | Cao hơn cho thấy vấn đề UX câu hỏi, không nên đổ lỗi cư dân                       |
| Tỷ lệ giải quyết ở vòng hỏi đầu tiên (trong số ticket có hỏi) | ≥ 50%                                                | Nếu đa số phải hỏi tới vòng 2–3, câu hỏi đầu cần thiết kế lại                     |

---

## 8. Quy trình chạy đánh giá

```
Tập test giữ kín
      ↓
Chạy Agent thật hàng loạt, dùng test thủ công hoặc giả lập  cho mọi ticket rơi vào vòng hỏi
      ↓
So sánh output với nhãn đúng (field-level) + đo hành vi agentic (mục 3)
      ↓
Tính bảng metric (mục 2, 3) + Confusion Matrix cho Category và Severity
      ↓
Lưu kết quả
      ↓
Chạy lại mỗi khi đổi prompt/model → so sánh có cải thiện không
```

---

## 9. Công cụ sử dụng

- `scikit-learn` (`classification_report`, `confusion_matrix`) cho các metric phân loại chuẩn.
- Bộ đếm tool-call, đọc trực tiếp từ log trace của Agent, để tính các metric ở mục 3.
