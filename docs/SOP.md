# Lumina · 标准作业流程（SOP）

> 本文档是**可执行的操作手册**。每条流程都包含：触发条件 → 前置检查 → 分步操作 → 产出物 → 质量门禁 → 回滚方案。
> 所有命令均在仓库根目录执行。

---

## 0. 流程总览

```
                     ┌──────────────────┐
                     │  用户反馈 / 埋点  │
                     │  点踩 / 低分 / 崩溃 │
                     └────────┬─────────┘
                              │ 落库 bad_cases
                              ▼
                     ┌──────────────────┐
                     │  ① 归因分类       │  人工 + 规则，8 类标签
                     └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        检索问题          生成问题         工程问题
              │               │               │
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ 修 RAG   │   │ 改 Prompt│   │ 改代码   │
        └────┬─────┘   └────┬─────┘   └────┬─────┘
             └──────────────┼──────────────┘
                            ▼
                     ┌──────────────────┐
                     │  ② 加回归用例    │  BadCase → 黄金集
                     └────────┬─────────┘
                              ▼
                     ┌──────────────────┐
                     │  ③ 改 + 离线评测  │  make eval，门禁不通过则不合并
                     └────────┬─────────┘
                              ▼
                     ┌──────────────────┐
                     │  ④ 灰度发布       │  1% → 10% → 50% → 100%
                     └────────┬─────────┘
                              ▼
                     ┌──────────────────┐
                     │  ⑤ 观测 + 复盘   │  指标看板 + 迭代报告
                     └──────────────────┘
```

**核心原则：任何修复必须先变成一条回归用例，否则不允许合并。**

---

## SOP-0 · 代码托管与协作（GitHub）

> 本仓库托管在 `github.com/dawnkeyan/perma`。本节讲三件事：第一次怎么把仓库推上去、
> 日常怎么推送与发 PR、以及一台机器上如何让「公司仓库」与「个人仓库」的身份互不干扰。

### 触发条件

- 第一次把本地仓库关联到 GitHub 远程仓库
- 换机器 / 换账号后要能推送
- 日常提交后要推上去并走 PR

### Step 1 · 一次性配置（只写仓库级配置，不动全局账号）

```bash
# 1.1 关联远程仓库（写进 .git/config）
git remote add origin git@github.com:dawnkeyan/perma.git
git remote -v

# 1.2 固定这个仓库的提交身份
git config --local user.name  "dawnkeyan"
git config --local user.email "dawnkeyan@126.com"

# 1.3 指定用哪把私钥（不要把公司的密钥交给 GitHub）
git config --local core.sshCommand "ssh -i ~/.ssh/id_ed25519_github -o IdentitiesOnly=yes"

# 1.4 验证认证：应回显 "Hi dawnkeyan! You've successfully authenticated"
ssh -T git@github.com
```

**为什么坚持用 `--local`**：全局 `~/.gitconfig` 里往往还是上一个工作环境的身份与凭据。
`--local` 只改当前仓库的 `.git/config`，于是同一台机器上可以同时存在
「公司仓库用公司身份、个人仓库用个人身份」，两边互不污染，也不用来回
`git config --global --unset`。

**每次提交前复核身份**（防止误用全局身份把公司邮箱带进公开仓库）：

```bash
git log -1 --format='%an <%ae>'
# 期望：dawnkeyan <dawnkeyan@126.com>
```

### Step 2 · 公钥没注册时的处理

现象：`Permission denied (publickey)`。GitHub 不认识你的公钥就一定推不上去。

**先有一把够格的钥匙**：用 ed25519（GitHub 官方推荐）。若用 RSA，
长度建议 ≥ 3072 位，低于 3072 会被表单拦下并提示 `Key is too short`。

```bash
ssh-keygen -t ed25519 -C "github:<你的邮箱>" -f ~/.ssh/id_ed25519_github
pbcopy < ~/.ssh/id_ed25519_github.pub   # 直接进剪贴板，避免手打残缺
```

