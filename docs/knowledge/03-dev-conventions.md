# 03 · 开发约定

> 目的：让代码风格、命名、错误处理在项目内保持一致，降低评审成本。
> 最后更新：2026-10-20

## 一、语言与运行时

| 项 | 约定 |
| --- | --- |
| Python | 3.12，全量 type hints，`from __future__ import annotations` |
| 异步 | 一律 async（`async def` + `await`），同步调用必须显式包 `run_in_threadpool` |
| TypeScript | strict 模式，禁止 `any`（必要时用 `unknown` + 类型收窄） |
| 行宽 | Python 100（ruff 配置），TypeScript 100 |
| 字符串 | 全部显式 `encoding="utf-8"` |

## 二、Python 代码规范

### 类型标注

```python
# ✅ 公开函数必须有完整标注
async def search(self, query: str, *, limit: int = 10) -> list[Chunk]:
    ...

# ❌ 缺少返回类型
async def search(self, query, limit=10):
    ...
```

内部工具函数可放宽，但**导出的公共接口必须有标注**（mypy 门禁会检查 `app` 包）。

### 日志

```python
from app.core.logging import get_logger
logger = get_logger(__name__)

logger.info("chat_request_started", session_id=sid, user_id=uid)
logger.error("chat_persist_failed", error=str(exc))
```

约定：

- **用结构化 key=value**，不要拼字符串：`logger.info(f"user {uid} said {msg}")` 是错的。
- **不要打印用户原文**。用户内容属于 PII，日志里只记长度、哈希或脱敏摘要。
- 级别：DEBUG 开发细节 / INFO 业务里程碑 / WARNING 可降级 / ERROR 需人工介入。
- 异常必须带 `error=str(exc)`，不要只写 `logger.exception` 而丢上下文。

### 异常处理

```python
# ✅ 捕获具体异常，做明确的降级
try:
    chunks = await self.retriever.search(query, limit=limit)
except (TimeoutError, ConnectionError) as exc:
    logger.warning("retrieval_degraded", error=str(exc))
    chunks = []

# ❌ 裸 except 吞掉所有错误
try:
    ...
except Exception:
    pass
```

规则：

- 禁止 `except: pass`。
- 降级路径必须写日志，且降级行为要在响应或 trace 中可见（不能静默）。
- 用户可见的失败走 `app/core/errors.py` 定义的错误码，不要裸抛 `HTTPException`（路由层统一处理）。

### 注释

- **注释写「为什么」，不写「是什么」**。
- 公开模块、类、复杂算法需要 docstring；简单函数不需要。
- 临时方案必须写 `TODO(owner): 说明` 并关联 Issue。

```python
# ✅
# RRF 融合：向量的余弦相似度与 BM25 分数量纲不同，不能直接加权相加
# （详见 docs/knowledge/04-rag-prompt.md §2.3）
score = sum(1.0 / (k + rank) for rank in ranks)

# ❌
# 计算 RRF 分数
score = sum(1.0 / (k + rank) for rank in ranks)
```

## 三、目录与命名

| 类别 | 规范 | 例 |
| --- | --- | --- |
| 模块/文件 | `snake_case` | `retriever.py`、`chat_service.py` |
| 类 | `PascalCase` | `HybridRetriever`、`LLMGateway` |
| 函数/变量 | `snake_case` | `build_emotion_context()` |
| 常量 | `UPPER_SNAKE` | `MAX_GUARD_RETRY` |
| Pydantic 模型 | `PascalCase` + `Request/Response` 后缀 | `ChatRequest`、`ChatResponse` |
| 私有成员 | 前缀 `_` | `_rrf_fuse()` |
| 测试文件 | `test_<被测模块>.py` | `test_rag_retrieval.py` |

**类型别名与协议**统一放 `app/core/` 或就近模块，避免出现「types.py 垃圾桶」。

## 四、配置

- **所有配置从 `app/core/config.py` 的 `settings` 读取**，禁止在业务代码里 `os.getenv`。
- 新增配置项必须同时加到 `.env.example`，并在 `config.py` 里有类型与默认值。
- 布尔用 `bool`，开关类命名以 `ENABLED_` / `DISABLE_` 开头。
- 有默认值且不敏感的项给默认值；**敏感项（密钥、密码）不给默认值**，缺失时启动即失败。

