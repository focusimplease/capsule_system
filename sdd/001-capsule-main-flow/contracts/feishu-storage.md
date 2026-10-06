# 契约：飞书终态存储与集中写入

## 职责划分

| 飞书组件 | 承担的职责 | 禁止保存的内容 |
| --- | --- | --- |
| 知识库（Wiki） | 主题、概念和证据分支的导航与层级结构 | 工作流状态、附件 |
| 云文档（Docx） | 衍生证据权威文字；可修改的主题页和概念页 | PDF、图片、音频、视频；内嵌或外部引用的来源图片 |
| 多维表格（Base） | 资料索引、最终处理结果、资源索引、追溯关系、审核项、审核决定和规则改进候选 | 运行中状态、恢复检查点、二进制附件、衍生证据权威长文本 |
| 云盘（Drive） | 元数据、权限、版本以及经过明确确认后的文本资源删除 | 原始附件仓库 |

飞书是唯一长期业务数据层。第一版不建立 SQLite、outbox、本地状态库或持久任务队列。

第一版 Python 适配器只通过 `lark-cli` 1.0.68 调用飞书，并解析其结构化结果。如果 CLI 缺少必要能力，应触发 Plan 变更，而不是擅自加入 SDK 或原始 HTTP 静默兜底。

## 知识库布局

```text
胶囊知识库
├── 主题
│   └── <主题页云文档节点>
├── 概念
│   └── <概念页云文档节点>
└── 证据库
    └── <每份衍生证据对应一个固定后的云文档节点>
```

应用同时保存 `wiki_node_token` 和底层 `obj_token`/`docx_token`，这些 token 不能混用。使用知识库 URL 调用云文档 API 以前，必须先解析出对应资源。

## 多维表格数据表

下面这些逻辑表可以创建在同一个多维表格应用中。字段名是稳定的契约名称，向用户展示的名称可以本地化。

### `submissions`

`submission_id`, `input_kind`, `source_url`, `content_fingerprint`, `status`, `created_at`, `updated_at`

不允许保存本地附件路径，也不允许创建多维表格附件字段。

### `processing_records`

`processing_record_id`, `submission_id`, `outcome`, `reason_code`, `reason_detail`, `omissions_json`, `evidence_refs_json`, `affected_page_refs_json`, `created_at`

每个成功形成终态的资料任务创建一条最终处理记录。运行中的步骤、普通失败和重试计划不得写入本表。

### `evidence_index`

`evidence_id`, `submission_id`, `original_evidence_id`, `source_url`, `title`, `preview`, `content_hash`, `docx_token`, `wiki_node_token`, `status`, `supersedes_evidence_id`, `created_at`

衍生证据的权威文字从云文档读取，`preview` 只是预览，不具有权威性。

### `knowledge_pages`

`page_id`, `page_type`, `title`, `docx_token`, `wiki_node_token`, `revision_id`, `content_hash`, `status`, `created_at`, `updated_at`

### `evidence_contributions`

`contribution_id`, `evidence_id`, `page_id`, `understanding_id`, `contribution_kind`, `claim_anchors_json`, `page_revision_before`, `page_revision_after`, `summary`, `created_at`

### `understanding_results`

`understanding_id`, `submission_id`, `evidence_id`, `schema_version`, `topics_json`, `key_claims_json`, `duplicate_parts_json`, `novel_parts_json`, `conflicts_json`, `target_pages_json`, `conservative_notes_json`, `validation_status`, `created_at`

保存已经通过 Python 校验的终态理解结果，使后续会话能够区分主题、关键观点、重复内容、新增价值和冲突。它不包含模型推理过程或未完成任务状态。

### `review_items`

`review_item_id`, `submission_id`, `review_type`, `status`, `trigger_code`, `detected_stage`, `reason`, `evidence_refs_json`, `affected_page_refs_json`, `attempted_actions_json`, `allowed_decisions_json`, `recommendation_json`, `suggested_next_task_json`, `version`, `rule_gap_flag`, `created_at`, `resolved_at`

### `review_decisions`

`review_decision_id`, `review_item_id`, `decision_code`, `review_item_version`, `user_note`, `target_refs_json`, `explicit_confirmation`, `execution_status`, `result_json`, `created_at`, `executed_at`

记录采用追加方式。更新 `review_items` 的当前状态，绝不能替代这里的决定历史。

### `rule_candidates` 与 `rule_candidate_cases`

候选项字段：`candidate_id`、`title`、`gap_summary`、`trigger_pattern_json`、`affected_steps_json`、`status`、`created_at`、`updated_at`。

案例关联字段：`candidate_id`、`review_item_id`、`case_summary`。第一版的候选项不存在“启用为运行时规则”的字段。

## 文档内容规则

### 衍生证据云文档

文档包含简短的元数据头和权威的纯文本或类 Markdown 文字证据。元数据包括证据 ID、资料提交 ID、可选来源 URL、提取方式、创建时间和已知省略内容。正文可以包含 Markdown 表格、ASCII 图或作为文字保存的 Mermaid 源码。