1. 打开 <https://github.com/settings/ssh/new>
2. **先确认右上角登录的是目标账号**——加到别的账号名下是最常见的失败原因
3. Title 填 `perma-mac`，Key 框直接 `Cmd+V` → **Add SSH key**
4. 页面会列出该密钥的指纹，与本地核对一致才算加对：
   ```bash
   ssh-keygen -lf ~/.ssh/id_ed25519_github.pub
   ```
5. 回到终端重跑 `ssh -T git@github.com` 验证（GitHub 侧立即生效，不用等）

### Step 3 · 推送分支

```bash
git push -u origin main
git push -u origin develop
git push -u origin feat/tool-catalog-and-quality-gate
```

分支模型（与 CONTRIBUTING 一致）：

| 分支 | 用途 | 能否直推 |
| --- | --- | --- |
| `main` | 生产发布 | 否；只能由 PR 合入，用 merge commit 保留历史 |
| `develop` | 集成分支 | 否；只能由 PR 合入，用 squash merge |
| `feat/ fix/ docs/ refactor/ ops/` | 特性分支，从 `develop` 切出 | 是 |

### Step 4 · 发 PR

1. 推分支 → 仓库页会出现 **Compare & pull request** 按钮
2. PR 描述按模板写全四段（详见 CONTRIBUTING）：
   - **改了什么** — 改动文件与行为变化
   - **为什么改** — 问题现象 + 根因 + 为什么选这个方案（**最重要**）
   - **怎么验证** — 可复现的验证命令；涉及评测的写清指标分数变化
   - **影响面** — 是否改协议 / 是否需要迁移 / 是否影响线上
3. 四个 CI job 全绿才允许合并

### Step 5 · CI 四个 job 分别拦什么

| job | 拦的问题 | 失败时怎么办 |
| --- | --- | --- |
| `backend`（后端质量门禁） | ruff / mypy 报错 | 本地 `make lint && make typecheck` 先过 |
| `frontend`（小程序质量门禁） | TS 类型 / ESLint 报错 | 本地 `npm run typecheck && npm run lint` |
| `eval`（离线评测门禁） | 指标下降、安全类指标退步 | `make eval` 看哪条指标掉了，转 BadCase SOP |
| `security`（安全扫描） | 提交历史里出现密钥 | 换掉并轮换密钥，**不要**只删文件 |

CI 触发时机：push 到 `main` / `develop`，以及以它们为目标的 PR。

### 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `REMOTE HOST IDENTIFICATION HAS CHANGED!` | GitHub 轮换过 SSH 主机密钥，本机 `known_hosts` 里存的是旧的那把 | 先把新密钥指纹与 [官方指纹清单](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) 核对一致，再 `ssh-keygen -R github.com` 重连 |
| `Permission denied (publickey)` | 公钥没加到 GitHub，或 `core.sshCommand` 指向了别的私钥 | 走 Step 2；核对 `git config --local core.sshCommand` |
| 添加公钥时提示 `Key is too short` | RSA 密钥长度不足 3072 位 | 改用 ed25519：`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_github` |
| 公钥明明加了却仍被拒 | 钥匙加到了另一个 GitHub 账号下 | 打开 settings/keys 核对右上角用户名与密钥列表 |
| HTTPS push 极慢甚至超时 | 到 `github.com` 的 443 链路质量差 | 改用 SSH；实在不行走 `ssh -p 443 git@ssh.github.com` |
| 提交带出公司邮箱 | 用了全局 `user.email` | `git config --local` 固定身份，并按 Step 1 复核 |
| 推完 CI 没跑 | PR 目标分支不是 `main`/`develop` | 确认 PR 目标分支；CI 只监听这两个分支 |

### 发布前自检清单（推到公开仓库前逐条过）

