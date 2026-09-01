# Tóm tắt sản phẩm

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01

## Vấn đề

Sự cố căn hộ được gửi qua nhiều kênh rời rạc với bằng chứng thiếu nhất quán. Ban quản lý tòa nhà phải tự diễn giải báo cáo, xác định tình huống khẩn cấp, phát hiện nhiều báo cáo về cùng một sự cố, chọn kỹ thuật viên đủ năng lực và cập nhật thông tin cho cư dân. Hệ quả là phân loại ban đầu chậm, trách nhiệm không rõ ràng và khả năng kiểm toán yếu.

## Sản phẩm

FixIt Agent là ứng dụng quản lý sự cố theo vai trò dành cho các tòa nhà căn hộ. Cư dân gửi mô tả, vị trí và hình ảnh tùy chọn. Agent AI có trạng thái trích xuất bằng chứng có cấu trúc, đặt câu hỏi làm rõ tập trung khi cần và đề xuất đầu vào về rủi ro. Backend xác thực các đầu vào đó, tính mức ưu tiên và kiểm soát mọi chuyển đổi trong quy trình. Điều phối viên quản lý ngoại lệ và phân công; kỹ thuật viên thực hiện công việc và cung cấp bằng chứng hoàn thành.

## Người dùng

### Cư dân

Cần trải nghiệm báo cáo nhanh, câu hỏi rõ ràng, quyền riêng tư trong quá trình phân tích và cập nhật tiến độ đáng tin cậy mà không phải gọi cho ban quản lý tòa nhà.

### Điều phối viên

Cần góc nhìn vận hành đã sắp xếp theo ưu tiên, bằng chứng AI có thể giải thích, quyền kiểm soát rõ ràng đối với tình huống khẩn cấp và chưa rõ ràng, công cụ phân công an toàn và dấu vết kiểm toán.

### Kỹ thuật viên

Cần hàng đợi cá nhân rõ ràng, ngữ cảnh công việc, chuyển đổi trạng thái có kiểm soát và cách đơn giản để báo hoàn thành hoặc không thể xử lý phân công.

## Đề xuất giá trị

- **Tiếp nhận nhanh hơn:** báo cáo có cấu trúc và diễn giải có hỗ trợ AI giúp giảm trao đổi qua lại.
- **Phân loại an toàn hơn:** bằng chứng rủi ro cao được thể hiện rõ và không thể bị trung bình hóa một cách âm thầm.
- **Ưu tiên nhất quán:** cách tính xác định tại Backend thay thế quyết định khẩn cấp tùy ý.
- **Ít công việc trùng lặp hơn:** nhiều báo cáo có thể theo dõi một sự cố gốc duy nhất.
- **Kiểm soát vận hành:** phân công thủ công, trực quan và tự động dùng chung các ràng buộc cứng.
- **Khả năng kiểm toán:** phân tích, tính điểm, xem xét, điều phối và chuyển đổi vòng đời luôn có thể truy vết.

## Phạm vi sản phẩm

### Bao gồm

- Xác thực cư dân và liên kết căn hộ.
- Tạo sự cố bằng văn bản, vị trí và hình ảnh đính kèm.
- Phân loại AI, làm rõ và suy luận trùng lặp.
- Tính điểm rủi ro xác định và xem xét khẩn cấp.
- Điều phối viên xem xét, quản lý danh mục, tài khoản và báo cáo.
- Gom nhóm sự cố và liên kết trùng lặp.
- Phân công kỹ thuật viên thủ công, trực quan và tự động.
- Trạng thái sẵn sàng, hàng đợi, bắt đầu, từ chối, không thể xử lý và hoàn thành của kỹ thuật viên.
- Thông báo trong ứng dụng, kiểm soát quyền truy cập tệp đính kèm và log kiểm toán.
- Khả năng quan sát Agent trên máy cục bộ và từ xa, không chặn hành vi sản phẩm.

### Ràng buộc sản phẩm

- Backend có thẩm quyền đối với trạng thái, mức ưu tiên và phân công.
- Trường hợp khẩn cấp P5 do điều phối viên xử lý và không bao giờ đi vào điều phối tự động.
- Mô hình chỉ được chọn trong các ứng viên đủ điều kiện do Backend cung cấp.
- Nội dung riêng tư của cư dân không hiển thị cho thành viên khác trong căn hộ khi AI vẫn đang phân tích.
- Lỗi dữ liệu quan sát không được làm xử lý ticket thất bại.

## Tín hiệu thành công

| Phạm vi | Tín hiệu |
| --- | --- |
| Chất lượng tiếp nhận | Ít báo cáo cần làm rõ thủ công hơn sau khi Agent hoàn tất. |
| An toàn | Mọi blocker được hỗ trợ đều tạo ra ít nhất sàn ưu tiên đã cấu hình. |
| Vận hành | Ticket không khẩn cấp đã phê duyệt nhận được phân công hoặc có lý do chuyển cấp được ghi lại. |
| SLA | Có thể đo việc hoàn thành P1–P4 theo chính sách giờ phục vụ đang áp dụng. |
| Trải nghiệm cư dân | Cư dân xem được tiến độ hiện tại mà không truy cập báo cáo riêng tư của cư dân khác. |
| Kiểm toán | Có thể dựng lại mọi mức ưu tiên cuối cùng và nguồn phân công từ các bản ghi đã lưu. |

## Nguyên tắc sản phẩm

1. Có bằng chứng trước khi tự động hóa.
2. Đặt chính sách xác định bao quanh mô hình xác suất.
3. Con người có thẩm quyền tại các ranh giới an toàn và chưa rõ ràng.
4. Dùng trạng thái bền vững thay cho bộ nhớ tiến trình.
5. Mặc định bảo vệ quyền riêng tư trong quá trình phân tích.
