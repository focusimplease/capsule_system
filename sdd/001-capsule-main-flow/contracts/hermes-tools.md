# 已废弃：旧版 Hermes 工具契约

> 本文件是上一版 Plan 的历史产物，其中包含持久 `operation_id`、检查点和跨会话恢复设计，不属于当前 Plan。当前有效契约是 [agent-cli.md](./agent-cli.md)。下面内容只用于比较旧方案，不得作为实现依据。

## 契约目的

Hermes 是唯一的用户交互入口。它把用户的自然语言转换成少量带版本约束的 CLI 操作，向用户展示结构化结果，并在用户决定存在歧义时继续询问。Python CLI 负责校验、策略检查、状态迁移、幂等和持久化。

计划中的可执行程序名为 `capsule`。Hermes 根据项目 `capsule-system` Skill 的说明，通过 terminal 工具调用它。Skill 只记录调用分流和展示规则，不包含业务状态。

## 运行驱动契约

```text
用户自然语言
  -> Hermes 识别意图
  -> capsule-system Skill 启动或恢复工作流
  -> capsule CLI 执行当前确定性步骤
  -> CLI 返回 next_action
  -> Hermes 按 Skill 完成语义任务
  -> Skill 通过 CLI 提交结构化结果
  -> Python 校验并推进，直到完成、等待或进入审核
```

- Skill 只能调用本文公开的 CLI 命令，不能依赖 `src/capsule_system` 中某个内部函数或文件路径。
- CLI 命令是 Agent 与业务核心之间的版本化边界。内部模块可以重构，但相同契约版本的外部行为不能静默变化。
- Hermes 可以把自然语言整理为结构化参数，但 Python 必须重新校验，不能因为请求来自 Skill 就默认可信。
- Python 不直接调用另一个 LLM API。需要模型推理时，它持久保存待执行的 Agent 动作，并返回 `awaiting_agent` 和 `next_action`。
- 长时间确定性操作先返回 `accepted`、`operation_id` 和可查询状态；Hermes 后续通过状态命令读取结果，不保持一个必须持续在线的 Python 进程或对话。
- Hermes 会话可以随时结束。业务恢复只依赖持久化的操作、资料、审核和恢复位置。

## 调用与传输规则

- 一次 CLI 调用只接收一个操作。
- 结构化请求可以通过 `--json '<object>'` 或 `--input <temporary-json-file>` 传入；秘密信息不能出现在命令参数中。
- `stdout` 只输出一个 JSON 结果信封；日志和诊断信息写入 `stderr`。
- 统一使用 UTF-8；时间戳使用 UTC RFC 3339；ID 作为不透明字符串处理。
- 所有写操作都必须包含 `request_id` 和 `idempotency_key`。
- 相同幂等键与相同请求正文再次提交时，返回首次执行结果；相同键对应不同内容时返回 `IDEMPOTENCY_CONFLICT`。
- `needs_review` 表示正常产生了“需要审核”的业务结果，不属于程序异常。

## 统一结果信封

```json
{
  "schema_version": "1.0",
  "request_id": "req-opaque",
  "operation_id": "op-opaque",
  "status": "succeeded",
  "data": {},
  "next_action": null,
  "review_item_id": null,
  "warnings": [],
  "error": null
}
```

`status` 只能取以下值之一：

- `succeeded`：操作已经达到请求要求的持久化结果。
- `accepted`：已经建立持久化执行状态，可以继续查询进度。
- `awaiting_agent`：Python 已经保存检查点，Hermes 必须执行并提交 `next_action`。
- `needs_review`：流程已经安全暂停，并返回 `review_item_id`。
- `failed`：本次调用不能再自动继续。

错误对象示例：

```json
{
  "code": "STALE_VERSION",
  "message": "审核项已发生变化，请重新读取后再决定。",
  "retryable": false,
  "details": {"current_version": 4},
  "recovery_hint": "使用相同的 review_item_id 执行 capsule review show。"
}
```

稳定错误代码至少包括 `VALIDATION_ERROR`、`NOT_FOUND`、`IDEMPOTENCY_CONFLICT`、`STALE_VERSION`、`ACTION_MISMATCH`、`ACTION_EXPIRED`、`CONFIRMATION_REQUIRED`、`CONFIRMATION_EXPIRED`、`POLICY_BLOCKED`、`DEPENDENCY_UNAVAILABLE`、`RATE_LIMITED` 和 `INTERNAL_ERROR`。