```bash
# 1. 真实 .env 没被跟踪（.env.example 是模板，可以跟踪）
git ls-files | grep -E '^\.env$' && echo "危险：.env 被跟踪了"

# 2. 没有残留占位符
git grep -nE 'your-org|<your-repo-url>' -- .

# 3. 没有公司标识、内网地址、个人绝对路径
git grep -inE 'weipaitang|/Users/[a-z]+/' -- .

# 4. 提交身份只有本人
git log --format='%an <%ae>' | sort -u

# 5. 质量门禁本地全绿
make lint && make typecheck && make test && make eval
```

个人私有材料（本地笔记、简历类文件等）统一由 `.gitignore` 末段的规则排除，
**不要**为了「让仓库看起来更完整」而把这类文件 `git add -f` 进去。

---

## SOP-1 · 项目冷启动（Day 1 ~ Day 14）

### 触发条件
第一次 clone 仓库，或新环境部署。

### 步骤

| # | 操作 | 命令 / 产出 |
| --- | --- | --- |
| 1 | 复制环境变量 | `cp .env.example .env`，填写 LLM_API_KEY、POSTGRES_PASSWORD、JWT_SECRET |
| 2 | 拉起基础依赖 | `make init`（postgres + redis + 建表 + 建 pgvector 扩展） |
| 3 | 校验连通性 | `curl localhost:8000/healthz` 返回 `{"status":"ok"}` |
| 4 | 灌入演示数据 | `make db-seed` |
| 5 | 构建知识库 | `make kb-ingest`（向量化 + 建 BM25 索引） |
| 6 | 跑通评测 | `make eval`（首次会建立基线快照） |
| 7 | 起前端 | `make miniapp` |

### 门禁
- `/healthz` 返回 ok
- `make eval` 全部用例有结果，无异常抛出（分数低没关系，要有基线）

---

## SOP-2 · 新增一个功能

### 触发条件
需求进入迭代池。

### 步骤

**Step 1 — 需求定义（0.5h）**

在 `docs/PRD.md` 补一行功能条目，含：编号、优先级、**验收标准**（可测）、**失败边界**。
> 验收标准必须写成"给定 X，当 Y，则 Z"的形态。没有验收标准的条目不许进开发。

**Step 2 — 契约先行（0.5h）**

```python
# services/api/app/schemas/feature.py
class FeatureRequest(BaseModel):
    """..."""
    model_config = ConfigDict(extra="forbid")   # 多传字段直接报错，不静默忽略
    ...

class FeatureResponse(BaseModel):
    """..."""
```

**Step 3 — 实现（按分层顺序）**

```
schemas/          契约
    ↓
domain/           业务模型与不变量（纯函数，可单测）
    ↓
services/         用例编排（事务边界在这里）
    ↓
api/v1/            路由（只做参数校验 + 调用 service + 序列化）
```

> 规则：**路由层不写业务逻辑，service 层不碰 HTTP 对象。** 这条边界守住了，测试才能不依赖 FastAPI。

**Step 4 — 单测（与实现同步）**

```bash
cd services/api
pytest tests/test_features.py -v --cov=app.services.features
```

必测：正常路径 / 参数非法 / 边界值 / 依赖失败时的降级路径。

**Step 5 — 契约测试**

```bash
pytest -m contract          # schemathesis 校验 OpenAPI
```

**Step 6 — 埋点**

在 `apps/miniapp/src/utils/track.ts` 补事件；后端在 `llm_calls` 记录 `feature` 字段。

**Step 7 — 合并门禁**

```bash
make lint && make typecheck && make test
```

全部通过才能开 PR。

---

## SOP-3 · Prompt 变更（最高频，也最容易出事）

### 触发条件
- 用户反馈回答质量差
- 评测指标下滑
- 要上新场景

### 步骤

**Step 1 — 先确认是 Prompt 问题还是别的问题**

对照归因表，**不要一上来就改 Prompt**：

