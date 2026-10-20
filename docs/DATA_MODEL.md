# Lumina · 数据模型

数据库：PostgreSQL 16 + pgvector 0.7
字符集：UTF-8 / zh_CN；时区：Asia/Shanghai（存储 UTC，展示本地）

---

## 1. 实体关系

```
users 1──n journal_entries
  │        │
  │        └──(派生) journal_insights
  │
  ├──n chat_sessions 1──n chat_messages
  │                      │
  │                      └──n chat_feedback 1──1 bad_cases
  │
  ├──n reflections
  ├──n strength_sessions 1──n strength_answers
  ├──n user_profiles
  └──n llm_calls

kb_documents 1──n kb_chunks (embedding vector(1024))
```

---

## 2. 表定义

### 2.1 `users`

```sql
CREATE TABLE users (
    id              TEXT PRIMARY KEY,              -- u_<ulid>
    wechat_openid   TEXT UNIQUE NOT NULL,
    unionid         TEXT,
    nickname        TEXT,
    avatar_url      TEXT,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | deleted | banned
    settings        JSONB NOT NULL DEFAULT '{}',      -- 推送时间、语言、隐私开关
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_users_openid ON users(wechat_openid);
```

### 2.2 `journal_entries`（情绪打卡）

```sql
CREATE TABLE journal_entries (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id),
    emotion       TEXT NOT NULL,                    -- joy|calm|expectation|gratitude|
                                                    -- fatigue|anxiety|sadness|anger
    intensity     SMALLINT NOT NULL CHECK (intensity BETWEEN 1 AND 5),
    tags          TEXT[] NOT NULL DEFAULT '{}',     -- work|interpersonal|body|finance|self|future
    content       TEXT,                             -- ≤ 500 字
    occurred_at   TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    source        TEXT NOT NULL DEFAULT 'manual'    -- manual|import|agent
);
CREATE INDEX idx_journal_user_time ON journal_entries(user_id, occurred_at DESC);
CREATE INDEX idx_journal_user_emotion ON journal_entries(user_id, emotion);
```

### 2.3 `chat_sessions` / `chat_messages`

```sql
CREATE TABLE chat_sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    title           TEXT,                           -- 由首条消息自动生成
    summary         TEXT,                           -- 结构化摘要，滚动压缩用
    summary_state   JSONB,                          -- {topics, advice_given, user_facts}
    turn_count      INT NOT NULL DEFAULT 0,
    total_tokens    INT NOT NULL DEFAULT 0,
    total_cost_cny  NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user ON chat_sessions(user_id, updated_at DESC);

CREATE TABLE chat_messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,
    role            TEXT NOT NULL,                  -- user|assistant|system
    content         TEXT NOT NULL,
    sections        JSONB,                          -- {empathy, insight, evidence[], action}
    citations       JSONB,                          -- [{id,chunk_id,title,score,evidence_level}]
    intent          TEXT,
    safety_verdict  JSONB,                          -- {is_cris, is_medical, model_used}
    prompt_version  TEXT,
    trace_id        TEXT,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_session ON chat_messages(session_id, created_at);
```

### 2.4 `llm_calls`（调用审计与成本归因）

```sql
CREATE TABLE llm_calls (
    id                TEXT PRIMARY KEY,
    trace_id          TEXT,
    user_id           TEXT,
    feature           TEXT NOT NULL,               -- chat|insight|reflection|intent|guard|summary
    provider          TEXT NOT NULL,               -- dashscope|deepseek|vllm|local
    model             TEXT NOT NULL,
    prompt_version    TEXT,
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    cached            BOOLEAN NOT NULL DEFAULT false,
    latency_ms        INT,
    ttft_ms           INT,
    status            TEXT NOT NULL,               -- ok|error|timeout|fallback
    error_code        TEXT,
    cost_cny          NUMERIC(10,8) NOT NULL DEFAULT 0,
    request_meta      JSONB,                       -- temperature、max_tokens、attempt 序号
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_calls_user_time ON llm_calls(user_id, created_at DESC);
CREATE INDEX idx_llm_calls_feature ON llm_calls(feature, created_at DESC);
CREATE INDEX idx_llm_calls_trace ON llm_calls(trace_id);
```

> 这张表是整个系统**最有价值的一张表**。成本、延迟、模型效果、Prompt 版本效果都能从它算出来。

