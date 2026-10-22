#!/usr/bin/env python3
"""构造微调（SFT）数据集。

数据来源与优先级：
    1. 线上真实 BadCase      —— 最高价值，是模型没见过的真实分布
    2. 人工标注补充集        —— 覆盖低频但关键的类别（如危机边界）
    3. 黄金集评测用例        —— 作为验证集，也可用作训练数据的种子

重要原则（与 docs/SOP.md SOP-6 一致）：
    **微调数据必须来自真实失败样本。**
    凭空编造的数据微调完，在真实流量上不会更好——因为编造数据的分布
    和真实用户表达的分布不一样。

输出格式：ms-swift / LLaMA-Factory 通用 messages 格式
    {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}

用法：
    # 基本用法
    python packages/models/scripts/build_dataset.py --output data/sft

    # 包含线上 BadCase
    python packages/models/scripts/build_dataset.py \\
        --output data/sft \\
        --real-data packages/datasets/real_user/labeled.jsonl

    # 只做数据检查不写文件
    python packages/models/scripts/build_dataset.py --dry-run
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import hashlib
import json
import random
import sys
from pathlib import Path

# 意图标签 → 训练时使用的简短描述。
# 这些描述会进 Prompt，措辞必须与线上 entry 节点的意图定义保持一致，
# 否则微调出来的模型和线上编排对不上。
INTENT_DESCRIPTIONS = {
    "EMOTION_SUPPORT": "识别用户当前的情绪状态并给予共情与支持。",
    "KNOWLEDGE_QUERY": "回答积极心理学、认知行为疗法等专业知识问题。",
    "COGNITIVE_RESTRUCTURING": "帮助用户识别并检验负性自动思维。",
    "BEHAVIORAL_ACTIVATION": "帮助用户把意图转化为可执行的小行动。",
    "STRENGTHS_EXPLORATION": "引导用户识别与回顾个人优势。",
    "JOURNALING": "引导用户记录情绪日记。",
    "SMALLTALK": "一般性闲聊与寒暄。",
    "MEDICAL_QUERY": "涉及疾病、诊断、用药等医疗问题，需要转介专业机构。",
    "CRISIS": "用户表达自伤或自杀风险，必须立即进入危机干预流程。",
    "UNKNOWN": "意图不明确，需要澄清。",
}

# 验证集占比。10% 是经验值：太小则验证噪声大，太大则浪费训练数据。
VAL_RATIO = 0.1

# 完全重复样本的相似度阈值（基于归一化文本的哈希）
DEDUP_SEED = 42


@dataclasses.dataclass
class Sample:
    """一条训练样本。"""

    system: str
    user: str
    assistant: str
    intent: str
    source: str  # 数据来源标记，用于分析各来源的贡献

    def to_messages(self) -> dict[str, list[dict[str, str]]]:
        """转成 ms-swift / LLaMA-Factory 的 messages 格式。"""
        return {
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.user},
                {"role": "assistant", "content": self.assistant},
            ]
        }

    def fingerprint(self) -> str:
        """生成去重指纹。

        只用 user + assistant 内容，忽略 system（system 模板是固定的，
        带上会让本该去重的样本指纹不同）。
        """
        raw = f"{self.user.strip()}|{self.assistant.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _log(message: str) -> None:
    """统一日志输出。"""
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _read_jsonl(path: Path) -> list[dict]:
    """读取 JSONL 文件，跳过空行与解析失败的行。"""
    if not path.exists():
        _log(f"文件不存在，跳过：{path}")
        return []
    items: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                _log(f"第 {lineno} 行解析失败，已跳过：{exc}")
    return items


def _load_real_data(path: Path) -> list[Sample]:
    """加载线上真实标注数据。

    期望格式（每行一个 JSON）：
        {"user": "...", "assistant": "...", "intent": "EMOTION_SUPPORT"}
    """
    samples: list[Sample] = []
    for item in _read_jsonl(path):
        user = (item.get("user") or item.get("input") or "").strip()
        assistant = (item.get("assistant") or item.get("output") or "").strip()
        intent = (item.get("intent") or "UNKNOWN").strip()
        if not user or not assistant:
            _log("缺少 user/assistant 字段，已跳过")
            continue
        samples.append(
            Sample(
                system=INTENT_DESCRIPTIONS.get(intent, INTENT_DESCRIPTIONS["UNKNOWN"]),
                user=user,
                assistant=assistant,
                intent=intent,
                source="real",
            )
        )
    return samples


def _load_golden_cases(path: Path) -> list[Sample]:
    """把黄金集评测用例转成训练样本。

    黄金集默认只用作验证集；加 --use-golden-for-train 时才进训练集，
    避免「用测试集训练」导致评测指标虚高。
    """
    samples: list[Sample] = []
    for item in _read_jsonl(path):
        user = (item.get("input") or "").strip()
        expected = (item.get("expected_output") or "").strip()
        intent = (item.get("intent") or "UNKNOWN").strip()
        if not user or not expected:
            continue
        samples.append(
            Sample(
                system=INTENT_DESCRIPTIONS.get(intent, INTENT_DESCRIPTIONS["UNKNOWN"]),
                user=user,
                assistant=expected,
                intent=intent,
                source="golden",
            )
        )
    return samples


def _dedup(samples: list[Sample]) -> list[Sample]:
    """按指纹去重，保留首次出现的样本。

    真实数据里同一条用户输入被标注多次很常见（多人标注 / 重复上报），
    不去重会让某些样本权重过高。
    """
    seen: set[str] = set()
    result: list[Sample] = []
    for sample in samples:
        fp = sample.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        result.append(sample)
    return result


def _balance(samples: list[Sample], max_per_intent: int) -> list[Sample]:
    """按意图类别做数量上采样/下采样，避免类别极度不均衡。

    意图识别任务里，SMALLTALK / UNKNOWN 天然数量远多于 CRISIS。
    不平衡会让模型对高频类别过拟合、低频类别学不会。
    """
    by_intent: dict[str, list[Sample]] = collections.defaultdict(list)
    for sample in samples:
        by_intent[sample.intent].append(sample)

    balanced: list[Sample] = []
    for intent, group in by_intent.items():
        if len(group) > max_per_intent:
            _log(f"意图 {intent} 有 {len(group)} 条，下采样到 {max_per_intent}")
            group = random.sample(group, max_per_intent)
        elif len(group) < max_per_intent // 3:
            # 样本太少的类别做有限上采样（最多 3 倍），避免完全丢失该类别信号
            _log(f"意图 {intent} 仅 {len(group)} 条，有限上采样")
            extra = random.choices(group, k=min(max_per_intent // 3 - len(group), len(group)))
            group = group + extra
        balanced.extend(group)
    return balanced


def _split(samples: list[Sample], val_ratio: float) -> tuple[list[Sample], list[Sample]]:
    """划分训练集与验证集。

    **按 intent 分层划分**：保证验证集里每个类别都有样本，
    否则某个意图的指标会因为验证集里没样本而失真。
    """
    by_intent: dict[str, list[Sample]] = collections.defaultdict(list)
    for sample in samples:
        by_intent[sample.intent].append(sample)

    train: list[Sample] = []
    val: list[Sample] = []
    for group in by_intent.values():
        random.shuffle(group)
        # 至少留 1 条进验证集（除非该类别只有 1 条）
        val_size = max(1, int(len(group) * val_ratio)) if len(group) > 1 else 0
        val.extend(group[:val_size])
        train.extend(group[val_size:])

    random.shuffle(train)
    random.shuffle(val)
    return train, val


def _write_jsonl(samples: list[Sample], path: Path) -> None:
    """写出 JSONL 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample.to_messages(), ensure_ascii=False) + "\n")
    _log(f"已写入 {path}（{len(samples)} 条）")


