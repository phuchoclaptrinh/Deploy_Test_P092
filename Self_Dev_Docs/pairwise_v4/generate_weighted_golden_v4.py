from __future__ import annotations

import csv
import itertools
import json
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

SEED = 9204
OUTPUT_DIR = Path(__file__).resolve().parent
JSON_OUTPUT = OUTPUT_DIR / "weighted_golden_cases_v4.json"
TSV_OUTPUT = OUTPUT_DIR / "weighted_golden_cases_v4.tsv"
SUMMARY_OUTPUT = OUTPUT_DIR / "weighted_golden_summary_v4.json"


@dataclass(frozen=True)
class Cluster:
    code: str
    name: str
    weight_percent: int
    quota: int
    dimensions: dict[str, tuple[str, ...]]
    is_valid: Callable[[dict[str, str]], bool]
    fixed_values: dict[str, str]


def always_valid(_: dict[str, str]) -> bool:
    return True


def valid_agent_content(case: dict[str, str]) -> bool:
    clarity = case["Mức độ dễ hiểu"]
    completeness = case["Mức độ đầy đủ thông tin"]
    image = case["Trạng thái ảnh"]
    severity = case["Mức nghiêm trọng hiệu lực"]
    tone = case["Quan hệ giữa giọng văn và sự cố"]
    text_count = case["Số Category trong text"]
    image_count = case["Số Category trong ảnh"]
    relation = case["Quan hệ Category giữa text và ảnh"]

    if clarity == "Rõ ràng" and completeness == "Thiếu thông tin cốt lõi":
        return False
    if clarity == "Hiểu ý chính nhưng thiếu chi tiết" and completeness == "Đủ thông tin":
        return False
    if clarity == "Không hiểu được vấn đề" and completeness != "Thiếu thông tin cốt lõi":
        return False

    if image == "Không có ảnh":
        if image_count != "Không có ảnh" or relation != "Chỉ có text":
            return False
    else:
        if image_count == "Không có ảnh" or relation == "Chỉ có text":
            return False

    if image in {"Ảnh mờ", "Ảnh không liên quan"}:
        if image_count != "Không xác định được" or relation != "Không đủ dữ liệu so sánh":
            return False

    if relation in {"Có đúng một Category chung", "Không có Category chung"}:
        if text_count == "Không xác định được" or image_count == "Không xác định được":
            return False
    if relation == "Có nhiều Category chung":
        if text_count != "Nhiều Category" or image_count != "Nhiều Category":
            return False
    if relation == "Không đủ dữ liệu so sánh":
        if not (
            text_count == "Không xác định được"
            or image_count == "Không xác định được"
            or image in {"Ảnh mờ", "Ảnh không liên quan"}
        ):
            return False

    if tone == "Giọng khẩn cấp nhưng sự cố nhẹ" and severity != "Thấp":
        return False
    if tone == "Giọng bình thường nhưng sự cố nghiêm trọng" and severity != "Cao":
        return False
    return True


def valid_agent_grouping(case: dict[str, str]) -> bool:
    issue_type = case["Loại vấn đề"]
    evidence = case["Bằng chứng gộp cụm"]
    if issue_type in {"Thấm tường", "Category khác"}:
        return evidence == "Không có bằng chứng"
    if evidence == "Hợp lệ trong cùng tầng hoặc tầng liền kề và không quá ba ngày":
        return issue_type in {"Rò nước", "Chập điện"}
    return True


def valid_agent_interaction(case: dict[str, str]) -> bool:
    completeness = case["Mức độ đầy đủ thông tin"]
    image = case["Trạng thái ảnh"]
    danger = case["Nguồn dấu hiệu nguy hiểm"]
    interaction = case["Kịch bản tương tác Cư dân"]

    if danger in {"Chỉ trong ảnh", "Có trong text và ảnh"} and image != "Ảnh rõ và liên quan":
        return False
    if danger == "Có trong câu trả lời bổ sung" and interaction != "Đã trả lời và bổ sung dấu hiệu nguy hiểm":
        return False
    if interaction == "Đã trả lời và bổ sung dấu hiệu nguy hiểm" and danger != "Có trong câu trả lời bổ sung":
        return False
    if interaction == "Không cần hỏi" and completeness != "Đủ thông tin":
        return False
    if interaction != "Không cần hỏi" and completeness == "Đủ thông tin":
        return False
    if interaction in {
        "Đã trả lời và làm rõ Category",
        "Đã trả lời nhưng vẫn không đủ thông tin",
        "Không trả lời",
    } and danger == "Có trong câu trả lời bổ sung":
        return False
    return True


