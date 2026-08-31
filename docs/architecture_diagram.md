# Architecture Diagram — FixIt Agent (P-092)

> Hệ thống tiếp nhận, phân loại và ưu tiên phản ánh sự cố trong chung cư, với một AI Agent (LangGraph) làm bước phân loại tự động giữa Cư dân và Ban quản lý.
>
> **Đã lỗi thời — giữ lại làm tài liệu lịch sử.** Sơ đồ dưới đây mô tả kiến
> trúc `v3` với hai bước trích xuất text/ảnh riêng, `red_flag`, `severity` và
> công thức `base_score + location_bonus + density + severity`. Không phần nào
> trong số đó còn tồn tại: pipeline đã gộp thành một lượt phân loại đa phương
> thức, và rubric rủi ro v2 đã thay toàn bộ công thức chấm điểm.
>
> Nguồn đúng cho từng phần:
>
> - Chấm điểm và mức ưu tiên: [`docs/risk_scoring_v2.md`](risk_scoring_v2.md)
> - Luồng phân loại: [`docs/ui/CLASSIFICATION_FLOW.md`](ui/CLASSIFICATION_FLOW.md)
> - Vận hành và migration: [`docs/operations.md`](operations.md)
>
> Viết lại sơ đồ này là việc riêng, không gộp vào đợt đổi rubric — một sơ đồ sai
> một nửa khó đọc hơn một sơ đồ nói rõ nó đã cũ.

---

## 1. Tổng quan hệ thống

FixIt Agent là hệ thống 3 tầng với một AI Agent chạy nền:

- **Frontend** — Next.js 15 (App Router) + React 19, ba khu vực theo vai trò (`/resident`, `/manager`, `/technician`), riêng khu Cư dân là PWA (service worker + webmanifest).
- **Backend** — FastAPI (đồng bộ, SQLAlchemy 2.0), phân tầng `routes → services → repositories → models`, trả về envelope thống nhất `{data, error, meta, request_id}`.
- **AI Agent** — LangGraph state machine chạy trong tiến trình backend qua `BackgroundTasks`, có khả năng **tạm dừng giữa chừng để hỏi lại cư dân** rồi tiếp tục ở request sau.
- **Hạ tầng ngoài** — Supabase (Auth + PostgreSQL + Storage) và OpenAI (vision + structured output).

Ba đặc điểm kiến trúc quan trọng nhất:

| Đặc điểm | Ý nghĩa |
|---|---|
| **Backend là ranh giới tin cậy** | Agent chỉ *đề xuất*; Backend xác thực mọi tool call, giữ ngân sách, và tự tính điểm/ưu tiên trong `finalize()`. Agent không được ghi thẳng vào ticket. |
| **Human-in-the-loop thật sự** | `interrupt()` của LangGraph dừng graph tại `tool_ask_wait`; state được checkpoint và chỉ chạy tiếp khi cư dân trả lời qua một HTTP request khác (có thể vài phút sau). |
| **Không có Vector Store / RAG** | Bài toán là phân loại có cấu trúc trên một catalog Category động, không phải hỏi–đáp tài liệu. Ngữ cảnh lấy từ chính DB nghiệp vụ (tool `search_related_tickets`), không từ embedding. |

---

## 2. Sơ đồ components — mức hệ thống

```mermaid
graph TB
    subgraph Actors["Người dùng"]
        R([Cư dân<br/>phone + OTP])
        C([Điều phối viên BQL<br/>email + password])
        T([Kỹ thuật viên<br/>hồ sơ backend cấp])
    end

    subgraph FE["Frontend — Next.js 15 / React 19"]
        RES["/resident<br/>PWA + service worker"]
        MGR["/manager<br/>dashboard, P0, cụm, báo cáo"]
        TEC["/technician<br/>assignment workflow"]
        CLI["api/client.ts<br/>envelope, GET cache 5s, dedup, timeout"]
    end

    subgraph BE["Backend — FastAPI"]
        MW["Middleware<br/>x-request-id · CORS · error handlers"]
        RT["API Routes<br/>/api/v1/*"]
        DEP["Dependencies<br/>auth · roles · db session"]
        SVC["Services<br/>nghiệp vụ + chính sách"]
        REPO["Repositories<br/>truy vấn + khoá hàng"]
        AG["LangGraph Agent<br/>src/agents"]
        TR["Agent Tracer<br/>JSONL .ai-log/agent"]
    end

    subgraph EXT["Dịch vụ ngoài"]
        SBA["Supabase Auth<br/>JWT / JWKS"]
        SBD[("Supabase PostgreSQL")]
        SBS["Supabase Storage<br/>bucket ticket-attachments"]
        LLM["OpenAI Chat<br/>vision + structured output"]
    end

    R --> RES
    C --> MGR
    T --> TEC
    RES & MGR & TEC --> CLI
    CLI -->|"REST + Bearer JWT"| MW
    RES -.->|"đăng nhập trực tiếp"| SBA
    CLI -.->|"PUT ảnh qua signed URL"| SBS

    MW --> RT --> DEP --> SVC --> REPO --> SBD
    RT -->|"BackgroundTasks"| AG
    AG -->|"tool calls qua AgentBackendService"| SVC
    AG --> LLM
    AG --> TR
    DEP -->|"verify token"| SBA
    SVC -->|"signed URL"| SBS
```

