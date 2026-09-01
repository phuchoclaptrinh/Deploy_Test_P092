"""FixIt Agent backend package.

Chặn sớm trường hợp chạy bằng Python quá cũ.

Không có đoạn này, một venv tạo nhầm bằng Python 3.10 sẽ chết ở tận
`from datetime import UTC` trong một service ngẫu nhiên (hằng số đó chỉ có từ
3.11), hoặc tệ hơn: lệnh `uvicorn` trần rơi sang bản Python global và báo
`ModuleNotFoundError: No module named 'psycopg'`. Cả hai thông báo đều không
chỉ ra nguyên nhân thật, và người mới clone repo sẽ đi sửa nhầm chỗ.

Đặt ở `src/__init__.py` vì Python nạp file này trước mọi `src.*`, nên guard áp
dụng đồng đều cho uvicorn, pytest, alembic và các script trong `scripts/`.
Toàn bộ file phải parse được trên Python cũ — không dùng cú pháp 3.11+ ở đây.
"""

import sys

MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:
    _required = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
    _running = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
    raise RuntimeError(
        "\n\n"
        "  FixIt Agent cần Python " + _required + "+ nhưng đang chạy Python " + _running + "\n"
        "  Interpreter: " + sys.executable + "\n\n"
        "  Hai nguyên nhân thường gặp:\n"
        "    1. .venv được tạo bằng Python cũ  -> tạo lại bằng script bên dưới\n"
        "    2. gọi `uvicorn` trần nên rơi sang Python global  -> dùng `python -m uvicorn`\n\n"
        "  Tạo lại môi trường:\n"
        "    Windows:      powershell -ExecutionPolicy Bypass -File scripts\\setup.ps1\n"
        "    macOS/Linux:  bash scripts/setup.sh\n"
    )
