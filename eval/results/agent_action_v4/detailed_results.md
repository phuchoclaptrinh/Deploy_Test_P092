# Kết quả đầu ra chi tiết — Agent v4 action-only

Bảng dưới đây hiển thị output thực tế, không chỉ PASS/FAIL. UUID được rút gọn còn 8 ký tự để đọc nhanh; payload đầy đủ nằm trong `results.jsonl` và `case_results.tsv`.

## A1 — Agent — Nội dung, ảnh và Category

| Case | Tên case | Expected | Actual output | Action model | Tool sequence | Tương tác Cư dân |
|---|---|---|---|---|---|---|
| `A1-001` | Rò nước rõ ràng, chỉ có text | exit=ANALYSIS_COMPLETE; severity=MEDIUM | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-002` | Chập điện có một lỗi chính tả | exit=ANALYSIS_COMPLETE; severity=HIGH | exit=ANALYSIS_COMPLETE; category=Chập điện; severity=HIGH; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-003` | Hai vấn đề trong cùng mô tả | exit=ANALYSIS_COMPLETE; severity=LOW | exit=ANALYSIS_COMPLETE; category=Điều hòa và thông gió,Chiếu sáng khu vực chung; severity=LOW; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-004` | Thiếu vị trí rò cụ thể có thể hỏi thêm | exit=ASK_RESIDENT | exit=AWAITING_RESIDENT; category=Rò rỉ nước; severity=null; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-005` | Mô tả quá mơ hồ không xác định Category | exit=INSUFFICIENT_INPUT | exit=INSUFFICIENT_INPUT; category=[]; severity=null; red_flag=False | — | — | — |
| `A1-006` | Thiếu chữ nhưng vẫn hiểu lỗi thang máy | exit=ANALYSIS_COMPLETE; severity=HIGH | exit=ANALYSIS_COMPLETE; category=Thang máy; severity=HIGH; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-007` | Nhiều lỗi câu chữ vẫn nhận diện mất điện | exit=ASK_RESIDENT; severity=MEDIUM | exit=AWAITING_RESIDENT; category=Mất điện cục bộ; severity=MEDIUM; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-008` | Giọng khẩn cấp nhưng sự cố nhẹ | exit=ANALYSIS_COMPLETE; severity=LOW | exit=ANALYSIS_COMPLETE; category=Chiếu sáng khu vực chung; severity=LOW; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-009` | Giọng bình thường nhưng kết cấu nghiêm trọng | exit=ANALYSIS_COMPLETE; severity=HIGH | exit=ANALYSIS_COMPLETE; category=Kết cấu công trình; severity=HIGH; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-010` | Hai Category do hai sự cố độc lập | exit=ANALYSIS_COMPLETE; severity=LOW | exit=ANALYSIS_COMPLETE; category=Khóa và cửa,Điều hòa và thông gió; severity=LOW; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A1-011` | Tiếng ồn thiếu thời điểm | exit=ASK_RESIDENT; severity=LOW | exit=AWAITING_RESIDENT; category=Tiếng ồn hàng xóm; severity=LOW; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-012` | Mùi lạ không rõ nguồn | exit=ASK_RESIDENT | exit=AWAITING_RESIDENT; category=Mùi và vệ sinh; severity=null; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-013` | Chuỗi ký tự không mang nghĩa | exit=INSUFFICIENT_INPUT | exit=INSUFFICIENT_INPUT; category=[]; severity=null; red_flag=False | — | — | — |
| `A1-033` | Nhiều lỗi chữ và nhiều Category vẫn hiểu | exit=ASK_RESIDENT; severity=MEDIUM | exit=AWAITING_RESIDENT; category=Rò rỉ nước,Điều hòa và thông gió; severity=MEDIUM; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-034` | Mô tả rõ nhưng thiếu mức độ | exit=ASK_RESIDENT | exit=AWAITING_RESIDENT; category=Thang máy; severity=null; red_flag=False | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-035` | Category không xác định dù câu chữ rõ | exit=ASK_RESIDENT | exit=AWAITING_RESIDENT; category=[]; severity=null; red_flag=False | ASK_RESIDENT | ask_resident | vòng 1=PENDING: Vui lòng mô tả rõ hơn biểu hiện và mức độ sự cố. |
| `A1-036` | Nhận diện Category gây rối an ninh nghiêm trọng | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Gây rối an ninh nghiêm trọng; severity=HIGH; red_flag=True | — | — | — |

## A2 — Agent — Grouping và sự cố đang xử lý

| Case | Tên case | Expected | Actual output | Action model | Tool sequence | Tương tác Cư dân |
|---|---|---|---|---|---|---|
| `A2-001` | Rò nước trùng đúng sự cố đang xử lý | exit=DUPLICATE_EXISTING; duplicate=2f92c522 | exit=DUPLICATE_EXISTING; category=Rò rỉ nước; severity=MEDIUM; red_flag=False; duplicate=2f92c522 | — | search_related_tickets:DUPLICATE | — |
| `A2-002` | Rò nước cùng vị trí nhưng khác biểu hiện | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | SEARCH_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING | — |
| `A2-003` | Ứng viên thiếu location_id không được auto duplicate | exit=DUPLICATE_UNCERTAIN | exit=DUPLICATE_UNCERTAIN; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | — | search_related_tickets:DUPLICATE | — |
| `A2-004` | Gộp rò nước hai căn trong tầng liền kề | exit=ANALYSIS_COMPLETE; grouping=e78bc763 | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False; grouping=e78bc763 | SEARCH_GROUPING → PROPOSE_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING → propose_case_grouping | — |
| `A2-005` | Gộp rò nước bốn căn | exit=ANALYSIS_COMPLETE; grouping=a8d45ef0,a15979d2,a2a7c950 | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False; grouping=a8d45ef0,a15979d2,a2a7c950 | SEARCH_GROUPING → PROPOSE_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING → propose_case_grouping | — |
| `A2-006` | Rò nước quá ba ngày không được gộp | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | SEARCH_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING | — |
| `A2-007` | Rò nước một căn không tạo bằng chứng lan rộng | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | SEARCH_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING | — |
| `A2-008` | Chập điện trùng đúng tủ điện đang xử lý | exit=DUPLICATE_EXISTING; duplicate=5298d7e2 | exit=DUPLICATE_EXISTING; category=Chập điện; severity=MEDIUM; red_flag=False; duplicate=5298d7e2 | — | search_related_tickets:DUPLICATE | — |
| `A2-009` | Gộp chập điện ba căn liền kề | exit=ANALYSIS_COMPLETE; grouping=6222da47,5e80dbce | exit=ANALYSIS_COMPLETE; category=Chập điện; severity=MEDIUM; red_flag=False; grouping=6222da47,5e80dbce | SEARCH_GROUPING → PROPOSE_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING → propose_case_grouping | — |
| `A2-010` | Gộp chập điện bốn căn | exit=ANALYSIS_COMPLETE; grouping=424ba76f,71bf68d6,8d9ed13c | exit=ANALYSIS_COMPLETE; category=Chập điện; severity=MEDIUM; red_flag=False; grouping=424ba76f,71bf68d6,8d9ed13c | SEARCH_GROUPING → PROPOSE_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING → propose_case_grouping | — |
| `A2-011` | Chập điện quá ba ngày không được gộp | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Chập điện; severity=MEDIUM; red_flag=False | SEARCH_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING | — |
| `A2-012` | Thấm tường không thuộc Category grouping | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Kết cấu công trình; severity=MEDIUM; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A2-013` | Thang máy không thuộc Category grouping | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Thang máy; severity=MEDIUM; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A2-014` | Search duplicate rỗng và grouping rỗng | exit=ANALYSIS_COMPLETE | exit=ANALYSIS_COMPLETE; category=Rò rỉ nước; severity=MEDIUM; red_flag=False | SEARCH_GROUPING → CONCLUDE | search_related_tickets:DUPLICATE → search_related_tickets:GROUPING | — |

## A3 — Agent — Red flag và tương tác Cư dân

| Case | Tên case | Expected | Actual output | Action model | Tool sequence | Tương tác Cư dân |
|---|---|---|---|---|---|---|
| `A3-001` | Khói trong mô tả | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Chập điện; severity=HIGH; red_flag=True | — | — | — |
| `A3-002` | Lửa thật trong mô tả | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Chập điện; severity=HIGH; red_flag=True | — | — | — |
| `A3-003` | Dây điện hở | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Chập điện; severity=HIGH; red_flag=True | — | — | — |
| `A3-004` | Nước tràn diện rộng | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Rò rỉ nước; severity=HIGH; red_flag=True | — | — | — |
| `A3-005` | Có người ngất | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Gây rối an ninh nghiêm trọng; severity=HIGH; red_flag=True | — | — | — |
| `A3-006` | Gây rối nghiêm trọng | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Khóa và cửa; severity=HIGH; red_flag=True | — | — | — |
| `A3-007` | Người mắc kẹt trong thang máy | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Thang máy; severity=HIGH; red_flag=True | — | — | — |
| `A3-015` | Câu trả lời bổ sung phát hiện khói | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Chập điện; severity=HIGH; red_flag=True | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=ANSWERED: Hiện có khói, lửa hoặc mùi khét không? → Có khói đen và mùi khét xuất hiện ngay lúc này. |
| `A3-016` | Câu trả lời bổ sung có người mắc kẹt | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Thang máy; severity=HIGH; red_flag=True | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=ANSWERED: Có người đang mắc kẹt trong thang máy không? → Có khói đen và mùi khét xuất hiện ngay lúc này. |
| `A3-018` | Red-flag ngay từ đầu dù sự cố cũ đang được xử lý | exit=RED_FLAG; severity=HIGH | exit=RED_FLAG; category=Chập điện; severity=HIGH; red_flag=True | — | — | — |
| `A3-019` | Làm rõ Category sau một lượt hỏi | exit=ANALYSIS_COMPLETE; severity=MEDIUM | exit=ANALYSIS_COMPLETE; category=Điều hòa và thông gió; severity=MEDIUM; red_flag=False | ASK_RESIDENT → CONCLUDE | ask_resident → search_related_tickets:DUPLICATE | vòng 1=ANSWERED: Thiết bị hoặc biểu hiện cụ thể là gì? → Đó là máy điều hòa phòng ngủ không làm lạnh. |
| `A3-020` | Làm rõ Category ở lượt ba | exit=ANALYSIS_COMPLETE; severity=LOW | exit=ANALYSIS_COMPLETE; category=Điều hòa và thông gió; severity=LOW; red_flag=False | ASK_RESIDENT → ASK_RESIDENT → ASK_RESIDENT | ask_resident → ask_resident → ask_resident → search_related_tickets:DUPLICATE | vòng 1=ANSWERED: Bạn thấy thiết bị nào bất thường? → Tôi chưa xác định được.; vòng 2=ANSWERED: Bạn nghe, ngửi hoặc nhìn thấy biểu hiện gì? → Tôi chưa xác định được.; vòng 3=ANSWERED: Vui lòng xác nhận thiết bị đang không hoạt động. → Đó là máy điều hòa phòng ngủ không làm lạnh. |
| `A3-021` | Trả lời vẫn không đủ thông tin | exit=INSUFFICIENT_INPUT | exit=INSUFFICIENT_INPUT; category=[]; severity=null; red_flag=False | ASK_RESIDENT → CONCLUDE | ask_resident | vòng 1=ANSWERED: Thiết bị hoặc biểu hiện cụ thể là gì? → Tôi cũng không biết thiết bị nào hay biểu hiện cụ thể. |
| `A3-022` | Không trả lời hết 300 giây | exit=INSUFFICIENT_INPUT | exit=INSUFFICIENT_INPUT; category=[]; severity=null; red_flag=False | ASK_RESIDENT | ask_resident | vòng 1=PENDING: Thiết bị hoặc biểu hiện cụ thể là gì? |
| `A3-023` | Không cần hỏi khi dữ liệu đầy đủ | exit=ANALYSIS_COMPLETE; severity=LOW | exit=ANALYSIS_COMPLETE; category=Khóa và cửa; severity=LOW; red_flag=False | CONCLUDE | search_related_tickets:DUPLICATE | — |
| `A3-026` | Red-flag xuất hiện sau khi đã có candidate duplicate | exit=RED_FLAG; severity=HIGH; red_relation=28c92074 | exit=RED_FLAG; category=Thang máy; severity=HIGH; red_flag=True; red_relation=28c92074 | ASK_RESIDENT | search_related_tickets:DUPLICATE → ask_resident | vòng 1=ANSWERED: Có người mắc kẹt bên trong không? → Có người đang mắc kẹt bên trong thang máy. |

## B1 — LLM — DIRECT

| Case | Tên case | Expected business output | Actual output | Fallback | Failure còn lại |
|---|---|---|---|---|---|
| `B1-001` | Phân việc lần đầu cho một ticket | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=7aab3d52→4ff17582(primary) | Không | — |
| `B1-002` | Nhiều đơn vị phải cập nhật tải dự kiến tuần tự | SELECTED:2 | business=SELECTED:2; models=primary:2; selected=35e9ee1b→13ef64a3(primary), 98af49e8→13ef64a3(primary) | Không | — |
| `B1-003` | Cụm đủ năm ticket được giao cho một người | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=14c4b2ba→667b35ac(primary) | Không | — |
| `B1-004` | Batch có cả ticket đơn và cụm sự cố | SELECTED:2 | business=SELECTED:2; models=primary:2; selected=5b8fcb83→444f0d78(primary), 38938e5e→444f0d78(primary) | Không | — |
| `B1-006` | Biên hợp lệ đúng hai mươi ticket | SELECTED:20 | business=SELECTED:20; models=primary:20; selected=9197f384→e23477e5(primary), 39b67988→e23477e5(primary), 8c8f43aa→e23477e5(primary), d341be9c→e23477e5(primary), 928901bf→e23477e5(primary), e21e9537→e23477e5(primary), 37962571→e23477e5(primary), 0316a601→e23477e5(primary), 6755eb3d→e23477e5(primary), 53578ba9→e23477e5(primary), 84ac6d59→e23477e5(primary), 71132dae→e23477e5(primary), f2de711e→e23477e5(primary), 1ac72115→e23477e5(primary), 581452bb→e23477e5(primary), 096a12a5→e23477e5(primary), 01b16854→e23477e5(primary), 3feb8a27→e23477e5(primary), d32f8374→e23477e5(primary), 0c0eb12d→e23477e5(primary) | Không | — |
| `B1-008` | Mô hình chính lỗi và fallback thành công | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=b89418b2→8bf0d2d3(fallback) | Có | — |
| `B1-009` | Primary thiếu một decision và fallback cục bộ | SELECTED:2 | business=SELECTED:2; models=fallback:1,primary:1; selected=9f9bca75→61b9f867(primary), 34fbd146→61b9f867(fallback) | Có | — |
| `B1-010` | Cả primary và fallback đều lỗi | MANUAL_REQUIRED:1 | business=MANUAL_REQUIRED:1; models=none; failures=FALLBACK_ENVELOPE_ERROR | Có | FALLBACK_ENVELOPE_ERROR: FALLBACK_TIMEOUT |
| `B1-011` | NO_SUITABLE_CANDIDATE là kết quả hợp lệ | MANUAL_REQUIRED:1 | business=MANUAL_REQUIRED:1; models=primary:1 | Không | — |
| `B1-012` | Phân lại do Kỹ thuật viên từ chối | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=6852491f→6d361bda(primary) | Không | — |
| `B1-013` | Phân lại do Kỹ thuật viên không nhận việc đúng hạn | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=c9f13f07→9f4cf84f(primary) | Không | — |
| `B1-014` | Model chọn người ngoài candidate snapshot | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=4458173f→c1479877(fallback) | Có | — |
| `B1-015` | Model trả sai work_item_id | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=be8cb237→06d84d14(fallback) | Có | — |
| `B1-016` | Model trả trùng decision_id | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=a0e8c752→8dd4506f(fallback) | Có | — |
| `B1-017` | Model trả lý do rỗng | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=88ed822d→272ab9f1(fallback) | Có | — |
| `B1-018` | Model trả lý do vượt 500 ký tự | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=3ef4bdb0→de739582(fallback) | Có | — |
| `B1-019` | Không làm theo câu lệnh chèn trong mô tả ticket | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=0ba32b5f→5d62fca6(primary) | Không | — |
| `B1-020` | Batch cuối trộn decision primary và fallback | SELECTED:2 | business=SELECTED:2; models=fallback:1,primary:1; selected=61ff1d53→31dce042(primary), 5de87d19→31dce042(fallback) | Có | — |

## B2 — LLM — PROPOSAL

| Case | Tên case | Expected business output | Actual output | Fallback | Failure còn lại |
|---|---|---|---|---|---|
| `B2-001` | Đề xuất cho một ticket trong hàng chờ | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=188a7a9a→91f2d979(primary) | Không | — |
| `B2-002` | Tải của cụm tính theo số ticket thành viên | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=f286c6bc→fb7f666d(primary) | Không | — |
| `B2-003` | Batch có cả ticket đơn và cụm sự cố | SELECTED:2 | business=SELECTED:2; models=primary:2; selected=540fdb19→ce153879(primary), f7f4d925→ce153879(primary) | Không | — |
| `B2-004` | Biên hợp lệ đúng hai mươi ticket | SELECTED:20 | business=SELECTED:20; models=primary:20; selected=a2eb82ac→681de8bf(primary), a370bcf7→681de8bf(primary), b65d4862→681de8bf(primary), 2efaf69d→681de8bf(primary), 142dbf7b→681de8bf(primary), 78205860→681de8bf(primary), de8346f9→681de8bf(primary), 7d2c9c42→681de8bf(primary), 1c3e92ef→681de8bf(primary), 61d82a27→681de8bf(primary), 79555908→681de8bf(primary), caeb045d→681de8bf(primary), ed2bdf98→681de8bf(primary), d8efd5c9→681de8bf(primary), 9985c87c→681de8bf(primary), 8a00feef→681de8bf(primary), a594c7d3→681de8bf(primary), f719d5c1→681de8bf(primary), 00923249→681de8bf(primary), b0237975→681de8bf(primary) | Không | — |
| `B2-007` | Mô hình chính lỗi và fallback thành công | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=b8abf075→70223509(fallback) | Có | — |
| `B2-008` | Primary thiếu một decision và fallback cục bộ | SELECTED:2 | business=SELECTED:2; models=fallback:1,primary:1; selected=dc62f612→7149313e(primary), 20d14288→7149313e(fallback) | Có | — |
| `B2-009` | Cả primary và fallback đều lỗi | EMPTY:1 | business=EMPTY:1; models=none; failures=FALLBACK_ENVELOPE_ERROR | Có | FALLBACK_ENVELOPE_ERROR: FALLBACK_TIMEOUT |
| `B2-010` | NO_SUITABLE_CANDIDATE là kết quả hợp lệ | EMPTY:1 | business=EMPTY:1; models=primary:1 | Không | — |
| `B2-011` | Nhiều đơn vị phải cập nhật tải dự kiến tuần tự | SELECTED:2 | business=SELECTED:2; models=primary:2; selected=91223d29→2f0c4475(primary), 163818fe→2f0c4475(primary) | Không | — |
| `B2-012` | Cụm đủ năm ticket được giao cho một người | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=4b3a28ef→8ba2e1b3(primary) | Không | — |
| `B2-013` | Model chọn người ngoài candidate snapshot | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=01f0e12e→6d628689(fallback) | Có | — |
| `B2-014` | Model trả sai work_item_id | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=7be56734→785f27ae(fallback) | Có | — |
| `B2-015` | Envelope chứa decision không thuộc request | SELECTED:2 | business=SELECTED:2; models=fallback:2; selected=63de3f35→b7664fd8(fallback), cd1fb531→b7664fd8(fallback) | Có | — |
| `B2-016` | Model trả trùng decision_id | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=3574fa9e→741261b4(fallback) | Có | — |
| `B2-017` | Model trả lý do vượt 500 ký tự | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=61ea872a→4ed61274(fallback) | Có | — |
| `B2-018` | Không làm theo câu lệnh chèn trong mô tả ticket | SELECTED:1 | business=SELECTED:1; models=primary:1; selected=39104bf2→cbe81c2a(primary) | Không | — |
| `B2-019` | Batch cuối trộn decision primary và fallback | SELECTED:2 | business=SELECTED:2; models=fallback:1,primary:1; selected=5eb119df→9b75774b(primary), 262c039c→9b75774b(fallback) | Có | — |
| `B2-020` | Model trả decision_id lạ cho work item hợp lệ | SELECTED:1 | business=SELECTED:1; models=fallback:1; selected=b524ceb4→92116fd0(fallback) | Có | — |