def valid_direct(case: dict[str, str]) -> bool:
    if case["Tổng số ticket riêng biệt"] == "21":
        return case["Kịch bản ứng viên và mô hình"] == "Không có ứng viên"
    return True


CLUSTERS = (
    Cluster(
        code="A1",
        name="Agent — Nội dung, ảnh và Category",
        weight_percent=35,
        quota=35,
        dimensions={
            "Mức độ dễ hiểu": (
                "Rõ ràng",
                "Hiểu ý chính nhưng thiếu chi tiết",
                "Không hiểu được vấn đề",
            ),
            "Mức độ đầy đủ thông tin": (
                "Đủ thông tin",
                "Thiếu thông tin có thể hỏi thêm",
                "Thiếu thông tin cốt lõi",
            ),
            "Lỗi câu chữ": ("Không lỗi", "Một loại lỗi", "Nhiều loại lỗi"),
            "Số vấn đề trong text": ("Một vấn đề", "Nhiều vấn đề"),
            "Trạng thái ảnh": (
                "Không có ảnh",
                "Ảnh rõ và liên quan",
                "Ảnh mờ",
                "Ảnh không liên quan",
            ),
            "Mức nghiêm trọng hiệu lực": ("Thấp", "Trung bình", "Cao"),
            "Quan hệ giữa giọng văn và sự cố": (
                "Phù hợp",
                "Giọng khẩn cấp nhưng sự cố nhẹ",
                "Giọng bình thường nhưng sự cố nghiêm trọng",
            ),
            "Số Category trong text": (
                "Không xác định được",
                "Một Category",
                "Nhiều Category",
            ),
            "Số Category trong ảnh": (
                "Không có ảnh",
                "Không xác định được",
                "Một Category",
                "Nhiều Category",
            ),
            "Quan hệ Category giữa text và ảnh": (
                "Chỉ có text",
                "Có đúng một Category chung",
                "Không có Category chung",
                "Có nhiều Category chung",
                "Không đủ dữ liệu so sánh",
            ),
        },
        is_valid=valid_agent_content,
        fixed_values={"Nguồn dấu hiệu nguy hiểm": "Không có"},
    ),
    Cluster(
        code="A2",
        name="Agent — Grouping và sự cố đang xử lý",
        weight_percent=10,
        quota=14,
        dimensions={
            "Loại vấn đề": ("Rò nước", "Thấm tường", "Chập điện", "Category khác"),
            "Bằng chứng gộp cụm": (
                "Không có bằng chứng",
                "Hợp lệ trong cùng tầng hoặc tầng liền kề và không quá ba ngày",
                "Quá ba ngày",
            ),
            "Số căn hộ riêng biệt bị ảnh hưởng": (
                "Một căn hộ",
                "Hai đến ba căn hộ",
                "Từ bốn căn hộ",
            ),
            "Bằng chứng sự cố đang được xử lý": (
                "Không có ticket",
                "Có ticket cùng vị trí và Category",
            ),
        },
        is_valid=valid_agent_grouping,
        fixed_values={"Nguồn dấu hiệu nguy hiểm": "Không có"},
    ),
    Cluster(
        code="A3",
        name="Agent — Red flag và tương tác Cư dân",
        weight_percent=15,
        quota=26,
        dimensions={
            "Mức độ đầy đủ thông tin": (
                "Đủ thông tin",
                "Thiếu thông tin có thể hỏi thêm",
                "Thiếu thông tin cốt lõi",
            ),
            "Trạng thái ảnh": (
                "Không có ảnh",
                "Ảnh rõ và liên quan",
                "Ảnh mờ",
                "Ảnh không liên quan",
            ),
            "Nguồn dấu hiệu nguy hiểm": (
                "Không có",
                "Chỉ trong text",
                "Chỉ trong ảnh",
                "Có trong text và ảnh",
                "Có trong câu trả lời bổ sung",
            ),
            "Kịch bản tương tác Cư dân": (
                "Không cần hỏi",
                "Đã trả lời và làm rõ Category",
                "Đã trả lời và bổ sung dấu hiệu nguy hiểm",
                "Đã trả lời nhưng vẫn không đủ thông tin",
                "Không trả lời",
            ),
            "Bằng chứng sự cố đang được xử lý": (
                "Không có ticket",
                "Có ticket cùng vị trí và Category",
            ),
        },
        is_valid=valid_agent_interaction,
        fixed_values={},
    ),
    Cluster(
        code="B1",
        name="LLM — DIRECT",
        weight_percent=20,
        quota=20,
        dimensions={
            "Tổng số ticket riêng biệt": ("0+", "20", "21"),
            "Thành phần yêu cầu": ("Chỉ ticket đơn", "Chỉ cụm sự cố", "Có cả hai"),
            "Kịch bản ứng viên và mô hình": (
                "Không có ứng viên",
                "Có ứng viên và không gọi được mô hình 1",
                "Có ứng viên và không gọi được mô hình 2",
                "Có ứng viên và không gọi được cả hai mô hình",
            ),
        },
        is_valid=valid_direct,
        fixed_values={"Lý do gọi": "Phân việc lần đầu"},
    ),
    Cluster(
        code="B2",
        name="LLM — PROPOSAL",
        weight_percent=20,
        quota=20,
        dimensions={
            "Lý do gọi": (
                "Phân lại do Kỹ thuật viên từ chối",
                "Phân lại do Kỹ thuật viên không nhận việc đúng hạn",
            ),
            "Thành phần yêu cầu": ("Ticket đơn", "Cụm sự cố"),
            "Kịch bản ứng viên và mô hình": (
                "Không có ứng viên",
                "Có ứng viên và không gọi được mô hình 1",
                "Có ứng viên và không gọi được mô hình 2",
                "Có ứng viên và không gọi được cả hai mô hình",
            ),
        },
        is_valid=always_valid,
        fixed_values={},
    ),
)


