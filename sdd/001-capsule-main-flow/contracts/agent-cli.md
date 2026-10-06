# 契约：智能体（Agent）与胶囊系统命令行接口（CLI）

## 契约目的

`capsule` CLI 是兼容 Agent 基座与 Python 业务核心之间的稳定边界。Codex、Hermes 或其他兼容基座都通过同一组命令和 JSON Schema 使用胶囊系统。Skill 负责选择命令、整理参数、处理 `next_action` 和展示结果；Python 负责校验、确定性操作、策略约束和最终写入。

CLI 不保存可恢复的工作流。一次任务可以包含多次 CLI 调用，但中间上下文只存在于当前 Agent 会话、进程内存或受控临时目录中。任务结束状态只有成功、生成审核项或失败。

## 调用规则

- 一次 CLI 调用只完成一项边界明确的操作。
- 请求使用命令专属 Pydantic Schema，通过 `--json` 或当前任务的临时 JSON 文件传入。
- `stdout` 只输出一个 JSON 结果信封；日志和诊断写入 `stderr`。
- 时间戳使用 UTC RFC 3339；领域 ID 和飞书 token 都作为不透明字符串处理。
- 二进制、base64、秘密信息和长期附件路径不得出现在结果信封或日志中。
- Python 不信任 Agent 生成的结构化结果，所有枚举、引用、证据依据和写入条件都要重新校验。
- 结果信封不得出现 `operation_id`、`checkpoint`、`resume_cursor`、`outbox_status` 或 `retry_at`。

## 统一结果信封

```json
{
  "schema_version": "1.0",
  "request_id": "req-opaque",
  "status": "success",
  "data": {},
  "result_refs": [],
  "next_action": null,
  "error": null
}
```

`status` 只能取以下值：

- `success`：当前操作成功；如果这是最终写入，`result_refs` 指向已保存的终态结果。
- `review_required`：当前任务已经生成审核项并结束，`result_refs` 必须包含审核项引用。
- `failed`：当前任务无法完成且没有形成审核项；用户需要重新发起任务。

需要 Agent 完成语义任务时，状态仍表示本次确定性步骤成功，并返回 `next_action`：

```json
{
  "action_code": "compare_knowledge",
  "context_refs": ["temporary-context-ref"],
  "result_schema_version": "1.0",
  "allowed_next_commands": ["capsule ingest continue"]
}
```

`context_refs` 只能指向当前任务可用的文字、飞书资源或受控临时输入。它不是长期业务引用，会话中断后不得用于恢复。

## 稳定错误