### 2.5 `kb_documents` / `kb_chunks`

```sql
CREATE TABLE kb_documents (
    id            TEXT PRIMARY KEY,
    source_path   TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    theory        TEXT,                            -- CBT|PERMA|VIA|行为激活|...
    tags          TEXT[] NOT NULL DEFAULT '{}',
    evidence_level SMALLINT NOT NULL CHECK (evidence_level BETWEEN 1 AND 5),
    applicable_to TEXT[] NOT NULL DEFAULT '{}',
    contraindications TEXT[] NOT NULL DEFAULT '{}',
    license       TEXT NOT NULL DEFAULT 'original',
    content_hash  TEXT NOT NULL,                   -- 内容变更检测
    doc_version   INT NOT NULL DEFAULT 1,
    ingested_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE kb_chunks (
    id           TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    parent_id    TEXT,                             -- 父子块：小块 -> 大块
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    token_count  INT NOT NULL,
    heading_path TEXT[],                           -- ["认知重构", "操作步骤"]
    metadata     JSONB NOT NULL DEFAULT '{}',
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_kb_chunks_embedding ON kb_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_kb_chunks_doc ON kb_chunks(doc_id);
```

> **为什么 pgvector 索引参数这样设？**
> `m=16` 是召回率/内存的平衡点（官方默认 16），`ef_construction=64` 略高于默认 64 保持一致。查询时 `ef_search=200` 远大于默认 40——**检索场景更看重召回**（Recall@80 > 0.95 是硬要求），而 HNSW 的 `ef_search` 越大召回越高、延迟越高，可按 query 类型动态调整（rerank 路径用 200，纯缓存路径用 50）。

### 2.6 `bad_cases`（质量闭环）

```sql
CREATE TABLE bad_cases (
    id                TEXT PRIMARY KEY,
    user_id           TEXT,
    session_id        TEXT,
    message_id        TEXT,
    priority          TEXT NOT NULL DEFAULT 'P2',  -- P0|P1|P2|P3
    category          TEXT,                        -- 8 类归因标签
    source            TEXT NOT NULL,               -- user_report|manual|auto_rule|monitor
    user_input        TEXT,
    ai_output         TEXT,
    auto_tags         TEXT[],
    status            TEXT NOT NULL DEFAULT 'open',-- open|processing|fixed|wontfix|invalid
    assignee          TEXT,
    note              TEXT,
    promoted_case_id  TEXT,                        -- 提升为黄金集用例后的 id
    fixed_by          TEXT,                        -- PR 编号
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_badcases_status ON bad_cases(status, priority, created_at DESC);
CREATE INDEX idx_badcases_category ON bad_cases(category, created_at DESC);
```

### 2.7 其余表（简表）

```sql
-- 用户画像：优势、偏好、长期主题（由 worker 从对话中抽取）
CREATE TABLE user_profiles (
    user_id        TEXT PRIMARY KEY REFERENCES users(id),
    strengths      JSONB,          -- [{name, score}]
    blind_spots    JSONB,
    preferences    JSONB,          -- 喜欢的干预方式、忌讳的表达
    long_term_theme JSONB,         -- 反复出现的主题
    memory_summary TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 认知重构会话
CREATE TABLE reflections (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users(id),
    situation          TEXT NOT NULL,
    automatic_thought  TEXT NOT NULL,
    emotion            TEXT,
    intensity_before   SMALLINT,
    intensity_after    SMALLINT,
    detected_biases    JSONB,
    guiding_questions  JSONB,
    alternative_thought TEXT,
    answers            JSONB,
    status             TEXT NOT NULL DEFAULT 'in_progress', -- in_progress|completed
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 优势测评
CREATE TABLE strength_sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    status     TEXT NOT NULL DEFAULT 'in_progress',
    result     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE strength_answers (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES strength_sessions(id) ON DELETE CASCADE,
    question_no  INT NOT NULL,
    question_key TEXT NOT NULL,
    score        SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
    answered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 异步洞察（每日生成、缓存）
CREATE TABLE journal_insights (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    period      TEXT NOT NULL,          -- 2026-10-20
    summary     TEXT NOT NULL,
    patterns    JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_insights_user_period ON journal_insights(user_id, period);

-- Prompt 版本
CREATE TABLE prompt_versions (
    id           TEXT PRIMARY KEY,       -- coach@v4
    name         TEXT NOT NULL,
    version      INT NOT NULL,
    content_hash TEXT NOT NULL,
    body         TEXT NOT NULL,
    changelog    TEXT,
    model_scope  TEXT[],
    status       TEXT NOT NULL DEFAULT 'draft',  -- draft|active|archived
    eval_scores  JSONB,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);
```