### Vai trò từng component

| Component | Vị trí | Trách nhiệm |
|---|---|---|
| API Routes | [src/api/routes/](../src/api/routes/) | Nhận request, validate Pydantic, gọi service, gói envelope. Không chứa logic nghiệp vụ. |
| Auth dependency | [auth.py](../src/api/dependencies/auth.py) | Xác thực JWT Supabase → phân giải `CurrentActor` (resident/coordinator/technician) từ `user_profiles`. |
| Services | [src/services/](../src/services/) | Toàn bộ chính sách nghiệp vụ: ticket, phân công, điều phối, chấm điểm, storage, và 4 service Agent. |
| Repositories | [src/repositories/](../src/repositories/) | Truy vấn có kiểm soát, `SELECT ... FOR UPDATE`, phân trang. |
| Domain transitions | [src/domain/](../src/domain/) | Bảng chuyển trạng thái hợp lệ của ticket và assignment. |
| Agent | [src/agents/](../src/agents/) | Graph, nodes, state, LLM client, tracing. |
| Scoring | [scoring_service.py](../src/services/scoring_service.py) | Công thức điểm minh bạch → Priority + SLA. Không dùng LLM. |

### Kiến trúc bên trong Backend

```mermaid
graph LR
    subgraph API["Tầng API"]
        A1[auth]
        A3[storage]
        A4[tickets]
        A6[coordinator]
        A7[technician_assignments]
    end

    subgraph SERVICES["Tầng Service"]
        S1[TicketService]
        S2[CoordinatorService]
        S3[AssignmentService]
        S4[StorageService]
        S5[ScoringService]
        S6[AgentBackendService]
    end

    subgraph AGENTSVC["AgentBackendService = 4 mixin"]
        M1[AgentSessionService<br/>session + catalog snapshot]
        M2[AgentToolService<br/>search · propose_grouping]
        M3[AgentQuestionService<br/>ask · answer · timeout]
        M4[AgentResultService<br/>finalize · scoring · grouping]
    end

    subgraph DATA["Tầng dữ liệu"]
        RP[Repositories]
        OR[SQLAlchemy models]
        DB[(PostgreSQL)]
    end

    A4 --> S1 --> RP
    A6 --> S2 --> RP
    A7 --> S3 --> RP
    A3 --> S4
    S2 & S3 & S6 --> S5
    A4 --> S6
    S6 --- M1 & M2 & M3 & M4
    M1 & M2 & M3 & M4 --> RP
    RP --> OR --> DB
```

`AgentBackendService` là **façade** ghép 4 mixin ([agent_backend_service.py](../src/services/agent_backend_service.py)) trên nền `AgentServiceBase` — nơi đặt hằng số ngân sách, helper khoá session, ghi audit và thông báo.

---

## 3. AI Agent — LangGraph

### 3.1 State

`AgentState` ([state.py](../src/agents/state.py)) là `TypedDict` **chỉ chứa kiểu JSON nguyên thuỷ** — không ORM, không `Session` — để checkpoint được qua khoảng dừng chờ cư dân trả lời.

| Nhóm | Trường |
|---|---|
| Input cố định | `ticket_id`, `session_id`, `description`, `floor_label`, `location_label`, `image_paths`, `image_urls`, `model_version` |
| Catalog ghim | `catalog`, `catalog_version` |
| Kết quả trích xuất  | `text_categories`, `image_categories`, `red_flag_text`, `red_flag_signal`, `text_understandable`, `is_relevant`, `severity`, `severity_source`, `is_confident`, `confidence_notes` |
| Vòng lặp  | `related_tickets`, `grouping`, `iterations`, `pending_question_id`, `answer_notes`, `reextraction` |
| Quyết định bước  | `next_action`, `action_reason`, `action_grouping_ticket_ids`, `action_question_text`, `action_question_options`, `action_allow_free_text`, `ask_prepare_failed` |
| Kết cục | `exit_reason` |

