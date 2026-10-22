"""Worker 模块入口。

`python -m app.worker` 会执行本文件。
存在的意义：让「怎么启动 worker」这件事只有一个答案，
不依赖使用者记住 arq 的命令行参数。
"""

from __future__ import annotations

from app.worker.main import main

if __name__ == "__main__":
    # main() 内部已经处理了 KeyboardInterrupt 与日志，
    # 这里不需要（也不能）再包一层 sys.exit：它返回 None。
    main()
