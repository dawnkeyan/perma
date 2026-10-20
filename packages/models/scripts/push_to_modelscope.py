#!/usr/bin/env python3
"""把微调产物（LoRA 适配器）推送到 ModelScope 魔搭模型仓库。

为什么需要这个脚本：
- 微调产物几十 MB 到几 GB，不适合放进 Git 仓库
- 推送到魔搭后，CI/CD 的部署机器可以直接 `snapshot_download` 拉取，
  实现「训练机 → 模型仓库 → 推理机」的标准化流转
- 附带模型卡（README）与元信息，方便他人复现与二次使用

前置准备：
    1. 在 https://modelscope.cn 创建模型仓库（类型选 LoRA）
    2. 生成访问令牌：右上角头像 → 访问令牌 → 新建令牌
    3. export MODELSCOPE_TOKEN=xxxx

用法：
    # 推送微调产物
    python packages/models/scripts/push_to_modelscope.py \\
        --adapter lora_out \\
        --repo your-org/lumina-intent-lora

    # 带说明推送
    python packages/models/scripts/push_to_modelscope.py \\
        --adapter lora_out \\
        --repo your-org/lumina-intent-lora \\
        --note "基于 Qwen2.5-1.5B 的意图识别 LoRA，训练集 1200 条"

    # 先 dry-run 看会推哪些文件
    python packages/models/scripts/push_to_modelscope.py \\
        --adapter lora_out --repo your-org/lumina-intent-lora --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# LoRA 适配器的关键文件清单。
# 只推这些文件，避免把训练中间产物（checkpoint、优化器状态）也推上去。
# checkpoint 每个约等于适配器大小，几十个 checkpoint 会让仓库体积爆炸。
ADAPTER_FILES = [
    "adapter_config.json",
    "adapter_model.safetensors",
    "adapter_model.bin",
    "README.md",
]

# 训练配置与元信息，一并推上去便于复现
EXTRA_FILES = [
    "train_config.json",
    "eval_results.json",
    "training_args.json",
    "tokenizer_config.json",
]


def _log(message: str) -> None:
    """统一日志输出。"""
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _fail(message: str, hint: str = "") -> int:
    """打印错误并返回退出码。"""
    print(f"\n错误：{message}", file=sys.stderr)
    if hint:
        print(f"提示：{hint}", file=sys.stderr)
    return 1


def _collect_files(adapter_dir: Path) -> list[Path]:
    """收集需要上传的文件。

    优先取标准 LoRA 文件；缺失时回退到目录里所有 safetensors / json，
    兼容不同训练框架（ms-swift / LLaMA-Factory / peft）的产物命名差异。
    """
    found: list[Path] = []
    for name in ADAPTER_FILES + EXTRA_FILES:
        candidate = adapter_dir / name
        if candidate.is_file():
            found.append(candidate)

    if not any(p.name.startswith("adapter_model") for p in found):
        _log("未找到标准 adapter_model.* 文件，回退为扫描目录")
        for pattern in ("*.safetensors", "*.bin", "*.json"):
            found.extend(sorted(adapter_dir.glob(pattern)))

    # 去重并保持稳定顺序
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _write_model_card(adapter_dir: Path, repo_id: str, note: str, base_model: str) -> Path:
    """生成模型卡 README.md。

    魔搭的模型页会渲染这个文件，写清楚基座模型、用途、限制，
    能显著提升模型的可发现性与可信度。
    """
    card_path = adapter_dir / "README.md"
    card = f"""---
license: apache-2.0
library_name: peft
base_model: {base_model}
tags:
- lumina
- lora
- intent-classification
- positive-psychology
---

# {repo_id}

## 模型简介

{note or "Lumina 意图识别 LoRA 适配器。"}

## 训练基座

- 基座模型：`{base_model}`
- 训练框架：ms-swift（LoRA）
- 任务：对话意图分类（情绪支持 / 知识问答 / 闲聊 / 医疗越界 / 危机）

## 使用方式