### 3.2 Sơ đồ graph

```mermaid
graph TD
    START((START)) --> EX[extract<br/>LLM: text + ảnh → Category, red-flag, severity]

    EX --> R1{route_after_extract}
    R1 -->|"ảnh không liên quan<br/>hoặc text quá sơ sài, không ảnh"| EI[exit_insufficient]
    R1 -->|"red_flag_text hoặc red_flag_signal"| ERF[exit_red_flag]
    R1 -->|hợp lệ| DA[decide_action<br/>LLM: chọn 1 action]

    DA --> R2{route_after_decision}
    R2 -->|SEARCH_RELATED| TS[tool_search]
    R2 -->|PROPOSE_GROUPING| TG[tool_group]
    R2 -->|ASK_RESIDENT| TAP[tool_ask_prepare<br/>ghi câu hỏi vào DB]
    R2 -->|CONCLUDE| R3{route_conclude}

    TS --> DA
    TG --> DA

    TAP --> R4{ask_prepare_failed?}
    R4 -->|"hết ngân sách"| R3
    R4 -->|ok| TAW["tool_ask_wait<br/>⏸ interrupt()"]
    TAW -->|"cư dân trả lời → resume"| TAF[tool_ask_finalize<br/>gộp câu trả lời / ảnh mới]
    TAF --> EX

    R3 -->|"is_confident = false"| EL[exit_limit]
    R3 -->|"giải ra đúng 1 Category"| EC[exit_confident]
    R3 -->|"0 hoặc nhiều Category"| EM[exit_mismatch]

    ERF & EI & EL & EM & EC --> FIN[["backend.finalize()"]] --> E((END))
```

Điểm cần chú ý khi đọc graph:

- **`extract` được chạy lại sau mỗi vòng hỏi** (`tool_ask_finalize → extract`). Khi `reextraction=True`, chốt chặn "thiếu thông tin" bị bỏ qua nhưng **red-flag vẫn kiểm tra lại ở mọi vòng**.
- **Tách `tool_ask_prepare` / `tool_ask_wait`** để lệnh ghi DB (tạo câu hỏi) không bị replay khi graph resume.
- `route_conclude` dùng **đúng luật của `AgentResultService._resolve_confident_category`** (giao của category từ text và từ ảnh phải ra đúng một phần tử), để `exit_reason` trong audit không mâu thuẫn với kết quả thật của ticket.

### 3.3 Hai lần gọi LLM

| Lệnh gọi | Prompt | Structured output | Vai trò |
|---|---|---|---|
| `extract` | `_EXTRACTION_SYSTEM_PROMPT` | `ExtractionResult` | Trích xuất Category (theo **display name** trong catalog ghim), red-flag riêng cho text và ảnh, severity, độ tự tin. Text và ảnh được đánh giá **độc lập** để bước sau phát hiện mâu thuẫn. |
| `decide_next_action` | `_DECISION_SYSTEM_PROMPT` | `ActionDecision` | Chọn đúng 1 trong `SEARCH_RELATED / PROPOSE_GROUPING / ASK_RESIDENT / CONCLUDE`; prompt nhận cả lịch sử hỏi–đáp để không hỏi lại câu đã được trả lời. |