`succeeded`、`accepted`、`awaiting_agent` 和 `needs_review` 的退出码为 `0`；请求校验失败为 `2`；策略或确认被拒绝为 `3`；外部依赖或可重试失败为 `4`；非预期内部错误为 `1`。Hermes 必须根据 JSON 状态和错误代码决定下一步，不能只依赖自然语言或退出码。

## 命令

### `capsule admin check`

执行只读的环境预检，包括项目 Python 版本、SQLite 路径、Hermes 调用环境、飞书身份、准确目标资源、API 权限和资源权限。使用 `--json` 时返回统一结果信封。该命令绝不创建资源或上传数据。

### `capsule admin bootstrap`

在明确命名的环境中，以幂等方式创建或检查约定的知识库分支和多维表格 Schema。配置中必须提供准确的目标知识空间、多维表格和幂等键。选择 `--environment acceptance` 时，绝不修改类似生产环境的资源，也绝不创建附件字段。

### `capsule ingest`

开始处理一次资料提交，或者安全地重复同一次请求。

请求载荷：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `request_id` | string | 必填 |
| `idempotency_key` | string | 必填 |
| `input.kind` | enum | `text`, `pdf`, `image`, `audio`, `video`, `web_url` |
| `input.text` | string/null | 纯文本输入时必填 |
| `input.temporary_path` | string/null | 只用于 PDF、图片、音频或视频；绝不作为业务数据持久保存 |
| `input.url` | URL/null | `web_url` 时必填；其他输入类型可作为来源 URL |
| `user_context` | string/null | 用户附带的图注或说明 |

成功结果根据实际情况包含 `submission_id`、`processing_status`、`current_step`、`evidence_id`、`affected_pages`、`omissions` 和 `review_item_id`。

Hermes 不得声称来源附件已经保存。命令返回后，来源路径可能已经不存在。

当流程需要 Hermes 完成语义任务时，结果信封使用 `status=awaiting_agent`，并返回：

```json
{
  "next_action": {
    "agent_action_id": "action-id",
    "operation_id": "operation-id",
    "action_code": "compare_knowledge",
    "workflow_version": 4,
    "result_schema_version": "1.0",
    "step_token": "one-time-step-token",
    "context": {
      "evidence_id": "evidence-id",
      "candidate_page_ids": ["page-id"]
    },
    "temporary_input": null
  }
}
```

`context` 只包含完成当前动作所需的文字或资源引用。`understand_image` 可以在当前调用中额外返回 `temporary_input`，其中包含胶囊系统拥有的短期路径和租约到期时间；该字段不得写入飞书、日志或会话长期记录，动作结束后立即清理。恢复时租约已经失效，则必须要求用户重新提交图片。Skill 根据 `action_code` 选择对应说明，不能自行替换为另一个动作。

### `capsule workflow resume`

根据 `operation_id` 读取并恢复当前工作流。如果存在待处理 Agent 动作，返回同一个 `agent_action_id`、动作内容和新的安全传输令牌，并使旧令牌失效；如果正在执行长时间确定性任务，返回 `accepted`；如果已经完成或进入审核，返回对应终态。恢复不依赖原 Hermes 对话。

### `capsule workflow submit-agent-result`

把 Hermes 根据 `next_action` 产生的结构化结果交回 Python。请求必须包含 `operation_id`、`agent_action_id`、`workflow_version`、`result_schema_version`、`step_token`、`idempotency_key` 和 `result`。

Python 按顺序检查动作状态、步骤令牌、工作流版本、结果 Schema、证据锚点和当前资源版本。校验通过后消费该动作并推进工作流；校验失败时进行次数受限的重新生成，或者创建“处理中断”审核项。重复提交相同幂等请求返回首次结果，不能重复推进状态。

### `capsule status show`

根据 `submission_id` 读取持久化处理结果。返回当前状态、步骤历史、证据结果、来源 URL 可用性、主题或页面关联、省略内容以及审核和决定历史。即使新建 Hermes 会话，该命令也必须可用。

### `capsule evidence show`

根据 `evidence_id` 读取证据元数据和权威文字。只有存在来源 URL 时才返回该 URL，绝不返回附件定位符。

### `capsule review list`

