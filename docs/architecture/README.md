# Kiến trúc hệ thống

- **Trạng thái:** Hiện hành
- **Kiểm chứng lần cuối:** 2026-09-01
- **Kiểu kiến trúc:** Ứng dụng web ba tầng với công việc bất đồng bộ được lưu bền vững trong cơ sở dữ liệu

## 1. Bối cảnh hệ thống

FixIt Agent phục vụ ba vai trò đã xác thực:

- **Cư dân:** báo cáo sự cố, cung cấp bằng chứng, trả lời câu hỏi làm rõ và theo dõi tiến độ.
- **Điều phối viên:** xem xét ngoại lệ, quản lý danh mục và tài khoản, phân công công việc, giám sát điều phối và xem báo cáo.
- **Kỹ thuật viên:** quản lý trạng thái sẵn sàng và thực hiện công việc được giao.

```mermaid
flowchart LR
    Resident[Cư dân]
    Coordinator[Điều phối viên]
    Technician[Kỹ thuật viên]

    Web[Ứng dụng web Next.js]
    API[Backend FastAPI]
    Worker[Worker phân công]
    DB[(PostgreSQL)]
    Auth[Supabase Auth]
    Storage[Supabase Storage]
    LLM[Nhà cung cấp LLM]
    Trace[Braintrust / Trace local]

    Resident --> Web
    Coordinator --> Web
    Technician --> Web
    Web -->|HTTPS / REST| API
    Web -->|Đăng nhập| Auth
    API -->|Xác minh danh tính| Auth
    API -->|Đọc / ghi| DB
    API -->|Tải lên bằng URL ký số| Storage
    API -->|Agent phân tích| LLM
    Worker -->|Nhận công việc bền vững| DB
    Worker -->|Agent phân công có rủi ro| LLM
    API -. dữ liệu quan sát .-> Trace
    Worker -. dữ liệu quan sát .-> Trace
```

## 2. Các container khi chạy

```mermaid
flowchart TB
    subgraph Browser[Trình duyệt]
        FE[Next.js 15 + React 19]
    end

    subgraph Application[Ứng dụng]
        BE[API FastAPI]
        AW[Worker điều phối]
    end

    subgraph BackendComponents[Thành phần Backend]
        Routes[Route API]
        Services[Dịch vụ ứng dụng]
        Agent[Agent phân tích LangGraph]
        Dispatch[Bộ lập lịch điều phối + Agent xử lý rủi ro]
        Repositories[Repository]
        Domain[Quy tắc miền nghiệp vụ]
    end

    subgraph ManagedServices[Dịch vụ được quản lý]
        PG[(Supabase PostgreSQL)]
        SA[Supabase Auth]
        SS[Supabase Storage]
        Model[Mô hình tương thích OpenAI]
    end

    FE --> BE
    BE --> Routes --> Services
    Services --> Agent
    Services --> Domain
    Services --> Repositories --> PG
    Agent --> Model
    BE --> SA
    BE --> SS
    AW --> Dispatch
    Dispatch --> Domain
    Dispatch --> Repositories
    Dispatch --> Model
```

## 3. Trách nhiệm của các thành phần

| Thành phần | Trách nhiệm | Khu vực triển khai chính |
| --- | --- | --- |
| Ứng dụng Next.js | Màn hình theo vai trò, tích hợp API, trạng thái xác thực và hành vi PWA cho cư dân. | `frontend/app`, `frontend/components`, `frontend/lib` |
| Tuyến FastAPI | Hợp đồng HTTP, thành phần phụ thuộc phục vụ xác thực, kiểm tra dữ liệu và cấu trúc phản hồi. | `src/api` |
| Dịch vụ ứng dụng | Ranh giới giao dịch và điều phối ca sử dụng. | `src/services` |
| Agent phân tích | Phân loại đa phương thức, làm rõ thông tin, suy luận trùng lặp và bằng chứng rủi ro có cấu trúc. | `src/agents` |
| Quy tắc miền nghiệp vụ | Tính điểm xác định, điều kiện vòng đời, đồng hồ SLA và chuyển đổi phân công. | `src/domain` |
| Hệ thống điều phối | Hàng đợi sự kiện bền vững, mô phỏng năng lực, bộ lập lịch và quyết định xếp việc có rủi ro. | `src/dispatch`, `src/workers` |
| Lớp truy cập dữ liệu | Truy cập cơ sở dữ liệu sau ranh giới giao dịch của lớp dịch vụ. | `src/repositories` |
| Lưu trữ bền vững | Các bảng PostgreSQL và lược đồ do Alembic kiểm soát. | `src/database`, `alembic/versions` |
| Khả năng quan sát | ID yêu cầu, bản ghi truy vết JSONL của Agent và span Braintrust tùy chọn. | `src/observability`, `src/agents/trace.py` |

