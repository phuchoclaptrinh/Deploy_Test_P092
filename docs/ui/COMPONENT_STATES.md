# Resident Mobile Component States

> Companion to [`UX_FLOWS.md`](./UX_FLOWS.md) and [`SCREEN_INVENTORY.md`](./SCREEN_INVENTORY.md)
>
> All visible product copy in this document is Vietnamese. Internal names are shown only so implementation can match the current backend.

## 1. State rules

1. The backend is the source of truth for report status, allowed actions, ownership, and expected completion information.
2. The frontend may validate obvious form problems, but it must handle the backend rejecting stale or invalid actions.
3. A loading, empty, disabled, success, and error state must be defined for every interactive component.
4. Never remove a Resident’s draft because an upload, request, or connection failed.
5. Never show internal status codes, priority codes, scores, AI reasoning, or the word “SLA” to a Resident.
6. Color supports meaning but is never the only signal.

## 2. Component inventory

| ID | Component | Used in |
| --- | --- | --- |
| C-01 | Resident app shell | R-01, R-03, R-05, R-06 |
| C-02 | Bottom navigation item | Resident app shell |
| C-03 | Central report button | Resident app shell |
| C-04 | Page header | All screens |
| C-05 | Primary button | Forms and main actions |
| C-06 | Secondary/text button | Supporting actions |
| C-07 | Guidance block | Place, Report an issue |
| C-08 | Location selector | Report an issue |
| C-09 | Description field | Report and supplement forms |
| C-10 | Photo uploader | Report, assistant answer, supplement |
| C-11 | Photo item | Photo uploader and report details |
| C-12 | Report card | Requests |
| C-13 | Status badge | Report card and details |
| C-14 | Expected-time block | Report card, details, notices |
| C-15 | Assistant question card | Report details |
| C-17 | Existing-issue card | Report details |
| C-18 | Timeline | Report details |
| C-19 | Notice row | Notice |
| C-20 | Notice badge | Bottom navigation |
| C-21 | Filter chip and filter sheet | Requests |
| C-22 | Empty state | Lists and details |
| C-23 | Inline alert | All screens |
| C-24 | Confirmation dialog | Discard, cancel, logout |
| C-25 | Profile information row | Profile |
| C-26 | Loading skeleton | Lists and details |

## 3. C-01 — Resident app shell

| State | Visual behavior | Interaction |
| --- | --- | --- |
| Ready | Current main screen and bottom navigation visible | All allowed navigation works. |
| Loading access | Neutral full-screen progress with **Đang kiểm tra tài khoản…** | Navigation disabled. |
| No session | No private content rendered | Move to shared login. |
| Wrong role | No Resident content rendered | Clear Resident session and move to shared login. |
| No apartment | Blocking card: **Tài khoản chưa được liên kết với căn hộ.** | Allow approved Building Management contact only. |
| Offline | Slim banner: **Bạn đang ngoại tuyến. Một số thông tin có thể chưa được cập nhật.** | Cached safe content remains usable. |

## 4. C-02 and C-03 — Bottom navigation

### Bottom navigation item

| State | Appearance | Accessibility |
| --- | --- | --- |
| Inactive | Neutral icon and label | Announces only the label. |
| Active | Stronger icon, label, and shape/indicator | Announces **đang chọn**. |
| Pressed | Brief pressed surface | Does not move surrounding items. |
| Disabled | Avoid where possible; reduce emphasis | Explain why if a main section is unavailable. |
| With badge | Badge beside icon, not over label | Announces unread count once. |

Visible labels:

- **Trang chủ**
- **Thông báo**
- **Phản ánh**
- **Tài khoản**

### Central report button

| State | Appearance | Behavior |
| --- | --- | --- |
| Ready | Prominent circular `+` with label for screen readers: **Gửi phản ánh** | Opens R-02. |
| Pressed | Small visual compression or tone change | One open action only. |
| Offline | Remains visible | Opens R-02 with an offline notice; submission stays unavailable. |
| No apartment | Visually available only if explanation is useful | Shows the apartment-link blocker instead of the form. |

The central button must never show a tab-active state.

## 5. C-04 — Page header

| Context | Left | Center/title | Right |
| --- | --- | --- | --- |
| Main tab | Optional brand/home mark | Vietnamese page title | Optional contextual action |
| Report sheet | Close button | **Gửi phản ánh** | Empty spacer |
| Report details | Back button | **Chi tiết phản ánh** | Empty or safe contextual action |

States:

