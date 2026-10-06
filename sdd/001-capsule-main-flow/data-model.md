# 数据模型：胶囊系统第一版主流程

## 建模规则

- 所有持久化业务对象都使用应用生成的 UUID。飞书 token 和多维表格记录 ID 只是外部定位符，不是领域身份。
- 飞书是唯一的长期业务数据层。第一版不建立 SQLite、outbox、持久任务或恢复检查点。
- 衍生证据发布后即固定。需要修正时创建替代证据，并与被替代证据建立关系。
- 知识页可以修改，但每项实质性贡献都必须能够追溯到衍生证据。
- 用户决定采用追加记录。审核项上的当前状态只是结果投影，不能代替决定历史。
- 二进制输入路径只能存在于当前任务的请求、内存或受控临时目录中，绝不能进入飞书或任何长期记录。

## 业务实体

### 资料提交（`Submission`）

表示用户发起的一次资料处理请求。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `submission_id` | UUID | 主键 |
| `input_kind` | enum | `text`, `pdf`, `image`, `audio`, `video`, `web_url` |
| `source_url` | URL 或 null | 唯一允许保存为原始证据的值 |
| `user_context` | text 或 null | 用户随输入提供的图注、说明或指令 |
| `content_fingerprint` | string 或 null | 用于确定性入口去重的哈希，不能作为文件定位符 |
| `status` | enum | `completed`, `no_evidence`, `needs_review`, `terminated`；只保存终态 |
| `created_at`, `updated_at` | timestamp | 使用 UTC |

校验规则：

- `web_url` 必须包含 `source_url`，不接受持久化本地路径。
- `text` 必须在请求中包含文字；清理后的权威结果保存为衍生证据。
- 媒体和 PDF 路径只能作为临时输入，持久化序列化时必须排除。
- 第一版中，一次资料提交可以产生零份或一份主要衍生证据。没有生成证据时，必须记录原因或生成审核项。

### 原始证据（`OriginalEvidence`）

表示资料存在来源 URL 时保存的原始证据。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `original_evidence_id` | UUID | 主键 |
| `submission_id` | UUID | 指向 Submission 的唯一外键 |
| `url` | URL | 必填；不能是附件地址或网页图片 URL |
| `availability` | enum | `unknown`, `reachable`, `unreachable`, `changed` |
| `last_checked_at` | timestamp 或 null | 可用性检查不得改变历史衍生证据 |

资料没有来源 URL 时，不创建 `OriginalEvidence` 记录。网页内嵌图片的 URL 不属于原始证据。

### 衍生证据（`DerivedEvidence`）

表示经过清理、提取或转写后形成的文字证据。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `evidence_id` | UUID | 主键 |
| `submission_id` | UUID | 必填外键 |
| `original_evidence_id` | UUID 或 null | 可选的来源追溯关系 |
| `title` | string | 供用户阅读的标题 |
| `content_hash` | string | 权威文字内容的哈希 |
| `preview` | string | 多维表格中的简短预览，不是权威正文 |
| `docx_token` | string | 独立证据云文档的定位符 |
| `wiki_node_token` | string | “证据库”分支中的知识库节点 |
| `status` | enum | `active`, `superseded`, `deletion_pending`, `deleted` |
| `supersedes_evidence_id` | UUID 或 null | 证据修正与替代关系 |
| `created_at` | timestamp | 发布时间 |

权威文字保存在独立云文档中。知识页演化不得静默修改这份证据。

### 处理记录（`ProcessingRecord`）

记录一次资料任务已经形成的最终处理结果。运行中的步骤和普通失败不持久化。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `processing_record_id` | UUID | 主键 |
| `submission_id` | UUID | 必填外键 |
| `outcome` | enum | `completed`, `no_evidence`, `needs_review`, `terminated` |
| `reason_code` | string 或 null | 稳定的原因代码 |
| `reason_detail` | text 或 null | 不包含秘密信息、可向用户展示的说明 |
| `omissions` | JSON list | 例如被忽略的 PDF 图片或网页图片 |
| `evidence_refs`, `affected_page_refs` | JSON lists | 最终产生或影响的对象 |
| `created_at` | timestamp | 使用 UTC |

### 理解结果（`UnderstandingResult`）

表示衍生证据相对于现有知识的、带版本的结构化理解结果。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `understanding_id` | UUID | 主键 |
| `submission_id`, `evidence_id` | UUID | 必填关联 |
| `schema_version` | string | 契约版本 |
| `topics` | JSON list | 候选主题标识及判断理由 |
| `key_claims` | JSON list | 带证据锚点的关键判断 |
| `duplicate_parts` | JSON list | 不应再次合并的重复内容 |
| `novel_parts` | JSON list | 值得合并的案例、边界、解释或修正 |
| `conflicts` | JSON list | 既有判断、新判断及双方证据引用 |
| `target_pages` | JSON list | 建议处理的主题页或概念页及对应动作 |
| `validation_status` | enum | `valid`, `needs_review`, `rejected` |
| `created_at` | timestamp | 使用 UTC |

LLM 可以提出这个对象，但 Python 必须在任何写入之前校验其 Schema、证据锚点、页面版本和策略约束。

### 知识页（`KnowledgePage`）

表示可以持续修改的主题页或概念页。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `page_id` | UUID | 主键 |
| `page_type` | enum | 第一版只允许 `topic`、`concept` |
| `title` | string | 在父级范围内唯一 |
| `docx_token`, `wiki_node_token` | string | 飞书资源定位符 |
| `revision_id` | integer/string | 更新前最后读取的版本 |
| `content_hash` | string | 用于发现未经预期的外部修改 |
| `status` | enum | `active`, `conflicted`, `archived` |
| `created_at`, `updated_at` | timestamp | 使用 UTC |

