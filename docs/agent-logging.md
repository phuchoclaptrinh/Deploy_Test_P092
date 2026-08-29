# Log hành vi Agent

Mỗi phiên phân tích của Agent v3 ghi ra **một file JSONL** trong `.ai-log/agent/`, đặt tên theo `analysis_session_id`. Một dòng là một sự kiện.

## Vì sao tách khỏi bảng audit trong DB

Ba bảng `ai_analysis_sessions` / `ai_agent_tool_calls` / `ai_agent_questions` ghi lại những gì **Backend chấp nhận** — đó là dấu vết audit chính thức, dùng để đối chiếu và báo cáo.

File JSONL ghi lại những gì **Agent đã làm và vì sao**: cả những bước Backend từ chối, nhánh rẽ đã chọn, và thời gian từng lần gọi LLM. Khi cần hiểu vì sao một ticket bị phân loại sai, bạn cần loại thứ hai — loại thứ nhất chỉ cho thấy kết quả cuối.

## Cách xem

```bash
python scripts/read_agent_trace.py --list          # liệt kê session, mới nhất trước
python scripts/read_agent_trace.py --last          # dòng thời gian của session mới nhất
python scripts/read_agent_trace.py --session <id>  # một session cụ thể
python scripts/read_agent_trace.py --last --raw    # in nguyên JSON
```

Ví dụ output:

```
04:43:19.437 ▶ run_start: run {"model_version": "fixit-agent-v3-langgraph-1", "catalog_size": 12}
04:43:19.438     ↗ llm_request: extract {"image_urls": ["https://.../a.jpg?<redacted>"]}
04:43:19.438     ↘ llm_response: extract [1843.2ms] {"result": {"severity": "HIGH"}}
04:43:19.440   ⑂ route: route_after_extract → decide_action {"state": {"iterations": 0}}
04:43:19.440   → node_enter: decide_action {"state": {"iterations": 1}}
04:43:19.441   ← node_exit: decide_action [0.0ms] {"updates": {"next_action": "ASK_RESIDENT"}}
04:43:19.441 ⏸ run_paused: run {"pending_question_id": "q-1"}
```

Vì là JSONL nên `jq` dùng được trực tiếp:

```bash
# Mọi quyết định chọn action, kèm lý do
jq -r 'select(.event=="node_exit" and .node=="decide_action") | .updates.action_reason' .ai-log/agent/<id>.jsonl

# Tổng thời gian chờ LLM của một session
jq -s '[.[] | select(.event=="llm_response") | .duration_ms] | add' .ai-log/agent/<id>.jsonl
```

## Các loại sự kiện

| Event | Ghi khi | Trường đáng chú ý |
|---|---|---|
| `run_start` | Bắt đầu `run_ticket_analysis` hoặc `resume_ticket_analysis` | `kind` (`run`/`resume`), `catalog_version`, `image_count` |
| `node_enter` | Vào một node của graph | `node`, `state` (bản rút gọn) |
| `node_exit` | Node trả về bình thường | `node`, `duration_ms`, `updates` |
| `node_error` | Node ném exception | `node`, `error_type`, `error` |
| `route` | Một conditional edge chọn nhánh | `router`, `target` |
| `llm_request` | Ngay trước khi gọi LLM | `call`, `description`, `image_count` |
| `llm_response` | LLM trả về, đã parse | `call`, `duration_ms`, `result` |
| `llm_error` | Gọi LLM thất bại | `call`, `error_type` |
| `run_paused` | Graph dừng chờ cư dân trả lời | `pending_question_id` |
| `run_end` | Chạy xong | `exit_reason`, `iterations`, `is_confident` |
| `run_error` | Lỗi thoát ra tới `service.py` | `error_type`, `error` |

`run_paused` và `run_end` được tách riêng có chủ đích: một phiên dừng chờ cư dân và một phiên chết giữa chừng đều làm file ngừng lại, và đó đúng là hai trường hợp cần phân biệt khi một ticket có vẻ bị treo.

Khi cư dân trả lời, `resume_ticket_analysis` **ghi tiếp vào đúng file cũ**, nên toàn bộ hội thoại của một ticket nằm gọn một chỗ dù trải qua nhiều HTTP request.

## Dữ liệu nhạy cảm

Làm sạch theo đúng quy ước của các cột `sanitized_*` trong DB:

- **Signed URL** của Supabase Storage bị cắt query string (`?<redacted>`). Token trong đó là credential đọc được file trong suốt TTL, không được rơi vào log.
- **Văn bản của cư dân và của model** (`description`, `answer_notes`, `confidence_notes`, `action_reason`, `question_text`...) bị cắt còn `AGENT_TRACE_TEXT_LIMIT` ký tự, có ghi rõ đã cắt bao nhiêu.
- **Catalog** chỉ ghi số lượng, không dump toàn bộ — nó lặp lại không đổi suốt phiên và đã có snapshot trong `ai_analysis_sessions.category_catalog_snapshot`.

Prompt hệ thống không được ghi: nó là hằng số trong mã nguồn, ghi lại mỗi lần gọi chỉ làm phình file.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `AGENT_TRACE_ENABLED` | `true` | Tắt hoàn toàn việc ghi trace |
| `AGENT_TRACE_DIR` | `.ai-log/agent` | Đường dẫn tương đối tính từ gốc repo |
| `AGENT_TRACE_TEXT_LIMIT` | `500` | Số ký tự văn bản giữ lại mỗi trường; `0` = không cắt |

`.ai-log/agent/` đã nằm trong `.gitignore` nên trace không bao giờ bị commit.

## Thiết kế

Toàn bộ phần ghi log nằm ở lớp bọc ngoài, không trộn vào logic nghiệp vụ:

- `src/agents/trace.py` — bộ ghi JSONL và hàm làm sạch.
- `src/agents/tracing.py` — decorator `traced_node` / `traced_router` và lớp bọc `TracingLLMClient`.
- `src/agents/graph.py` — áp decorator lúc `build_graph`.

Nhờ vậy `nodes.py` và `llm_client.py` không chứa một dòng log nào. Khi tắt trace, `build_graph` dựng graph từ hàm gốc chứ không để lại nhánh `if enabled` trong đường chạy nóng.

Việc ghi trace **không bao giờ làm hỏng một phiên phân tích**: `AgentTracer.emit` nuốt mọi lỗi của chính nó, và tự tắt sau lần lỗi đầu tiên để một ổ đĩa đầy không sinh ra một cảnh báo cho mỗi sự kiện.
