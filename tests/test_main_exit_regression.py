# -*- coding: utf-8 -*-
"""回归测试：`python -m zeroai.main` 必须透传递退出码。

修复前（bug）：`zeroai/main.py:250` 直接写 `main()`，丢弃返回值，导致
`python -m zeroai.main --task ...` 无论失败与否都返回 0，CI 无法据此判定成败。

修复后：`raise SystemExit(main())`，返回值变成进程退出码。

本测试用子进程跑真实入口，断言缺少 API Key 时返回 2（不是 0）。
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_main_module_exit_code_not_zero_on_credential_error():
    proc = subprocess.run(
        [sys.executable, "-m", "zeroai.main", "--task", "hi", "--json"],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode != 0, (
        f"`python -m zeroai.main` 在缺少 API Key 时必须返回非 0，"
        f"实际返回 {proc.returncode}\nSTDOUT:\n{proc.stdout.decode('utf-8','replace')}\n"
        f"STDERR:\n{proc.stderr.decode('utf-8','replace')}"
    )
