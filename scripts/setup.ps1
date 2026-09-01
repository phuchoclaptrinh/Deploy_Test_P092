# Bootstrap môi trường phát triển FixIt Agent trên Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Script này tồn tại vì hai lỗi lặp đi lặp lại khi clone repo về máy mới:
#   1. `python -m venv .venv` dùng nhầm Python 3.10 trên PATH, trong khi dự án
#      cần 3.11+ (14 file dùng `from datetime import UTC`).
#   2. Sau khi activate, gõ `uvicorn` trần thì lệnh rơi sang bản uvicorn của
#      Python global, không phải của venv -> báo thiếu `psycopg` một cách khó hiểu.
#
# Cách chữa: chọn interpreter 3.11+ một cách tường minh, và luôn gọi qua
# đường dẫn tuyệt đối tới python.exe trong .venv.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$MIN_MAJOR = 3
$MIN_MINOR = 11

Write-Host ""
Write-Host "=== FixIt Agent - setup moi truong ===" -ForegroundColor Cyan
Write-Host "Repo: $repoRoot"
Write-Host ""

# ---- 1. Tìm interpreter 3.11+ -------------------------------------------------

function Get-PythonVersion($exe) {
    try {
        $out = & $exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
        if ($LASTEXITCODE -ne 0) { return $null }
        return $out.Trim()
    } catch {
        return $null
    }
}

function Test-VersionOk($versionText) {
    if (-not $versionText) { return $false }
    $parts = $versionText.Split(".")
    if ($parts.Count -lt 2) { return $false }
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -gt $MIN_MAJOR) { return $true }
    if ($major -eq $MIN_MAJOR -and $minor -ge $MIN_MINOR) { return $true }
    return $false
}

$basePython = $null
$basePythonVersion = $null

# Ưu tiên py launcher: nó liệt kê được mọi bản đã cài, không phụ thuộc PATH.
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($tag in @("-3.13", "-3.12", "-3.11")) {
        try {
            $out = & py $tag -c "import sys; print(sys.executable)"
            if ($LASTEXITCODE -eq 0 -and $out) {
                $basePython = $out.Trim()
                $basePythonVersion = Get-PythonVersion $basePython
                break
            }
        } catch {
            # bản này chưa cài, thử bản kế tiếp
        }
    }
}

# Dự phòng: python trên PATH, nhưng chỉ nhận nếu đủ phiên bản.
if (-not $basePython) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) {
        $candidateVersion = Get-PythonVersion $onPath.Source
        if (Test-VersionOk $candidateVersion) {
            $basePython = $onPath.Source
            $basePythonVersion = $candidateVersion
        }
    }
}

if (-not $basePython) {
    Write-Host "KHONG tim thay Python $MIN_MAJOR.$MIN_MINOR+ tren may nay." -ForegroundColor Red
    Write-Host ""
    Write-Host "Cac ban Python dang co:"
    if (Get-Command py -ErrorAction SilentlyContinue) { py -0p }
    Write-Host ""
    Write-Host "Cai Python 3.13 tai https://www.python.org/downloads/ roi chay lai script nay."
    exit 1
}

Write-Host "[1/5] Python nen: $basePythonVersion" -ForegroundColor Green
Write-Host "      $basePython"

# ---- 2. Tạo .venv -------------------------------------------------------------

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $existing = Get-PythonVersion $venvPython
    if (Test-VersionOk $existing) {
        Write-Host "[2/5] .venv da co san (Python $existing), dung lai" -ForegroundColor Green
    } else {
        Write-Host "[2/5] .venv dang la Python $existing - qua cu, tao lai" -ForegroundColor Yellow
        Remove-Item -Recurse -Force (Join-Path $repoRoot ".venv")
        & $basePython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Tao .venv that bai." }
    }
} else {
    Write-Host "[2/5] Tao .venv..." -ForegroundColor Green
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Tao .venv that bai." }
}

# ---- 3. Cài dependency VÀO .venv ---------------------------------------------

Write-Host "[3/5] Cai dependency vao .venv..." -ForegroundColor Green
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Nang cap pip that bai." }

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Cai requirements.txt that bai." }

& $venvPython -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Cai requirements-dev.txt that bai." }

# ---- 4. .env ------------------------------------------------------------------

$backendEnvCreated = $false
if (-not (Test-Path (Join-Path $repoRoot ".env"))) {
    Copy-Item ".env.example" ".env"
    $backendEnvCreated = $true
    Write-Host "[4/5] Da tao .env tu .env.example - CAN DIEN GIA TRI THAT" -ForegroundColor Yellow
} else {
    Write-Host "[4/5] .env da ton tai, giu nguyen" -ForegroundColor Green
}

# ---- 5. Frontend --------------------------------------------------------------
#
# Frontend cần .env.local riêng và node_modules riêng. Gộp vào đây để một lệnh
# setup là đủ cho cả hai tầng. Thiếu Node thì chỉ cảnh báo, không dừng — backend
# vẫn dùng được độc lập.

$frontendDir = Join-Path $repoRoot "frontend"
$frontendEnvCreated = $false
$frontendReady = $false

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }

if (-not (Test-Path (Join-Path $frontendDir ".env.local"))) {
    Copy-Item (Join-Path $frontendDir ".env.example") (Join-Path $frontendDir ".env.local")
    $frontendEnvCreated = $true
}

if ($npm) {
    Write-Host "[5/5] Cai dependency frontend..." -ForegroundColor Green
    Push-Location $frontendDir
    try {
        & $npm.Source install --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw "npm install that bai." }
        $frontendReady = $true
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[5/5] KHONG tim thay npm - bo qua frontend" -ForegroundColor Yellow
    Write-Host "      Cai Node.js 20+ tai https://nodejs.org roi chay lai script nay."
}

# ---- Kiểm chứng ---------------------------------------------------------------

Write-Host ""
& $venvPython -c "import sys, psycopg, fastapi, langgraph; print('Kiem tra import: OK  (Python %d.%d.%d)' % sys.version_info[:3])"
if ($LASTEXITCODE -ne 0) { throw "Kiem tra import that bai." }

Write-Host ""
Write-Host "=== Setup xong ===" -ForegroundColor Cyan
Write-Host ""

$step = 1
if ($backendEnvCreated) {
    Write-Host "  $step. Mo .env va dien DATABASE_URL, SUPABASE_*, OPENAI_API_KEY, MODEL_NAME" -ForegroundColor Yellow
    $step++
}
if ($frontendEnvCreated) {
    Write-Host "  $step. Mo frontend\.env.local va dien NEXT_PUBLIC_SUPABASE_*" -ForegroundColor Yellow
    $step++
}
Write-Host "  $step. Chay backend:  .venv\Scripts\python.exe -m uvicorn src.main:app --reload --port 8000"
$step++
if ($frontendReady) {
    Write-Host "  $step. Chay frontend (tab khac):  cd frontend; npm.cmd run dev"
    $step++
}
Write-Host "  $step. Kiem tra:  http://localhost:8000/ready  va  http://localhost:3000"
Write-Host ""
Write-Host "Neu DB chua co bang: bat co ngay tren lenh, dung de san trong .env"
Write-Host "  `$env:ALLOW_LIVE_MIGRATION='true'; .venv\Scripts\python.exe -m alembic upgrade head"
Write-Host ""
Write-Host "LUU Y: luon dung 'python -m uvicorn', dung goi 'uvicorn' tran." -ForegroundColor Yellow
Write-Host "       Dang '-m' bat buoc chay bang interpreter cua .venv nen khong the"
Write-Host "       roi nham sang ban Python global."
Write-Host ""