证据被索引为 `active` 后，其权威内容即固定。修正时创建新证据文档，并记录 `supersedes_evidence_id`。如果版本或哈希不一致，表明可能发生手工修改，依赖该证据的更新必须暂停并进入审核。

### 知识页云文档

主题页和概念页是可以持续修改的完整文章。更新前，适配器先读取最新文档和版本。更新方案必须基于该版本校验，并以乐观并发方式提交。发现版本不一致时，绝不能用“直接覆盖最新版本”处理，而是重新读取、重新合并，或者进入审核。

任何文档操作都不能上传、嵌入或外部引用来源图片。生成的表格或图形必须保留为可编辑文字。

## 最终写入协议

飞书不存在横跨云文档、知识库和多维表格的共同事务。第一版不增加 SQLite 或持久补偿队列，而是采用“先计算，后提交”的一次性写入协议。

### 计算与校验阶段

```text
1. 检查身份、目标资源、API scope 和资源权限。
2. 读取目标知识页、当前 revision 和已有证据关系。
3. 在当前任务中完成提取、语义理解、冲突判断和完整正文生成。
4. 在内存或受控临时目录中构造完整 FinalWriteSet。
5. Python 校验全部 Schema、来源 URL、证据依据、页面引用和审核规则。
```

任何一步失败都不得写入证据、知识页或终态记录。需要用户判断时，只创建完整审核项并结束当前任务。

### 集中提交阶段

```text
1. 创建包含完整正文的衍生证据文档。
2. 创建需要新增的知识页。
3. 使用读取时取得的 revision 更新现有知识页，每页尽量只写一次。
4. 写入 evidence_index、knowledge_pages 和 evidence_contributions。
5. 最后写入 submissions 与 processing_records 终态记录。
6. 重新读取关键文档和记录，核对正文、revision、引用与终态一致。
7. 清理当前任务拥有的临时输入。
```

Base 终态记录是任务完成的可见标志。没有通过写后核对时，不能返回成功。

### 部分写入

写入返回 `partial_success`、revision 冲突或写后结果不一致时：

1. 停止剩余正常写入；
2. 读取已受影响资源，确认实际结果；
3. 只在当前任务中执行能够证明安全的有限补偿；
4. 无法确认完全恢复时创建“处理中断”审核项，列出受影响资源、实际结果、风险和可选处理方式；
5. 结束当前任务，不创建恢复游标，也不把部分结果标记为完成。

审核项是终态业务结果。用户决定后启动新的修复或重新处理任务。

## 身份认证与授权

- 单个用户的知识库、云文档、多维表格和云盘资源默认使用 `user_access_token`。
- API 权限和具体资源权限是两个独立关卡，初始化校验时必须同时通过。
- 只申请实现所选 API 所需的最小权限，不申请笼统的全量权限。
- token 保存在现有飞书客户端凭证存储或操作系统安全秘密存储中，绝不写入多维表格、云文档、日志、CLI 参数或提交到版本库的配置。
- 不静默替换为机器人身份，因为机器人可能看不到或不拥有用户资源。

## 频率、分页与并发规则

- 多维表格批量创建或更新，每次最多处理 200 条记录。
- 多维表格列表和搜索必须处理全部分页，每页最多请求 200 条记录。
- 对同一张多维表格串行写入。遇到错误 `1254291` 时只在当前任务中进行次数受限的指数退避，耗尽后进入审核或失败。
- 遇到 HTTP 429 时，按照对应接口的重试说明进行次数受限的退避。
- 云文档写入使用生成更新方案时读取的版本。正常知识页演化不能无条件指定最新版本并直接覆盖。
- 核心流程由 Agent 命令驱动，不依赖飞书事件，也不建立后台恢复任务。

## 删除契约

- 只有已保存的来源 URL、衍生证据文字资源和明确列出的其他文字记录允许执行删除。
- 请求必须携带绑定准确领域 ID 和当前版本的有效确认。
- 准备删除衍生证据以前，必须检查仍在使用的知识页贡献关系，并说明对追溯能力的影响。
- 适配器删除或标记删除准确的飞书文字与索引资源；出现部分成功时停止并生成包含受影响对象的审核项。
- 系统从不尝试删除用户外部保存的 PDF、图片、音频或视频，因为这些文件不归系统管理。

## 外部依赖失败映射

| 情况 | 领域处理结果 |
| --- | --- |
| OAuth 或 token 过期 | 返回 `DEPENDENCY_UNAVAILABLE`，重新授权后再重试 |
| 缺少 API 权限或资源权限 | 返回 `POLICY_BLOCKED`，展示准确缺失的能力 |
| 429 或多维表格 `1254291`，且仍在重试预算内 | 在内部退避后重试 |
| 重试预算已经耗尽 | 用户可以解决时生成“处理中断”审核项，否则当前任务失败并要求重新发起 |
| 云文档版本发生变化 | 重新读取并校验；无法解决的语义差异进入审核 |
| 远端写入成功但响应丢失 | 在当前任务中读取目标资源核对；无法确认时生成“处理中断”审核项 |
| 来源 URL 无法访问 | 保留已有证据和历史，标记来源可用性；新的处理可能需要进入审核 |