错误对象至少包含：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "可向用户解释的错误",
  "retryable_in_current_task": false,
  "details": {},
  "suggested_action": "重新提交资料"
}
```

稳定错误码至少包括：

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `STALE_VERSION`
- `POLICY_BLOCKED`
- `CONFIRMATION_REQUIRED`
- `DEPENDENCY_UNAVAILABLE`
- `RATE_LIMITED`
- `PARTIAL_WRITE`
- `INTERNAL_ERROR`

只有外部依赖的短暂错误允许在当前任务中进行次数受限的重试。重试预算耗尽、版本冲突或无法确认的部分写入，应生成“处理中断”审核项；普通输入失败则返回 `failed`。

## 命令

### `capsule admin check`

只读检查 Python 版本、必要依赖、飞书身份、目标测试资源、API scope 和资源权限。该命令不创建资源、不上传资料。

### `capsule admin bootstrap-test-space`

只用于开发测试空间，创建或检查约定的 Wiki 分支、Docx 访问能力和 Base Schema。命令必须要求明确的测试环境标识，并拒绝附件字段。它不是面向最终用户的安装命令。

### `capsule ingest prepare`

读取本次输入并完成当前可以确定执行的提取、校验和查询。该命令不写入证据、
知识页或处理成功记录；如果输入已经满足人工审核条件，可以创建审核项，作为
本次任务的终态结果。

请求字段：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `request_id` | string | 本次调用关联 ID，只用于当前任务 |
| `input.kind` | enum | `text`, `pdf`, `image`, `audio`, `video`, `web_url` |
| `input.text` | string/null | 纯文本输入时必填 |
| `input.temporary_path` | string/null | 仅用于当前任务的 PDF、图片、音视频输入 |
| `input.url` | URL/null | 网页时必填；其他输入可以作为来源 URL |
| `user_context` | string/null | 用户说明或图注 |

返回结果可以是：

- `next_action`：要求 Agent 理解图片、分析证据、比较知识或组织知识页；
- `review_required`：输入已经满足人工审核条件；
- `failed`：输入无效或当前任务无法继续；
- 可以直接提交的完整终态候选。

该命令不声称附件已经保存。通常不建立 `Submission` 记录；只有返回
`review_required` 时，才同时建立终态的 `Submission`、`ProcessingRecord` 和
`ReviewItem`，使审核项具有可追溯的资料来源。这些记录不包含恢复位置或其他
运行中状态。

### `capsule ingest continue`

提交 Agent 针对上一项 `next_action` 生成的结构化结果。请求包含当前任务上下文引用、动作代码、结果 Schema 版本和结果正文。

Python 依次检查：

1. 动作代码与允许的后续命令是否匹配；
2. 结果是否符合 Schema；
3. 引用的证据文字、页面和版本是否存在；
4. 新增、重复和冲突分类是否自洽；
5. 页面重要判断是否有证据依据；
6. 是否触发人工审核。

校验通过后可以返回新的 `next_action`，也可以进入最终提交。所有往返都必须在当前任务中完成。

### `capsule ingest commit`

接收已经完成全部语义任务和 Python 校验的最终写入集合。Python 先完整校验证据正文、页面创建或更新、来源 URL、关系记录和终态处理结果，再集中写入飞书。

提交顺序：

1. 创建完整衍生证据文档；
2. 创建新知识页；
3. 使用读取时的 revision 更新既有知识页；
4. 写入证据、页面关系和最终处理记录；
5. 重新读取关键资源验证结果。

只有全部必要结果通过写后检查，才返回 `success`。部分成功无法在当前任务安全处理时，返回 `review_required` 并指向“处理中断”审核项。

### `capsule result show`

根据 `submission_id` 读取已保存的终态处理结果、判断理由、衍生证据、来源 URL、关联知识页和审核决定。不存在运行中状态。

### `capsule evidence show`

根据 `evidence_id` 读取证据元数据和权威文字。只有存在来源 URL 时才返回该 URL，绝不返回附件定位符。

### `capsule review list`

按 `status`、`review_type`、`trigger_code`、`detected_stage` 和 `rule_gap_flag` 筛选审核项，并提供分页。

### `capsule review show`

返回审核类型、发现问题的业务步骤、原因、来源 URL、衍生证据、受影响页面、已尝试处理、允许的决定、各决定后果、系统建议和建议的新任务处理方式。Agent 可以重新排版，但不能增加允许的决定或省略不可逆后果。

### `capsule review decide`

请求包含 `review_item_id`、`decision_code`、用户补充信息、准确目标，以及不可逆操作所需的明确确认。Python 必须拒绝未列出的决定、已经关闭的审核项、缺少准确对象的删除和未经确认的不可逆操作。

有效决定追加 `ReviewDecision`。如果还需要处理资料，返回建议的新任务输入，由 Agent 另行调用 `ingest prepare`；不得恢复原任务执行现场。

### `capsule rule-candidate create/list/show`

创建或查询由审核案例形成的规则改进候选。候选项不会直接改变全局规则；规则生效必须先更新需求、SPEC 和验收样例。

## 智能体（Agent）决策规则

1. Agent 可以理解用户语言和内容，但不能绕过 CLI 直接修改业务数据。
2. 审核回复存在歧义时继续询问，不调用写命令。
3. 只展示后端返回的允许决定，不能自行创造决定代码。
4. 遇到 `review_required` 时解释原因并返回审核项，不能继续猜测或无限重试。
5. 模型自报的置信度不能单独触发审核；必须给出缺少信息、校验失败、判断冲突或重试耗尽等可检查条件。
6. 单次用户决定不能被 Agent 泛化成全局规则。
7. 会话中断后，未形成终态的任务必须重新发起。
