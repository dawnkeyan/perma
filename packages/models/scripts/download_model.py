#!/usr/bin/env python3
"""从 ModelScope（魔搭）拉取模型权重到本地目录。

为什么需要这个脚本：
- 生产服务器在内网/无外网环境时，镜像里不能直接拉模型，必须在有网环境预下载后打进镜像或上传到 OSS
- 统一缓存目录，避免每次容器重建都重新下载几十 GB 权重
- 校验下载完整性并输出清单，便于排查「模型版本不对」类问题

用法：
    # 拉取基座模型到默认缓存目录
    python packages/models/scripts/download_model.py --model Qwen/Qwen2.5-1.5B-Instruct

    # 拉取到指定目录（用于 docker build 上下文或离线部署）
    python packages/models/scripts/download_model.py \\
        --model Qwen/Qwen2.5-1.5B-Instruct \\
        --output ./models/qwen2.5-1.5b-instruct

    # 只拉 LoRA 适配器（体积小，几十 MB）
    python packages/models/scripts/download_model.py \\
        --model your-org/lumina-intent-lora --output ./models/lora

环境变量：
    MODELSCOPE_TOKEN   访问令牌（https://modelscope.cn 右上角头像 → 访问令牌）
    MODELSCOPE_CACHE   缓存目录，默认 ~/.cache/modelscope
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# 魔搭 SDK 不是本项目的运行时依赖，只在执行模型脚本时需要，
# 因此放在函数内 import，缺失时给出可操作的提示而不是让整个脚本崩掉。
_MIN_VERSION = "1.18.0"


def _log(message: str) -> None:
    """统一日志输出，带时间戳前缀。"""
    import datetime

    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _fail(message: str, hint: str = "") -> int:
    """打印错误并返回退出码。"""
    print(f"\n错误：{message}", file=sys.stderr)
    if hint:
        print(f"提示：{hint}", file=sys.stderr)
    return 1


def _ensure_sdk() -> None:
    """检查 modelscope SDK 是否可用，不可用时给出安装指引。"""
    try:
        import modelscope  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "未安装 modelscope SDK。请执行：pip install -U 'modelscope>="
            f"{_MIN_VERSION}' 或 pip install -r packages/models/requirements.txt"
        ) from None


def _resolve_cache_dir(explicit: str | None) -> Path:
    """决定缓存目录：命令行 > 环境变量 > 默认值。

    统一成绝对路径，避免在不同工作目录下产生多份重复缓存。
    """
    raw = explicit or os.getenv("MODELSCOPE_CACHE") or "~/.cache/modelscope"
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _login_if_needed() -> bool:
    """尝试登录魔搭。公开模型无需登录也能下载，因此失败不阻断。"""
    token = os.getenv("MODELSCOPE_TOKEN", "").strip()
    if not token:
        _log("未配置 MODELSCOPE_TOKEN，尝试匿名下载（私有模型会失败）")
        return False
    try:
        from modelscope.hub.api import HubApi

        HubApi().login_token(token)
        _log("魔搭登录成功")
        return True
    except Exception as exc:  # noqa: BLE001 - 登录失败不应直接中断公开模型下载
        _log(f"魔搭登录失败（继续尝试匿名下载）：{exc}")
        return False


def _human_size(num_bytes: float) -> str:
    """把字节数格式化成人类可读形式，用于打印模型体积。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f}{unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f}PB"


def _dir_size(path: Path) -> int:
    """递归统计目录占用空间。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file_path = Path(root) / name
            try:
                total += file_path.stat().st_size
            except OSError:
                # 文件可能在遍历过程中被清理，忽略即可
                continue
    return total


def _write_manifest(model_dir: Path, model_id: str, revision: str | None) -> None:
    """写出下载清单，记录来源与体积。

    用途：线上排查「加载的到底是哪个版本的模型」，看这个文件即可。
    """
    manifest = model_dir / "lumina_manifest.json"
    content = {
        "model_id": model_id,
        "revision": revision or "latest",
        "source": "modelscope",
        "size_bytes": _dir_size(model_dir),
        "downloaded_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    manifest.write_text(__import__("json").dumps(content, ensure_ascii=False, indent=2), "utf-8")
    _log(f"清单已写入 {manifest}")


def download_model(
    model_id: str,
    output: str | None = None,
    cache_dir: str | None = None,
    revision: str | None = None,
    allow_patterns: list[str] | None = None,
) -> int:
    """主下载流程。

    Args:
        model_id: 魔搭模型 ID，形如 `Qwen/Qwen2.5-1.5B-Instruct` 或 `your-org/your-model`
        output: 输出目录；None 表示只放到缓存目录（SDK 管理）
        cache_dir: 缓存目录
        revision: 模型版本（分支/标签/commit），默认最新
        allow_patterns: 只下载匹配的文件，用于只取权重跳过多余文件

    Returns:
        进程退出码，0 表示成功
    """
    try:
        _ensure_sdk()
    except RuntimeError as exc:
        return _fail(str(exc))

    _log(f"准备下载模型：{model_id}")
    cache_path = _resolve_cache_dir(cache_dir)
    _login_if_needed()

    try:
        from modelscope import snapshot_download
    except ImportError:
        return _fail("modelscope SDK 导入失败", "请确认已 pip install modelscope")

    # 关键参数说明：
    # - cache_dir：统一缓存，避免重复下载
    # - revision：锁定版本，保证「今天下载的和上个月下载的是同一个模型」
    # - allow_patterns：LoRA 适配器只需要 adapter_model.safetensors 等少量文件
    kwargs: dict[str, object] = {
        "model_id": model_id,
        "cache_dir": str(cache_path),
    }
    if revision:
        kwargs["revision"] = revision
    if allow_patterns:
        kwargs["allow_patterns"] = allow_patterns

    _log(f"缓存目录：{cache_path}")
    try:
        local_dir = snapshot_download(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        return _fail(
            f"下载失败：{exc}",
            "常见原因：1) 网络问题；2) Token 无效；3) 模型 ID 写错；4) 磁盘空间不足",
        )

    local_path = Path(local_dir)
    size = _dir_size(local_path)
    _log(f"下载完成：{local_path}（{_human_size(size)}）")

    if output:
        target = Path(output).expanduser().resolve()
        if target.exists():
            _log(f"目标目录已存在，先清空：{target}")
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _log(f"复制到目标目录：{target}")
        shutil.copytree(local_path, target)
        _write_manifest(target, model_id, revision)
        _log(f"✅ 完成：{target}")
    else:
        _write_manifest(local_path, model_id, revision)
        _log(f"✅ 完成（缓存目录）：{local_path}")

    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="从 ModelScope 魔搭下载模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", required=True, help="魔搭模型 ID，如 Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--output", help="输出目录（不指定则只放缓存目录）")
    parser.add_argument("--cache-dir", help="缓存目录，默认取 MODELSCOPE_CACHE")
    parser.add_argument("--revision", help="模型版本/分支，默认最新")
    parser.add_argument(
        "--only-adapter",
        action="store_true",
        help="只下载 LoRA 适配器相关文件（体积小，适配器场景使用）",
    )
    args = parser.parse_args()

    # LoRA 适配器只需要权重与配置，下载全量基座模型会浪费几十 GB 带宽
    adapter_patterns = [
        "*.safetensors",
        "*.json",
        "*.txt",
        "*.model",
    ]

    return download_model(
        model_id=args.model,
        output=args.output,
        cache_dir=args.cache_dir,
        revision=args.revision,
        allow_patterns=adapter_patterns if args.only_adapter else None,
    )


if __name__ == "__main__":
    sys.exit(main())
