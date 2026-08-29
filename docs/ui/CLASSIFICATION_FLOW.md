# Luồng phân loại phản ánh

Tài liệu này mô tả **đúng luồng đang được triển khai** cho phân loại AI, hỏi cư dân, phát hiện phản ánh trùng và gom cụm. Mục tiêu là chỉ có một lần phân loại đa phương thức cho mỗi phiên bản bằng chứng, đồng thời giữ thời gian phản hồi cho cư dân ngắn.

## 1. Dữ liệu đầu vào cho phân loại

Mỗi lượt phân loại nhận một gói bằng chứng thống nhất, gồm:

- Mô tả do cư dân viết.
- Các ảnh đính kèm có URL truy cập được.
- `location_id`, nhãn vị trí, loại vị trí, tầng và căn hộ (nếu có).
- Danh mục đang hoạt động, được ghim theo phiên AI.
- Toàn bộ câu hỏi và câu trả lời trước đó của cư dân trong phiên.
- Category đã được cư dân xác nhận, nếu có.

Agent đọc mô tả, ảnh, vị trí và lịch sử hỏi đáp trong **một lời gọi phân loại đa phương thức**. Không tồn tại hai luồng phân loại độc lập chỉ dùng chữ hoặc chỉ dùng ảnh. Các nhận định từ chữ và ảnh, nếu có, chỉ là bằng chứng giải thích cho một `category_id` chung.

Nếu một ảnh không tạo được URL truy cập, hệ thống ghi nhận lỗi và vẫn có thể chạy phân loại với phần bằng chứng còn lại. Đây là hành vi chịu lỗi hiện tại; ảnh không được tự thay thế bằng dữ liệu khác.

## 2. Kết quả phân loại

Kết quả phân loại thống nhất có thể gồm:

- `category_id` hợp lệ trong catalog đã ghim.
- Mức độ nghiêm trọng và nguồn xác định mức độ.
- Dấu hiệu nguy hiểm (`red_flag`), nếu có.
- Lý do AI và các bằng chứng liên quan.
- Câu hỏi cần gửi cư dân, nếu cần thêm dữ kiện.
- Kết luận duplicate, lý do và `master_ticket_id`, nếu đã đánh giá duplicate.

Câu hỏi/câu trả lời được lưu trong phiên và bản ghi câu hỏi riêng; chúng không được đóng gói lại thành một trường của kết quả cuối cùng. Đề xuất grouping cũng không nằm trong kết quả foreground này, vì được đánh giá ở bước nền riêng.

## 3. Luồng phân loại và hỏi cư dân

```text
Cư dân gửi mô tả + ảnh + vị trí
        ↓
Một lượt phân loại đa phương thức
        ├─ Thiếu dữ kiện hợp lệ → hỏi cư dân
        │        ↓
        │  Lưu câu trả lời vào cùng phiên → phân loại lại trên toàn bộ bằng chứng
        │
        ├─ P3 / có dấu hiệu nguy hiểm → chuyển BQL xử lý tay
        │
        └─ Đủ dữ kiện → tìm candidate duplicate → đánh giá duplicate
                 ↓
          Chốt kết quả foreground và thông báo cư dân
                 ↓
          Bước nền: tìm và đánh giá grouping (nếu phù hợp)
```

Agent chỉ được hỏi để làm rõ:

- Category.
- Mức độ nghiêm trọng.
- Vị trí thực tế khi có mâu thuẫn đáng kể với sự cố.

Category sau khi đã được cư dân xác nhận được backend giữ cố định trong các lượt phân loại sau. Prompt cũng yêu cầu không lặp lại câu hỏi severity/location khi cư dân đã trả lời đủ; hiện tại đây chưa phải là điều kiện chặn cứng ở backend cho hai loại câu hỏi này.

Ngoại lệ: nếu Agent kết luận ticket trùng với ticket vừa hoàn thành trong vòng một giờ, hệ thống hỏi thêm cư dân liệu sự cố có tái diễn hay không trước khi liên kết. Mục đích là tránh gộp nhầm một sự cố mới vào ticket vừa đóng.

## 4. Cập nhật bằng chứng sau khi cư dân trả lời

Sau mỗi câu trả lời, hệ thống đánh giá phiên bản bằng chứng theo ba thay đổi nghiệp vụ:

- Category.
- `location_id`.
- Bản chất sự cố (`incident_facts`).

