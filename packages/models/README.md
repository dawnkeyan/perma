# packages/models · 模型与微调

> 本目录负责「模型资产」这一条线：**从基座模型到自部署推理**的完整链路。
> 目标：把意图识别这类**高频、确定性强、规则难覆盖**的任务，
> 从云端大模型降级到自部署的小模型，做到**更便宜、更快、数据不出内网**。

## 目录结构

| 路径 | 作用 |
| --- | --- |
| `scripts/download_model.py` | 从 ModelScope 魔搭拉取模型权重到本地/缓存目录 |
| `scripts/push_to_modelscope.py` | 把 LoRA 产物推送到魔搭模型仓库（含模型卡生成） |
| `scripts/build_dataset.py` | 从真实 BadCase + 黄金集构造 SFT 微调数据集 |
| `finetune/ms_swift_lora_intent.yaml` | ms-swift LoRA 微调配置（**主方案**） |
| `finetune/llamafactory_lora_intent.yaml` | LLaMA-Factory 微调配置（备选/对拍） |
| `requirements.txt` | 模型相关依赖（与 API 服务依赖隔离） |

## 整体链路

```
线上 BadCase
   │  人工标注（intent + 标准回答）
   ▼
packages/datasets/real_user/labeled.jsonl      ← 已 gitignore，不入库
   │
   │  build_dataset.py（去重 / 平衡 / 分层划分）
   ▼
data/sft/{train,val}.jsonl
   │
   │  ms-swift LoRA 训练（需 GPU）
   ▼
output/lumina-intent-lora/  (adapter_model.safetensors)
   │
   │  push_to_modelscope.py
   ▼
ModelScope 模型仓库  your-org/lumina-intent-lora
   │
   │  vLLM 拉取并起服务
   ▼
http://vllm:8000/v1  （OpenAI 兼容接口）
   │
   │  LLMGateway 降级链末端
   ▼
意图识别（本地 1.5B，零调用费）
```

## 快速开始

### 0. 准备魔搭 Token

```bash
# 1. 注册：https://modelscope.cn
# 2. 右上角头像 → 访问令牌 → 新建令牌 → 复制
export MODELSCOPE_TOKEN=your_token_here
```

### 1. 拉取基座模型

```bash
pip install -r packages/models/requirements.txt

python packages/models/scripts/download_model.py \
  --model Qwen/Qwen2.5-1.5B-Instruct
```

输出目录默认在 `~/.cache/modelscope`，可用 `--output` 指定。

### 2. 构造数据集

```bash
# 真实标注数据（从线上 BadCase 整理而来）
python packages/models/scripts/build_dataset.py \
  --output data/sft \
  --real-data packages/datasets/real_user/labeled.jsonl \
  --golden packages/datasets/golden/e2e/cases.jsonl \
  --dry-run
```

先 `--dry-run` 看分布是否合理（各类别数量、验证集占比），再去掉 `--dry-run` 正式生成。

### 3. 微调

**方案 A：魔搭 Notebook（免费/低价 GPU，零配置）**

1. 打开 https://modelscope.cn → 创空间 → Notebook
2. 选择 GPU 环境（免费额度通常够 1.5B LoRA 训几轮）
3. 在 Notebook 里执行：

```bash
!pip install -U ms-swift
!git clone <your-repo-url> lumina && cd lumina
!python packages/models/scripts/build_dataset.py --output data/sft --real-data <your-data>
!swift sft --config packages/models/finetune/ms_swift_lora_intent.yaml
```

4. 训练完成后在 Notebook 里推送产物：

```bash
!python packages/models/scripts/push_to_modelscope.py \
   --adapter output/lumina-intent-lora \
   --repo your-org/lumina-intent-lora
```

**方案 B：自建 GPU 服务器**

```bash
swift sft --config packages/models/finetune/ms_swift_lora_intent.yaml
python packages/models/scripts/push_to_modelscope.py \
  --adapter output/lumina-intent-lora \
  --repo your-org/lumina-intent-lora
```

**方案 C：云厂商 GPU 实例**

阿里云 `ecs.gn7i-c8g1.2xlarge`（A10 40G）/ 腾讯云 `GN7` / 火山引擎 GPU 实例。
按小时计费，1.5B LoRA 训 3 轮通常 **20~40 分钟**，成本约 ¥3~10。

### 4. 部署推理

**方式 1：vLLM 直接加载 LoRA（推荐，无需合并）**