---

## 3. 关键查询

### 3.1 近 N 天情绪上下文（Agent 每次对话都要用，必须快）

```sql
WITH recent AS (
    SELECT emotion, intensity, tags, occurred_at
    FROM journal_entries
    WHERE user_id = :uid AND occurred_at >= now() - (:days || ' days')::interval
)
SELECT
    round(avg(intensity)::numeric, 2)                AS avg_intensity,
    count(*)                                          AS entry_count,
    count(DISTINCT occurred_at::date)                 AS active_days,
    mode() WITHIN GROUP (ORDER BY emotion)            AS dominant_emotion,
    array_agg(DISTINCT t)                             AS all_tags
FROM recent, LATERAL unnest(tags) AS t;
```

配套 Redis 缓存：`journal:ctx:{user_id}:{days}` TTL 300s，打卡后主动失效。

### 3.2 情绪趋势

```sql
SELECT
    occurred_at::date                          AS day,
    round(avg(intensity)::numeric, 2)          AS avg_intensity,
    count(*)                                   AS entries,
    mode() WITHIN GROUP (ORDER BY emotion)     AS dominant_emotion
FROM journal_entries
WHERE user_id = :uid AND occurred_at >= now() - interval '30 days'
GROUP BY 1
ORDER BY 1;
```

### 3.3 成本归因

```sql
SELECT
    feature,
    count(*)                                   AS calls,
    sum(prompt_tokens + completion_tokens)     AS tokens,
    sum(cost_cny)                              AS cost_cny,
    round(100.0 * sum(cost_cny) / sum(sum(cost_cny)) OVER (), 1) AS ratio_pct
FROM llm_calls
WHERE created_at >= now() - interval '7 days'
GROUP BY 1
ORDER BY cost_cny DESC;
```

### 3.4 BadCase 周报

```sql
SELECT
    category,
    priority,
    status,
    count(*) AS n
FROM bad_cases
WHERE created_at >= now() - interval '7 days'
GROUP BY ROLLUP(category, priority, status);
```

---

## 4. 数据治理

### 4.1 保留策略

| 数据 | 保留期 | 依据 |
| --- | --- | --- |
| 情绪打卡/对话 | 用户账户存续期 + 30 天 | 用户可随时删除 |
| `llm_calls` 明细 | 90 天 | 成本分析需要，更细粒度无必要 |
| 成本聚合统计 | 永久（去标识化） | 长期趋势 |
| BadCase 中的用户内容 | 30 天后脱敏为摘要 | 归因需要原文，修复后不需要 |
| 会话摘要 | 随会话 | — |
| 备份 | 30 天滚动 | — |

### 4.2 脱敏

```python
# app/core/pii.py
PII_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "PHONE"),
    (re.compile(r"\d{17}[\dXx]"), "ID_CARD"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "EMAIL"),
    (re.compile(r"(微信|wechat|vx|wx)[:：\s]*[A-Za-z0-9_-]{5,}"), "WECHAT_ID"),
    (re.compile(r"\d{1,4}号?楼|\d+单元|\d+室"), "ADDRESS"),
]
```

落库前统一执行，替换为 `[PHONE]` 等占位符。`llm_calls` 不存 prompt 原文（只存 token 数与版本号），从源头避免泄漏。

### 4.3 用户数据导出与删除

- `GET /me/export` → 生成 JSON + CSV，打包为对象存储中的短期签名 URL（24h 有效）
- `DELETE /me/account` →
  1. 立即置 `users.status='deleted'`，所有查询路径过滤该状态
  2. 异步删除关联行（分批，避免长事务锁表）
  3. 记录到 `deletion_queue`，由定时任务扫描备份并在 30 天内清除

### 4.4 多租户与越权防护

- 所有业务表带 `user_id`，仓储层**强制**注入过滤条件（不提供不带 user_id 的查询方法）
- 单元测试中有一条专门的"越权访问"测试集，覆盖所有按 id 查询资源的接口
