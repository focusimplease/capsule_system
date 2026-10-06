# capsule 命令手册

全部命令通过 `--json '<对象>'` 或 `--input <文件>` 传入请求正文，
stdout 返回一个结果信封（见 cli-contract.md）。

## capsule admin check

只读检查依赖、飞书身份与目标资源；不创建资源。

```json
{"request_id": "req-1"}
```

## capsule admin bootstrap-test-space

只用于开发测试空间；必须携带明确测试环境标识，拒绝附件字段。

```json
{"request_id": "req-1", "environment": "acceptance"}
```

## capsule ingest prepare

读取本次输入并完成可确定执行的提取、校验和查询。输入即满足人工审核条件时
直接返回 `review_required`（同时建立终态记录）。

| 字段 | 规则 |
| --- | --- |
| `input.kind` | `text` / `pdf` / `image` / `audio` / `video` / `web_url` |
| `input.text` | 纯文本输入时必填 |
| `input.temporary_path` | PDF、图片、音视频输入的当前任务临时路径 |
| `input.url` | 网页时必填；其他输入可作为来源 URL |
| `user_context` | 用户说明或图注，参与共同理解 |

```json
{"request_id": "req-1", "input": {"kind": "text", "text": "..."},
 "user_context": "训练原理笔记"}
```

返回：`next_action`（四种语义动作之一）、`review_required`、`failed`，
或入口去重结果（`data.entry_duplicate`）。

## capsule ingest continue

提交针对上一项 `next_action` 的结构化结果。

```json
{"request_id": "req-1", "task_ref": "<prepare 返回的任务引用>",
 "action_code": "compare_knowledge", "result": { ... }, "result_schema_version": "1.0"}
```

`result` 的结构由 action_code 决定：

- `understand_image`：`{"outcome": "usable|no_evidence|review",
  "evidence_text": "...", "omissions": [], "uncertainties": [], "reason": "..."}`
- `understand_web_images`：`{"images": [{"image_key": "...", "ignored": false,
  "reason": "...", "text": "转换后的文字", "core_incomplete": false}],
  "core_incomplete": false, "reason": "..."}`
- `compare_knowledge`：`{"topics": [{"name": "...", "existing_page_id": null,
  "rationale": "..."}], "key_claims": [{"claim": "...", "evidence_anchor": "..."}],
  "duplicate_parts": [], "novel_parts": [{"kind": "example|boundary|explanation|correction|other",
  "text": "...", "evidence_anchor": "..."}], "conflicts": [{"page_id": "...",
  "existing_claim": "...", "new_claim": "...", "evidence_anchor": "...",
  "resolution": "auto_supersede|needs_user"}], "target_pages": [],
  "conservative_notes": []}`
- `organize_pages`：`{"creates": [{"page_type": "topic|concept", "title": "...",
  "content": "...", "claim_anchors": [...]}], "updates": [{"page_id": "...",
  "revision_id": "...", "content": "...", "claim_anchors": [...],
  "change_summary": "..."}], "pending_updates": [], "not_adopted_reason": null}`

## capsule ingest commit

集中提交已通过全部校验的最终写入集合。

```json
{"request_id": "req-1", "task_ref": "..."}
```

## capsule result show

按 `submission_id` 读取终态处理结果、判断理由、证据、来源 URL、
关联知识页和审核决定。

```json
{"request_id": "req-1", "submission_id": "..."}
```

## capsule evidence show

按 `evidence_id` 读取证据元数据和权威文字；存在来源 URL 时返回该 URL。

```json
{"request_id": "req-1", "evidence_id": "..."}
```

## capsule evidence propose-deletion

提出删除建议（补充命令）：生成不可逆操作审核项并等待用户确认；
永久删除必须经 `capsule review decide` 的明确确认。

```json
{"request_id": "req-1", "target_kind": "derived_evidence|original_evidence_url",
 "target_id": "..."}
```

## capsule review list

筛选审核项，支持 `status`、`review_type`、`trigger_code`、`detected_stage`、
`rule_gap_flag` 与分页。

```json
{"request_id": "req-1", "status": "open", "limit": 50, "offset": 0}
```

## capsule review show

返回审核项详情与作出决定所需的全部信息（见 review-format.md）。

```json
{"request_id": "req-1", "review_item_id": "..."}
```

## capsule review decide

执行用户决定。不可逆决定必须携带 `explicit_confirmation: true`，
且 `target_refs` 与审核项列出的对象完全一致。

```json
{"request_id": "req-1", "review_item_id": "...", "decision_code": "terminate",
 "user_note": "不需要这份资料了", "target_refs": [], "explicit_confirmation": false}
```

## capsule rule-candidate create / list / show

从审核案例创建规则改进候选并关联来源案例；候选不改变全局规则。

```json
{"request_id": "req-1", "title": "扫描版 PDF 处理建议",
 "gap_summary": "连续多次遇到扫描件，建议约定预处理方式",
 "trigger_pattern": {"trigger_code": "EXTRACTION_INCOMPLETE"},
 "affected_steps": ["ingest prepare"], "review_item_ids": ["..."]}
```

## capsule rule-candidate close

关闭候选（补充命令）：用户决定不处理某个规则缺口；已完成的个案决定
不被反向修改，审核记录保留。

```json
{"request_id": "req-1", "candidate_id": "...", "reason": "按现有规则处理即可"}
```
