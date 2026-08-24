# Báo cáo chạy Agent v4 — action-only

Thời điểm chạy: `2026-08-24T12:00:42.015241+00:00`

Phạm vi: dùng extraction/judgement/model response đã script theo golden data; chạy code Agent hiện tại để đo routing, tool, pause/resume, duplicate, grouping và primary/fallback. Đây chưa phải điểm chất lượng suy luận của model và chưa áp rubric.

## Tổng quan

- Đã chạy: **83** case
- PASS kỹ thuật: **83**
- FAIL so với golden: **0**
- ERROR khi thực thi: **0**
- Chưa chạy trong phạm vi này: **33**
- Entrypoint production: **PASS** — không có lỗi

| Cụm | Chạy | PASS | FAIL | ERROR |
|---|---:|---:|---:|---:|
| A1 | 17 | 17 | 0 | 0 |
| A2 | 14 | 14 | 0 | 0 |
| A3 | 16 | 16 | 0 | 0 |
| B1 | 18 | 18 | 0 | 0 |
| B2 | 18 | 18 | 0 | 0 |

## Hành vi thực tế đã quan sát

| Exit/hành vi Agent phân tích | Số case |
|---|---:|
| ANALYSIS_COMPLETE | 21 |
| AWAITING_RESIDENT | 7 |
| DUPLICATE_EXISTING | 2 |
| DUPLICATE_UNCERTAIN | 1 |
| INSUFFICIENT_INPUT | 4 |
| RED_FLAG | 12 |

| Tool call | Số lần |
|---|---:|
| ask_resident | 16 |
| propose_case_grouping | 4 |
| search_related_tickets:DUPLICATE | 33 |
| search_related_tickets:GROUPING | 9 |

- Tổng câu hỏi đã tạo: **16**
- Assignment dùng fallback: **19 / 36** case
- Assignment decisions hợp lệ trả ra: **81**
- Assignment failures còn lại sau fallback: **2**

## Case chưa khớp

Không có.

## Dữ liệu để xây rubric

Mỗi dòng JSONL giữ expected, actual final payload, chuỗi model-call đã script, tool-call thực tế, câu hỏi Cư dân, fallback và lỗi validation. Có thể dùng các trường này để định nghĩa rubric mà không phải suy ngược từ bảng tổng hợp.

Các trục quan sát có sẵn: `exit_reason`, thứ tự/loại `tool_calls`, số lượt hỏi, `grouping`, `duplicate`, `red_flag_relation`, `severity`, `fallback_used`, `model_version_by_decision_id`, `failures`.

## Case loại khỏi lần chạy

- 29 case cần ảnh: chờ fixture ảnh từ người dùng.
- 4 case Backend-only: pre-filter trước khi request tới Assignment Agent (`B1-005`, `B1-007`, `B2-005`, `B2-006`).

Danh sách chi tiết:

- `A1-014` — Backend pre-filter, không vào Agent
- `A1-015` — Backend pre-filter, không vào Agent
- `A1-016` — Backend pre-filter, không vào Agent
- `A1-017` — Backend pre-filter, không vào Agent
- `A1-018` — Backend pre-filter, không vào Agent
- `A1-019` — Backend pre-filter, không vào Agent
- `A1-020` — Backend pre-filter, không vào Agent
- `A1-021` — Backend pre-filter, không vào Agent
- `A1-022` — Backend pre-filter, không vào Agent
- `A1-023` — Backend pre-filter, không vào Agent
- `A1-024` — Backend pre-filter, không vào Agent
- `A1-025` — Backend pre-filter, không vào Agent
- `A1-026` — Backend pre-filter, không vào Agent
- `A1-027` — Backend pre-filter, không vào Agent
- `A1-028` — Backend pre-filter, không vào Agent
- `A1-029` — Backend pre-filter, không vào Agent
- `A1-030` — Backend pre-filter, không vào Agent
- `A1-031` — Backend pre-filter, không vào Agent
- `A1-032` — Backend pre-filter, không vào Agent
- `A3-008` — Backend pre-filter, không vào Agent
- `A3-009` — Backend pre-filter, không vào Agent
- `A3-010` — Backend pre-filter, không vào Agent
- `A3-011` — Backend pre-filter, không vào Agent
- `A3-012` — Backend pre-filter, không vào Agent
- `A3-013` — Backend pre-filter, không vào Agent
- `A3-014` — Backend pre-filter, không vào Agent
- `A3-017` — Backend pre-filter, không vào Agent
- `A3-024` — Backend pre-filter, không vào Agent
- `A3-025` — Backend pre-filter, không vào Agent
- `B1-005` — Backend pre-filter, không vào Agent
- `B1-007` — Backend pre-filter, không vào Agent
- `B2-005` — Backend pre-filter, không vào Agent
- `B2-006` — Backend pre-filter, không vào Agent
