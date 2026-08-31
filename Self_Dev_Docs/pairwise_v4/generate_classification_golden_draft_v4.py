"""Generate the human-review draft for the production V4 classifier contract.

This dataset intentionally stays separate from the legacy 116-case suite.  It
uses the current single-category ``UnifiedClassification`` schema and the
single-building category catalog.  Every generated row remains PENDING_REVIEW
until a human confirms it.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.llm_client import UnifiedClassification  # noqa: E402
from src.models.enums import Category  # noqa: E402

FIXTURE_DIR = HERE / "fixtures" / "images"
DATASET_VERSION = "classification-v4-draft-2026-08-29"

JSON_OUTPUT = HERE / "classification_golden_draft_v4.json"
JSONL_OUTPUT = HERE / "classification_golden_draft_v4.jsonl"
TSV_OUTPUT = HERE / "classification_golden_draft_v4.tsv"
SUMMARY_OUTPUT = HERE / "classification_golden_draft_summary_v4.json"

CATALOG = {
    "WATER": "Nước",
    "WALL_DAMP": "Thấm tường",
    "ELEVATOR": "Thang máy",
    "POWER_OUTAGE": "Mất điện",
    "SECURITY_SAFETY": "An ninh / An toàn",
    "NOISE": "Ồn ào",
    "LOCK_DOOR": "Khóa / cửa",
    "HVAC": "Điều hòa",
    "ODOR_HYGIENE": "Mùi / vệ sinh",
    "INTERNET_TV": "Internet / truyền hình",
    "COMMON_AREA_DAMAGE": "Hư hỏng khu vực chung",
}


def name(code: str | None) -> str | None:
    return CATALOG[code] if code else None


def expected(
    *,
    category: str | None,
    text_category: str | None,
    image_category: str | None,
    severity: str | None,
    red_flag: bool = False,
    understandable: bool = True,
    image_relevant: bool | None = None,
    location_consistent: bool = True,
    facts: list[str] | None = None,
    reason: str,
    question_kind: str = "NONE",
    question_text: str | None = None,
    category_options: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": name(category),
        "text_category": name(text_category),
        "image_category": name(image_category),
        "severity": severity,
        "red_flag": red_flag,
        "understandable": understandable,
        "image_relevant": image_relevant,
        "location_consistent": location_consistent,
        "incident_facts": facts or [],
        "ai_reason": reason,
        "question_kind": question_kind,
        "question_text": question_text,
        "category_options": [name(code) for code in category_options] if category_options else None,
    }


def image_path(fixture_id: str) -> str:
    matches = sorted(FIXTURE_DIR.glob(f"{fixture_id}.*"))
    if len(matches) != 1:
        raise AssertionError(f"{fixture_id}: expected exactly one fixture, found {len(matches)}")
    return matches[0].relative_to(REPO_ROOT).as_posix()


def relation(output: dict[str, Any], has_image: bool) -> str:
    text_category = output["text_category"]
    image_category = output["image_category"]
    if not has_image:
        return "TEXT_ONLY"
    if text_category and image_category and text_category == image_category:
        return "TEXT_IMAGE_AGREE"
    if text_category and image_category:
        return "TEXT_IMAGE_DISAGREE"
    if image_category:
        return "IMAGE_ONLY_EVIDENCE"
    if text_category:
        return "TEXT_ONLY_EVIDENCE"
    return "NO_CATEGORY_EVIDENCE"


def case(
    number: int,
    title: str,
    description: str,
    output: dict[str, Any],
    *,
    fixture_id: str | None = None,
    image_state: str = "NO_IMAGE",
    location_label: str = "Căn hộ",
    floor_label: str = "Tầng 8",
    unit_code: str | None = "0801",
    confirmed_category: str | None = None,
    conversation: list[dict[str, object]] | None = None,
    review_notes: list[str] | None = None,
) -> dict[str, Any]:
    paths = [image_path(fixture_id)] if fixture_id else []
    return {
        "case_id": f"CLS4-{number:03d}",
        "title": title,
        "review_status": "PENDING_REVIEW",
        "source": {
            "kind": "EXISTING_FIXTURE" if fixture_id else "SYNTHETIC_TEXT",
            "fixture_id": fixture_id,
        },
        "dimensions": {
            "input_mode": "TEXT_AND_IMAGE" if paths else "TEXT_ONLY",
            "image_state": image_state,
            "evidence_relation": relation(output, bool(paths)),
            "question_kind": output["question_kind"],
            "red_flag_source": (
                "TEXT_AND_IMAGE"
                if output["red_flag"] and output["text_category"] and output["image_category"]
                else "IMAGE"
                if output["red_flag"] and output["image_category"]
                else "TEXT"
                if output["red_flag"]
                else "NONE"
            ),
            "confirmed_category": bool(confirmed_category),
        },
        "input": {
            "description": description,
            "image_paths": paths,
            "catalog_snapshot_id": DATASET_VERSION,
            "catalog_names": list(CATALOG.values()),
            "location_label": location_label,
            "floor_label": floor_label,
            "unit_code": unit_code,
            "conversation": conversation or [],
            "confirmed_category": name(confirmed_category),
        },
        "expected_output": output,
        "review_notes": review_notes or [],
    }


def build_image_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(title: str, description: str, fixture_id: str, output: dict[str, Any], **kwargs: Any) -> None:
        rows.append(case(len(rows) + 1, title, description, output, fixture_id=fixture_id, **kwargs))

    add(
        "Tường hành lang bị thấm và đọng nước",
        "Chân tường hành lang tầng 6 bị thấm, bong sơn và có một vũng nước nhỏ trên sàn.",
        "IMG-A1-014",
        expected(
            category="WALL_DAMP",
            text_category="WALL_DAMP",
            image_category="WALL_DAMP",
            severity="MEDIUM",
            image_relevant=True,
            facts=["chân tường ẩm và bong sơn", "sàn có vũng nước nhỏ"],
            reason="Vệt ẩm và lớp sơn bong tập trung dọc chân tường cho thấy tình trạng thấm tường; phạm vi đã ảnh hưởng một đoạn tường và sàn.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 6",
        floor_label="Tầng 6",
        unit_code=None,
    )
    add(
        "Ống thoát nước điều hòa nhỏ giọt",
        "Ống thoát nước của điều hòa cạnh cửa ban công đang nhỏ giọt liên tục xuống sàn.",
        "IMG-A1-015",
        expected(
            category="HVAC",
            text_category="HVAC",
            image_category="HVAC",
            severity="MEDIUM",
            image_relevant=True,
            facts=["nước nhỏ từ ống bọc bảo ôn", "sàn bên dưới bị ướt"],
            reason="Nguồn nước được mô tả và nhìn thấy nằm ở đường ống của hệ thống điều hòa, nên vấn đề chính thuộc Điều hòa thay vì một nguồn cấp nước độc lập.",
        ),
        image_state="RELEVANT",
    )
    add(
        "Ảnh có điều hòa nhưng text yêu cầu xử lý vết thấm",
        "Tôi muốn xử lý vệt thấm chạy dọc tường; điều hòa vẫn hoạt động bình thường.",
        "IMG-A1-016",
        expected(
            category=None,
            text_category="WALL_DAMP",
            image_category="HVAC",
            severity="MEDIUM",
            image_relevant=True,
            facts=["tường có vệt ẩm dọc", "ảnh có dàn lạnh điều hòa"],
            reason="Phần chữ tập trung vào vết thấm nhưng ảnh đồng thời làm nổi bật hệ thống điều hòa, nên cần Cư dân xác nhận vấn đề muốn xử lý trong ticket này.",
            question_kind="CATEGORY_CONFIRMATION",
            question_text="Bạn muốn xử lý vết thấm tường hay sự cố của điều hòa trong phản ánh này? Nếu có cả hai, vui lòng chọn một vấn đề và tạo phản ánh riêng cho vấn đề còn lại.",
            category_options=["WALL_DAMP", "HVAC"],
        ),
        image_state="RELEVANT_AMBIGUOUS",
        review_notes=["Ảnh không chứng minh điều hòa bị hỏng; nhãn image_category=HVAC cần người duyệt xác nhận."],
    )
    add(
        "Cửa thang máy dừng giữa chừng",
        "Cửa thang máy tầng 8 chỉ mở một khe rồi đứng yên, không đóng hoặc mở tiếp.",
        "IMG-A1-017",
        expected(
            category="ELEVATOR",
            text_category="ELEVATOR",
            image_category="ELEVATOR",
            severity="MEDIUM",
            image_relevant=True,
            facts=["cửa thang máy mở một khe", "cửa không tiếp tục di chuyển"],
            reason="Mô tả và ảnh đều tập trung vào cửa thang máy dừng ở trạng thái mở một phần; chưa có thông tin người bị mắc kẹt nên không bật red flag.",
        ),
        image_state="RELEVANT_CONTEXT_REQUIRED",
        location_label="Thang máy tầng 8",
        unit_code=None,
        review_notes=["Ảnh tĩnh một mình không chứng minh cửa bị kẹt; nhãn dựa trên kết hợp text và ảnh."],
    )
    add(
        "Cửa căn hộ cạ nền",
        "Cửa căn hộ bị cạ nền và không thể đóng kín từ tối qua.",
        "IMG-A1-018",
        expected(
            category="LOCK_DOOR",
            text_category="LOCK_DOOR",
            image_category="LOCK_DOOR",
            severity="MEDIUM",
            image_relevant=True,
            facts=["cửa không đóng kín", "mép dưới cửa sát nền"],
            reason="Sự cố trực tiếp liên quan đến khả năng đóng cửa căn hộ, thuộc nhóm Khóa / cửa; ảnh không cho thấy nguy hiểm tức thời.",
        ),
        image_state="RELEVANT_CONTEXT_REQUIRED",
        review_notes=["Hư hỏng không hiện rõ trong ảnh; cần dùng text để kết luận."],
    )
    add(
        "Cả hành lang mất điện",
        "Toàn bộ đèn và ổ điện ở hành lang tầng 9 đều mất nguồn, chỉ còn biển thoát hiểm sáng.",
        "IMG-A1-019",
        expected(
            category="POWER_OUTAGE",
            text_category="POWER_OUTAGE",
            image_category="POWER_OUTAGE",
            severity="MEDIUM",
            image_relevant=True,
            facts=["hành lang tối", "biển thoát hiểm vẫn sáng"],
            reason="Mất nguồn trên cả đèn và ổ điện của hành lang phù hợp với Mất điện, không chỉ là một bóng đèn riêng lẻ bị hỏng.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 9",
        floor_label="Tầng 9",
        unit_code=None,
    )
    add(
        "Vết nứt nhưng vị trí đã chọn là khu vực chung",
        "Bức tường trong phòng ngủ căn hộ có một vết nứt dài cần kiểm tra.",
        "IMG-A1-020",
        expected(
            category="COMMON_AREA_DAMAGE",
            text_category="COMMON_AREA_DAMAGE",
            image_category="COMMON_AREA_DAMAGE",
            severity="MEDIUM",
            image_relevant=True,
            location_consistent=False,
            facts=["tường có vết nứt dài", "mô tả nói vị trí trong phòng ngủ"],
            reason="Vết nứt là một hư hỏng vật lý nhưng vị trí trong mô tả là phòng ngủ, không khớp với vị trí khu sinh hoạt chung đã chọn.",
            question_kind="LOCATION_CONFIRMATION",
            question_text="Bạn đã chọn Khu sinh hoạt chung nhưng mô tả cho biết vết nứt nằm trong phòng ngủ. Vui lòng xác nhận lại vị trí sự cố.",
        ),
        image_state="RELEVANT",
        location_label="Khu sinh hoạt chung tầng 1",
        floor_label="Tầng 1",
        unit_code=None,
        review_notes=["Catalog hiện không có Category riêng cho nứt tường trong căn hộ; nhãn COMMON_AREA_DAMAGE cần BQL xác nhận."],
    )
    add(
        "Quạt thông gió bung mặt che",
        "Mặt che quạt thông gió trần nhà vệ sinh đang bung xuống và rung khi bật quạt.",
        "IMG-A1-021",
        expected(
            category="HVAC",
            text_category="HVAC",
            image_category="HVAC",
            severity="HIGH",
            image_relevant=True,
            facts=["mặt che quạt thông gió bung khỏi trần", "mặt che có thể rơi"],
            reason="Thiết bị thông gió thuộc hệ thống Điều hòa/thông gió và phần che đang có nguy cơ rơi; chưa có dấu hiệu đe dọa trực tiếp đang diễn ra để bật red flag.",
        ),
        image_state="RELEVANT",
    )
    add(
        "Rò nước dưới bồn rửa",
        "Ống xả dưới bồn rửa rỉ nước, đáy tủ đã ướt và có vết bẩn đen.",
        "IMG-A1-022",
        expected(
            category="WATER",
            text_category="WATER",
            image_category="WATER",
            severity="MEDIUM",
            image_relevant=True,
            facts=["ống xả có giọt nước", "đáy tủ bị ướt"],
            reason="Nguồn sự cố chính là nước rò từ hệ thống xả dưới bồn; vết bẩn là hậu quả đi kèm nên Category cuối cùng là Nước.",
        ),
        image_state="RELEVANT",
    )
    add(
        "Aptomat nhảy và căn hộ mất điện",
        "Aptomat tổng liên tục nhảy, hiện toàn bộ căn hộ không có điện.",
        "IMG-A1-023",
        expected(
            category="POWER_OUTAGE",
            text_category="POWER_OUTAGE",
            image_category="POWER_OUTAGE",
            severity="MEDIUM",
            image_relevant=True,
            facts=["aptomat tổng liên tục nhảy", "toàn bộ căn hộ mất điện"],
            reason="Kết quả trực tiếp của sự cố là toàn bộ căn hộ mất nguồn điện; ảnh cho thấy đúng tủ aptomat liên quan nhưng không có khói, lửa hay dây trần hở.",
        ),
        image_state="RELEVANT_CONTEXT_REQUIRED",
    )
    add(
        "Bảng gọi thang máy báo lỗi E",
        "Bảng gọi thang máy tầng 5 chỉ hiện chữ E và thang không nhận lệnh gọi.",
        "IMG-A1-024",
        expected(
            category="ELEVATOR",
            text_category="ELEVATOR",
            image_category="ELEVATOR",
            severity="MEDIUM",
            image_relevant=True,
            facts=["bảng gọi hiển thị E", "thang không nhận lệnh gọi"],
            reason="Mã hiển thị bất thường và việc không nhận lệnh đều là bằng chứng của sự cố Thang máy; không có người mắc kẹt.",
        ),
        image_state="RELEVANT",
        location_label="Sảnh thang máy tầng 5",
        floor_label="Tầng 5",
        unit_code=None,
    )
    add(
        "Ảnh mờ nhưng còn nhận ra vòi nước",
        "Vòi rửa đang chảy dù đã khóa hết cỡ.",
        "IMG-A1-025",
        expected(
            category="WATER",
            text_category="WATER",
            image_category="WATER",
            severity="LOW",
            image_relevant=True,
            facts=["vòi không khóa được", "ảnh mờ nhưng còn thấy vòi nước"],
            reason="Phần chữ mô tả rõ vòi không ngắt nước và ảnh vẫn nhận ra được khu vực vòi, đủ để xếp vào Nước ở mức ảnh hưởng cục bộ.",
        ),
        image_state="BLURRY_BUT_USABLE",
    )
    add(
        "Ảnh quá mờ cho phản ánh Internet",
        "Router trong căn hộ mất tín hiệu từ sáng, cả Wi-Fi và truyền hình đều không dùng được.",
        "IMG-A1-026",
        expected(
            category="INTERNET_TV",
            text_category="INTERNET_TV",
            image_category=None,
            severity="MEDIUM",
            image_relevant=False,
            facts=["Wi-Fi và truyền hình mất tín hiệu", "ảnh không thể hiện được thiết bị"],
            reason="Mô tả xác định rõ sự cố Internet / truyền hình, còn ảnh quá mờ nên không được dùng làm bằng chứng Category.",
        ),
        image_state="UNREADABLE",
        review_notes=["Production hiện không có image_quality field; ảnh quá mờ được biểu diễn bằng image_relevant=false và cần người duyệt xác nhận semantics."],
    )
    add(
        "Khóa cửa phòng kỹ thuật bị kẹt",
        "Khóa cửa phòng kỹ thuật tầng 7 bị kẹt, chìa cắm vào nhưng không xoay được.",
        "IMG-A1-027",
        expected(
            category="LOCK_DOOR",
            text_category="LOCK_DOOR",
            image_category="LOCK_DOOR",
            severity="MEDIUM",
            image_relevant=True,
            facts=["khóa cửa phòng kỹ thuật không xoay được"],
            reason="Vấn đề được mô tả trực tiếp ở khóa cửa và ảnh cho thấy đúng cửa phòng kỹ thuật liên quan.",
        ),
        image_state="RELEVANT_CONTEXT_REQUIRED",
        location_label="Phòng kỹ thuật tầng 7",
        floor_label="Tầng 7",
        unit_code=None,
    )
    add(
        "Ổ cắm lỏng nhưng chưa có khói lửa",
        "Ổ cắm này bị lỏng và có vết sẫm cũ, hiện không nóng, không có khói hay tia lửa.",
        "IMG-A1-028",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="MEDIUM",
            image_relevant=True,
            facts=["ổ cắm bị lỏng", "không có khói, lửa hoặc tia điện hiện tại"],
            reason="Ổ cắm lỏng là rủi ro an toàn cần kiểm tra, nhưng các dấu hiệu nguy hiểm tức thời đều được phủ định nên không bật red flag.",
        ),
        image_state="BLURRY_BUT_USABLE",
        review_notes=["Catalog mới không có Category điện; draft ánh xạ ổ cắm nguy hiểm sang SECURITY_SAFETY."],
    )
    add(
        "Ảnh phong cảnh không liên quan đến điều hòa",
        "Điều hòa phòng ngủ chạy nhưng không làm lạnh.",
        "IMG-A1-029",
        expected(
            category="HVAC",
            text_category="HVAC",
            image_category=None,
            severity="MEDIUM",
            image_relevant=False,
            facts=["điều hòa không làm lạnh", "ảnh không thể hiện sự cố trong căn hộ"],
            reason="Category Điều hòa được xác định từ mô tả; ảnh chụp quang cảnh bên ngoài không cung cấp bằng chứng cho sự cố.",
        ),
        image_state="IRRELEVANT",
    )
    add(
        "Ảnh selfie không liên quan đến đèn hành lang",
        "Một cụm đèn ở hành lang tầng 4 không sáng.",
        "IMG-A1-030",
        expected(
            category="COMMON_AREA_DAMAGE",
            text_category="COMMON_AREA_DAMAGE",
            image_category=None,
            severity="LOW",
            image_relevant=False,
            facts=["cụm đèn hành lang không sáng", "ảnh là ảnh cá nhân không liên quan"],
            reason="Đèn hành lang là tài sản khu vực chung nên thuộc Hư hỏng khu vực chung; ảnh selfie không liên quan và không được dùng để phân loại.",
        ),
        image_state="IRRELEVANT",
        location_label="Hành lang tầng 4",
        floor_label="Tầng 4",
        unit_code=None,
    )
    add(
        "Ảnh tờ giấy báo thang máy hỏng",
        "Tôi chỉ nhận được thông báo trong ảnh, chưa biết thang bị lỗi gì.",
        "IMG-A1-031",
        expected(
            category="ELEVATOR",
            text_category=None,
            image_category="ELEVATOR",
            severity=None,
            image_relevant=True,
            facts=["ảnh ghi thang máy không hoạt động"],
            reason="Ảnh cung cấp Category Thang máy nhưng không cho biết phạm vi ảnh hưởng hay trạng thái vận hành cụ thể để xác định mức độ.",
            question_kind="SEVERITY_CONFIRMATION",
            question_text="Thang máy ngừng hoàn toàn hay vẫn hoạt động nhưng có dấu hiệu bất thường?",
        ),
        image_state="TEXT_IN_IMAGE",
        location_label="Sảnh thang máy",
        unit_code=None,
        review_notes=["Cần BQL chốt ảnh chụp tờ giấy có được coi là image_relevant=true hay không."],
    )
    add(
        "Mèo ướt và mùi hôi ở hành lang",
        "Có một con mèo ướt ở góc hành lang và khu vực đang có mùi hôi khó chịu.",
        "IMG-A1-032",
        expected(
            category="ODOR_HYGIENE",
            text_category="ODOR_HYGIENE",
            image_category="ODOR_HYGIENE",
            severity="LOW",
            image_relevant=True,
            facts=["động vật ướt ở hành lang", "khu vực có mùi hôi theo mô tả"],
            reason="Vấn đề cần xử lý là tình trạng vệ sinh và mùi tại khu vực chung; ảnh cho thấy đúng nguồn có thể liên quan nhưng không tự chứng minh được mùi.",
        ),
        image_state="RELEVANT_CONTEXT_REQUIRED",
        location_label="Hành lang tầng 3",
        floor_label="Tầng 3",
        unit_code=None,
    )
    add(
        "Khói đen từ tủ điện hành lang",
        "Khói đen đang bốc ra từ tủ điện hành lang.",
        "IMG-A3-008",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["khói đen bốc từ tủ điện"],
            reason="Khói đang bốc ra từ thiết bị điện tại hành lang là nguy hiểm trực tiếp tới con người, cần xử lý khẩn cấp theo An ninh / An toàn.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 8",
        unit_code=None,
    )
    add(
        "Lửa lớn trong nồi trên bếp",
        "Nồi trên bếp đang bốc lửa lớn và có khói.",
        "IMG-A3-009",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["lửa lớn bốc khỏi nồi", "có khói trong bếp"],
            reason="Lửa và khói đang hiện diện trong căn hộ là nguy hiểm tức thời nên phải bật red flag và xếp An ninh / An toàn.",
        ),
        image_state="RELEVANT",
        location_label="Bếp căn hộ",
    )
    add(
        "Dây điện trần hở trên trần hành lang",
        "Dây điện trên trần hành lang bị tuột khỏi đế và đang để đầu dây hở.",
        "IMG-A3-010",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["đầu dây điện trần hở", "thiết bị trần đã rơi khỏi vị trí"],
            reason="Dây điện trần hở là một dấu hiệu nguy hiểm trực tiếp được contract liệt kê, nên phải bật red flag.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 12",
        floor_label="Tầng 12",
        unit_code=None,
    )
    add(
        "Vũng nước ở bãi xe chưa ngập diện rộng",
        "Bãi xe có một vùng nước đọng gần lối đi, nước chưa lan hết mặt sàn.",
        "IMG-A3-011",
        expected(
            category="WATER",
            text_category="WATER",
            image_category="WATER",
            severity="MEDIUM",
            image_relevant=True,
            facts=["nước đọng trên một phần sàn bãi xe"],
            reason="Nước mới đọng cục bộ ở bãi xe và chưa tạo tình trạng ngập diện rộng, vì vậy thuộc Nước nhưng chưa đủ điều kiện red flag.",
        ),
        image_state="RELEVANT",
        location_label="Bãi đỗ xe tầng hầm",
        floor_label="Tầng hầm",
        unit_code=None,
    )
    add(
        "Hành lang ngập diện rộng có người đi lại",
        "Nước đã ngập gần hết chiều rộng hành lang và vẫn đang dâng.",
        "IMG-A3-012",
        expected(
            category="WATER",
            text_category="WATER",
            image_category="WATER",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["nước phủ gần toàn bộ chiều rộng hành lang", "có người ở khu vực ngập"],
            reason="Ngập diện rộng tại lối đi có người hiện diện là nguy hiểm trực tiếp, nên bật red flag và xử lý theo Category Nước.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 11",
        floor_label="Tầng 11",
        unit_code=None,
    )
    add(
        "Lửa trong tủ điện đang mở",
        "Trong tủ điện hành lang đang có lửa cháy.",
        "IMG-A3-013",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["lửa đang cháy trong tủ điện", "tủ điện mở ra hành lang"],
            reason="Lửa đang cháy trong tủ điện ở khu vực cư dân đi lại là nguy hiểm tức thời, bắt buộc bật red flag.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 10",
        floor_label="Tầng 10",
        unit_code=None,
    )
    add(
        "Nước phủ kín chiều dài hành lang",
        "Nước tràn nhanh và đã phủ gần kín toàn bộ hành lang tầng 10.",
        "IMG-A3-014",
        expected(
            category="WATER",
            text_category="WATER",
            image_category="WATER",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["nước phủ diện rộng trên hành lang", "dòng nước kéo dài hết lối đi"],
            reason="Ảnh và mô tả đều cho thấy ngập lụt diện rộng ở lối đi chung, đáp ứng điều kiện red flag của contract.",
        ),
        image_state="RELEVANT",
        location_label="Hành lang tầng 10",
        floor_label="Tầng 10",
        unit_code=None,
    )
    add(
        "Đầu dây điện bị hở khỏi tường",
        "Hai đầu dây điện đang thò ra khỏi tường và chưa được bọc kín.",
        "IMG-A3-017-SUPPLEMENT",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["đầu dây điện trần hở khỏi tường"],
            reason="Dây điện trần hở là nguy hiểm trực tiếp theo contract, nên phải bật red flag dù chưa thấy khói hay lửa.",
        ),
        image_state="RELEVANT",
    )
    add(
        "Thông báo cháy lớn chỉ xuất hiện trong ảnh",
        "Tôi nhận được ảnh này từ hàng xóm và chưa có thêm thông tin.",
        "IMG-A3-024",
        expected(
            category="SECURITY_SAFETY",
            text_category=None,
            image_category="SECURITY_SAFETY",
            severity="HIGH",
            red_flag=True,
            image_relevant=True,
            facts=["ảnh ghi thông báo cháy lớn ở tầng 3"],
            reason="Nội dung trong ảnh báo một đám cháy lớn tại tòa nhà; theo quy tắc, chữ trong ảnh là dữ liệu sự cố và phải kích hoạt red flag.",
        ),
        image_state="TEXT_IN_IMAGE",
        location_label="Tầng 3",
        floor_label="Tầng 3",
        unit_code=None,
        review_notes=["Cần BQL xác nhận có chấp nhận ảnh chụp tờ giấy làm bằng chứng red flag hay yêu cầu bằng chứng hiện trường."],
    )
    add(
        "Ổ cắm có vết ố cũ nhưng không nguy hiểm hiện tại",
        "Ổ cắm có vết ố cũ nhưng hiện không nóng, không khói, không tia lửa và vẫn hoạt động.",
        "IMG-A3-025",
        expected(
            category="SECURITY_SAFETY",
            text_category="SECURITY_SAFETY",
            image_category="SECURITY_SAFETY",
            severity="LOW",
            image_relevant=True,
            facts=["ổ cắm có vết ố cũ", "không có dấu hiệu nguy hiểm hiện tại"],
            reason="Ổ cắm cần được kiểm tra về an toàn nhưng mọi dấu hiệu nguy hiểm tức thời đều bị phủ định, nên không bật red flag.",
        ),
        image_state="RELEVANT",
        review_notes=["Catalog mới không có Category điện; draft ánh xạ ổ cắm sang SECURITY_SAFETY."],
    )
    return rows


def build_text_cases(start: int) -> list[dict[str, Any]]:
    specs = [
        (
            "Vòi cấp nước rò liên tục",
            "Van dưới bồn rửa rò nước thành dòng nhỏ từ sáng.",
            expected(category="WATER", text_category="WATER", image_category=None, severity="MEDIUM", facts=["van dưới bồn rửa rò nước"], reason="Mô tả xác định rõ nguồn nước đang rò liên tục nên thuộc Category Nước."),
        ),
        (
            "Trần phòng ngủ bị thấm",
            "Trần phòng ngủ xuất hiện mảng ẩm lan rộng và sơn bắt đầu bong.",
            expected(category="WALL_DAMP", text_category="WALL_DAMP", image_category=None, severity="MEDIUM", facts=["mảng ẩm trên trần lan rộng", "sơn bắt đầu bong"], reason="Dấu hiệu chính là ẩm và bong sơn trên bề mặt trần, phù hợp với Thấm tường."),
        ),
        (
            "Thang máy rung mạnh",
            "Thang máy rung mạnh và phát tiếng va đập khi đi qua tầng 7, không có ai mắc kẹt.",
            expected(category="ELEVATOR", text_category="ELEVATOR", image_category=None, severity="HIGH", facts=["thang máy rung mạnh", "có tiếng va đập", "không có người mắc kẹt"], reason="Rung mạnh và tiếng va đập cho thấy sự cố vận hành nghiêm trọng của Thang máy nhưng chưa có red flag vì không có người mắc kẹt."),
        ),
        (
            "Căn hộ mất điện toàn bộ",
            "Toàn bộ căn hộ mất điện trong khi hành lang bên ngoài vẫn có điện.",
            expected(category="POWER_OUTAGE", text_category="POWER_OUTAGE", image_category=None, severity="MEDIUM", facts=["toàn bộ căn hộ mất điện", "hành lang vẫn có điện"], reason="Mất nguồn chỉ trong căn hộ vẫn thuộc Category Mất điện và chưa có dấu hiệu nguy hiểm tức thời."),
        ),
        (
            "Người lạ cố phá cửa",
            "Có người lạ đang đập mạnh và cố cạy cửa căn hộ của tôi.",
            expected(category="SECURITY_SAFETY", text_category="SECURITY_SAFETY", image_category=None, severity="HIGH", red_flag=True, facts=["người lạ đang cố cạy cửa căn hộ"], reason="Hành vi xâm nhập đang diễn ra đe dọa trực tiếp an toàn cư dân, nên bật red flag và xếp An ninh / An toàn."),
        ),
        (
            "Nhạc lớn kéo dài ban đêm",
            "Căn bên cạnh mở nhạc rất lớn từ 23 giờ đến hơn 1 giờ sáng.",
            expected(category="NOISE", text_category="NOISE", image_category=None, severity="MEDIUM", facts=["nhạc lớn kéo dài sau 23 giờ"], reason="Phản ánh tập trung vào tiếng nhạc lớn từ căn hộ lân cận nên thuộc Category Ồn ào."),
        ),
        (
            "Chìa khóa gãy trong ổ",
            "Chìa khóa bị gãy trong ổ nên cửa căn hộ không thể mở.",
            expected(category="LOCK_DOOR", text_category="LOCK_DOOR", image_category=None, severity="HIGH", facts=["chìa khóa gãy trong ổ", "cửa không thể mở"], reason="Cư dân không thể mở cửa do chìa gãy trong ổ, là sự cố Khóa / cửa gây cản trở lớn."),
        ),
        (
            "Điều hòa không làm lạnh",
            "Điều hòa chạy liên tục nhưng phòng vẫn nóng và không có hơi lạnh.",
            expected(category="HVAC", text_category="HVAC", image_category=None, severity="MEDIUM", facts=["điều hòa chạy nhưng không có hơi lạnh"], reason="Thiết bị vẫn chạy nhưng mất chức năng làm lạnh, phù hợp trực tiếp với Category Điều hòa."),
        ),
        (
            "Rác gây mùi ở hành lang",
            "Túi rác bị để ngoài hành lang hai ngày và đang bốc mùi rất khó chịu.",
            expected(category="ODOR_HYGIENE", text_category="ODOR_HYGIENE", image_category=None, severity="MEDIUM", facts=["rác để hai ngày ở hành lang", "có mùi khó chịu"], reason="Nguồn phản ánh là rác và mùi tại khu vực chung nên thuộc Mùi / vệ sinh."),
        ),
        (
            "Internet mất tín hiệu nhưng chưa rõ ảnh hưởng",
            "Internet trong căn hộ có vấn đề.",
            expected(category="INTERNET_TV", text_category="INTERNET_TV", image_category=None, severity=None, facts=["Internet trong căn hộ có vấn đề"], reason="Category Internet / truyền hình đã rõ nhưng mô tả chưa cho biết mất hoàn toàn hay chỉ chập chờn, nên chưa đủ căn cứ chốt severity.", question_kind="SEVERITY_CONFIRMATION", question_text="Internet mất hoàn toàn hay chỉ chập chờn, và có ảnh hưởng tới tất cả thiết bị trong căn hộ không?"),
        ),
        (
            "Tay vịn cầu thang khu chung bị bung",
            "Một đoạn tay vịn cầu thang bộ tầng 6 bị bung khỏi tường.",
            expected(category="COMMON_AREA_DAMAGE", text_category="COMMON_AREA_DAMAGE", image_category=None, severity="HIGH", facts=["tay vịn cầu thang bộ bung khỏi tường"], reason="Tay vịn là tài sản khu vực chung và tình trạng bung khỏi tường có thể gây tai nạn, nên xếp Hư hỏng khu vực chung ở mức cao."),
        ),
        (
            "Phản ánh không hiểu được",
            "Nó lại bị như hôm trước rồi, xử lý giúp.",
            expected(category=None, text_category=None, image_category=None, severity=None, understandable=False, facts=[], reason="Mô tả không cho biết vật gì bị hỏng, biểu hiện gì hoặc vị trí cụ thể nên chưa thể hiểu vấn đề."),
        ),
        (
            "Giữ Category Cư dân đã xác nhận",
            "Tôi đã chọn xử lý điều hòa; ảnh cũ trước đó có vết thấm nhưng điều hòa vẫn không lạnh.",
            expected(category="HVAC", text_category="HVAC", image_category=None, severity="MEDIUM", facts=["Cư dân đã chọn xử lý điều hòa", "điều hòa không lạnh"], reason="Cư dân đã xác nhận vấn đề cần xử lý là Điều hòa, nên Category phải được giữ nguyên và không hỏi lại về vết thấm cũ."),
        ),
    ]
    rows = []
    for offset, (title, description, output) in enumerate(specs):
        confirmed = "HVAC" if title == "Giữ Category Cư dân đã xác nhận" else None
        rows.append(case(start + offset, title, description, output, confirmed_category=confirmed))
    return rows


def build_cases() -> list[dict[str, Any]]:
    image_cases = build_image_cases()
    return [*image_cases, *build_text_cases(len(image_cases) + 1)]


def validate_cases(cases: list[dict[str, Any]]) -> None:
    assert set(CATALOG) == {item.value for item in Category}
    assert len(cases) == 42, len(cases)
    assert len({item["case_id"] for item in cases}) == len(cases)
    assert all(item["review_status"] == "PENDING_REVIEW" for item in cases)

    used_fixtures: list[str] = []
    covered_codes: set[str] = set()
    for item in cases:
        output = item["expected_output"]
        UnifiedClassification.model_validate(output)
        paths = item["input"]["image_paths"]
        if paths:
            assert output["image_relevant"] is not None, item["case_id"]
        else:
            assert output["image_category"] is None, item["case_id"]
            assert output["image_relevant"] is None, item["case_id"]
        for path in paths:
            assert (REPO_ROOT / path).is_file(), (item["case_id"], path)
        if fixture_id := item["source"]["fixture_id"]:
            used_fixtures.append(fixture_id)
        for field in ("category", "text_category", "image_category"):
            value = output[field]
            if value:
                assert value in CATALOG.values(), (item["case_id"], field, value)
                covered_codes.add(next(code for code, display in CATALOG.items() if display == value))
        for value in output.get("category_options") or []:
            assert value in CATALOG.values(), (item["case_id"], value)
            covered_codes.add(next(code for code, display in CATALOG.items() if display == value))

    available_fixtures = {path.stem for path in FIXTURE_DIR.iterdir() if path.is_file()}
    assert set(used_fixtures) == available_fixtures
    assert len(used_fixtures) == len(set(used_fixtures)) == 29
    assert covered_codes == set(CATALOG), sorted(set(CATALOG) - covered_codes)


def write_outputs(cases: list[dict[str, Any]]) -> None:
    payload = {
        "dataset_version": DATASET_VERSION,
        "status": "PENDING_HUMAN_REVIEW",
        "contract_source": "src/agents/llm_client.py:UnifiedClassification",
        "catalog_source": "alembic/versions/5a6b7c8d9e0f_single_building_catalog.py",
        "catalog_snapshot": [{"code": code, "display_name": display} for code, display in CATALOG.items()],
        "total_cases": len(cases),
        "cases": cases,
    }
    JSON_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    JSONL_OUTPUT.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n", encoding="utf-8")
    with TSV_OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "title", "review_status", "dimensions", "input", "expected_output", "review_notes"],
            delimiter="\t",
        )
        writer.writeheader()
        for item in cases:
            writer.writerow(
                {
                    "case_id": item["case_id"],
                    "title": item["title"],
                    "review_status": item["review_status"],
                    "dimensions": json.dumps(item["dimensions"], ensure_ascii=False),
                    "input": json.dumps(item["input"], ensure_ascii=False),
                    "expected_output": json.dumps(item["expected_output"], ensure_ascii=False),
                    "review_notes": json.dumps(item["review_notes"], ensure_ascii=False),
                }
            )
    summary = {
        "dataset_version": DATASET_VERSION,
        "status": "PENDING_HUMAN_REVIEW",
        "total_cases": len(cases),
        "image_cases": sum(bool(item["input"]["image_paths"]) for item in cases),
        "text_only_cases": sum(not item["input"]["image_paths"] for item in cases),
        "fixtures_used": len({item["source"]["fixture_id"] for item in cases if item["source"]["fixture_id"]}),
        "question_kinds": dict(Counter(item["expected_output"]["question_kind"] for item in cases)),
        "red_flag_cases": sum(item["expected_output"]["red_flag"] for item in cases),
        "final_category_coverage": dict(
            Counter(item["expected_output"]["category"] or "<null>" for item in cases)
        ),
        "cases_with_review_notes": [item["case_id"] for item in cases if item["review_notes"]],
    }
    SUMMARY_OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    cases = build_cases()
    validate_cases(cases)
    write_outputs(cases)
    print(f"Generated {len(cases)} classification cases ({sum(bool(item['input']['image_paths']) for item in cases)} with images)")


if __name__ == "__main__":
    main()