| 现象 | 大概率根因 | 正确动作 |
| --- | --- | --- |
| 答案内容对，但漏了要点 | Prompt 约束不够 | 改 Prompt |
| 答案内容完全错 | 检索没召回 / 知识库缺失 | 先修 RAG 或加知识 |
| 答案编造了知识库没有的内容 | 上下文不足 + 约束缺失 | 先修 RAG，再加负向约束 |
| 回答格式乱 | 协议约束问题 | 改 Prompt + 用结构化输出 |
| 首字太慢 | 上下文太长 | 裁剪上下文，不是改 Prompt |
| 某类问题总是不好 | 该类问题知识库覆盖不足 | 补知识库 |

**Step 2 — 建分支并改版本文件**

```bash
git checkout -b feat/prompt-coach-v4
```

编辑 `packages/prompts/system/coach.yaml`，**版本号 +1**，并写 `changelog`：

```yaml
version: 4
changelog: |
  v4 修复：微行动常超时长。增加"必须 <=5 分钟可完成"约束并加 2 组负例。
```

> 规则：**永远不原地改旧版本**。旧版本保留，用于回滚和 A/B 对比。

**Step 3 — 跑评测并对比基线**

```bash
make eval
```

CI 会输出与基线的对比表：

| case | baseline | current | delta | status |
| --- | --- | --- | --- | --- |
| case_012 微行动超时 | 0.41 | 0.78 | +0.37 | PASS |
| case_034 过度共情 | 0.82 | 0.79 | -0.03 | PASS |
| case_051 引用编造 | 0.88 | 0.85 | -0.03 | WARN |

**门禁规则**：
- 任一 case 跌幅 > 0.10 → **FAIL**，禁止合并
- 总体核心指标跌幅 > 0.02 → **FAIL**
- 单 case 跌幅 > 0.03 → **WARN**，需在 PR 里写明原因并人工确认

**Step 4 — 人工抽检（必做，不能只信自动指标）**

```bash
python -m app.eval.report --show-cases 20 --show-failures-only
```

导出 20 条人工过一遍。**LLM-as-Judge 只是代理指标，最终判断必须有人看。**

**Step 5 — 灰度**

```bash
# 1% 灰度
export FEATURE_FLAG_COACH_V4=1
make up-prod
# 观察 2 小时，看 llm_calls 中 prompt_version=4 的样本
```

观察指标：点踩率、平均轮次、TTFT、token 成本、危机误判率。

**Step 6 — 扩大或回滚**

```bash
# 扩大到 10%
export FEATURE_FLAG_COACH_V4=10
# 回滚
export FEATURE_FLAG_COACH_V4=0
```

> 回滚只改环境变量 + 重启，**不需要重新部署镜像**。这是灰度开关设计的核心价值。

---

## SOP-4 · BadCase 处理闭环（核心 SOP）

### 触发条件
每日 / 每周例行；或用户投诉。

### Step 1 — 收集

三个来源统一进 `bad_cases` 表：

| 来源 | 采集方式 | 优先级 |
| --- | --- | --- |
| 用户点踩 | 小程序端上报，服务端落库 | P0 |
| 低分（1-2 星） | 同上 | P1 |
| 人工抽检发现 | 运营后台手动录入 | P0 |
| 线上监控异常 | 规则自动生成（如引用缺失率突增） | P0 |

### Step 2 — 归因（8 类标签）

```
retrieval_miss      检索未召回正确知识
retrieval_noise     召回了但被无关内容挤掉
context_truncated   上下文被截断导致信息丢失
instruction_fail    没按输出协议/约束执行
hallucination       编造了知识库没有的内容
over_empathy        过度共情，缺少实质内容
medical_overreach   越界给出医疗结论
latency_cost        延迟过高或成本超预算
```

归因方式：**规则预判 + 人工确认**。规则可自动抓的：
- `retrieval_miss`：黄金 chunk 不在召回列表里
- `hallucination`：输出的引用编号不在检索结果中
- `instruction_fail`：输出正则不匹配协议
- `medical_overreach`：命中医疗词表
- `latency_cost`：`llm_calls.latency_ms` 超阈值

### Step 3 — 定级与分派

