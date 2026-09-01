# Tài liệu yêu cầu sản phẩm

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Sản phẩm:** FixIt Agent

## 1. Mục tiêu

1. Cho phép cư dân gửi báo cáo sự cố có thể xử lý từ web hoặc thiết bị di động.
2. Tạo danh mục và đầu vào rủi ro có thể giải thích từ văn bản và hình ảnh.
3. Chuyển trường hợp chưa rõ ràng và khẩn cấp đến điều phối viên mà không làm mất bằng chứng.
4. Ngăn công việc kỹ thuật trùng lặp cho cùng một sự cố đang hoạt động.
5. Phân công công việc không khẩn cấp cho kỹ thuật viên đủ điều kiện với quyết định có thể kiểm toán.
6. Cung cấp cho mỗi vai trò một góc nhìn tập trung trên cùng một quy trình bền vững.

## 2. Vai trò và quyền

| Năng lực | Cư dân | Điều phối viên | Kỹ thuật viên |
| --- | :---: | :---: | :---: |
| Gửi ticket | Có | Không | Không |
| Trả lời câu hỏi của Agent | Chỉ người báo cáo | Không | Không |
| Xem ticket trong căn hộ | Theo phạm vi riêng tư | Mọi ticket đã công bố | Chỉ ticket được phân công |
| Giải quyết xem xét thủ công/khẩn cấp | Không | Có | Không |
| Quản lý danh mục và tài khoản | Không | Có | Không |
| Phân công hoặc phân công lại | Không | Có | Không |
| Quản lý trạng thái sẵn sàng của bản thân | Không | Chỉ đọc | Có |
| Bắt đầu hoặc hoàn thành công việc | Không | Không | Chỉ phân công của bản thân |
| Xem báo cáo vận hành và kiểm toán | Không | Có | Không |

## 3. Yêu cầu đối với cư dân

### R-01 Xác thực và liên kết căn hộ

- Cư dân xác thực bằng luồng OTP qua số điện thoại.
- Hồ sơ cư dân phải được liên kết với một căn hộ hợp lệ trước khi gửi ticket.
- Backend suy ra vai trò và quyền truy cập căn hộ từ hồ sơ đáng tin cậy, không dùng siêu dữ liệu mà máy khách có thể sửa.

### R-02 Tạo ticket

- Ticket yêu cầu mô tả không rỗng và vị trí hợp lệ.
- Cư dân có thể đính kèm các loại hình ảnh được hỗ trợ qua luồng tải lên bằng URL ký số.
- Thao tác tạo trả về ID ticket bền vững trước khi phân tích nền hoàn tất.
- Trạng thái nghiệp vụ ban đầu là `NEW`; quá trình phân loại bắt đầu độc lập.

### R-03 Làm rõ

- Agent có thể hỏi từng câu hỏi có thể thực hiện trong giới hạn số vòng và thời gian đã cấu hình.
- Chỉ người báo cáo được đọc và trả lời câu hỏi đang hoạt động.
- Câu trả lời hợp lệ tiếp tục phiên phân tích hiện có thay vì tạo ticket mới.
- Hết thời gian phản hồi có thể đóng báo cáo với trạng thái `INVALID` và lý do được ghi lại.

### R-04 Hiển thị và theo dõi

- Trong khi trạng thái phân loại là `PENDING` hoặc `PROCESSING`, chỉ người báo cáo được xem báo cáo.
- Sau khi phân loại hoàn tất, cư dân khác trong cùng căn hộ có thể xem ticket đã công bố.
- Cư dân xem bản tóm tắt an toàn cho ticket trùng lặp và không nhận văn bản riêng tư, hình ảnh hoặc danh tính của người báo cáo ticket gốc.
- Danh sách ticket phân biệt công việc đang hoạt động và đã kết thúc.

### R-05 Hành động của cư dân

- Người báo cáo chỉ có thể hủy ticket khi ticket vẫn còn đủ điều kiện hủy.
- Người báo cáo có thể bổ sung thông tin khi quy trình cho phép thêm bằng chứng.
- Cư dân nhận thông báo trong ứng dụng về các thay đổi tiến độ quan trọng.

## 4. Yêu cầu đối với điều phối viên

### C-01 Quản lý ticket vận hành

- Điều phối viên có thể lọc và kiểm tra mọi ticket đã công bố.
- Chi tiết ticket hiển thị bằng chứng phân loại, phân tích rủi ro, ứng viên trùng lặp, tệp đính kèm và lịch sử trạng thái.
- Điều phối viên có thể thử lại phân tích thất bại bằng một hành động rõ ràng.

### C-02 Ranh giới xem xét

- Ticket cần xem xét thủ công phải được giải quyết hoặc từ chối rõ ràng.
- Ứng viên P5 cần được xác nhận khẩn cấp hoặc hạ mức rõ ràng.
- Ticket P5 đã xác nhận tiếp tục nằm ngoài luồng điều phối kỹ thuật viên.
- Thao tác hạ mức tạo phiên bản đánh giá rủi ro có thể kiểm toán và có thể tiếp tục qua phân tích trùng lặp cùng quy trình thông thường.

### C-03 Quản trị phân loại

- Điều phối viên có thể xử lý trường hợp chưa chắc chắn trùng lặp, liên kết ticket với ticket gốc hợp lệ hoặc giữ ticket độc lập.
- Ghi đè phân loại cần có lý do và tạo bản ghi kiểm toán.
- Thay đổi danh mục được thực hiện qua API Backend và bảo toàn tính toàn vẹn tham chiếu.

### C-04 Phân công