## 4. Ranh giới tin cậy

Backend là nguồn có thẩm quyền duy nhất đối với trạng thái nghiệp vụ.

- Trình duyệt không bao giờ ghi trực tiếp vào các dòng trong cơ sở dữ liệu.
- Token Supabase xác lập danh tính; kiểm tra vai trò tại Backend xác lập quyền truy cập.
- Agent phân tích đề xuất các dữ kiện có cấu trúc và điểm tiêu chí. Agent không thể trực tiếp đặt mức ưu tiên cuối cùng hoặc trạng thái ticket.
- Điểm rủi ro và mức ưu tiên cuối cùng được mã Backend xác định bằng quy tắc.
- Ứng viên phân công được lọc theo các ràng buộc cứng trước khi bất kỳ mô hình nào được chọn giữa các ứng viên.
- Mọi thao tác thay đổi trạng thái đều được xác thực lại bên trong giao dịch cơ sở dữ liệu.
- Quyền truy cập Storage dùng URL ký số có thời hạn ngắn; đường dẫn đối tượng và phiên tải lên được xác thực trước khi bản ghi tệp đính kèm hiển thị.

## 5. Các luồng dữ liệu chính

### Luồng sự cố của cư dân

```mermaid
sequenceDiagram
    actor R as Cư dân
    participant W as Web
    participant A as FastAPI
    participant D as PostgreSQL
    participant G as Agent phân tích
    participant M as LLM

    R->>W: Gửi mô tả và hình ảnh tùy chọn
    W->>A: POST /api/v1/tickets
    A->>D: Tạo ticket và phiên phân tích
    A-->>W: Chấp nhận ticket
    A->>G: Chạy phân tích trong nền
    G->>M: Phân loại đa phương thức có cấu trúc
    G->>D: Tìm ứng viên và lưu lệnh gọi công cụ
    G->>A: Trả kết quả phân tích có cấu trúc
    A->>D: Xác thực, tính điểm, hoàn tất và ghi kiểm toán
```

### Luồng phân công

```mermaid
sequenceDiagram
    participant A as FastAPI
    participant D as PostgreSQL
    participant W as Worker phân công
    participant S as Bộ lập lịch
    participant M as Agent xử lý rủi ro

    A->>D: Phê duyệt ticket và xếp sự kiện điều phối vào hàng đợi
    W->>D: Nhận một micro-batch đang chờ
    W->>S: Tải kỹ thuật viên, hàng đợi và thời hạn
    alt Có phương án xếp việc an toàn
        S-->>W: Phương án tốt nhất theo quy tắc xác định
    else Mọi phương án đều có rủi ro lịch trình
        W->>M: Chọn trong các phương án đủ điều kiện
        M-->>W: Quyết định có cấu trúc
    end
    W->>D: Kiểm tra lại ràng buộc và tạo phân công
```

## 6. Tài liệu kiến trúc

- [Luồng Agent phân tích](agent-flow.md)
- [Mô hình dữ liệu](data-model.md)
- [Kiến trúc triển khai](deployment.md)
- [Thiết kế API](../specification-and-design/api-design.md)
- [Thiết kế phân công](../specification-and-design/assignment-design.md)
