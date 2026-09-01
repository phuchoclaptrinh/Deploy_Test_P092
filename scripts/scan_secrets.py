"""Scan tracked text files for obvious credential patterns without printing values."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", ".env", ".ai-log"}

# Thư mục bỏ qua theo đường dẫn tương đối, khác với SKIP_PARTS (khớp tên thành
# phần ở bất kỳ vị trí nào).
#
# docs/guide là sách hướng dẫn kỹ thuật do BTC phát hành kèm template, không
# phải code của nhóm. Nó chứa sẵn nhiều connection string ví dụ có kèm mật khẩu
# giả nên luôn kích hoạt database_url_with_password. Allowlist từng chuỗi một sẽ
# phải vá lại mỗi lần tài liệu thêm ví dụ mới, nên bỏ qua cả thư mục. Tài liệu
# do nhóm tự viết (docs/*.md) vẫn được quét bình thường.
#
# Lưu ý khi sửa file này: đừng viết connection string đầy đủ vào comment —
# scanner quét cả chính nó và sẽ tự báo động.
SKIP_DIR_PREFIXES = (("docs", "guide"),)
PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "ai_log_key": re.compile(r"AI_LOG_API_KEY=[A-Za-z0-9_-]{30,}"),
    "supabase_secret_key": re.compile(r"(?<![A-Za-z0-9_])sb_secret_[A-Za-z0-9_-]{20,}"),
    "supabase_jwt": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    "database_url_with_password": re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
}
ALLOWLISTED_MATCHES = {
    "supabase_secret_key": {
        "sb_secret_xxxxxxxxxxxxxxxxxxxx",
        "sb_secret_yourbackendsecretplaceholder",
    },
    "database_url_with_password": {
        "postgresql://user:password@",
    },
}


def tracked_files() -> list[Path]:
    """Return Git-tracked files, or a safe filesystem fallback for source archives."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        result = None

    if result is not None and result.returncode == 0:
        return [ROOT / line for line in result.stdout.splitlines() if line.strip()]

    return sorted(path for path in ROOT.rglob("*") if path.is_file() and not is_skipped(path))


def is_skipped(path: Path) -> bool:
    parts = _skip_parts(path)
    if any(part in SKIP_PARTS for part in parts):
        return True
    return any(parts[: len(prefix)] == prefix for prefix in SKIP_DIR_PREFIXES)


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if is_skipped(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if match.group(0) in ALLOWLISTED_MATCHES.get(name, set()):
                    continue
                findings.append(f"{_display_path(path)}: {name}")
    return findings


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return Path(path.name)


def _skip_parts(path: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(ROOT).parts
    except ValueError:
        return path.parts


def main() -> int:
    findings = scan_paths(tracked_files())
    if findings:
        for finding in findings:
            print(finding)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
