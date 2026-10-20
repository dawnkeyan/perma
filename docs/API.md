# Lumina · API 契约

Base URL: `https://api.lumina.app/v1`
认证: `Authorization: Bearer <token>`（微信 `code2Session` 换取）
编码: UTF-8 / JSON；对话接口使用 SSE 流式。

---

## 通用约定

**错误响应**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法",
    "details": [{"field": "emotion.intensity", "reason": "必须在 1~5 之间"}],
    "trace_id": "01HQ8X..."
  }
}
```

| HTTP | code | 含义 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 参数不合法 |
| 401 | `UNAUTHORIZED` | token 无效或过期 |
| 403 | `FORBIDDEN` | 无权访问该资源 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 幂等键冲突 |
| 422 | `UNPROCESSABLE` | 业务规则不满足 |
| 429 | `RATE_LIMITED` | 触发限流 |
| 503 | `MODEL_UNAVAILABLE` | 模型链路全降级（极少见，客户端应重试） |
| 500 | `INTERNAL` | 服务端异常 |

---

## 认证

### `POST /auth/wechat`

微信小程序登录。

```json
{ "code": "081Xyz...", "nickname": "阿May", "avatar_url": "https://..." }
```

**200**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 604800,
  "user": { "id": "u_8f3a", "nickname": "阿May", "is_new_user": true }
}
```

---

## 情绪打卡

### `POST /journal/entries`

```json
{
  "emotion": "anxious",
  "intensity": 3,
  "tags": ["工作", "人际"],
  "content": "开会前一直手心出汗",
  "occurred_at": "2026-10-20T09:30:00+08:00"
}
```

**201**

```json
{
  "id": "j_10231",
  "emotion": "anxious",
  "intensity": 3,
  "tags": ["工作", "人际"],
  "content": "开会前一直手心出汗",
  "occurred_at": "2026-10-20T09:30:00+08:00",
  "created_at": "2026-10-20T09:31:12+08:00"
}
```

### `GET /journal/timeline?start=2026-09-20&end=2026-10-20&granularity=day`

**200**

```json
{
  "items": [
    {
      "date": "2026-10-20",
      "avg_intensity": 2.8,
      "dominant_emotion": "anxious",
      "entries": [ { "id": "j_10231", "emotion": "anxious", "intensity": 3, "content": "..." } ]
    }
  ],
  "stats": {
    "streak_days": 5,
    "avg_intensity_7d": 3.1,
    "trend": "down",
    "top_tags": [{"tag": "工作", "count": 6}]
  }
}
```

### `GET /journal/insights`

基于打卡数据的统计洞察（由 worker 异步生成并缓存）。

```json
{
  "summary": "近 7 天工作场景情绪明显走低，强度中位数从 3.4 降到 2.6。",
  "patterns": [
    { "type": "tag_concentration", "tag": "工作", "detail": "43% 的负面记录与工作相关", "confidence": 0.82 }
  ],
  "recovered_minutes_median": 145,
  "generated_at": "2026-10-20T06:00:00+08:00"
}
```

---

## AI 对话

### `POST /chat/stream`  (SSE)

**请求**

```json
{
  "session_id": "s_8891",
  "message": "今天开会我又说错话了，感觉自己特别没用",
  "context": {
    "emotion_window_days": 7,
    "include_profile": true
  },
  "idempotency_key": "b3f1c9e2-..."
}
```

**响应** `text/event-stream`

```
event: meta
data: {"request_id":"r_001","model":"qwen-plus","prompt_version":4,"ttft_ms":812}

event: delta
data: {"text":"听起来"}

event: delta
data: {"text":"今天在会议上"}

event: citation
data: {"id":1,"title":"认知重构（CBT）","score":0.87,"evidence_level":4}

event: action
data: {"text":"把今天这件事写在纸上，然后写下「我犯了错」和「我这个人有问题」这两句话，圈出区别。","estimated_minutes":3}

event: done
data: {"message_id":"m_002","tokens":412,"cost_cny":0.0132}
```

**错误事件**

```
event: error
data: {"code":"RATE_LIMITED","message":"请求过于频繁，请稍后再试"}
```

> 流式实现的两个关键点：
> 1. 每个 SSE 事件带 `event:` 类型，前端可分别处理正文/引用/微行动。
> 2. `meta` 事件先于 `delta` 发出，让前端能提前渲染骨架并显示"思考中"。

### `POST /chat/messages`（非流式，便于自动化测试）

```json
{ "session_id": "s_8891", "message": "认知重构怎么做？" }
```

**200**

```json
{
  "message_id": "m_003",
  "content": "认知重构是认知行为疗法中的核心技术……",
  "sections": {
    "empathy": "听起来你想理解这个方法。",
    "insight": "你在找一种能自己操作的方法。",
    "evidence": [{"id":1,"title":"认知重构","quote":"…","score":0.91}],
    "action": "现在就拿你最近一次情绪波动试一次：写下自动想法，然后找一条支持它的事实和一条反对它的事实。"
  },
  "usage": { "prompt_tokens": 812, "completion_tokens": 356, "cost_cny": 0.0098 },
  "prompt_version": 4
}
```

### `GET /chat/sessions?limit=20&cursor=...`

会话列表（用于"我的对话"页）。

### `GET /chat/sessions/{session_id}/messages`

### `POST /chat/feedback`

```json
{
  "message_id": "m_003",
  "rating": 1,
  "reason_code": "too_generic",
  "comment": "说得太笼统了，没告诉我具体怎么做"
}
```

`reason_code` 枚举：`too_generic` / `not_empathetic` / `wrong_understanding` / `no_action` / `too_long` / `unrelated` / `other`

