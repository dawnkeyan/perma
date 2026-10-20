# Git 与协作流程规则

## 一、分支

| 分支 | 用途 | 保护 |
| --- | --- | --- |
| `main` | 生产发布 | 禁止直接 push，必须走 PR + CI 绿灯 |
| `develop` | 集成分支 | 禁止直接 push |
| `feat/*` `fix/*` `refactor/*` `docs/*` `ops/*` | 特性分支 | 从 `develop` 切出 |

**不要在 `main` 上直接改代码。** 这条看似繁琐，但它是「随时能回滚」的前提。

## 二、提交信息

[Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <描述>

<正文：为什么这么改>

<脚注：Closes #123 / BREAKING CHANGE: ...>
```

| type | 用途 | scope 可选值 |
| --- | --- | --- |
| `feat` | 新功能 | `rag` `llm` `agents` `safety` `prompts` `api` `eval` `miniapp` `ops` |
| `fix` | 缺陷修复 | 同上 |
| `refactor` | 不改行为的重构 | 同上 |
| `perf` | 性能 | `rag` `llm` `api` |
| `test` | 只改测试 | — |
| `docs` | 只改文档 | — |
| `build`/`ci` | 构建与流水线 | `docker` `deps` |
| `chore` | 杂项 | `deps` `format` |

**一个提交只做一件事。** 提交里混着「改 Prompt」和「格式化全仓库」，
review 时无法判断哪些改动是实质性的，回滚时也无法拆分。

## 三、什么绝对不能提交

| 内容 | 原因 |
| --- | --- |
| `.env`、任何真实密钥 | 泄漏即损失 |
| 用户原文、日记、真实对话 | 隐私事故 |
| 模型权重、`.safetensors`、checkpoint | 体积巨大且托管在魔搭 |
| 向量库索引快照 | 可由 `make kb-ingest` 重建 |
| `reports/eval/` 评测产物 | 体积大且含答案全文 |
| `node_modules/`、`.venv/`、构建产物 | 可重建 |
| 大段调试输出、`print` 残留 | 污染历史 |

`.gitignore` 已覆盖以上全部，并额外排除了个人私有材料目录。

**提交前自查**：

```bash
git status --short          # 有没有不该进的文件
git diff --staged           # 暂存区里有没有密钥
gitleaks detect --source . --redact   # 有没有漏网的密钥
```

## 四、PR 规范

### 标题

```
<type>(<scope>): <描述>
```

与提交信息同构，方便 `git log --oneline` 追溯。

### 描述必须包含三段

```markdown
## 改了什么
（做了什么）

## 为什么改
（**重点**。问题现象 → 根因 → 为什么用这个方案。不写清楚大概率被打回。）

## 怎么验证
（reviewer 怎么确认它是对的。给出可复现步骤或命令。）

## 影响面
- [ ] 是否改动 API 契约
- [ ] 是否改动数据库（需要迁移？）
- [ ] 是否改动 Prompt / 知识库（需要重跑评测？）
- [ ] 是否触碰安全红线
```

### 合并条件

- [ ] CI 四个 job 全绿（后端 / 前端 / 评测门禁 / 安全扫描）
- [ ] 新逻辑有测试覆盖
- [ ] 改了 Prompt / 检索 → 贴了评测对比数据
- [ ] 改了数据库 → 迁移向后兼容
- [ ] 文档已同步（`docs/` + `docs/knowledge/`）
- [ ] 没有密钥、用户数据、模型权重进仓库

**合并方式**：`develop` 用 squash；`main` 用 merge commit 保留完整历史。

## 五、PR 描述里的回滚方式

每个 PR 都要能回答：**如果这个改动上线后出问题，怎么回滚？**

| 改动类型 | 回滚方式 |
| --- | --- |
| 纯代码 | revert 提交，重新部署 |
| Prompt | 改回上一个 `version`（Prompt 已版本化，可精确回滚） |
| 数据库迁移 | 必须**向后兼容**（先加列、双写、再切读写、最后删旧列），不能直接 down |
| 知识库 | 恢复上一个索引快照（`ops/scripts/backup.sh`） |
| 依赖升级 | revert + 重新构建镜像 |

## 六、版本与发布

- 版本号遵循语义化版本
- `CHANGELOG.md` 记录所有重要变更，格式遵循 Keep a Changelog
- 发布走 `.github/workflows/release.yml`，镜像推到 GHCR
- **发布 tag 必须在所有 CI job 通过后打**

## 七、评审重点

Reviewer 按这个顺序看：

1. **依赖方向**有没有被破坏（见 `project-context.md`）
2. **错误路径**处理是否完整（只处理 happy path 等于没处理）
3. **安全红线**是否被削弱（见 `safety-and-privacy.md`）
4. 有没有硬编码的密钥、地址、用户数据
5. 日志里会不会漏出用户内容
6. 新增的断言**真的能失败吗**（把条件改坏试试）

## 八、不要做的事

- ❌ 直接向 `main` push
- ❌ 一个提交里混多件事
- ❌ 提交信息写 `update` / `fix bug` / `临时改一下`
- ❌ 修改已发布的 Alembic 迁移（只能新增）
- ❌ 为了让 CI 变绿而随手更新评测基线
- ❌ 在提交信息或代码注释里写绕过原因的临时方案
- ❌ force push 到 `main`