| 级别 | 定义 | SLA | 处理 |
| --- | --- | --- | --- |
| P0 | 危机识别失败 / 医疗越界 / 严重幻觉 | 24h | 立即修复 + 紧急发布 |
| P1 | 明显答非所问 / 格式乱 / 体验断崖 | 3d | 本迭代内 |
| P2 | 表达不佳 / 微行动不够具体 | 1w | 排入迭代池 |
| P3 | 偏好类 | 1月 | 择机 |

### Step 4 — 修复（按归因走不同路径）

```
P0/P1 走"热修"流程：
  1. 在 staging 复现
  2. 最小改动修复
  3. 补回归用例到 packages/datasets/golden/
  4. make eval 通过
  5. 灰度 1% → 观察 1h → 10% → 观察 4h → 100%
  6. 写 incident 记录到 CHANGELOG.md
```

### Step 5 — 沉淀（最容易漏，但最重要）

```bash
# 把 BadCase 转成黄金集回归用例
python -m app.eval.promote \
  --case-id bc_20261020_0031 \
  --category regression \
  --note "微行动超时，v4 修复后需保持"
```

**规则：同一类问题出现第 2 次，必须沉淀为回归用例；出现第 3 次，必须沉淀为自动化检测规则。**

### Step 6 — 复盘

每周五写 `reports/weekly/W{周数}.md`（在 gitignored 的 `docs/_internal/`，不提交），内容：

- 本周 BadCase 数按类别分布
- 已修复 / 未修复 / 复发
- Prompt 版本变更记录
- 指标变化（点踩率、留存、成本）
- 下周计划

> 复盘材料是**对外沟通技术工作过程**的最佳素材来源（讲"我怎么做迭代"比讲"我写了什么功能"有力得多）。

---

## SOP-5 · 知识库更新

### 触发条件
新增心理学方法、修正错误内容、发现召回缺失。

### 步骤

1. 在 `packages/knowledge/<主题>/<条目>.md` 新增/修改条目
2. **元数据 frontmatter 必须完整**（缺失会被 ingest 脚本拒绝）：

```yaml
---
title: 认知重构
theory: CBT
tags: [认知行为疗法, 情绪调节]
evidence_level: 4        # 1-5，低于 3 的不得作为主要依据
applicable_to: [焦虑, 低落, 自责]
contraindications: [危机状态, 严重失眠]
source: Beck (1976) 认知行为疗法
license: original        # original | quoted
---
```

3. 本地校验：

```bash
python -m app.rag.ingest --validate-only --source packages/knowledge
```

4. 重建索引（分片重建，不锁全表）：

```bash
make kb-ingest
```

5. **检索回归**（关键：新增知识不能破坏原有召回）

```bash
python -m app.eval.retrieval --dataset packages/datasets/golden --top-k 5
```

对比 Recall@5 / nDCG@5，任何指标跌幅 > 2% 都要排查（通常是新条目与旧条目语义冲突）。

6. 发布。

### 禁止事项

- ❌ 直接往生产库里 `INSERT` chunk（绕过 embed/index 流程会导致向量与文本不一致）
- ❌ 一次改超过 20 个条目（无法归因指标变化）
- ❌ 使用无出处的内容

---

## SOP-6 · 模型微调

### 触发条件
意图分类准确率 < 95% 或人工规则误判率高；守卫模型召回不达标。

### 步骤

**Step 1 — 数据准备**

```bash
cd packages/models
python prepare_data.py \
  --input ../datasets/annotated_intent.jsonl \
  --output data/train.jsonl \
  --min-confidence 0.9 \
  --dedupe --strip-pii
```

检查输出：

```bash
python prepare_data.py --report data/train.jsonl
# 输出类别分布、长度分布、PII 残留检查结果
```

**门禁**：
- 类别分布：任一类占比 < 5% → 补数据
- 危机类样本 ≥ 200 条
- PII 残留 = 0（必须为 0，非 0 直接拒绝训练）

**Step 2 — 训练**

