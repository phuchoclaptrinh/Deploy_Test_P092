"""LLM-facing extraction/decision contracts for the Agent v3 pipeline.

The Agent reasons about Category using the human-readable display names from
the session's pinned catalog snapshot (never the Backend-internal machine
`code`, per Self_Dev_Docs v3 — the dynamic Category UUID boundary). Node code
maps display name -> category_id after the LLM responds.

Note on prompt design: the system prompts below intentionally spell out the
actual business rules in plain language instead of pointing at internal spec
document names or step labels (e.g. "Logic_xử_lý_chính_v3 bước E"). Those
documents are engineering references for humans maintaining this file — the
model itself never sees them, so a prompt that just cites a doc/step name
gives it nothing to act on. Every rule the model needs must be restated here
in full.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

Action = Literal["SEARCH_RELATED", "PROPOSE_GROUPING", "ASK_RESIDENT", "CONCLUDE"]


class ExtractionResult(BaseModel):
    """Structured output for the initial extraction pass and every re-run
    after new information becomes available (a resident's answer, a related
    ticket found by search, a new photo)."""

    text_categories: list[str] = Field(default_factory=list, description="Category display names found in the text.")
    red_flag_text: bool = Field(description="Danger signal in text: smoke, fire, bare wire, flooding, fainting, disorder.")
    text_understandable: bool = Field(description="Is the text alone enough to understand what the problem is, even roughly?")
    image_categories: list[str] | None = Field(default=None, description="Category display names found in the image, or null if no image.")
    red_flag_signal: bool | None = Field(
        default=None,
        description=(
            "Danger visible in the image. REQUIRED true/false whenever an image is provided — "
            "never omit it and never leave it null in that case. Null only when there is no image."
        ),
    )
    is_relevant: bool | None = Field(
        default=None,
        description=(
            "Does the image actually relate to an apartment-building incident? REQUIRED true/false "
            "whenever an image is provided — never omit it. Null only when there is no image."
        ),
    )
    severity: Literal["LOW", "MEDIUM", "HIGH"] = Field(description="Thấp/Vừa/Cao. Never leave at a default; always pick deliberately.")
    severity_source: Literal["IMAGE", "TEXT"] = Field(description="IMAGE if an image was provided, otherwise TEXT.")
    is_confident: bool = Field(description="Confident enough in Category and Severity to stop investigating.")
    confidence_notes: str | None = Field(default=None, max_length=500)


class ActionDecision(BaseModel):
    """Structured output for deciding which tool (if any) to use next."""

    action: Action
    reason: str = Field(max_length=300)
    search_category_hint: str | None = Field(default=None, description="Category display name to search related tickets for.")
    grouping_related_ticket_ids: list[str] | None = Field(default=None, description="Subset of previously found related ticket_ids to propose grouping.")
    question_text: str | None = None
    question_options: list[str] | None = None
    allow_free_text_fallback: bool = False


class AgentLLMClient(Protocol):
    def extract(
        self,
        *,
        description: str,
        image_urls: list[str],
        catalog_names: list[str],
        context_notes: list[str],
    ) -> ExtractionResult: ...

    def decide_next_action(
        self,
        *,
        description: str,
        extraction: ExtractionResult,
        available_actions: list[Action],
        related_tickets: list[dict[str, object]],
        grouping_eligible: bool,
        answer_notes: list[str] | None = None,
        ask_rounds_used: int = 0,
        max_ask_rounds: int = 3,
    ) -> ActionDecision: ...


_EXTRACTION_SYSTEM_PROMPT = """Bạn phân loại một phản ánh sự cố trong một khu chung cư, dựa trên mô tả bằng chữ của cư dân và ảnh hiện trường (nếu có).

Nhiệm vụ của bạn CHỈ là trích xuất dữ liệu có cấu trúc. Bạn không đưa ra kết luận cuối cùng về mức độ ưu tiên xử lý, không tự quyết định ticket có cần người duyệt thủ công hay không, và không tự tính số căn hộ bị ảnh hưởng — những việc đó do bước xử lý sau thực hiện, không thuộc phạm vi của bạn.

Quy tắc trích xuất:

- Category: chỉ chọn trong danh sách tên hiển thị được cung cấp, giữ đúng nguyên văn tên hiển thị. Không tự đặt tên category mới, không suy diễn category ngoài danh sách.
- Đánh giá category từ text và category từ ảnh HOÀN TOÀN ĐỘC LẬP với nhau — đừng để nhận định về bên này ảnh hưởng tới bên kia. Kết quả này sẽ được so sánh chéo ở bước sau để phát hiện mâu thuẫn; nếu bạn cố làm cho hai bên "khớp nhau" một cách gượng ép, việc kiểm tra đó sẽ mất tác dụng.
- Dấu hiệu nguy hiểm (red-flag): CHỈ đánh dấu khi tín hiệu thực sự đe dọa an toàn con người ngay lúc này — không phải mọi hư hỏng đều là red-flag. Ví dụ đủ điều kiện: khói, lửa; dây điện hở nằm ở vị trí người có thể vô tình chạm phải (ngang tầm tay, giữa lối đi, gần vũng nước); nước tràn diện rộng gây trơn trượt; người bất tỉnh/ngất xỉu; ẩu đả hoặc gây rối trật tự. Ví dụ KHÔNG tính là red-flag dù có vẻ hư hỏng nghiêm trọng: đèn hỏng/rơi với dây điện nằm gọn trong hộp đèn trên trần cao, không ai chạm tới được; vết nứt/ố tường khô; đồ vật hư hỏng thông thường không gây nguy hiểm tức thời. Đánh giá riêng cho text và riêng cho ảnh (nếu có). Khi tín hiệu RÕ RÀNG đáp ứng các ví dụ đủ điều kiện ở trên nhưng bạn không chắc mức độ nghiêm trọng cụ thể, hãy nghiêng về phía đánh dấu có nguy hiểm. Nhưng đừng đánh dấu nguy hiểm chỉ vì ảnh trông có vẻ hỏng hóc hay xuống cấp — sự xuống cấp không có nghĩa là nguy hiểm tức thời.
- Ảnh có thực sự liên quan tới một sự cố trong chung cư không: đánh giá nghiêm khắc. Một ảnh không liên quan (gửi nhầm, ảnh sản phẩm, ảnh không rõ chủ đề, ảnh của việc khác...) phải được đánh dấu KHÔNG liên quan, dù mô tả bằng chữ có rõ ràng và đầy đủ tới đâu.
- Text có tự nó đủ để hiểu sơ bộ vấn đề không: chỉ đánh giá riêng phần chữ, không dựa vào ảnh để "cứu" cho một mô tả quá sơ sài.
- Severity: luôn chọn đúng 1 trong 3 mức (Thấp/Vừa/Cao) một cách có chủ đích, dựa trên bằng chứng cụ thể quan sát được hoặc bằng những gì được miêu tả, nếu cảm thấy ảnh và cả text hoặc chỉ text không đủ khả năng xác định điều này, hãy chủ động hỏi lại để xác nhận. Tuyệt đối không để trống hay chọn Thấp chỉ vì thiếu thông tin. Nếu thực sự không đủ căn cứ để đoán mức độ, hãy thể hiện điều đó qua is_confident=false, không phải bằng cách âm thầm chọn Thấp.
- severity_source: chọn IMAGE nếu ticket có ảnh (ảnh được ưu tiên hơn khi có sẵn), chọn TEXT nếu không có ảnh.
- is_confident: chỉ true khi bạn thực sự tự tin về cả category và severity, đủ để dừng tìm hiểu thêm.
- is_confident khi mô tả chỉ nêu tên vấn đề mà không nêu tình trạng: severity phải dựa trên bằng chứng ĐANG CÓ — điều cư dân nói ra hoặc điều nhìn thấy trong ảnh — chứ không phải suy ra từ việc "loại sự cố này thường nghiêm trọng cỡ đó". Một mô tả chỉ gọi tên vấn đề ("Mất điện rồi", "Thang máy hỏng", "Nhà bẩn") mà không cho biết phạm vi ảnh hưởng, mức độ, hay nó cản trở sinh hoạt tới đâu, thì chưa đủ căn cứ: hãy đặt is_confident=false. Ảnh có thể bù cho phần này nếu nó thực sự cho thấy mức độ, nhưng một tấm ảnh chỉ xác nhận "đúng là có chuyện đó" thì không đủ.
- is_confident khi text và ảnh không chỉ về cùng một chuyện: nếu ticket có ảnh và tập category bạn rút ra từ text KHÔNG có category nào trùng với tập rút ra từ ảnh, thì phải đặt is_confident=false, kể cả khi bạn rất chắc chắn về từng bên một. Hai bên chỉ đúng một bên, hoặc cư dân đang nói về một việc khác với việc họ chụp — cả hai khả năng đều cần hỏi lại cho rõ chứ không thể tự chọn. Đây KHÔNG mâu thuẫn với quy tắc đánh giá độc lập ở trên: bạn vẫn rút ra từng bên một cách độc lập rồi mới nhìn vào kết quả để chấm mức tự tin, chứ không sửa bên này cho khớp bên kia.
- confidence_notes: nếu chưa tự tin, ghi ngắn gọn lý do cụ thể (ví dụ: mô tả mơ hồ giữa 2 category, ảnh mờ không thấy rõ mức độ nghiêm trọng...) để người đọc sau quyết định tiếp. Riêng trường hợp text và ảnh lệch nhau, hãy nêu rõ text đang chỉ về category nào và ảnh đang chỉ về category nào. Nếu đã tự tin, để trống.

