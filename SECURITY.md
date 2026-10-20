# 安全策略

Lumina 拾光面向的是**真实情绪困扰的用户**。这意味着安全不是「加分项」，
而是这个产品能不能存在的前提。

本文说明：项目如何处理安全问题、哪些是硬性红线、以及如何报告漏洞。

---

## 安全模型

### 威胁来源

| 类别 | 具体场景 | 处置 |
| --- | --- | --- |
| **用户自身风险** | 自伤/自杀倾向、极度痛苦 | 危机短路 + 转介专业机构 |
| **医疗越界** | 索要疾病诊断、用药建议 | 意图识别 + 输出后校验双侧拦截 |
| **提示注入** | 用户输入试图篡改系统指令 | 输入标签隔离 + 输出侧泄露检测 |
| **数据泄漏** | 日志/链路追踪记录用户原文 | OTel scrub 处理器 + 日志脱敏 |
| **凭据泄漏** | API Key、JWT 密钥进 Git | `.gitignore` + gitleaks CI 扫描 |
| **依赖漏洞** | 第三方库 CVE | Trivy CI 扫描（CRITICAL/HIGH 阻断） |

### 三条不可协商的红线

这三条由 `services/api/app/agents/safety.py` 实现，
由 `services/api/tests/test_safety.py` 守护。

#### 1. 危机场景绝不调用模型

命中危机信号时，请求在 LangGraph 的 `entry` 节点**直接短路**，
返回硬编码的干预话术与求助渠道（全国心理援助热线 **12356** / 急救 **120**）。

这条路径**不经过任何模型**，因此：
- 不会因模型超时、限流、故障而失效
- 延迟为零、成本为零
- 行为完全可预期、可审计

> ⚠️ 这条红线**不能被"改进"掉**。不要用模型生成危机回应，
> 不要加"更智能"的危机判断——关键词 + 正则的规则路径虽然召回有限，
> 但它的**确定性**正是它存在的理由。提高召回请走规则词表扩充，不要走模型。

#### 2. 不做医疗诊断

不允许给出疾病判断、用药建议、剂量建议。

- **输入侧**：`detect_medical_overreach()` 识别「我是不是抑郁症」「要不要吃药」这类意图
- **输出侧**：`check_output_for_diagnosis()` 拦截「你这是抑郁症」「建议服用 XX」这类表述

「建议你去医院 / 找专业心理咨询师」是**正确**引导，代码里有专门的放行规则
（`_ALLOWED_REFERRAL`），不要误伤。

#### 3. 不做虚假承诺

不允许「一定会好」「保证你」「没事的」「会过去的」这类表达。
危机场景下给出虚假承诺是有害的。

### 隐私保护

用户数据是**情绪记录**，属于高度敏感信息。

| 环节 | 措施 |
| --- | --- |
| 数据库 | ORM 的 `__repr__` 刻意不输出用户原文，避免误打日志 |
| 应用日志 | `structlog` 脱敏处理器过滤 `password` / `token` / `openid` 等字段 |
| 链路追踪 | OTel Collector 的 `attributes/scrub` 处理器移除 `llm.prompt` / `llm.completion` / `user.message` / `db.statement` |
| 模型调用 | 只传必要的上下文；`llm_calls` 表记录元数据（token 数、成本、耗时），**不记录完整 prompt** |
| 第三方 | 不向任何第三方发送用户原文；埋点只上报事件类型与数值，不带内容 |
| 保留期 | 日记与会话数据有清理策略（见 `docs/RUNBOOK.md`） |

---

## 密钥管理

### 哪些环境变量是敏感的

| 变量 | 敏感度 | 泄漏后果 |
| --- | --- | --- |
| `LLM_API_KEY` | 高 | **直接产生费用**，可能被用于刷量 |
| `JWT_SECRET` | 高 | **可伪造任意用户 token**，直接越权 |
| `POSTGRES_PASSWORD` / `REDIS_PASSWORD` | 高 | 数据全量泄漏 |
| `MODELSCOPE_TOKEN` | 中 | 可读写你的模型仓库 |
| `GRAFANA_PASSWORD` | 中 | 监控数据泄漏、可被用于横向移动 |
| `OPENAI_API_KEY`（备用渠道） | 高 | 同 `LLM_API_KEY` |

### 规矩