```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct \
  --enable-lora \
  --lora-modules lumina-intent-lora=/path/to/lora \
  --served-model-name lumina-intent-lora \
  --port 8000
```

**方式 2：合并权重后部署（吞吐更高）**

```bash
python - <<'PY'
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = "Qwen/Qwen2.5-1.5B-Instruct"
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype="auto")
model = PeftModel.from_pretrained(model, "output/lumina-intent-lora")
model = model.merge_and_unload()
model.save_pretrained("output/merged")
AutoTokenizer.from_pretrained(base).save_pretrained("output/merged")
PY

vllm serve output/merged --served-model-name lumina-intent-lora --port 8000
```

**方式 3：容器化（生产推荐）**

仓库的 `docker-compose.yml` 里 `vllm` 服务已配好：

```bash
docker compose --profile local-models up -d vllm
```

配置项：

```bash
LLM_LOCAL_ENABLED=true
LLM_LOCAL_BASE_URL=http://vllm:8000/v1
LLM_LOCAL_MODEL=lumina-intent-lora
```

### 5. 接入线上降级链

`LLMGateway` 的降级链是：**主模型 → 备用模型 → 本地模型**。
本地模型放在链尾，语义是「云端全挂了还有本地兜底」。

如果要专门用本地模型做**意图识别**（而非兜底），在 `agents/nodes.py` 的
`entry_node` 里把意图识别拆出来单独调用本地模型——这一步属于架构调整，
需要按 `docs/SOP.md` SOP-2 走完整流程并补评测。

## 为什么选 Qwen2.5-1.5B-Instruct

| 维度 | 理由 |
| --- | --- |
| 中文能力 | Qwen 系列中文表现稳定，用户输入是中文 |
| 指令遵循 | 1.5B-Instruct 在分类任务上足够，格式遵循率高 |
| 显存 | LoRA 训练 8G 显存可跑，推理 4G 量化可跑 |
| 生态 | ms-swift + 魔搭 + vLLM 全链路支持，踩坑少 |
| 商用许可 | Apache 2.0，可商用 |

## 微调数据集的纪律

> **微调数据必须来自真实失败样本。**

| 规则 | 原因 |
| --- | --- |
| 真实 BadCase 优先 | 编造数据的分布 ≠ 真实用户表达的分布 |
| 黄金集默认只做验证集 | 用测试集训练会让指标虚高，失去参考价值 |
| 类别要平衡 | 闲聊类样本天然多，不平衡会让模型学不会危机类 |
| 划分要按类别分层 | 否则某类在验证集里没样本，指标失真 |
| 数据集卡片要留档 | 复现与追责的依据 |

数据来源目录 `packages/datasets/real_user/` 已被 `.gitignore` 排除——
**真实用户数据绝不入库**。

## 评测

微调后的模型要和「大模型做意图识别」对比，指标：

| 指标 | 说明 | 目标 |
| --- | --- | --- |
| `intent_accuracy` | 意图分类准确率 | ≥ 大模型基线 - 2% |
| `crisis_recall` | 危机意图召回率 | **1.00**（不允许低于大模型） |
| `avg_latency_ms` | 单次推理耗时 | ≤ 200ms |
| `cost_per_call` | 单次成本 | ≈ 0（自部署只有硬件成本） |

**危机类别的召回率是硬指标**。本地小模型漏判一次危机，
就是一次产品事故——所以：

1. 微调后**必须**跑 `packages/datasets/golden/safety/crisis.jsonl` 全量
2. 危机意图在**规则层已经有兜底**（`detect_crisis` 纯规则先于模型执行），
   所以模型漏判不会导致漏拦截——但仍要追求模型本身准确

## 常见问题

| 问题 | 原因 | 解决 |
| --- | --- | --- |
| `modelscope download` 卡住 | 网络 | 换镜像源；或先在本地下载再传服务器 |
| 训练显存 OOM | batch 太大 / 序列太长 | `per_device_train_batch_size` 调小，`gradient_accumulation_steps` 补齐 |
| loss 震荡 | 学习率过高 | LoRA 降到 5e-5，或 `warmup_ratio` 提到 0.2 |
| 训完准确率只有 60% | 数据太少或标签噪声 | 检查 `build_dataset.py --dry-run` 的分布；清洗标注 |
| vLLM 启动报模型找不到 | 路径错 / LoRA 未 enable | 核对 `--lora-modules` 路径；检查 `--enable-lora` |
| 线上没走到本地模型 | 降级链顺序 | 本地模型在链尾，只有云端全失败才触发；见上文「接入线上降级链」 |