def enumerate_cases(cluster: Cluster) -> list[dict[str, str]]:
    names = tuple(cluster.dimensions)
    values = tuple(cluster.dimensions[name] for name in names)
    result = []
    for combination in itertools.product(*values):
        case = dict(zip(names, combination))
        if cluster.is_valid(case):
            result.append(case)
    return result


def pair_tokens(case: dict[str, str]) -> set[tuple[str, str, str, str]]:
    items = sorted(case.items())
    return {
        (left_name, left_value, right_name, right_value)
        for index, (left_name, left_value) in enumerate(items)
        for right_name, right_value in items[index + 1 :]
    }


def choose_diverse_cases(
    candidates: list[dict[str, str]], quota: int, rng: random.Random
) -> list[dict[str, str]]:
    remaining = list(candidates)
    rng.shuffle(remaining)
    selected: list[dict[str, str]] = []
    covered_pairs: set[tuple[str, str, str, str]] = set()
    covered_values: set[tuple[str, str]] = set()

    while remaining and len(selected) < min(quota, len(candidates)):
        best_index = 0
        best_score = -1
        for index, candidate in enumerate(remaining):
            value_gain = sum(
                (name, value) not in covered_values for name, value in candidate.items()
            )
            pair_gain = len(pair_tokens(candidate) - covered_pairs)
            score = value_gain * 1000 + pair_gain
            if score > best_score:
                best_score = score
                best_index = index
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        covered_values.update(chosen.items())
        covered_pairs.update(pair_tokens(chosen))

    base_selection = [
        case
        for case in selected
        if case.get("Kịch bản ứng viên và mô hình") != "Không có ứng viên"
    ] or list(selected)
    repeat_index = 0
    while len(selected) < quota:
        selected.append(dict(base_selection[repeat_index % len(base_selection)]))
        repeat_index += 1
    return selected