- Default
- Scrolled with a subtle separating surface
- Keyboard open
- Long text at larger accessibility sizes

Buttons use visible or accessible labels: **Đóng**, **Quay lại**.

## 6. C-05 — Primary button

| State | Example | Behavior |
| --- | --- | --- |
| Enabled | **Gửi phản ánh** | Accepts one tap. |
| Pressed | Same label | Immediate tactile/visual feedback. |
| Disabled | Same label with lower emphasis | Paired with nearby explanation; never color-only. |
| Loading | **Hệ thống đang phân loại phản ánh của bạn** | Disabled; spinner may appear beside text. |
| Success | **Gửi phản ánh thành công** | Brief state before navigation. |
| Recoverable error | **Hãy gửi lại phản ánh** | Keeps entered data. |

Do not replace the label with a spinner alone.

## 7. C-07 — Guidance block

### Place version

- Title: **Cách gửi phản ánh**
- Three short steps with simple icons.
- Follow-up note separated from the steps.

### Report form version

- Title: **Vui lòng cung cấp:**
- Three compact bullets: location, description, optional photo.

States:

| State | Behavior |
| --- | --- |
| Ready | Full approved copy shown. |
| Remote content loading | Use bundled fallback copy immediately; no skeleton needed. |
| Remote content failed | Keep fallback copy. |
| Large text | Stack icon and copy without clipping. |

## 8. C-08 — Location selector

The selector represents one backend-approved location ID.

| State | Visible content | Behavior |
| --- | --- | --- |
| Empty | **Chọn tầng** and **Chọn vị trí** | Opens picker. |
| Loading catalog | **Đang tải vị trí…** | Control disabled. |
| Floor selected | Floor label plus empty location field | Limit location list to the floor. |
| Selected | Full location label | Form can become valid. |
| Search active | Search field: **Tìm vị trí** | Filters existing options only. |
| No search result | **Không tìm thấy vị trí phù hợp.** | Clear search or contact Building Management. |
| Catalog empty | **Chưa có danh sách vị trí.** | Submission blocked. |
| Invalidated after submit | **Vị trí này không còn khả dụng. Vui lòng chọn lại.** | Clear selected ID; preserve other fields. |
| Load error | **Không tải được danh sách vị trí.** | Show **Thử lại**. |

The control never accepts a free-text value as the submitted location.

## 9. C-09 — Description field

### New report

- Label: **Mô tả sự cố**
- Required
- Maximum 5000 characters

The assistant's written answer (C-15) is the only other multi-line field; it has
its own 2000-character limit.

| State | Behavior and copy |
| --- | --- |
| Empty | Show the approved placeholder. |
| Focused | Keep label visible; bring field above keyboard. |
| Filled | Preserve content until confirmed submission. |
| Near limit | Show remaining character count. |
| At limit | Stop or reject extra input without deleting text. |
| Required error | **Vui lòng mô tả sự cố trước khi gửi.** |
| Disabled during submit | Content remains readable. |

Blank spaces are treated as empty.

## 10. C-10 and C-11 — Photo uploader

### Photo uploader container

| State | Visible content | Behavior |
| --- | --- | --- |
| Empty | **Thêm ảnh** with `+` icon | Opens photo source picker. |
| One to four ready | Preview grid plus **Thêm ảnh khác** | Add or remove. |
| Five ready | Preview grid, no add tile | Explain **Bạn đã thêm tối đa 5 ảnh.** |
| Some uploading | Per-item progress | Report Submit remains disabled. |
| Some failed | Failed items remain visible | Retry or remove each failed item. |
| All removed | Return to Empty | Description remains unchanged. |
| Permission denied | **Ứng dụng chưa được phép truy cập camera hoặc thư viện ảnh.** | Offer device-settings guidance. |

### Photo item

| State | Overlay/action |
| --- | --- |
| Preparing | Progress indicator and **Đang chuẩn bị…** |
| Uploading | Percentage or indeterminate progress and **Đang tải ảnh…** |
| Ready | Preview and remove button **Xóa ảnh** |
| Failed format | **Định dạng ảnh không được hỗ trợ.** |
| Failed size | **Ảnh vượt quá dung lượng cho phép.** |
| Upload failed | **Không tải được ảnh.** with **Thử lại** and **Xóa** |
| Expired upload | **Phiên tải ảnh đã hết hạn. Vui lòng tải lại.** |
| Viewing | Full image viewer with **Đóng**, **Ảnh trước**, **Ảnh tiếp theo** |