```bash
llamafactory-cli train \
  --model_name_or_path Qwen/Qwen2.5-1.5B-Instruct \
  --stage sft \
  --do_train true \
  --finetuning_type lora \
  --lora_rank 16 \
  --lora_alpha 32 \
  --dataset data/train.jsonl \
  --template qwen \
  --cutoff_len 2048 \
  --overwrite_cache true \
  --per_device_train_batch_size 4 \
  --gradient_accumulation_steps 4 \
  --learning_rate 1.0e-4 \
  --num_train_epochs 3 \
  --lr_scheduler_type cosine \
  --warmup_ratio 0.03 \
  --bf16 true \
  --gradient_checkpointing true \
  --output_dir saves/lumina-intent-lora
```

**Step 3 — 评测**

```bash
python eval_intent.py \
  --model saves/lumina-intent-lora \
  --test data/test.jsonl \
  --report reports/intent_eval.json
```

**门禁（硬性）**：

| 指标 | 阈值 |
| --- | --- |
| 整体准确率 | ≥ 0.95 |
| 危机类召回率 | ≥ 0.98（**红线，不可协商**） |
| 危机类精确率 | ≥ 0.85 |
| 推理延迟 p95 | ≤ 100ms |
| 与基线大模型相比 | 准确率下降 ≤ 0.01 才可上线（因为省 100 倍成本） |

**Step 4 — 部署**

```bash
# 推送到模型仓库
huggingface-cli upload lumina/lumina-intent-lora saves/lumina-intent-lora

# 本地 vLLM 启动
docker compose --profile local-models up -d vllm
```

**Step 5 — 影子模式 → 灰度**

```
阶段 1（影子）：线上流量复制一份发给微调模型，只记录不生效，比对准确率，跑 3 天
阶段 2（A/B）：10% 流量用微调模型，对比点踩率
阶段 3：50% → 100%
```

**Step 6 — 回滚**

配置层：`LLM_LOCAL_ENABLED=false`，秒级回滚，不影响主链路。

---

## SOP-7 · 发布上线

### 触发条件
功能/Bug 修复通过评测门禁。

### 步骤

```bash
# 1. 提交
git add -A && git commit -m "feat: 新增认知重构练习" && git push origin feature/xxx

# 2. PR + CI（必须全绿）
# 3. 合并到 main，CI 自动构建镜像
# 4. staging 部署并冒烟
bash ops/scripts/deploy.sh staging
bash ops/scripts/smoke.sh staging

# 5. 生产灰度
bash ops/scripts/deploy.sh production --canary 10
# 6. 观察 1h，无异常则全量
bash ops/scripts/deploy.sh production --canary 100
```

### 回滚

```bash
bash ops/scripts/rollback.sh production   # 回到上一个镜像 tag
# 或仅回滚 Prompt/功能开关（秒级）
```

### 发布检查清单

- [ ] CI 全绿（lint / typecheck / test / eval）
- [ ] 数据库迁移已在 staging 验证，且**向后兼容**（旧版本代码能跑）
- [ ] 健康检查通过 `/healthz`
- [ ] 冒烟脚本通过（登录、对话、危机词检测）
- [ ] 监控面板已打开（延迟/错误率/成本/点踩率）
- [ ] 回滚方案已就绪并演练过
- [ ] CHANGELOG 已更新

---

## SOP-8 · 日常与每周节奏

### 每日（30~60 分钟）

```bash
# 1. 看昨晚线上数据
open http://localhost:3000  # Grafana: Lumina Overview
# 关注：请求量、错误率、p95 延迟、成本、点踩率

# 2. 处理 P0/P1 BadCase
python -m app.eval.badcase list --priority P0 --status open

# 3. 提交代码
```

### 每周（半天）