1. **`.env` 永远不进 Git**（`.gitignore` 已排除，`.env.example` 只放占位符）
2. **生产环境的密钥不写在代码里**，由部署环境注入（`docker compose` 的 `environment` 段读环境变量）
3. **不把密钥写进日志**——包括"临时打印一下看看"的调试
4. **轮换**：怀疑泄漏立即轮换。`JWT_SECRET` 轮换会让所有已签发 token 失效（这是预期行为）
5. **CI 密钥**放 GitHub Secrets，评测用的放 `EVAL_LLM_API_KEY`（与生产 key 分离，避免评测打爆生产配额）

### 密钥泄漏应急

```bash
# 1. 立即轮换（云控制台 → 生成新 key → 更新部署环境 → 重启）
# 2. 检查是否已被使用
SELECT created_at, model, total_tokens, cost_cny
FROM llm_calls
WHERE created_at > '<泄漏时间>'
ORDER BY cost_cny DESC;
# 3. 查 Git 历史是否泄漏过
git log -p --all -S "<泄漏的 key 片段>" | head -50
# 4. 若进过 Git：轮换 + 用 git-filter-repo 清理历史（并强推，会影响所有协作者）
```

> `llm_calls` 表是这个项目的基础设施之一——它让「这次对话花了多少钱」可查，
> 也是泄漏后的取证依据。

---

## 依赖安全

CI 的 `security` job 做两件事：

| 工具 | 检查内容 | 阻断条件 |
| --- | --- | --- |
| [Trivy](https://trivy.dev/) | 依赖 CVE | `CRITICAL` / `HIGH` 且有修复版本 |
| [gitleaks](https://github.com/gitleaks/gitleaks) | 提交历史中的密钥 | 任意命中 |

本地提交前可以手动跑：

```bash
gitleaks detect --source . --redact
```

### 依赖策略

- 运行时依赖与微调依赖**物理隔离**（`packages/models/requirements.txt` 独立）
  ——torch + transformers 体积巨大，不该进 API 服务镜像
- 锁文件入库（`pip freeze` 结果进 `requirements.lock`），保证可复现构建
- 升级依赖走独立 PR，不和业务改动混在一起——出问题好回滚

---

## 报告漏洞

**请不要开公开 Issue 报告安全漏洞。**

请通过 GitHub 的 [Security Advisory](https://github.com/your-org/lumina/security/advisories/new)
私下提交，或联系维护者邮箱。

### 报告模板

```markdown
### 概述
（漏洞是什么，攻击者能做什么）

### 复现步骤
1.
2.
3.

### 影响范围
- 受影响版本：
- 受影响组件：
- 用户数据影响：（是否涉及用户内容泄漏）

### 建议修复
（如果有的话）
```

### 响应承诺

| 阶段 | 时限 |
| --- | --- |
| 确认收到 | 72 小时内 |
| 初步评估与分级 | 7 天内 |
| 修复发布 | 高危 14 天内 / 中危 30 天内 / 低危随下个版本 |
| 公开披露 | 修复发布后 30 天，或由报告者决定 |

### 范围说明

**在范围内的**：身份认证绕过、越权访问、注入、密钥泄漏、用户数据泄漏、
安全护栏被绕过导致危机场景调用了模型。

**不在范围内的**：
- 模型给出不理想的回答（属于产品质量问题，走 Issue）
- 知识库内容不准确（属于内容问题，走 Issue）
- 自行 DoS 自己的部署实例
- 社会工程与钓鱼

---

## 负责任的披露

我们认可并感谢负责任的漏洞报告者。符合条件的报告会得到：
- 公开致谢（除非你希望匿名）
- 修复进展同步
- 可在 CHANGELOG 中标注贡献者

请在报告前**不要**将漏洞细节公开到第三方渠道、社交媒体或issue tracker。

---

## 相关文件

| 内容 | 位置 |
| --- | --- |
| 安全实现 | `services/api/app/agents/safety.py` |
| 红线测试 | `services/api/tests/test_safety.py` |
| 安全知识库 | `docs/knowledge/06-safety.md` |
| 观测脱敏配置 | `ops/otel/otel-collector.yaml` |
| 密钥清单 | `docs/SETUP.md` |
| 故障处置 | `docs/RUNBOOK.md` |
| 贡献约定 | `CONTRIBUTING.md` |