Current limits: JPEG/PNG/WebP, 10 MB each, five photos. Treat limits as configuration and keep copy easy to update.

## 11. Report form state model

| Form state | Location | Description | Photos | Submit |
| --- | --- | --- | --- | --- |
| Pristine | Empty | Empty | Empty | Disabled |
| Incomplete | Missing or valid | Missing or filled | Any settled state | Disabled; show reason after attempted submit |
| Uploading | Valid or missing | Filled or missing | At least one uploading | Disabled |
| Ready | Valid | Filled | Empty or all ready | Enabled |
| Submitting | Valid | Filled | All ready | Loading and disabled |
| Accepted | Stored by backend | Stored | Stored IDs consumed | Brief success, then open details |
| Recoverable error | Preserved | Preserved | Preserved where safe | **Thử lại** |
| Rate limited | Preserved | Preserved | Preserved | Disabled until the returned time |
| Offline | Preserved | Preserved | Local/prepared state | Disabled with connection message |

Draft dirty state becomes true after any location, description, or photo change. Closing or logging out with a dirty draft opens C-24.

## 12. C-12 — Report card

### Anatomy

Every field below is required on every card and comes from the backend.

1. Report number
2. Issue name, or **Đang xác định loại sự cố**
3. C-13 status badge
4. Location, from `location_label`, or **Chưa cập nhật vị trí**
5. Sender: **Bạn** when `is_reporter`, else `reporter_name`, else **Thành viên trong căn hộ**
6. Submission date and time
7. C-14 expected time, while the report is active

Never render a sender’s phone number, and never render anything about the
reporter of a duplicate master that belongs to another apartment.

| State | Card behavior |
| --- | --- |
| Checking | Issue fallback and **Đang kiểm tra phản ánh** |
| Active | Current status and expected time |
| Finished | Final status; expected time may be removed |
| Existing issue link | **Sự cố này đã được báo và đang được xử lý**; grouped with its master |
| Not accepted | **Chưa được tiếp nhận**; finished group, no actions |
| Loading | Skeleton matching final card height |
| Stale offline | Keep card and show list-level stale indicator |

The whole card is one accessible navigation target. Do not place destructive actions on the list card.

## 13. C-13 — Status badge and backend mapping

The client should render the friendly backend status and should not expose the internal value. The mapping below defines expected meaning and copy.

| Backend condition | Vietnamese label | Tone | Active group |
| --- | --- | --- | --- |
| Classification pending/processing | **Đang kiểm tra phản ánh** | Informational | Yes |
| Open assistant question | **Đang chờ bạn trả lời** | Attention | Yes |
| Classification needs human review | **Ban quản lý đang xem xét** | Informational | Yes |
| `NEW` after classification | **Mới** | Neutral | Yes |
| `WAITING_RESIDENT_INFO` | **Đang chờ xử lý** | Attention | Yes |
| `APPROVED`, no active technician summary | **Đã duyệt** | Positive | Yes |
| `APPROVED`, active technician summary | **Đã có kỹ thuật viên** | Positive | Yes |
| Technician changed event | **Đã đổi kỹ thuật viên** | Attention | Yes |
| `IN_PROGRESS` | **Đang xử lý** | Informational | Yes |
| `COMPLETED` | **Hoàn thành** | Positive | No |
| `UNRESOLVABLE` | **Không xử lý được** | Critical | No |
| `CANCELLED` | **Đã hủy** | Neutral | No |
| `INVALID` | **Chưa được tiếp nhận** | Critical | No |
| `LINKED_DUPLICATE` | **Sự cố đã được báo và đang được xử lý** | Informational | Follows master |

Notes:

- `WAITING_RESIDENT_INFO` appears only on legacy rows. The workflow that set it —
  Building Management asking a resident for more information — has been removed.
- `INVALID` covers all three ways a report can end without being accepted:
  not enough detail, response timeout, and a Building Management rejection.
  `invalid_reason_text` carries the friendly explanation for each.
- The active/finished column is not decided by the client. The backend returns
  `lifecycle_group`, and a linked duplicate follows its canonical master.

Remaining backend alignment:

- Current backend returns **Đã gộp phản ánh** for linked reports. Product copy should change to the clearer wording above.

## 14. C-14 — Expected-time block

| State | Copy |
| --- | --- |
| Checking | **Đang cập nhật thời gian xử lý** |
| Human review | **Đang chờ Ban quản lý xác nhận** |
| Time available | **Dự kiến hoàn thành: {time}** |
| Duration only | **Dự kiến xử lý trong {duration}** |
| Technician changed | **Thời gian dự kiến mới: {time}** |
| Finished | Hide or show completion time, not an old estimate. |
| Unavailable | **Thời gian dự kiến đang được cập nhật** |