Agent **không bao giờ thấy `code` nội bộ của Category**, chỉ thấy `display_name`; node code map ngược `display_name → category_id` sau khi LLM trả lời ([nodes.py:40](../src/agents/nodes.py#L40)).

### 3.4 Tools và ngân sách

| Tool | Backend kiểm gì | Kết quả |
|---|---|---|
| `search_related_tickets` | Category phải thuộc snapshot; chỉ ticket **cùng toà, tầng liền kề, ≤ 3 ngày**; tóm tắt an toàn, không lộ mô tả gốc | Danh sách ticket liên quan |
| `propose_case_grouping` | Chỉ `WATER_LEAK` / `ELECTRICAL_SHORT`; ticket phải đến từ chính kết quả search của session này; không tạo `IncidentCase` ở bước này | `accepted`, `density`, `rejected_reason` |
| `ask_resident` | Kiểu câu hỏi hợp lệ, trắc nghiệm phải có options, còn ngân sách | Bản ghi `ai_agent_questions` + deadline |

Ngân sách cứng ([agent_common.py:31](../src/services/agent_common.py#L31)) — **tất cả tool, kể cả `ask_resident`, dùng chung quota 5 lần**:

| Giới hạn | Giá trị |
|---|---|
| `MAX_TOOL_CALLS` | 5 |
| `MAX_ASK_ROUNDS` | 3 |
| `MAX_WAIT_SECONDS` | 300s (tổng thời gian chờ cư dân) |
| `MAX_LOOP_ITERATIONS` | 12 (chặn vòng lặp graph) |

---

## 4. Data flow

### 4.1 Luồng chính — Cư dân tạo phản ánh

```mermaid
sequenceDiagram
    autonumber
    actor R as Cư dân
    participant FE as Next.js
    participant ST as Supabase Storage
    participant API as FastAPI
    participant TS as TicketService
    participant BG as BackgroundTasks
    participant G as LangGraph
    participant LLM as OpenAI
    participant DB as PostgreSQL

    R->>FE: mô tả + ảnh + vị trí
    FE->>API: POST /storage/ticket-attachments/upload-url
    API-->>FE: signed upload URL + storage_path
    FE->>ST: PUT ảnh
    FE->>API: POST /tickets (upload_ids)
    API->>TS: create_ticket()
    TS->>ST: verify_uploaded_object (MIME, size)
    TS->>DB: INSERT ticket (NEW / PENDING) + attachments
    API-->>FE: 202 "Đang phân tích..."
    API->>BG: run_ticket_analysis(ticket_id)

    Note over BG,DB: từ đây chạy nền, request của cư dân đã trả về
    BG->>DB: start_session + ghim catalog snapshot (sha256)
    BG->>ST: signed download URL cho từng ảnh
    BG->>G: invoke(state, thread_id=session_id)
    G->>LLM: extract (text + ảnh)
    LLM-->>G: ExtractionResult
    G->>LLM: decide_next_action
    LLM-->>G: ActionDecision
    G->>DB: tool calls (đã kiểm ngân sách)
    G->>DB: finalize() → Category, Severity, Score, Priority, SLA
    FE->>API: polling GET /tickets/{id}
    API-->>FE: trạng thái + kết quả
```

### 4.2 Vòng hỏi lại cư dân — pause & resume

Đây là luồng đặc thù nhất của hệ thống: **một lần phân tích trải qua nhiều HTTP request rời nhau**.

```mermaid
sequenceDiagram
    autonumber
    participant G as LangGraph
    participant CP as MemorySaver<br/>(thread_id = session_id)
    participant DB as PostgreSQL
    actor R as Cư dân
    participant API as FastAPI

    G->>DB: tool_ask_prepare → INSERT ai_agent_questions (PENDING, expires_at)
    G->>CP: interrupt() → checkpoint state
    Note over G: tiến trình nền kết thúc, state nằm trong checkpoint

    R->>API: GET /tickets/{id}/agent-question (polling)
    API-->>R: câu hỏi + options + allow_free_text

    R->>API: POST .../agent-question/{qid}/answer
    API->>DB: validate hạn, cộng elapsed, status=ANSWERED
    Note over API,DB: quá hạn ⇒ session TIMED_OUT + ticket INVALID
    API->>G: BackgroundTasks resume_ticket_analysis(session_id)
    G->>CP: invoke(Command(resume=...))
    CP-->>G: khôi phục state
    G->>G: tool_ask_finalize → thêm answer_notes / ảnh mới
    G->>G: extract lại toàn bộ (reextraction=True)
```

Câu trả lời được đưa trở lại LLM dưới dạng `answer_notes` — và được truyền vào **cả hai** prompt (extract và decide), vì thiếu nó ở prompt quyết định đã từng gây vòng hỏi lặp lại đúng một câu ([llm_client.py:186](../src/agents/llm_client.py#L186)).

### 4.3 Finalize — Agent đề xuất, Backend quyết

```mermaid
flowchart TD
    F["finalize(AgentAnalysisResultV3)"] --> V{"Xác thực"}
    V -->|"catalog_version lệch<br/>Category ngoài snapshot<br/>tool_usage không khớp DB"| ERR["DomainError 400/409"]
    V -->|ok| RF{"red_flag_text hoặc<br/>red_flag_signal?"}

    RF -->|Có| P3["Priority = P3<br/>score_total = null<br/>classification = RESOLVED<br/>bỏ qua mọi grouping"]
    RF -->|Không| EXIT{exit_reason}

    EXIT -->|"LIMIT_REACHED<br/>CATEGORY_MISMATCH"| MR["classification = MANUAL_REVIEW<br/>(chính là P0)"]
    EXIT -->|INSUFFICIENT_INPUT| INV["status = INVALID<br/>classification = FAILED<br/>thông báo cư dân gửi lại"]
    EXIT -->|CONFIDENT_MATCH| RES{"giải ra đúng 1 Category?"}

    RES -->|Không| MR
    RES -->|Có| SC["ScoringService.calculate_dynamic()"]

    SC --> SCORE["score = base_score<br/>+ location_bonus<br/>+ density<br/>+ severity"]
    SCORE --> TH{"ngưỡng"}
    TH -->|"< 30"| PP1[P1 · SLA 72h]
    TH -->|"30–59"| PP2[P2 · SLA 3h]
    TH -->|"≥ 60"| PP3[P3 · SLA 5 phút]
    PP1 & PP2 & PP3 --> CEIL["áp priority_ceiling của Category"]
    CEIL --> IC["nếu đủ điều kiện gộp:<br/>tạo IncidentCase + members"]
    P3 & MR & INV & IC --> DONE["session COMPLETED<br/>ticket.version += 1<br/>ghi AIAnalysisRun + AuditLog"]
```

Bảng ánh xạ kết cục:

| `exit_reason` | `classification_status` | `priority` | Ai xử lý tiếp |
|---|---|---|---|
| `RED_FLAG` | RESOLVED | **P3** (bỏ qua chấm điểm) | Điều phối viên duyệt gấp |
| `CONFIDENT_MATCH` (1 Category) | RESOLVED | P1/P2/P3 theo điểm | Điều phối viên duyệt & phân công |
| `CONFIDENT_MATCH` (mơ hồ) | MANUAL_REVIEW | — | Điều phối viên chốt tay |
| `CATEGORY_MISMATCH` | MANUAL_REVIEW | — | Điều phối viên chốt tay |
| `LIMIT_REACHED` | MANUAL_REVIEW | — | Điều phối viên chốt tay |
| `INSUFFICIENT_INPUT` | FAILED (ticket INVALID) | — | Cư dân gửi lại |

> **P0 không phải một Priority.** P0 = `classification_status = MANUAL_REVIEW`. Enum `Priority` chỉ có P1/P2/P3.

### 4.4 Luồng con người — duyệt, phân công, thi công

```mermaid
sequenceDiagram
    autonumber
    actor C as Điều phối viên
    participant API as FastAPI
    participant DB as PostgreSQL
    actor T as Kỹ thuật viên
    actor R as Cư dân

    C->>API: GET /coordinator/tickets (lọc P0, priority, SLA)
    alt ticket ở MANUAL_REVIEW
        C->>API: POST .../manual-review/resolve (chốt Category)
        C->>API: POST .../manual-review/reject (loại)
    end
    opt cần thêm thông tin
        C->>API: POST .../request-information
        API->>R: Notification + status WAITING_RESIDENT_INFO
        R->>API: POST /tickets/{id}/supplements
        API->>API: chạy lại run_ticket_analysis
    end
    C->>API: PATCH .../classification (override, bắt buộc có lý do)
    C->>API: POST .../approve → status APPROVED
    C->>API: POST .../assign (technician_id)
    API->>DB: TicketAssignment (ASSIGNED)
    T->>API: accept → start → complete (+ ảnh nghiệm thu)
    API->>DB: ticket IN_PROGRESS → COMPLETED
    API->>R: Notification
```

Cụm sự cố (`IncidentCase`) có luồng riêng cho điều phối viên: `GET /coordinator/clusters`, `POST /clusters/{case_id}/approve`, `POST /clusters/{case_id}/assign`, `DELETE /clusters/{case_id}/tickets/{ticket_id}`.

### 4.5 Đường đi của ảnh

```mermaid
graph LR
    A["FE xin signed upload URL"] --> B["PUT thẳng lên Supabase Storage"]
    B --> C["Backend verify object<br/>MIME + size + đường dẫn thuộc user"]
    C --> D["TicketAttachment<br/>ISSUE_ORIGINAL / RESIDENT_SUPPLEMENT / TECHNICIAN_COMPLETION"]
    D --> E["Agent xin signed download URL<br/>TTL 300s"]
    E --> F["Gửi cho LLM dạng image_url"]
    F --> G["Trace redact ?token=<br/>trước khi ghi JSONL"]
```

Ảnh **không bao giờ đi qua backend** — backend chỉ ký URL và kiểm metadata. Đường dẫn theo tiền tố `tickets/{user_id}/...`, chặn path traversal, chỉ cho `image/jpeg|png|webp`, tối đa 10MB.

---

## 5. Mô hình dữ liệu

```mermaid
erDiagram
    UserProfile ||--o| ResidentProfile : "hồ sơ cư dân"
    UserProfile ||--o| TechnicianProfile : "hồ sơ kỹ thuật"
    Building ||--o{ Floor : "có"
    Floor ||--o{ Location : "có"
    Building ||--o{ Unit : "có"
    Unit ||--o{ ResidentProfile : "cư trú"
    Unit ||--o{ Ticket : "source_unit"
    Location ||--o{ Ticket : "vị trí"
    CategoryCatalog ||--o{ Ticket : "phân loại"

    Ticket ||--o{ TicketAttachment : "ảnh"
    Ticket ||--o{ TicketStatusHistory : "lịch sử"
    Ticket ||--o{ TicketAssignment : "phân công"
    Ticket ||--o{ InformationRequest : "yêu cầu bổ sung"
    Ticket ||--o{ Notification : "thông báo"
    Ticket ||--o{ AIAnalysisRun : "kết quả chạy"
    Ticket ||--o{ AIAnalysisSession : "phiên phân tích"

    AIAnalysisSession ||--o{ AIAgentToolCall : "tool call"
    AIAnalysisSession ||--o{ AIAgentQuestion : "câu hỏi"
    AIAnalysisSession ||--o| AIAnalysisRun : "kết luận"

    IncidentCase ||--o{ IncidentCaseMember : "thành viên"
    Ticket ||--o| IncidentCaseMember : "thuộc cụm"
    TechnicianProfile ||--o{ TicketAssignment : "thực hiện"
    ScoringRuleVersion ||--o{ Ticket : "cấu hình điểm"
    AuditLog }o--|| Ticket : "entity"
```

Ba bảng lõi của Agent:

| Bảng | Ghi gì |
|---|---|
| `ai_analysis_sessions` | Một phiên phân tích: `status`, `category_catalog_version` + `category_catalog_snapshot` (ghim), `total_tool_calls`, `ask_resident_rounds`, `ask_resident_elapsed_seconds`, `waiting_deadline_at`. **Đây là nguồn sự thật về ngân sách.** |
| `ai_agent_tool_calls` | Mỗi tool call với `sanitized_request` / `sanitized_response`, `sequence` duy nhất trong session. |
| `ai_agent_questions` | Câu hỏi cho cư dân: loại, options, `round_number`, `expires_at`, câu trả lời (text hoặc `answer_upload_id`). |
| `ai_analysis_runs` | Kết quả cuối một lần chạy (contract `v3`): categories, red-flag, severity, `exit_reason`, `grouping`, `tool_usage` **do backend tính**, `model_version`. |

Migration bằng Alembic — 16 revision trong [alembic/versions/](../alembic/versions/), có cả RLS policy và view bảo mật.

---




## Phụ lục — bản đồ API

| Nhóm | Endpoint tiêu biểu |
|---|---|
| Health | `GET /health`, `GET /ready` |
| Auth | `POST /api/v1/auth/otp/request`, `POST /auth/otp/verify`, `GET /me`, `POST /me/bind-unit` |
| Catalog | `GET /catalog/locations`, `GET /catalog/categories` |
| Storage | `POST /storage/ticket-attachments/upload-url`, `POST /storage/completion-evidence/upload-url` |
| Cư dân | `POST /tickets`, `GET /tickets`, `GET /tickets/{id}`, `POST /tickets/{id}/cancel`, `POST /tickets/{id}/supplements`, `GET /tickets/{id}/agent-question`, `POST /tickets/{id}/agent-question/{qid}/answer` |
| Thông báo | `GET /notifications`, `POST /notifications/{id}/read` |
| Điều phối viên | `GET /coordinator/tickets`, `POST .../approve`, `POST .../assign`, `POST .../manual-review/{resolve,reject}`, `POST .../request-information`, `PATCH .../classification`, `GET /coordinator/clusters`, `GET /coordinator/categories`, `GET /coordinator/technicians`, `GET /coordinator/audit-logs`, `GET /coordinator/reports/*` |
tài | Kỹ thuật viên | `GET /technician/assignments`, `POST .../accept`, `POST .../start`, `POST .../complete`, `POST .../unable-to-handle` |

Tài liệu tương tác: `/docs` (Swagger), `/redoc`, `/openapi.json`.