```python
# ✅
from app.core.config import settings
timeout = settings.llm_timeout

# ❌
import os
timeout = int(os.getenv("LLM_TIMEOUT", "30"))
```

## 五、日志与追踪

- 每个请求生成 `trace_id`，贯穿 HTTP → Agent 节点 → LLM 调用 → 落库。
- 客户端报错时把 `trace_id` 返回给前端，方便对账。
- 关键 span 名称固定：`chat.request` / `llm.call` / `retrieval.search` / `safety.check`。
- 链路的 span 属性**不记录用户原文**（OTel collector 里也配了兜底删除规则）。

## 六、数据库

- 迁移用 Alembic，**手写迁移脚本，不改历史迁移**。
- 迁移必须**向后兼容**：旧版本代码在新 schema 上仍能跑（先加列 → 双写 → 再删列，分三次发布）。
- 所有查询显式写 `WHERE user_id = :uid`；越权防护是硬要求。
- 涉及用户隐私的字段（如 `content`）在导出/日志/埋点中一律脱敏。

## 七、Git 规范

### 分支

| 分支 | 用途 | 保护 |
| --- | --- | --- |
| `main` | 随时可发布 | 禁止直推，必须走 PR + CI |
| `develop` | 集成分支 | 允许直推 |
| `feat/<模块>-<短描述>` | 新功能 | — |
| `fix/<模块>-<短描述>` | 缺陷修复 | — |
| `refactor/<模块>-<短描述>` | 重构 | — |
| `chore/<模块>-<短描述>` | 构建/依赖/配置 | — |

### 提交信息（Conventional Commits）

```
<type>(<scope>): <subject>

<body>

<footer>
```

| type | 用于 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `refactor` | 不改变外部行为的重构 |
| `perf` | 性能优化 |
| `test` | 测试 |
| `docs` | 文档 |
| `chore` | 构建、依赖、配置 |
| `eval` | 评测集/指标变更（**本项目特有**） |
| `prompt` | Prompt 变更（**本项目特有**） |

示例：

```
prompt(coach): 强化微行动的具体性约束以提升可执行性

在 v3 中微行动经常停留在"放松一下"这类不可执行的表述。
v4 增加「必须包含动词 + 可验证的完成标准」约束。

评测：action_specificity 0.61 → 0.78，safety 指标持平
Closes #128
```

### PR 要求

见 `.github/PULL_REQUEST_TEMPLATE.md`。三条硬性要求：

1. 涉及 **Prompt / 检索 / 模型** 的改动，必须贴评测对比表。
2. 必须写明**回滚方式**。
3. 涉及**数据库**的改动，迁移必须向后兼容。

## 八、测试

| 层级 | 位置 | 要求 |
| --- | --- | --- |
| 单元测试 | `services/api/tests/` | 每个新增公共函数至少 1 个用例；纯函数优先覆盖 |
| 契约测试 | `tests/` | API 响应结构变更需同步 |
| 评测 | `packages/datasets/golden/` | Prompt/检索/模型改动必须补回归用例 |

约定：

- 测试不依赖真实网络与真实模型：用 `FakeProvider` / `NullCache` 替身。
- 测试名表达行为：✅ `test_crisis_input_never_calls_llm`，❌ `test_entry_2`。
- 关键安全行为（危机不调 LLM、提示注入被拦截）必须有**专门的断言型测试**。

## 九、禁止事项（会直接被 review 打回）

1. ❌ 把密钥、Token、用户数据、模型权重提交进仓库。
2. ❌ 在日志里打印用户原文或完整 Prompt。
3. ❌ 在危机路径里调用 LLM。
4. ❌ 绕过 `LLMGateway` 直接调供应商 SDK（破坏重试、熔断、计费、观测）。
5. ❌ 修改 Prompt 而不升版本、不跑评测。
6. ❌ 直接改已发布的 Alembic 迁移。
7. ❌ 在 `api/v1` 里写业务逻辑（应在 `services/`）。
8. ❌ 提交 `reports/`、`eval_results/`、`ops/data/backups/` 等运行产物。