Do not calculate priority timing in the frontend. Current backend returns friendly base estimates, but it must return the current value after technician changes or case-related extension.

## 15. C-15 — Assistant question card

### Shared states

| State | Content and behavior |
| --- | --- |
| Checking for question | Compact skeleton; do not flash an empty card. |
| No question | Component absent. |
| Pending | Question, round, remaining time, valid answer control. |
| Answer selected | Selected option clearly marked; Send enabled. |
| Sending | **Đang gửi câu trả lời…**; controls locked. |
| Answered | **Cảm ơn bạn. Hệ thống đang kiểm tra lại phản ánh.** |
| Next question | Replace content and keep remaining session time. |
| Expired | **Đã hết thời gian trả lời.**; controls removed. |
| Not sender | Component absent; report-level status may still say waiting. |
| Recoverable error | Keep answer and show **Không gửi được câu trả lời. Thử lại**. |
| State changed | Refresh details and explain **Trạng thái phản ánh vừa được cập nhật.** |

### Multiple-choice question

- One selectable option at a time.
- If written fallback is allowed, show **Câu trả lời khác**.
- Send remains disabled until an allowed answer is present.

### Written-answer question

- Label: **Câu trả lời của bạn**
- Maximum 2000 characters under the current backend schema.
- Empty text cannot be sent.

### New-photo answer

- Action: **Chụp ảnh mới**
- Exactly one successful new upload is sent for that answer.
- Use the same photo validation states as C-10/C-11.

### Timer

- Derived from the backend expiry time.
- Never resets when another question appears.
- Warning at one minute: **Còn 1 phút để trả lời.**
- Urgent warning at fifteen seconds: **Còn 15 giây để trả lời.**
- Do not announce every second to assistive technology.

## 16. C-17 — Existing-issue card

The card is informational. There is no appeal: it tells the resident their
report was folded into one already being worked on, and then tracks that work.

| State | Content and action |
| --- | --- |
| Linked | Existing report reference, status, issue name, expected time; no action |
| Result notice received | Show the duplicate-result notice and continue reduced master tracking |
| Master reaches a finished state | The linked report follows it into the finished group |
| Other apartment member viewing | Same card, same content — the report is published by the time it is linked |
| Master information unavailable | **Tiến độ sự cố đang được cập nhật.**; never fill with guessed data |

Privacy is part of every state: no identity, apartment, text, or photos from the
other report — only its reference code, friendly issue name, status and expected
time.

There is no **Sự cố của tôi khác** action and no "waiting for Building
Management to review" state. Both belonged to the removed duplicate-appeal
flow.

## 17. C-18 — Timeline

| State | Behavior |
| --- | --- |
| Loading | Three compact skeleton rows. |
| One event | Show the created/current event without an empty connector. |
| Multiple events | Oldest to newest vertically; newest visually strongest. |
| Public reason present | Show plain Vietnamese reason beneath the event. |
| Internal or untranslated reason | Do not expose raw internal text; use the status only. |
| Empty due to old data | Show current status and **Chưa có thêm cập nhật.** |

Each row includes status, date/time, and optional public reason. Never show internal actor IDs or audit details.

## 18. C-19 and C-20 — Notice row and badge

### Notice row

| State | Appearance and behavior |
| --- | --- |
| Unread | Unread marker, stronger title; opens and marks read. |
| Read | Normal emphasis. |
| Marking read | Navigation may proceed; prevent repeated write. |
| Mark-read failed | Keep item available and retry later; do not remove it. |
| Has report | Opens Report details after marking read. |
| No report | Opens or expands notice content only. |
| Report unavailable | Notice remains readable; show **Không thể mở phản ánh này.** |

### Notice badge

| State | Behavior |
| --- | --- |
| Accurate zero | Hidden. |
| Accurate 1–99 | Show number. |
| Accurate 100+ | Show **99+**. |
| Unknown because only part of list loaded | Show a dot, not a guessed number. |
| Offline stale | Keep last known dot/count only if clearly treated as cached. |

## 19. C-21 — Filters

### Filter chip

| State | Behavior |
| --- | --- |
| Inactive | Neutral label. |
| Active | Selected shape plus check/indicator. |
| Pressed | Brief feedback. |
| Has removable value | Accessible remove action. |