Nếu một trong ba giá trị thay đổi, backend tăng phiên bản bằng chứng và tìm lại candidate duplicate. Nếu không thay đổi, candidate duplicate đã có được tái sử dụng; không tìm lại không cần thiết.

Ảnh bổ sung từ câu hỏi kiểu `NEW_PHOTO` được thêm vào gói ảnh và toàn bộ bằng chứng lại được phân loại cùng nhau.

Grouping không sử dụng cơ chế tái sử dụng candidate foreground này, vì grouping là bước nền chạy độc lập sau khi kết quả phân loại/duplicate đã ổn định.

## 5. Cơ chế duplicate

### 5.1 Backend chuẩn bị candidate

Backend chỉ đưa candidate duplicate khi ticket hiện tại đã có cả category và `location_id`. Candidate phải thỏa tất cả điều kiện sau:

- Không phải ticket hiện tại.
- Cùng chính xác `location_id`.
- Cùng chính xác category.
- Có trạng thái đang xử lý, hoặc đã hoàn thành không quá một giờ.
- Có dữ liệu tóm tắt tối thiểu để Agent đánh giá.

Candidate được chuẩn hóa về ticket gốc/canonical ticket và giới hạn số lượng. Payload gửi Agent chỉ là dữ liệu đã rút gọn, không cung cấp tùy ý nội dung nhạy cảm.

Hai vị trí cùng tên nhưng khác `location_id` luôn là hai vị trí khác nhau. Ví dụ `Bếp · Căn 301` không thể tự coi là cùng sự cố với `Bếp · Căn 302`; tương tự cho thang máy ở các tầng khác nhau.

### 5.2 Agent đánh giá candidate

Khi có candidate, Agent chỉ có thể kết luận:

- `SAME_INCIDENT` — cùng sự cố.
- `DIFFERENT_INCIDENT` — khác sự cố.
- `UNCERTAIN` — chưa chắc.

Với `SAME_INCIDENT`, `master_ticket_id` bắt buộc thuộc chính tập candidate mà backend đã đưa. Nếu Agent trả về một ID ngoài tập đó, hệ thống không tự sửa sang ticket khác mà hạ kết quả thành `UNCERTAIN` để BQL xem xét.

`UNCERTAIN` chuyển ticket sang hàng BQL, kèm category, lý do và candidate snapshot để kiểm tra thủ công.

## 6. Cơ chế grouping: bước nền tách riêng

Grouping không được gọi trong cùng lượt phân loại/duplicate. Sau khi foreground đã kết thúc, worker nền mới:

1. Tìm các ticket có thể liên quan theo quy tắc grouping.
2. Gọi Agent đánh giá quan hệ grouping riêng.
3. Lưu đề xuất hoặc kết luận grouping.

Việc tách này là chủ đích để không kéo dài thời gian phản hồi của luồng cư dân. Hệ quả là UI/board phân việc phải coi trạng thái grouping là chưa hoàn tất cho đến khi worker nền xử lý xong; không được phân công ticket trước khi bước grouping bắt buộc đã hoàn thành.

Candidate grouping khác duplicate: nó dựa vào category có hỗ trợ grouping, vùng tầng lân cận và cửa sổ thời gian; không dùng điều kiện cùng chính xác `location_id` như duplicate.

## 7. Ranh giới trách nhiệm

- **Agent foreground:** phân loại, hỏi cư dân, đánh giá duplicate từ candidate do backend cung cấp.
- **Backend:** xây dựng evidence package, quản lý phiên/câu hỏi, kiểm tra thay đổi bằng chứng, tìm candidate, xác thực ID master và điều hướng BQL.
- **Worker grouping:** tìm candidate grouping và thực hiện đánh giá grouping bất đồng bộ.
- **BQL:** xử lý ticket P3, duplicate chưa chắc và các trường hợp cần quyết định thủ công.

## 8. Các điểm cần tiếp tục cải tiến nếu muốn siết chặt tài liệu

- Chặn ở backend việc hỏi lặp severity hoặc location sau khi cư dân đã trả lời đủ, thay vì chỉ dựa vào prompt.
- Quy định rõ hành vi khi không thể lấy ảnh: retry/chuyển BQL hay cho phép phân loại với bằng chứng thiếu.
- Nếu muốn grouping là một phần của kết quả thống nhất, cần đưa grouping candidate và đánh giá grouping vào foreground. Điều đó sẽ đổi kiến trúc hiện tại và có thể tăng latency cho cư dân.
