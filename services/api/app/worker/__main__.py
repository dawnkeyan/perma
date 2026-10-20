"""Worker 模块入口。

`python -m app.worker` 会执行本文件。
存在的意义：让「怎么启动 worker」这件事只有一个答案，
不依赖使用者记住 arq 的命令行参数。
"""

from __future__ import annotations

import sys

from app.worker.main import main

if __name__ == "__main__":
    sys.exit(main())