主题页必须保持为结构连贯的文章。概念页必须包含定义、边界、相近概念和典型用法。表格和图形使用可编辑的文字源保存。

### 证据贡献（`EvidenceContribution`）

记录衍生证据与知识页变更之间的多对多追溯关系。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `contribution_id` | UUID | 主键 |
| `evidence_id`, `page_id` | UUID | 与页面版本、贡献类型共同构成唯一约束 |
| `understanding_id` | UUID | 本次变更的判断依据 |
| `contribution_kind` | enum | `created`, `added`, `corrected`, `example`, `boundary`, `definition` |
| `claim_anchors` | JSON list | 页面重要判断及其证据锚点 |
| `page_revision_before`, `page_revision_after` | string | 可追溯的更新前后版本 |
| `summary` | text | 说明改动了什么，不复制整份证据 |
| `created_at` | timestamp | 使用 UTC |

### 审核项（`ReviewItem`）

表示当前任务已经结束，并留下一个需要用户判断的终态业务结果。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `review_item_id` | UUID | 主键 |
| `submission_id` | UUID 或 null | 只有页面或证据维护操作可以为空 |
| `review_type` | enum | `processing_interruption`, `knowledge_conflict`, `irreversible_operation` |
| `status` | enum | `open`, `resolved`, `cancelled` |
| `trigger_code` | string | 可以检查的触发条件，不能只依据模型自报的置信度 |
| `detected_stage` | string | 发现问题的业务步骤，仅用于解释和筛选，不是恢复游标 |
| `reason` | text | 可向用户展示的原因说明 |
| `evidence_refs`, `affected_page_refs` | JSON lists | 只包含经过授权的引用 |
| `attempted_actions` | JSON list | 已经尝试过的处理 |
| `allowed_decisions` | JSON list | 稳定的决定代码、显示名称及后果 |
| `recommendation` | JSON 或 null | 系统建议及理由 |
| `suggested_next_task` | JSON 或 null | 用户决定后可以启动的新任务及所需输入 |
| `version` | integer | 乐观并发控制版本 |
| `rule_gap_flag` | boolean | 是否可能暴露了规则缺口 |
| `created_at`, `resolved_at` | timestamp | 使用 UTC |

### 审核决定（`ReviewDecision`）

表示用户的一次审核决定及其执行结果。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `review_decision_id` | UUID | 主键 |
| `review_item_id` | UUID | 必填外键 |
| `decision_code` | string | 必须属于被审核版本允许的决定 |
| `review_item_version` | integer | 版本过期时拒绝执行 |
| `user_note` | text 或 null | 有必要时保留用户原话 |
| `target_refs` | JSON list | 本次决定影响的准确对象 |
| `explicit_confirmation` | boolean | 不可逆操作必须为 true，并与 `target_refs` 一起保存 |
| `execution_status` | enum | `succeeded`, `failed`, `cancelled` |
| `result` | JSON 或 null | 新任务、更新、删除、拒绝或终止的最终结果 |
| `created_at`, `executed_at` | timestamp | 使用 UTC |

### 规则改进候选（`RuleImprovementCandidate`）

表示以后可能采用的规则改进，而不是当前已经生效的规则。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `candidate_id` | UUID | 主键 |
| `title`, `gap_summary` | text | 共同缺失规则的概括 |
| `trigger_pattern` | JSON | 可查询的共同触发条件 |
| `affected_steps` | JSON list | 可能影响的步骤范围 |
| `status` | enum | `draft`, `accepted_for_spec_update`, `closed` |
| `created_at`, `updated_at` | timestamp | 使用 UTC |

`RuleCandidateCase` 把候选项与一个或多个审核项关联起来。在需求、SPEC、验收样例和实现分别完成修订以前，任何候选项都不能改变系统运行行为。

## 实体关系

```text
Submission 1 ── 0..1 OriginalEvidence
Submission 1 ── 0..1 DerivedEvidence
Submission 1 ── 1 ProcessingRecord
DerivedEvidence 1 ── * UnderstandingResult
DerivedEvidence * ── * KnowledgePage       (通过 EvidenceContribution)
Submission 1 ── * ReviewItem
ReviewItem 1 ── * ReviewDecision
ReviewItem * ── * RuleImprovementCandidate (通过 RuleCandidateCase)
```

## 状态迁移

### 资料提交（`Submission`）终态

```text
当前任务内处理
  -> completed
  -> no_evidence
  -> needs_review
  -> terminated

普通失败不创建 Submission 终态记录，用户重新发起任务。
```

迁移规则：

- 包含已采纳新增内容的资料要进入 `completed`，至少必须已经提交一条 `EvidenceContribution`。
- `needs_review` 必须对应一个打开的 `ReviewItem`；用户决定后启动新任务，不恢复原执行现场。
- 只有能够可靠判断为“不包含知识证据”的输入才允许进入 `no_evidence`；核心事实不完整时应进入审核。
- `next_action` 和语义结果只存在于当前任务，不能序列化到飞书作为中间状态。
- 最终写入以前完成全部内容和约束校验；发生无法安全消除的部分写入时，以“处理中断”审核项收口。

### 审核项（`ReviewItem`）状态

```text
open -> resolved
open -> cancelled
```

解决审核项时追加一条 `ReviewDecision`，绝不抹除先前决定。不可逆决定必须保存明确确认和准确对象。

### 知识页更新状态

```text
当前任务内 proposed -> validated -> committed
                              \-> needs_review
```

如果发现非预期的云文档版本、缺少证据依据的重要判断或尚未解决的冲突，必须生成审核项，不能直接替换页面内容。这里的 `proposed` 与 `validated` 只存在于当前任务内，不是持久状态。