def _print_stats(samples: list[Sample], title: str) -> None:
    """打印数据集统计。"""
    counter = collections.Counter(s.intent for s in samples)
    source_counter = collections.Counter(s.source for s in samples)
    _log(f"--- {title} ---")
    _log(f"总量：{len(samples)}")
    _log("意图分布：" + ", ".join(f"{k}={v}" for k, v in counter.most_common()))
    _log("来源分布：" + ", ".join(f"{k}={v}" for k, v in source_counter.most_common()))


def build(
    output: str,
    real_data: str | None = None,
    golden: str | None = None,
    use_golden_for_train: bool = False,
    max_per_intent: int = 800,
    val_ratio: float = VAL_RATIO,
    dry_run: bool = False,
) -> int:
    """主流程。

    Args:
        output: 输出目录
        real_data: 线上标注数据路径（JSONL）
        golden: 黄金集路径（目录或文件）
        use_golden_for_train: 是否把黄金集也放进训练集
        max_per_intent: 每个意图类别的最大样本数
        val_ratio: 验证集比例
        dry_run: 只统计不写文件

    Returns:
        进程退出码
    """
    random.seed(DEDUP_SEED)

    samples: list[Sample] = []

    # 1. 真实标注数据（价值最高）
    if real_data:
        real_samples = _load_real_data(Path(real_data))
        _log(f"加载真实数据：{len(real_samples)} 条")
        samples.extend(real_samples)

    # 2. 黄金集
    golden_path = Path(golden) if golden else None
    golden_samples: list[Sample] = []
    if golden_path and golden_path.exists():
        if golden_path.is_dir():
            golden_files = sorted(golden_path.rglob("*.jsonl"))
        else:
            golden_files = [golden_path]
        for file_path in golden_files:
            golden_samples.extend(_load_golden_cases(file_path))
        _log(f"加载黄金集：{len(golden_samples)} 条")

        if use_golden_for_train:
            samples.extend(golden_samples)
        else:
            # 默认黄金集只进验证集
            samples.extend(golden_samples[:0])
            _log("黄金集默认只作验证集，不进训练集（如需进训练集请加 --use-golden-for-train）")

    if not samples:
        _log("没有可用样本。请检查 --real-data / --golden 路径。")
        return 1

    # 3. 去重
    before = len(samples)
    samples = _dedup(samples)
    _log(f"去重：{before} → {len(samples)}")

    # 4. 平衡
    samples = _balance(samples, max_per_intent)

    # 5. 划分
    train, val = _split(samples, val_ratio)

    # 6. 黄金集补充验证集（保证验证集覆盖关键类别）
    if golden_samples and not use_golden_for_train:
        existing = {s.fingerprint() for s in val}
        for sample in golden_samples:
            if sample.fingerprint() not in existing:
                val.append(sample)
                existing.add(sample.fingerprint())
        _log(f"黄金集补充验证集后共 {len(val)} 条")

    _print_stats(train, "训练集")
    _print_stats(val, "验证集")

    if dry_run:
        _log("--dry-run 模式，不写文件")
        return 0

    out_dir = Path(output).expanduser().resolve()
    _write_jsonl(train, out_dir / "train.jsonl")
    _write_jsonl(val, out_dir / "val.jsonl")

    # 数据集卡片：记录构建参数与统计，便于复现
    card = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "train_count": len(train),
        "val_count": len(val),
        "val_ratio": val_ratio,
        "max_per_intent": max_per_intent,
        "seed": DEDUP_SEED,
        "sources": {
            "real_data": real_data,
            "golden": golden,
            "golden_in_train": use_golden_for_train,
        },
        "intent_distribution": dict(collections.Counter(s.intent for s in train)),
    }
    card_path = out_dir / "dataset_card.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"数据集卡片已写入 {card_path}")

    _log("✅ 完成。下一步：执行微调脚本")
    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="构造意图识别微调数据集")
    parser.add_argument("--output", default="data/sft", help="输出目录")
    parser.add_argument("--real-data", help="线上标注数据 JSONL 路径")
    parser.add_argument("--golden", help="黄金集路径（目录或文件）")
    parser.add_argument(
        "--use-golden-for-train",
        action="store_true",
        help="把黄金集也放进训练集（默认只作验证集）",
    )
    parser.add_argument("--max-per-intent", type=int, default=800, help="每个意图的最大样本数")
    parser.add_argument("--val-ratio", type=float, default=VAL_RATIO, help="验证集比例")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    return build(
        output=args.output,
        real_data=args.real_data,
        golden=args.golden,
        use_golden_for_train=args.use_golden_for_train,
        max_per_intent=args.max_per_intent,
        val_ratio=args.val_ratio,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