def concrete_positive_candidate_count(occurrence: int) -> int:
    representatives = (1, 2, 3, 5, 8)
    return representatives[(occurrence - 1) % len(representatives)]


def expand_interaction(case: dict[str, str], rng: random.Random) -> dict[str, object]:
    interaction = case.get("Kịch bản tương tác Cư dân")
    if interaction is None:
        return {}
    if interaction == "Không cần hỏi":
        return {
            "Số lượt hỏi Cư dân": 0,
            "Trạng thái phản hồi của Cư dân": "Chưa cần hỏi",
            "Dữ liệu mới sau khi hỏi": None,
        }
    if interaction == "Không trả lời":
        return {
            "Số lượt hỏi Cư dân": rng.randint(1, 3),
            "Trạng thái phản hồi của Cư dân": "Không trả lời",
            "Dữ liệu mới sau khi hỏi": None,
        }
    new_data = {
        "Đã trả lời và làm rõ Category": "Làm rõ Category",
        "Đã trả lời và bổ sung dấu hiệu nguy hiểm": "Bổ sung dấu hiệu nguy hiểm",
        "Đã trả lời nhưng vẫn không đủ thông tin": "Vẫn không đủ thông tin",
    }[interaction]
    return {
        "Số lượt hỏi Cư dân": rng.randint(1, 3),
        "Trạng thái phản hồi của Cư dân": "Đã trả lời",
        "Dữ liệu mới sau khi hỏi": new_data,
    }


def expand_assignment(case: dict[str, str], occurrence: int) -> dict[str, object]:
    scenario = case.get("Kịch bản ứng viên và mô hình")
    if scenario is None:
        return {}
    if scenario == "Không có ứng viên":
        return {
            "Số Kỹ thuật viên ứng viên hoạt động và cùng chuyên môn": 0,
            "Quá trình gọi mô hình": None,
        }
    process = scenario.replace("Có ứng viên và ", "", 1)
    return {
        "Số Kỹ thuật viên ứng viên hoạt động và cùng chuyên môn": concrete_positive_candidate_count(
            occurrence
        ),
        "Quá trình gọi mô hình": process,
    }

def expected_result(cluster: Cluster, case: dict[str, str], concrete: dict[str, object]) -> dict[str, object]:
    if cluster.code == "A1":
        clarity = case["Mức độ dễ hiểu"]
        completeness = case["Mức độ đầy đủ thông tin"]
        image = case["Trạng thái ảnh"]
        relation = case["Quan hệ Category giữa text và ảnh"]
        if clarity == "Không hiểu được vấn đề" and image != "Ảnh rõ và liên quan":
            exit_reason = "INSUFFICIENT_INPUT"
        elif completeness == "Thiếu thông tin có thể hỏi thêm":
            exit_reason = "NEEDS_RESIDENT_CLARIFICATION"
        else:
            exit_reason = "ANALYSIS_COMPLETE"
        category_handling = (
            "CHỐT_TỰ_ĐỘNG"
            if relation in {"Chỉ có text", "Có đúng một Category chung"}
            else "DUYỆT_THỦ_CÔNG"
        )
        return {"Kết quả Agent": exit_reason, "Xử lý Category": category_handling}

    if cluster.code == "A2":
        accepted = (
            case["Loại vấn đề"] in {"Rò nước", "Chập điện"}
            and case["Bằng chứng gộp cụm"]
            == "Hợp lệ trong cùng tầng hoặc tầng liền kề và không quá ba ngày"
        )
        density = {
            "Một căn hộ": 0,
            "Hai đến ba căn hộ": 15,
            "Từ bốn căn hộ": 30,
        }[case["Số căn hộ riêng biệt bị ảnh hưởng"]]
        return {
            "Chấp nhận grouping": accepted,
            "Điểm Density": density if accepted else None,
            "Có candidate sự cố đang xử lý": case["Bằng chứng sự cố đang được xử lý"]
            == "Có ticket cùng vị trí và Category",
        }

    if cluster.code == "A3":
        danger = case["Nguồn dấu hiệu nguy hiểm"]
        interaction = case["Kịch bản tương tác Cư dân"]
        if danger != "Không có":
            result = "RED_FLAG"
        elif interaction == "Không trả lời":
            result = "RESIDENT_RESPONSE_TIMEOUT"
        elif interaction == "Đã trả lời nhưng vẫn không đủ thông tin":
            result = "INSUFFICIENT_INPUT"
        elif interaction == "Đã trả lời và làm rõ Category":
            result = "ANALYSIS_COMPLETE"
        else:
            result = "ANALYSIS_COMPLETE"
        return {"Kết quả Agent": result}

    total = case.get("Tổng số ticket riêng biệt")
    if total == "21":
        return {"Kết quả phân việc": "TỪ_CHỐI_REQUEST_TRƯỚC_KHI_GỌI_MÔ_HÌNH"}
    candidate_count = concrete.get("Số Kỹ thuật viên ứng viên hoạt động và cùng chuyên môn")
    if candidate_count == 0:
        return {
            "Kết quả phân việc": "CHUYỂN_PHÂN_TAY"
            if cluster.code == "B1"
            else "ĐỀ_XUẤT_TRỐNG"
        }
    process = concrete.get("Quá trình gọi mô hình")
    if process == "không gọi được mô hình 1":
        return {"Kết quả phân việc": "GỌI_MÔ_HÌNH_2"}
    return {
        "Kết quả phân việc": "CHUYỂN_PHÂN_TAY"
        if cluster.code == "B1"
        else "ĐỀ_XUẤT_TRỐNG"
    }


