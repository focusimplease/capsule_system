# CLI 调用契约（capsule 命令行）

## 调用规则

- 一次 CLI 调用只完成一项边界明确的操作。
- 请求正文通过 `--json '<对象>'` 或 `--input <临时JSON文件>` 传入；
  秘密信息不得出现在命令参数中。
- `stdout` 只输出一个 JSON 结果信封；日志和诊断在 `stderr`。
- 时间戳使用 UTC RFC 3339；领域 ID 和飞书 token 都是不透明字符串。
- 二进制、base64、秘密信息和长期附件路径不得出现在结果信封或日志中。
- Python 不信任你提交的结构化结果：所有枚举、引用、证据锚点和写入条件
  都会被重新校验。

## 结果信封

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

- `success`：操作成功；最终写入时 `result_refs` 指向已保存的终态结果。
- `review_required`：任务已生成审核项并结束；`result_refs` 包含审核项引用。
- `failed`：任务无法完成且没有形成审核项；用户需要重新发起任务。

信封不得出现 `operation_id`、`checkpoint`、`resume_cursor`、
`outbox_status`、`retry_at` 字段——它们代表被禁止的持久工作流状态。

## next_action

需要你完成语义任务时，状态仍是 `success`，并附带：

```json
{
  "action_code": "compare_knowledge",
  "context_refs": ["temporary-context-ref"],
  "result_schema_version": "1.0",
  "allowed_next_commands": ["capsule ingest continue"]
}
```

`context_refs` 只指向当前任务可用的文字或受控临时输入，不是长期业务引用；
会话中断后不得用于恢复。完成后只能调用 `allowed_next_commands` 列出的命令。

## 稳定错误码

| 错误码 | 含义 | 你的处理 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 请求或提交的结果不符合 Schema | 修正后重试 |
| `NOT_FOUND` | 引用的资源不存在 | 向用户核实引用 |
| `STALE_VERSION` | 页面/审核项版本已变化 | 重新读取最新版本后再组织 |
| `POLICY_BLOCKED` | 违反策略约束（图片引用、粒度上限等） | 按建议调整内容 |
| `CONFIRMATION_REQUIRED` | 不可逆操作缺少明确确认 | 向用户确认准确对象 |
| `DEPENDENCY_UNAVAILABLE` | 外部依赖不可用（网络、飞书、转写） | 提示用户稍后重试 |
| `RATE_LIMITED` | 触发限流（可重试） | 等待后在当前任务内重试 |
| `PARTIAL_WRITE` | 写后核对发现部分写入 | 向用户说明并停止 |
| `INTERNAL_ERROR` | 未预期错误 | 向用户说明，重新发起任务 |

只有外部依赖的短暂错误允许在当前任务中做次数受限的重试；重试预算耗尽、
版本冲突或无法确认的部分写入应生成"处理中断"审核项。