> 提交后自动创建 `bad_cases` 记录，携带 `session_id` 与 `trace_id` 供归因。

---

## 认知重构

### `POST /reflection/cbt`

```json
{
  "situation": "项目评审前",
  "automatic_thought": "这次肯定会被否，我做什么都会失败",
  "emotion": "anxious",
  "intensity": 8
}
```

**200**

```json
{
  "reflection_id": "r_001",
  "detected_biases": [
    { "type": "灾难化", "evidence": "「肯定」「什么都会失败」等绝对化表述", "confidence": 0.88 }
  ],
  "guiding_questions": [
    "如果这次真的被否了，最坏的具体后果是什么？可逆吗？",
    "过去一年里，有哪次评审你是通过的？当时你做了什么？"
  ],
  "alternative_thought": null,
  "next_step": "先回答上面两个问题，我再帮你一起生成替代性想法。"
}
```

> 设计要点：接口**第一轮不给答案**，只给偏差识别与引导式提问。用户回答后调用 `POST /reflection/cbt/continue` 才会生成替代性想法。这是循证 CBT 的核心机制，刻意保留。

### `POST /reflection/cbt/continue`

```json
{ "reflection_id": "r_001", "answers": { "q1": "最坏是延期一周，可以补救", "q2": "上次我提前准备了数据，过了" } }
```

**200**

```json
{
  "alternative_thought": "这次评审我需要提前准备数据，这个方法上次有效。",
  "intensity_before": 8,
  "intensity_after": 5,
  "summary": "你把「一定会失败」换成了「我可以做准备」，焦虑下降了 3 分。"
}
```

---

## 优势测评

### `POST /strengths/question`  返回下一题

```json
{ "session_id": "st_001", "question_no": 3, "text": "面对复杂任务时，我倾向于…", "options": ["..."] }
```

### `POST /strengths/answer`

```json
{ "session_id": "st_001", "question_no": 3, "score": 4 }
```

### `GET /strengths/result`

```json
{
  "top_strengths": [
    { "name": "洞察", "category": "智慧与认知", "score": 0.91, "description": "..." }
  ],
  "blind_spot": "你的「洞察」很强，但「执行力」相关项偏低，这可能让你在分析后停住。",
  "recommended_actions": [ { "text": "为今天的一个决定设定 15 分钟的最小执行动作", "minutes": 15 } ]
}
```

---

## 成长时间线

### `GET /timeline/trend?range=30d`

```json
{
  "points": [ { "date": "2026-10-01", "avg_intensity": 3.4, "entries": 2 } ],
  "annotations": [
    { "date": "2026-10-08", "type": "milestone", "text": "连续打卡 7 天" }
  ]
}
```

### `GET /timeline/weekly-report?week=2026-W43`

```json
{
  "headline": "本周整体情绪回升，工作压力仍是主要来源",
  "sections": {
    "baseline": "本周平均强度 2.9，较上周下降 0.4",
    "top_trigger": "工作（52%）",
    "recovery": "中位恢复时长 128 分钟，优于上周",
    "highlight": "你在 10/16 完成了一次 7 天连续打卡"
  },
  "suggestion": "把「工作」相关的记录单独看，你的力量型应对多来自「准备充分」，试试把它迁移到人际场景。"
}
```

### `GET /me/export`  → 下载 JSON/CSV（异步生成，返回任务 ID）

### `DELETE /me/account`  → 彻底删除全部数据（30 天内从主库与备份清除）

---

## 管理后台（`/admin/*`，需管理员 token）

### `GET /admin/badcases?status=open&priority=P0&category=retrieval_miss`

```json
{
  "items": [
    {
      "id": "bc_20261020_0031",
      "priority": "P0",
      "category": "retrieval_miss",
      "user_id_hash": "a3f9...",
      "session_id": "s_8891",
      "message_id": "m_003",
      "user_input": "总是觉得自己很没用",
      "ai_output": "……",
      "auto_detected_tags": ["missing_action"],
      "status": "open",
      "created_at": "2026-10-20T11:02:00+08:00"
    }
  ],
  "total": 12
}
```

### `PATCH /admin/badcases/{id}`

```json
{ "status": "processing", "assignee": "@maintainer", "note": "疑似知识库缺少「以偏概全」条目" }
```

### `GET /admin/prompts/versions?name=coach`

```json
{
  "versions": [
    { "version": 4, "status": "active", "created_at": "...", "eval": { "faithfulness": 0.88 } },
    { "version": 3, "status": "archived", "created_at": "...", "eval": { "faithfulness": 0.86 } }
  ]
}
```

### `POST /admin/prompts/versions/{name}/{version}/activate`

### `GET /admin/eval/compare?a=coach@v3&b=coach@v4`

```json
{
  "cases": [
    { "id": "e2e_012", "a_score": 0.41, "b_score": 0.78, "delta": 0.37 }
  ],
  "summary": { "a_avg": 0.79, "b_avg": 0.83, "improved": 34, "regressed": 3 }
}
```

### `GET /admin/cost?group_by=feature&since=7d`

```json
{
  "rows": [
    { "feature": "chat", "calls": 4210, "tokens": 1284000, "cost_cny": 96.3, "ratio": 0.71 }
  ]
}
```

---

## 限流

| 端点 | 限制 |
| --- | --- |
| `/chat/*` | 30 次/分钟/用户，200 次/天/用户（免费档） |
| `/journal/entries` | 60 次/天/用户 |
| `/reflection/*` | 20 次/天/用户 |
| `/auth/wechat` | 20 次/分钟/IP |

超限返回 `429` + `Retry-After` 头。