def build_cases() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    output: list[dict[str, object]] = []
    for cluster in CLUSTERS:
        candidates = enumerate_cases(cluster)
        selected = choose_diverse_cases(candidates, cluster.quota, rng)
        occurrences: dict[str, int] = {}
        for number, factor_values in enumerate(selected, start=1):
            signature = json.dumps(factor_values, ensure_ascii=False, sort_keys=True)
            occurrences[signature] = occurrences.get(signature, 0) + 1
            factors = dict(cluster.fixed_values)
            factors.update(factor_values)
            concrete = {}
            concrete.update(expand_interaction(factors, rng))
            concrete.update(
                expand_assignment(factors, occurrences[signature])
            )
            output.append(
                {
                    "case_id": f"{cluster.code}-{number:03d}",
                    "cluster": cluster.name,
                    "cluster_weight_percent": cluster.weight_percent,
                    "factors": factors,
                    "concrete_values": concrete,
                    "expected": expected_result(cluster, factors, concrete),
                }
            )
    return output


def write_outputs(cases: Iterable[dict[str, object]]) -> None:
    rows = list(cases)
    JSON_OUTPUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with TSV_OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "case_id",
                "cluster",
                "cluster_weight_percent",
                "factors",
                "concrete_values",
                "expected",
            ),
            delimiter="\t",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "factors": json.dumps(row["factors"], ensure_ascii=False, sort_keys=True),
                    "concrete_values": json.dumps(
                        row["concrete_values"], ensure_ascii=False, sort_keys=True
                    ),
                    "expected": json.dumps(row["expected"], ensure_ascii=False, sort_keys=True),
                }
            )

    summary = {
        "seed": SEED,
        "total_cases": len(rows),
        "clusters": [
            {
                "code": cluster.code,
                "name": cluster.name,
                "weight_percent": cluster.weight_percent,
                "case_count": sum(row["cluster"] == cluster.name for row in rows),
                "valid_abstract_combinations": len(enumerate_cases(cluster)),
            }
            for cluster in CLUSTERS
        ],
    }
    SUMMARY_OUTPUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    cases = build_cases()
    write_outputs(cases)
    print(f"Generated {len(cases)} cases")
    for cluster in CLUSTERS:
        print(f"{cluster.code}: {cluster.quota} cases ({cluster.weight_percent}%)")
    print(JSON_OUTPUT)
    print(TSV_OUTPUT)


if __name__ == "__main__":
    main()