- Điều phối viên có thể phân công thủ công một ticket.
- Điều phối viên có thể chuẩn bị và xác nhận phương án xếp việc qua bảng Phân công trực quan.
- Điều phối viên có thể bật hoặc tắt Phân công tự động.
- Hệ thống phải từ chối phân công vi phạm điều kiện đủ cứng hoặc bất biến về phân công đang hoạt động.

### C-05 Năng lực quản lý

- Điều phối viên có thể quản lý tài khoản cư dân và kỹ thuật viên theo các quy tắc vòng đời được hỗ trợ.
- Điều phối viên có thể xem kỹ thuật viên, kỹ năng, trạng thái sẵn sàng và khối lượng công việc hiện tại.
- Điều phối viên có thể đọc nhật ký kiểm toán, bản tóm tắt ticket, báo cáo SLA và báo cáo năng suất kỹ thuật viên.
- Điều phối viên có thể chạy mô phỏng năng lực mà không thay đổi trạng thái phân công trong môi trường vận hành.

## 5. Yêu cầu đối với kỹ thuật viên

### T-01 Trạng thái sẵn sàng và hàng đợi

- Kỹ thuật viên có thể xem và thay đổi trạng thái sẵn sàng cá nhân.
- Kỹ thuật viên chỉ xem phân công của bản thân và một hàng đợi có thứ tự.
- Dữ liệu hàng đợi xác định phân công tiếp theo có thể xử lý và ngữ cảnh thời hạn liên quan.

### T-02 Vòng đời công việc

- Công việc `ASSIGNED` có thể được bắt đầu, từ chối hoặc đánh dấu không thể xử lý.
- Bắt đầu một phân công chuyển cả phân công và ticket sang `IN_PROGRESS`.
- Mỗi kỹ thuật viên chỉ có một phân công `IN_PROGRESS` tại một thời điểm.
- Hoàn thành công việc yêu cầu ghi chú và tệp bằng chứng hoàn thành đã được xác thực.
- Thao tác hoàn thành chuyển phân công và ticket sang `COMPLETED` trong một giao dịch.

### T-03 Thất bại và phân công lại

- Từ chối cần có lý do và loại kỹ thuật viên đó khỏi lần tự động chọn lại cho cùng mục công việc.
- Không thể xử lý cần có lý do và kết thúc ticket với trạng thái `UNRESOLVABLE`, trừ khi điều phối viên thực hiện một hành động được hỗ trợ khác.
- Phân công lại vẫn giữ phân công đã kết thúc để kiểm toán.

## 6. Yêu cầu đối với AI và hệ thống

### S-01 Phân tích có cấu trúc

- Agent trả về danh mục, bằng chứng, tiêu chí, blocker, dữ kiện chưa biết và thông tin trùng lặp đã được xác thực theo lược đồ.
- Agent không thể trả về mức ưu tiên cuối cùng, điểm rủi ro cuối cùng hoặc trạng thái ticket.
- Bản chụp danh mục và vị trí được xác thực theo giao dịch đang hoàn tất.

### S-02 Rủi ro xác định

- Backend tính rủi ro từ năm tiêu chí số nguyên và trọng số đã cấu hình.
- Blocker có tên thiết lập sàn ưu tiên tối thiểu và không bao giờ làm giảm kết quả.
- Mỗi lần tính điểm tạo một phiên bản đánh giá bất biến.

### S-03 Điều phối bền vững

- Công việc tự động được lưu dưới dạng sự kiện điều phối trước khi xử lý.
- Khởi động lại tiến trình phân công không làm mất sự kiện đang chờ.
- Yêu cầu nhận việc đã hết hạn có thể được nhận lại; sự kiện kết thúc không thể được nhận lại.
- Agent xử lý rủi ro chỉ nhận các ứng viên đã được Backend lọc và có thời gian quyết định giới hạn.

### S-04 Kiểm toán và khả năng quan sát

- Thay đổi trạng thái quan trọng ghi lại tác nhân, nguồn và ngữ cảnh trước/sau.
- Phản hồi API chứa ID yêu cầu.
- Dữ liệu truy vết Agent được làm sạch và là tùy chọn; lỗi truy vết không thể thay đổi hành vi nghiệp vụ.

## 7. Yêu cầu phi chức năng

| Phạm vi | Yêu cầu |
| --- | --- |
| Bảo mật | Xác minh JWT, vai trò do Backend quản lý, truy cập Storage bằng URL ký số và phân quyền phía máy chủ trên mọi điểm cuối được bảo vệ. |
| Nhất quán | Hiệu ứng lên ticket, rủi ro, phân công, lịch sử trạng thái, kiểm toán và thông báo được xác nhận trong cùng giao dịch khi đại diện cho một hành động. |
| Tin cậy | Trạng thái hàng đợi bền vững, khôi phục yêu cầu nhận việc và phương án dự phòng xác định ngăn công việc bị mất âm thầm. |
| Riêng tư | Ticket trong giai đoạn phân tích riêng tư và bản tóm tắt trùng lặp không được làm lộ báo cáo của cư dân khác. |
| Khả năng giải thích | Bằng chứng rủi ro, nguyên nhân blocker, phần đóng góp vào điểm và nguồn phân công luôn hiển thị cho người vận hành được cấp quyền. |
| Khả năng bảo trì | Quy tắc nghiệp vụ nằm trong miền nghiệp vụ/lớp dịch vụ; tuyến API không trở thành nguồn chính sách thay thế. |
| Khả năng triển khai | Container cung cấp kiểm tra sức khỏe/mức sẵn sàng và từ chối lược đồ cơ sở dữ liệu không tương thích. |
| Khả năng tiếp cận | Quy trình theo vai trò phải dùng được trên màn hình hẹp và cung cấp trạng thái đang tải, thành công và lỗi rõ ràng. |