```bash
# 1. 下载
pip install -U modelscope
modelscope download --model {repo_id} --local_dir ./lora

# 2. 合并权重（得到完整模型）
python scripts/merge_lora.py --base {base_model} --lora ./lora --output ./merged

# 3. 起 vLLM 服务
vllm serve ./merged --served-model-name lumina-intent-lora --port 8000
```

或者用 vLLM 直接加载 LoRA：

```bash
vllm serve {base_model} --enable-lora --lora-modules lumina-intent-lora=./lora
```

## 评测指标

见仓库内 `eval_results.json`。

## 限制与声明

- 本模型仅用于意图分类等辅助任务，**不用于心理诊断或医疗建议**
- 输出内容可能存在偏差，调用方需保留安全兜底规则
"""
    card_path.write_text(card, encoding="utf-8")
    _log(f"模型卡已生成：{card_path}")
    return card_path


def _write_metadata(adapter_dir: Path, repo_id: str, base_model: str, note: str) -> Path:
    """写出模型元信息，供服务端与审计使用。"""
    meta_path = adapter_dir / "train_config.json"
    payload = {
        "repo_id": repo_id,
        "base_model": base_model,
        "task": "intent-classification",
        "note": note,
        "pushed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta_path


def push_model(
    adapter_dir: str,
    repo_id: str,
    note: str = "",
    base_model: str = "Qwen/Qwen2.5-1.5B-Instruct",
    dry_run: bool = False,
) -> int:
    """主推送流程。

    Args:
        adapter_dir: 微调产物目录
        repo_id: 魔搭模型仓库 ID
        note: 模型说明
        base_model: 基座模型 ID
        dry_run: 只打印将要上传的文件，不实际上传

    Returns:
        进程退出码
    """
    source = Path(adapter_dir).expanduser().resolve()
    if not source.is_dir():
        return _fail(f"目录不存在：{source}")

    token = os.getenv("MODELSCOPE_TOKEN", "").strip()
    if not token and not dry_run:
        return _fail(
            "未设置 MODELSCOPE_TOKEN",
            "在 https://modelscope.cn 右上角头像 → 访问令牌 → 新建令牌 获取",
        )

    _log(f"产物目录：{source}")
    _log(f"目标仓库：{repo_id}")

    # 生成模型卡与元信息（这两步也写回产物目录，方便本地留档）
    _write_model_card(source, repo_id, note, base_model)
    _write_metadata(source, repo_id, base_model, note)

    files = _collect_files(source)
    if not files:
        return _fail(
            f"目录内没有可上传的文件：{source}",
            "确认微调是否已完成，以及产物目录是否正确",
        )

    total = 0
    _log(f"待上传文件 {len(files)} 个：")
    for path in files:
        size = path.stat().st_size
        total += size
        _log(f"  - {path.name}  ({size / 1024 / 1024:.2f} MB)")

    if dry_run:
        _log(f"--dry-run 模式，跳过上传。合计 {total / 1024 / 1024:.2f} MB")
        return 0

    try:
        from modelscope.hub.api import HubApi
    except ImportError:
        return _fail("未安装 modelscope SDK", "pip install -U 'modelscope>=1.18.0'")

    try:
        api = HubApi()
        api.login_token(token)
        _log("魔搭登录成功，开始上传")
        api.upload_folder(
            repo_id=repo_id,
            folder_path=str(source),
            repo_type="model",
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        return _fail(
            f"上传失败：{exc}",
            "常见原因：1) Token 权限不足；2) 仓库不存在或不是模型类型；3) 网络问题",
        )

    _log(f"✅ 上传完成：https://modelscope.cn/models/{repo_id}")
    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="推送 LoRA 适配器到 ModelScope 魔搭")
    parser.add_argument("--adapter", required=True, help="微调产物目录")
    parser.add_argument("--repo", required=True, help="魔搭模型仓库 ID，如 your-org/lumina-intent-lora")
    parser.add_argument("--note", default="", help="模型说明（会写进模型卡）")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-1.5B-Instruct", help="基座模型 ID")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要上传的文件")
    args = parser.parse_args()

    return push_model(
        adapter_dir=args.adapter,
        repo_id=args.repo,
        note=args.note,
        base_model=args.base_model,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
