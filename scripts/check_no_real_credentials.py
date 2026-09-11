#!/usr/bin/env python3
"""
scripts/check_no_real_credentials.py — 轻量 pre-commit 检查（无第三方依赖）

只做一件事：检查真实 credential 文件是否进入了 git 暂存区。
不读取、不打印、不展示该文件的任何内容 —— 只看路径。

用法:
  python scripts/check_no_real_credentials.py
  # 退出码 0 = 安全；1 = 发现 credential 文件被暂存

可选安装为本地 hook（非强制；.gitignore 与实施后的 git 验证仍是主要防线）:
  # Linux / macOS
  printf '#!/bin/sh\\nexec python scripts/check_no_real_credentials.py\\n' \\
      > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
  # Windows PowerShell
  Set-Content .git/hooks/pre-commit "#!/bin/sh`nexec python scripts/check_no_real_credentials.py"
"""

import subprocess
import sys

# 禁止进入暂存区的真实 credential 文件（精确路径匹配，不误伤 template）
FORBIDDEN_PATHS = {
    "iics_credentials.json",
    ".env_pins.json",
}


def staged_paths() -> list:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"[credential-guard] 无法读取 git 暂存区: {e}", file=sys.stderr)
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    offenders = sorted(set(staged_paths()) & FORBIDDEN_PATHS)
    if not offenders:
        print("[credential-guard] OK：暂存区未包含真实 credential 文件。")
        return 0

    print("[credential-guard] 阻断提交：以下文件不得进入 git。", file=sys.stderr)
    for path in offenders:
        print(f"  - {path}", file=sys.stderr)
    print("", file=sys.stderr)
    print("请执行：git restore --staged <文件>，并确认 .gitignore 中存在 "
          "/iics_credentials.json 规则。", file=sys.stderr)
    print("本脚本不会读取或显示该文件的内容。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
