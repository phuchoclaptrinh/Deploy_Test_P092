#!/bin/bash
# Bootstrap môi trường phát triển FixIt Agent trên macOS/Linux/Git Bash.
#
#   bash scripts/setup.sh
#
# Xem scripts/setup.ps1 cho bản Windows PowerShell.
#
# Script này chọn interpreter 3.11+ một cách tường minh rồi gọi mọi thứ qua
# .venv/bin/python. Lý do: `pip install` sau khi `activate` vẫn có thể trỏ nhầm
# interpreter nếu PATH bị can thiệp, và gọi `uvicorn` trần thì rơi sang bản
# Python global — báo lỗi thiếu `psycopg` thay vì chỉ ra nguyên nhân thật.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MIN_MAJOR=3
MIN_MINOR=11

echo
echo "=== FixIt Agent — setup môi trường ==="
echo "Repo: $REPO_ROOT"
echo

version_ok() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

# ---- 1. Tìm interpreter 3.11+ ------------------------------------------------

BASE_PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && version_ok "$candidate"; then
        BASE_PYTHON="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$BASE_PYTHON" ]; then
    echo "KHÔNG tìm thấy Python ${MIN_MAJOR}.${MIN_MINOR}+ trên máy này." >&2
    echo >&2
    echo "Đang có:" >&2
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "  $candidate -> $("$candidate" --version 2>&1)" >&2
        fi
    done
    echo >&2
    echo "Cài Python 3.13 rồi chạy lại script này." >&2
    exit 1
fi

echo "[1/5] Python nền: $("$BASE_PYTHON" --version 2>&1)"
echo "      $BASE_PYTHON"

# ---- 2. Tạo .venv ------------------------------------------------------------

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$VENV_PYTHON" ] || VENV_PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"   # Git Bash

if [ -x "$VENV_PYTHON" ] && version_ok "$VENV_PYTHON"; then
    echo "[2/5] .venv đã có sẵn ($("$VENV_PYTHON" --version 2>&1)), dùng lại"
else
    if [ -d "$REPO_ROOT/.venv" ]; then
        echo "[2/5] .venv hiện tại quá cũ — tạo lại"
        rm -rf "$REPO_ROOT/.venv"
    else
        echo "[2/5] Tạo .venv..."
    fi
    "$BASE_PYTHON" -m venv .venv
    VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
    [ -x "$VENV_PYTHON" ] || VENV_PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"
fi

# ---- 3. Cài dependency VÀO .venv --------------------------------------------

echo "[3/5] Cài dependency vào .venv..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r requirements.txt
"$VENV_PYTHON" -m pip install -r requirements-dev.txt

# ---- 4. .env -----------------------------------------------------------------

BACKEND_ENV_CREATED=0
if [ ! -f .env ]; then
    cp .env.example .env
    BACKEND_ENV_CREATED=1
    echo "[4/5] Đã tạo .env từ .env.example — CẦN ĐIỀN GIÁ TRỊ THẬT"
else
    echo "[4/5] .env đã tồn tại, giữ nguyên"
fi

# ---- 5. Frontend -------------------------------------------------------------
#
# Frontend cần .env.local riêng và node_modules riêng. Gộp vào đây để một lệnh
# setup là đủ cho cả hai tầng. Thiếu Node thì chỉ cảnh báo, không dừng — backend
# vẫn dùng được độc lập.

FRONTEND_ENV_CREATED=0
FRONTEND_READY=0

if [ ! -f frontend/.env.local ]; then
    cp frontend/.env.example frontend/.env.local
    FRONTEND_ENV_CREATED=1
fi

if command -v npm >/dev/null 2>&1; then
    echo "[5/5] Cài dependency frontend..."
    (cd frontend && npm install --no-fund --no-audit)
    FRONTEND_READY=1
else
    echo "[5/5] KHÔNG tìm thấy npm — bỏ qua frontend"
    echo "      Cài Node.js 20+ tại https://nodejs.org rồi chạy lại script này."
fi

# ---- Kiểm chứng --------------------------------------------------------------

echo
"$VENV_PYTHON" -c "import sys, psycopg, fastapi, langgraph; print('Kiểm tra import: OK  (Python %d.%d.%d)' % sys.version_info[:3])"

echo
echo "=== Setup xong ==="
echo

STEP=1
if [ "$BACKEND_ENV_CREATED" -eq 1 ]; then
    echo "  $STEP. Mở .env và điền DATABASE_URL, SUPABASE_*, OPENAI_API_KEY, MODEL_NAME"
    STEP=$((STEP + 1))
fi
if [ "$FRONTEND_ENV_CREATED" -eq 1 ]; then
    echo "  $STEP. Mở frontend/.env.local và điền NEXT_PUBLIC_SUPABASE_*"
    STEP=$((STEP + 1))
fi
echo "  $STEP. Chạy backend:  .venv/bin/python -m uvicorn src.main:app --reload --port 8000"
STEP=$((STEP + 1))
if [ "$FRONTEND_READY" -eq 1 ]; then
    echo "  $STEP. Chạy frontend (tab khác):  cd frontend && npm run dev"
    STEP=$((STEP + 1))
fi
echo "  $STEP. Kiểm tra:  http://localhost:8000/ready  và  http://localhost:3000"

cat <<'EOF'

Nếu DB chưa có bảng: đặt ALLOW_LIVE_MIGRATION=true trong .env rồi chạy
  .venv/bin/python -m alembic upgrade head

LƯU Ý: luôn dùng `python -m uvicorn`, đừng gọi `uvicorn` trần.
       Dạng `-m` bắt buộc chạy bằng interpreter của .venv nên không thể
       rơi nhầm sang bản Python global.
EOF