### Filter sheet

- Group: **Trạng thái** — Tất cả, Đang theo dõi, Đã kết thúc
- Group: **Loại sự cố** — values from the active category catalog
- Group: **Thời gian** — from/to date
- Actions: **Áp dụng**, **Xóa bộ lọc**

States:

- Loading categories
- Ready
- No categories available
- Invalid date range with **Ngày bắt đầu phải trước ngày kết thúc.**
- Applying
- Applied

## 20. C-22 and C-26 — Empty and loading states

### Empty state anatomy

1. Simple non-decorative icon or illustration
2. Short Vietnamese title
3. One-sentence explanation
4. One optional next action

Do not use an empty state while a first load is still running.

### Loading skeleton

- Match the final layout closely enough to prevent large movement.
- Do not animate indefinitely without explanatory copy.
- Preserve header and back navigation.
- After a longer check, show: **Bạn có thể rời trang này và theo dõi tiến độ trong mục Phản ánh.**

## 21. C-23 — Inline alerts

| Type | Example copy | Behavior |
| --- | --- | --- |
| Information | **Hệ thống đang kiểm tra phản ánh.** | Non-blocking. |
| Success | **Đã gửi phản ánh.** | Announced once. |
| Warning | **Còn 1 phút để trả lời.** | Does not steal focus. |
| Error | **Không gửi được phản ánh. Vui lòng thử lại.** | Keeps data and offers recovery. |
| Offline | **Bạn đang ngoại tuyến.** | Persistent until connection returns. |

Alerts must not expose backend error codes. A support reference number may be shown beneath unexpected errors.

## 22. C-24 — Confirmation dialogs

### Discard report draft

- Title: **Rời khỏi phản ánh này?**
- Body: **Những thay đổi chưa gửi sẽ bị mất.**
- Safe action: **Tiếp tục chỉnh sửa**
- Destructive action: **Bỏ bản nháp**

### Cancel report

- Title: **Hủy phản ánh này?**
- Body: **Ban quản lý sẽ dừng xử lý phản ánh.**
- Safe action: **Giữ phản ánh**
- Destructive action: **Hủy phản ánh**

### Logout with a draft

- Title: **Bạn đang có phản ánh chưa gửi**
- Body: **Hãy tiếp tục chỉnh sửa hoặc bỏ bản nháp trước khi đăng xuất.**
- Safe action: **Tiếp tục chỉnh sửa**
- Destructive action: **Bỏ bản nháp và đăng xuất**

Dialog rules:

- Focus starts on the safe action.
- Android Back or tapping outside behaves like the safe action for destructive dialogs.
- Destructive actions have text labels and do not rely on red color alone.

## 23. Error-to-copy mapping

Internal errors are included only for implementation. Residents see the Vietnamese copy.

| Internal error | Resident copy | Recovery |
| --- | --- | --- |
| Missing/ended session | **Phiên đăng nhập đã hết hạn.** | **Đăng nhập lại** |
| No active apartment | **Tài khoản chưa được liên kết với căn hộ.** | Contact Building Management |
| Invalid location | **Vị trí này không còn khả dụng. Vui lòng chọn lại.** | Reload location picker |
| Invalid attachment | **Không thể sử dụng ảnh này.** | Retry or remove photo |
| Create rate limited | **Bạn đã gửi quá nhiều phản ánh trong thời gian ngắn. Có thể gửi lại sau {time}.** | Wait; keep other sections available |
| Report not found | **Không tìm thấy phản ánh này.** | Return to Requests |
| Status changed | **Trạng thái phản ánh vừa được cập nhật.** | Refresh details |
| Question expired | **Đã hết thời gian trả lời.** | Refresh and offer new report when invalid |
| Forbidden | **Bạn không có quyền thực hiện thao tác này.** | Remove stale action after refresh |
| Unknown | **Đã xảy ra lỗi. Vui lòng thử lại.** | Retry and show support reference |

## 24. Backend gaps affecting component states

Before release, components need backend support for:

1. sender-aware allowed actions, and sender-only cancel and assistant-answer
   enforcement in the services themselves;
2. current expected completion time after reassignment;
3. reduced existing-report status/category/current time for C-17;
4. accurate unread total when a numeric badge is desired;
5. confirmed idempotent report submission for safe automatic retry.

Now supplied by the backend: report `location_label`, sender identity
(`reporter_name`, `is_reporter`), `invalid_reason_text`, and `lifecycle_group`
with its matching `status_group` list filter.