- [ ] BadCase 复盘（按类别统计，找 Top-1 类别集中攻克）
- [ ] Prompt 微调（针对本周最高频的两类问题）
- [ ] 知识库补充（针对召回缺失的 Top-3 主题）
- [ ] 黄金集扩充（新增 10~20 条真实场景）
- [ ] 跑一次全量评测，与上周对比
- [ ] 写周报（`docs/_internal/weekly/`）
- [ ] 依赖与镜像安全扫描

### 每月

- [ ] 模型成本复盘 + 路由策略调整
- [ ] 安全演练（危机场景全链路测试）
- [ ] 知识库质量抽检（证据等级标注准确率）
- [ ] 路线图调整

---

## SOP-9 · 项目资产沉淀（对外可见性）

> 这一节的目的：**让仓库的公开历史自然呈现"一个真实在运营的产品"**，而不是"一个练习项目"。

### 原则

| 应该做 | 不应该做 |
| --- | --- |
| 提交信息描述**功能与意图**，不描述"学习/练习" | 提交 "learn rag" / "practice llm" |
| README 写产品价值、架构、数据、路线图 | README 写"这是个人练习项目" |
| CHANGELOG 按版本记录真实迭代 | CHANGELOG 一次性写完 |
| Issue 写真实问题、真实讨论 | Issue 全是 "todo" / "fix bug" |
| 保留 BadCase 讨论记录（脱敏后） | 只留代码不留过程 |
| 用规范的分支名与 PR 模板 | 直接推 main |

### 分支与提交规范

```
分支：feat/xxx | fix/xxx | chore/xxx | docs/xxx
提交：feat(chat): 增加认知重构入口
     fix(rag): 修正 RRF 常数 k 导致的多样性不足
     perf(gateway): 语义缓存改为异步写入
     docs(sop): 补充 BadCase 归因规则
```

### PR 模板必填项

- 变更动机（关联 Issue）
- 影响面（是否改了 Prompt / 检索 / 数据结构）
- 评测结果（与基线对比）
- 截图 / 录屏（前端改动）
- 回滚方式

### 每周固定动作

1. 处理完当天问题后，把**有讨论价值的问题**开成 Issue（脱敏）
2. 至少合并 1~2 个符合规范的 PR（走完整 review 流程）
3. 更新 CHANGELOG
4. 更新 README 的「进展」区块

> 目标：3 个月后仓库呈现的是**连续迭代的产品**、**有深度的技术讨论**、**可验证的评测数据**。这三样东西比任何"个人练习项目"标签都有说服力。

---

## 附录 A · 常用命令速查

```bash
make help                      # 全部命令
make init                      # 首次初始化
make dev-api                   # 本地起服务
make kb-ingest                 # 重建知识库索引
make eval                      # 离线评测
make eval-report               # 生成评测报告

# BadCase
python -m app.eval.badcase list --priority P0
python -m app.eval.badcase promote --case-id bc_xxx --category regression

# 检索调试（排查召回问题最常用）
python -m app.rag.inspect --query "总是觉得自己没用" --top-k 5
# 输出：召回的 chunk、分数、RRF 贡献、是否在黄金答案中

# Trace 回放（排查 Agent 走了哪条路径）
python -m app.eval.trace --session-id xxx --from-node retrieve

# 成本
python -m app.eval.cost --since 7d --group-by feature
```

## 附录 B · 归因速查表

| 症状 | 先查 | 再查 | 最后查 |
| --- | --- | --- | --- |
| 回答不对 | 召回的 chunk 是否正确（`rag.inspect`） | Prompt 是否约束到位 | 模型能力 |
| 回答编造 | 是否有足够依据 | Prompt 是否禁编造 | 降级模型是否更弱 |
| 格式乱 | 输出协议约束 | 是否走了降级模型 | 结构化输出解析 |
| 回答太泛 | 知识库是否覆盖该主题 | 是否召回了泛泛的通用条目 | 少样本示例 |
| 太慢 | TTFT 分段耗时 | 上下文长度 | 降级链是否频繁触发 |
| 拒答过度 | 越界判定阈值 | Prompt 里的禁止项是否过严 | 守卫模型误报 |