按照以下条件筛选审核项：

```text
status, review_type, trigger_code, interrupted_step, rule_gap_flag, cursor, page_size
```

默认只返回 `open` 和 `prepared` 状态的审核项，并先按创建时间、再按 ID 稳定排序。必须支持分页。

### `capsule review show`

返回完整审核对象，包括：

- 审核项 ID、类型、状态和 `version`；
- 中断步骤和可以检查的触发条件；
- 可用的来源 URL 与衍生证据引用；
- 受影响页面和证据分析；
- 已经尝试过的操作；
- `allowed_decisions`，每个决定都包含稳定代码、向用户展示的名称和后果；
- 系统建议和恢复方式；
- 先前决定及其结果。

Hermes 可以为了方便阅读重新排版，但不能自行增加允许的决定，也不能省略破坏性操作的后果。

### `capsule review prepare-decision`

只有选中的决定会产生不可逆影响时才需要调用。

请求包含 `review_item_id`、`expected_version`、`decision_code`、`target_refs`、`request_id` 和 `idempotency_key`。响应返回：

```json
{
  "confirmation_token": "one-time-secret",
  "expires_at": "2026-08-16T07:30:00Z",
  "bound_action": {
    "review_item_id": "review-id",
    "version": 3,
    "decision_code": "delete_evidence",
    "targets": [{"type": "derived_evidence", "id": "evidence-id"}],
    "consequence": "该文字证据将被永久删除，且不能通过胶囊系统恢复。"
  }
}
```

Hermes 必须展示令牌绑定的准确操作，并再次取得用户的明确确认。用户之前说过“清理一下”之类的宽泛指令，不能作为有效确认。

### `capsule review decide`

请求示例：

```json
{
  "schema_version": "1.0",
  "request_id": "req-opaque",
  "idempotency_key": "idem-opaque",
  "payload": {
    "review_item_id": "review-id",
    "decision_code": "retry_with_context",
    "expected_version": 3,
    "user_note": "把这张图当作流程图处理",
    "supplemental_context": "图中箭头表示先后关系",
    "confirmation_token": null
  }
}
```

如果决定不在允许范围内、审核项已经过期或关闭、确认缺失或确认对象不匹配，Python 必须拒绝执行。有效决定会追加一条 `ReviewDecision`，执行或安排准确的状态迁移，并返回新状态和 `resume_cursor` 的处理结果。

### `capsule rule-candidate create`

根据用户明确选择的审核案例创建规则改进候选。请求包含标题、缺口概括和至少两个 `review_item_ids`；除非用户明确把单个案例记录为草稿。该命令绝不直接启用运行时规则。规则生效不属于本 SPEC，必须先更新需求、SPEC 和验收样例。

### `capsule rule-candidate list/show`

读取候选项及其关联审核案例，供后续需求修订使用。可以按照状态、中断步骤和触发模式筛选。

## Hermes 决策规则

1. Hermes 可以理解内容和用户语言，但不能直接修改持久化状态。
2. 审核回复存在歧义时，Hermes 继续询问，不调用 `review decide`。
3. Hermes 只展示后端返回的 `allowed_decisions`，不能自行创造新的动作名称并静默映射。
4. 遇到 `STALE_VERSION` 时，Hermes 重新读取审核项，先展示发生变化的事实，再次询问用户。
5. 遇到 `needs_review` 时，Hermes 解释暂停原因并提供审核项 ID，不能无限重试或补写缺失事实。
6. 模型自报的置信度不能单独触发审核。结构化结果必须指出可检查的条件，例如缺少必要信息、输出未通过校验、多个判断冲突或有限重试已经耗尽。
7. Hermes 不能把一次用户个案决定泛化为系统规则。

## 语义输出边界

Hermes 或模型生成的理解结果必须符合 `UnderstandingResult` 模型，并为重要判断提供证据锚点。Python 负责校验：

- Schema 和允许的枚举值；
- 引用的证据 ID、页面 ID 和当前版本；
- 引用锚点确实存在于衍生证据中；
- 除非明确记录为冲突，否则重复内容不能同时被标记为新增内容；
- 尚未解决的冲突不能导致知识页被静默替换；
- 页面变更不能包含缺少证据支持的重要判断。

这些检查失败时，只能进行次数受限的重试，或者生成“处理中断”审核项。
