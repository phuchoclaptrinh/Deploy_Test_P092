"""Endpoint mô phỏng công suất & SLA (chỉ Điều phối viên).

`POST /api/v1/coordinator/simulation/run` nhận một kịch bản JSON — tòa nhà,
chính sách SLA, các thiết lập, đội kỹ thuật viên và các phản ánh — phát lại nó
hai lần (quy trình thủ công cũ, và luồng tự động theo chính sách giả định) rồi
trả về cả hai cùng phần so sánh.

Ba điều về handler này chịu lực:

* **Nó không nhận database session.** Không phải "nó không commit": tham số
  không có ở đó, nên không có session nào trong tầm để ghi qua. Truy vấn duy
  nhất request này thực hiện là truy vấn `require_coordinator` dùng để xác minh
  người gọi. `tests/test_simulation/test_no_database_writes.py` kiểm tra chữ ký
  hàm giữ nguyên như vậy.
* **Chỉ Điều phối viên.** Một lần mô phỏng là công cụ lập kế hoạch quản lý; nó
  phơi bày tải của cả đội và mọi vi phạm SLA trong một ngày, không phải góc nhìn
  của cư dân hay của kỹ thuật viên.
* **Nó có trần.** Tối đa `MAX_TICKETS` phản ánh và `MAX_TECHNICIANS` kỹ thuật
  viên mỗi lần chạy. Công việc gần như tuyến tính và chạy trong tiến trình, và
  cái trần là thứ ngăn một file triệu dòng dán vào chiếm mất một worker.

Không luồng nào trong response là production. `NEW_APP` là mô phỏng một chính
sách **chưa được áp dụng**, và payload không mang cờ parity nào để ai đó đọc
nhầm thành hành vi hiện tại.
"""

from fastapi import APIRouter, Depends, Request

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.errors import SIMULATION_INPUT_INVALID, DomainError
from src.models.api.simulation import SimulationRunRequest, SimulationRunResponse
from src.simulation.engine import run_comparison
from src.simulation.validation import SimulationInputError, parse_scenario

router = APIRouter()


@router.post("/simulation/run", response_model=ApiResponse[SimulationRunResponse])
def run_simulation(
    request: Request,
    body: SimulationRunRequest,
    _actor: CurrentActor = Depends(require_coordinator),
):
    """Chạy cả hai luồng trên một tài liệu kịch bản được gửi lên.

    Chỉ đọc theo cấu trúc: không ticket, phân công hay dispatch event nào được
    tạo hoặc sửa, và lần chạy chỉ tồn tại trong độ dài của response này. Không
    có gì ở đây gọi mô hình hay agent — mọi con số trả về là số học trên phần
    body ở trên.
    """
    try:
        scenario = parse_scenario(body.scenario)
    except SimulationInputError as error:
        # 422: JSON đúng dạng và đúng schema, và một dòng bên trong nó sai.
        # `details` nêu tên dòng và tên trường để màn hình đánh dấu đúng ô thay
        # vì bắt ai đó đọc lại cả file.
        raise DomainError(
            SIMULATION_INPUT_INVALID, error.message, 422, details=error.as_details()
        ) from error

    return ok(request, SimulationRunResponse.of(run_comparison(scenario)))