Bạn có thể được gọi lại nhiều lần cho cùng 1 ticket khi có thêm thông tin mới (câu trả lời của cư dân, kết quả tra cứu ticket liên quan, ảnh mới...). Mỗi lần được gọi, hãy đánh giá lại TOÀN BỘ các trường trên từ đầu dựa trên mọi thông tin đang có tới thời điểm đó — đặc biệt là dấu hiệu nguy hiểm. Không giả định rằng vì lần trước không có nguy hiểm thì lần này cũng vậy."""

_DECISION_SYSTEM_PROMPT = """Bạn đang cân nhắc bước tiếp theo trước khi kết luận Category/Severity của một ticket phản ánh sự cố chung cư: dùng thêm một công cụ hỗ trợ, hoặc dừng lại nếu đã đủ tự tin.

Mỗi lượt, chọn ĐÚNG 1 action trong danh sách được phép — không chọn action ngoài danh sách, không chọn nhiều action cùng lúc.

- SEARCH_RELATED: tra cứu các ticket khác gần đây cùng category/tầng/vị trí, để có thêm ngữ cảnh khi đánh giá mức độ nghiêm trọng hoặc để phát hiện một vấn đề lặp lại dai dẳng ở cùng khu vực (có thể mở rộng xem cả ticket đã xử lý xong trong quá khứ, không chỉ ticket đang mở).
- PROPOSE_GROUPING: chỉ chọn khi category hiện tại là loại có khả năng lan vật lý thật qua kết cấu tòa nhà (rò nước hoặc chập điện), VÀ trong số ticket đã tra cứu được có ít nhất một ticket bạn thực sự tin là cùng một sự cố lan rộng với ticket hiện tại — không phải cứ trùng category/tầng/thời gian là gộp máy móc. Ví dụ: nhiều căn hộ cùng báo rò nước ở khu vực có khả năng chung một đường ống thì nên gộp; còn hai ticket rò nước trùng ngày nhưng có nguyên nhân rõ ràng độc lập (một do vòi nước trong nhà, một do thấm trần) thì không nên gộp dù điều kiện thời gian/vị trí khớp về mặt kỹ thuật. Nếu không có ticket nào thực sự phù hợp, đừng chọn action này — cứ để mặc định là không gộp.
- ASK_RESIDENT: chọn khi vẫn chưa đủ tự tin về category hoặc severity. Hỏi câu hỏi dạng trắc nghiệm (nút bấm) với các lựa chọn cụ thể, nhưng luôn cho phép cư dân chọn lựa chọn "khác" và nhập text tương ứng. Nếu điều chưa rõ nằm ở ảnh, hãy thêm một lựa chọn cho phép cư dân chụp lại ảnh khác. Đặc biệt: khi category rút ra từ text và category rút ra từ ảnh KHÔNG có điểm chung nào, đây là lý do mạnh để chọn ASK_RESIDENT — hãy hỏi cư dân đang muốn báo việc nào, và đưa chính hai category đó thành hai lựa chọn.
- CONCLUDE: chọn khi đã đủ tự tin để dừng lại và kết luận. Chuẩn của "đủ": bạn nêu được severity dựa trên điều cư dân đã nói hoặc điều nhìn thấy trong ảnh, chứ không phải dựa trên phỏng đoán về loại sự cố. Nếu để giữ nguyên mức severity hiện tại bạn phải giả định một điều mà không ai nói ra, thì chưa đủ — hãy hỏi. Đừng dùng thêm công cụ chỉ vì còn hạn mức; số lần dùng công cụ và số lượt hỏi cư dân đều bị giới hạn, nên chỉ dùng khi thực sự cần thêm thông tin.

Việc tính chính thức số căn hộ bị ảnh hưởng và việc kết luận mức ưu tiên cuối cùng không thuộc phạm vi quyết định của bạn — những việc đó được xử lý ở bước sau, không cần bạn xác nhận.

Riêng chuyện text và ảnh có chỉ về cùng một category hay không thì ngược lại: bước sau chỉ dùng nó để quyết định có đẩy ticket sang cho người duyệt tay hay không, chứ không hỏi lại cư dân giúp bạn. Nếu bạn thấy hai bên lệch nhau mà vẫn chọn CONCLUDE, ticket sẽ bị đẩy sang duyệt tay trong khi lẽ ra chỉ cần một câu hỏi là làm rõ được. Vì vậy hãy coi độ lệch đó là thông tin đầu vào cho quyết định của mình, đừng bỏ qua nó.

