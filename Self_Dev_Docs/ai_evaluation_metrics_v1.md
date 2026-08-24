# Quy trình Đánh giá AI (AI Evaluation) — Hệ thống phân loại & ưu tiên phản ánh chung cư FixAgent

---

## 1. Bảng Metric đánh giá theo từng Field đầu ra của AI Agent

| Field                                                     | Bản chất bài toán                        | Metric phù hợp                                                                                                | Vì sao không dùng Accuracy đơn giản                                                                                                               |
| --------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `text_categories` / `image_categories`                    | Multi-label classification               | Precision, Recall, F1 theo từng category                                                                      | 1 ticket có thể đúng nhãn "điện" nhưng thiếu nhãn "nước" — accuracy nhị phân (đúng/sai cả câu) không phản ánh đúng mức độ sai                     |
| `red_flag_text` / `red_flag_signal`                       | Binary classification, an toàn tính mạng | **Recall là số 1** (tối thiểu hóa False Negative), Precision là phụ                                           | Bỏ sót 1 ca cháy thật (False Negative) nguy hiểm hơn nhiều so với báo nhầm 1 ca không nguy hiểm (False Positive) — 2 loại lỗi không ngang giá trị |
| `severity`                                                | 3 lớp có thứ tự (Low<Medium<High)        | Accuracy + đo "lệch bao xa" (ordinal distance, không chỉ đúng/sai)                                            | Đoán "Medium" thay vì "High" đỡ tệ hơn đoán "Low" thay vì "High" — cần phân biệt 2 loại sai này                                                   |
| `category_match` (F5)                                     | So khớp 2 tập nhãn                       | Precision/Recall của chính cơ chế P0                                                                          | Đây là cơ chế an toàn (đẩy sang người xử lý thủ công) — nếu Recall thấp, ticket sai lọt qua mà không ai biết                                      |
| `is_relevant` (ảnh có liên quan tới sự cố chung cư không) | Binary classification                    | **Recall cao cho lớp "không liên quan"** — thà từ chối nhầm 1 ảnh thật còn hơn để lọt 1 ảnh rác vào tính điểm | Nếu bỏ sót, hệ thống tính ra Priority từ dữ liệu vô nghĩa — nguy hiểm hơn cả sai category, vì người dùng không biết ticket của mình đang "rác"    |

---

## 2. Xây dựng tập dữ liệu có nhãn

- **Không dùng chung** 1 tập dữ liệu để vừa tinh chỉnh prompt vừa đánh giá cuối — sẽ bị "học tủ", số đẹp giả tạo. Tách rõ:
  - 1 tập nhỏ để thử prompt lúc dev.
  - 1 tập **giữ kín**, chỉ chạy 1 lần cuối để báo cáo.
- **Chủ động làm lệch tỷ lệ **: nếu lấy mẫu ngẫu nhiên, red-flag sẽ cực hiếm (đúng thực tế), khiến Recall tính ra vô nghĩa vì mẫu số quá nhỏ. Cần cố tình đưa đủ số lượng ca red-flag thật vào tập test (ví dụ 20–30 ca).

---

## 3. Quy trình chạy đánh giá

```
Tập test (có nhãn đúng, giữ kín)
        ↓
Chạy pipeline AI Agent thật, hàng loạt (không phải tay từng ca)
        ↓
So sánh output AI vs nhãn đúng
        ↓
Tính bảng metric (mục 1) + Confusion Matrix cho Category và Severity
        ↓
Lưu kết quả kèm rule_version_id/model_version (đúng field đã thiết kế trong analysis_runs)
        ↓
Chạy lại mỗi khi đổi prompt/model → so sánh có cải thiện không
```

Việc lưu kèm version (bước áp chót) quan trọng đã có sẵn field này — nên tận dụng để chứng minh được đã cải thiện qua từng vòng, đúng yêu cầu deliverable "Evaluation Evidence".

---

## 4. Bảng Ngưỡng chấp nhận (Success Threshold)

| Field                                        | Ngưỡng đề xuất                                                             | Lý do                                                                                           |
| -------------------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Category (F1 trung bình)                     | ≥ 85–90%                                                                   | Khớp đúng mục tiêu đã ghi trong ĐẶC TẢ #2                                                       |
| Red-flag Recall                              | Càng gần 100% càng tốt, chấp nhận Precision thấp hơn để đổi lấy Recall cao | An toàn tính mạng — thà báo nhầm còn hơn bỏ sót                                                 |
| Severity                                     | Accuracy ≥ 75%, không có ca nào lệch quá 1 bậc (Low↔High)                  | Sai 1 bậc chấp nhận được vì còn Ceiling/Density cứu; sai 2 bậc thì nguy hiểm                    |
| `is_relevant` Recall (lớp "không liên quan") | ≥ 95%                                                                      | Case thật cVIGIL (Ấn Độ) cho thấy ảnh rác có thể chiếm tới 60% lượt gửi nếu không kiểm soát tốt |

---

## 5. Công cụ sử dụng

**Nên dùng:** công cụ chuẩn ML classification — `scikit-learn` (`classification_report`, `confusion_matrix`)

---
