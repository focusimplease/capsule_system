# 审核项展示与决定格式

## 三种审核类型

| review_type | 含义 | 允许的决定 |
| --- | --- | --- |
| `processing_interruption` | 处理中断：缺规则、缺信息、检查失败、重试耗尽 | `supplement_context` 补充上下文 / `retry` 重试 / `specify_topic` 指定主题 / `resubmit` 重新提交 / `terminate` 终止 |
| `knowledge_conflict` | 知识冲突：新资料挑战既有判断 | `keep_both_with_condition` 保留双方并限定条件 / `adopt_existing` 采纳既有 / `adopt_new` 采纳新观点 / `defer` 暂缓 |
| `irreversible_operation` | 不可逆操作：永久删除证据或 URL 记录 | `confirm_delete` 确认删除（需明确确认）/ `reject_delete` 拒绝删除 |

## 展示格式

向用户展示审核项时必须包含（数据来自 `capsule review show`）：

1. **审核类型**与发现问题的业务步骤（`trigger_code`、`detected_stage`）；
2. **原因**（`reason`）：系统为什么停止；
3. **已尝试处理**（`attempted_actions`）；
4. **依据**：来源 URL（如有）、衍生证据预览；
5. **受影响页面**（如已影响知识页）；
6. **允许的决定及各自后果**：逐项列出 `allowed_decisions` 的
   `display` 和 `consequence`；
7. **建议的新任务处理方式**（`suggested_next_task`）。

规则：
- 不能增加允许的决定，也不能省略不可逆后果；
- 审核回复存在歧义时继续询问，不调用写命令；
- 只展示后端返回的决定代码，不能自行创造。

## 不可逆决定的确认

`confirm_delete` 必须同时满足：

- `explicit_confirmation: true`；
- `target_refs` 与审核项列出的准备影响对象**完全一致**（集合相等）。

缺少任一条件时 CLI 返回 `CONFIRMATION_REQUIRED`，不执行删除。

## 决定之后

- 决定经 `capsule review decide` 执行并追加保存（ReviewDecision），
  审核项关闭（`defer` 为取消，其余为解决）；
- 需要继续处理资料时，按响应中的 `suggested_next_task` **另行调用**
  `capsule ingest prepare` 启动新任务，不恢复原任务执行现场；
- 无 URL 且后续判断需要原始内容时，向用户说明系统未保存原始附件，
  需要重新提交资料；
- 单次决定不会自动成为全局规则；反复出现的相似案例可由用户确认后
  通过 `capsule rule-candidate create` 形成规则改进候选。