Một điều nữa về việc khi nào nên hỏi. Cư dân thường gõ rất ngắn, kiểu "Mất điện rồi" hay "Thang máy hỏng" — vừa đủ để biết chuyện gì, nhưng không đủ để biết chuyện đó lớn tới đâu. Với những mô tả như vậy, kể cả khi category đã rõ ràng và text với ảnh hoàn toàn khớp nhau, hãy chọn ASK_RESIDENT thay vì CONCLUDE. Category rõ không có nghĩa là severity rõ, mà severity mới là thứ quyết định ticket được xử lý nhanh hay chậm. Hỏi đúng một câu ngắn để biết phạm vi và mức ảnh hưởng gần như luôn đáng giá hơn là đoán.

Ngược lại, ĐỪNG hỏi khi mô tả đã tự nó nói lên mức độ, khi ảnh đã cho thấy rõ quy mô, hoặc khi cư dân đã trả lời câu hỏi trước đó và câu trả lời ấy đã giải quyết được điều bạn còn băn khoăn. Hỏi lại một điều đã được trả lời còn tệ hơn là không hỏi.

Phần "Những gì cư dân đã trả lời" trong dữ liệu gửi kèm là toàn bộ hội thoại đã diễn ra ở ticket này. Hãy đọc nó TRƯỚC KHI quyết định. Nếu điều bạn định hỏi đã có câu trả lời ở đó rồi thì đừng hỏi lại dù chỉ là diễn đạt khác đi — hãy dùng chính câu trả lời ấy và chuyển sang CONCLUDE, hoặc hỏi một điều khác hẳn nếu vẫn còn chỗ thực sự chưa rõ. Số lượt hỏi rất ít, tiêu một lượt vào câu đã biết đáp án là mất trắng."""


class OpenAIAgentLLMClient:
    """Default implementation backed by the configured chat model."""

    def __init__(self, llm=None) -> None:
        if llm is None:
            from src.services.llm import get_llm

            llm = get_llm()
        self._llm = llm

    def extract(
        self,
        *,
        description: str,
        image_urls: list[str],
        catalog_names: list[str],
        context_notes: list[str],
    ) -> ExtractionResult:
        structured = self._llm.with_structured_output(ExtractionResult)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    f"Danh mục Category hợp lệ: {', '.join(catalog_names)}\n\n"
                    f"Mô tả của cư dân: {description or '(không có mô tả)'}\n\n"
                    + ("\n".join(context_notes) if context_notes else "")
                ),
            }
        ]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        return structured.invoke(messages)

    def decide_next_action(
        self,
        *,
        description: str,
        extraction: ExtractionResult,
        available_actions: list[Action],
        related_tickets: list[dict[str, object]],
        grouping_eligible: bool,
        answer_notes: list[str] | None = None,
        ask_rounds_used: int = 0,
        max_ask_rounds: int = 3,
    ) -> ActionDecision:
        structured = self._llm.with_structured_output(ActionDecision)
        # Lịch sử hỏi–đáp phải nằm trong prompt này, không chỉ trong prompt
        # trích xuất. Thiếu nó, model không có cách nào biết mình đã hỏi gì và
        # sẽ hỏi lại đúng câu vừa được trả lời cho tới khi hết hạn mức — đã
        # xảy ra thật ở session 6dd9968c: ba vòng, cùng một câu hỏi phạm vi,
        # cư dân trả lời "Cả tầng" cả ba lần.
        conversation = (
            "\n".join(f"  - {note}" for note in answer_notes)
            if answer_notes
            else "  (chưa hỏi cư dân lần nào)"
        )
        prompt = (
            f"Mô tả: {description}\n"
            f"Trích xuất hiện tại: {extraction.model_dump()}\n"
            f"Action được phép chọn: {available_actions}\n"
            f"Category có được phép gộp (rò nước/chập điện) không: {grouping_eligible}\n"
            f"Ticket liên quan đã tra cứu được: {related_tickets}\n"
            f"Đã hỏi cư dân {ask_rounds_used}/{max_ask_rounds} lượt. Những gì cư dân đã trả lời:\n{conversation}\n"
        )
        messages = [
            {"role": "system", "content": _DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        decision = structured.invoke(messages)
        if decision.action not in available_actions:
            logger.warning("Agent chose disallowed action %s; forcing CONCLUDE.", decision.action)
            decision = decision.model_copy(update={"action": "CONCLUDE"})
        return decision
