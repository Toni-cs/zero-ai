"""支持 python -m zeroai 启动"""
import sys

from zeroai.main import main

if __name__ == "__main__":
    # 必须用 SystemExit 把 main() 的返回值变成进程退出码。
    # 原来直接调用 main() 会丢弃返回值，导致 `python -m zeroai`
    # 无论失败与否都返回 0，CI 里无法据退出码判断成败。
    raise SystemExit(main())
