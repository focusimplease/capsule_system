# 快速开始与验证：胶囊系统第一版主流程

本文是阶段 1（Phase 1）为后续实现准备的开发验证指南。其中的命令要等实现完成后才能真正执行；当前写出这些命令，是为了提前固定验证入口和预期结果，并不表示源码已经存在，也不代表已经设计了面向用户的安装包。

## 1. 前置条件

- 面向单个用户的本地 macOS 环境。
- `uv`，当前机器版本为 0.9.17。
- 由 `uv` 管理的 Python 3.12；系统 Python 3.14 不作为项目运行环境。
- 能够读取 Skill、调用终端和处理图片的兼容 Agent 基座；主要开发使用 Codex，兼容性验证使用 Hermes。
- `lark-cli`，当前机器版本为 1.0.94。
- 一个飞书应用，以及专门用于验收的飞书知识库和多维表格。
- Agent 基座所需的模型服务凭证已经通过其自身安全方式配置。

选定的 `faster-whisper`/PyAV 方案不要求安装系统级 FFmpeg。

## 2. 准备开发环境

在项目根目录执行：

```bash
uv python install 3.12
uv sync --all-groups
uv run python --version
uv run capsule --help
```

预期结果：Python 显示为 3.12.x；`capsule --help` 列出资料处理、当前任务继续、最终结果、证据、审核和规则候选相关操作，不包含工作流恢复、后台任务或 Web 服务命令。

## 3. 配置飞书

当前机器返回 `not_configured`，因此需要先完成初始化和授权：

```bash
lark-cli config init --new
lark-cli auth login
lark-cli auth status --verify
```

在飞书开发者后台，只开通 [feishu-storage.md](./contracts/feishu-storage.md) 实际使用的知识库、云文档、多维表格和云盘权限，再让已授权用户获得目标测试资源的访问权。API 权限和具体资源权限必须同时通过检查。

通过不提交到版本库的本地配置或安全环境注入提供资源标识：

```text
CAPSULE_FEISHU_WIKI_SPACE_ID
CAPSULE_FEISHU_BASE_APP_TOKEN
CAPSULE_FEISHU_USER_ID
```

访问令牌和客户端秘密信息保存在飞书或 Hermes 的凭证存储中，不能写入 CLI 参数、测试样例文件、云文档或多维表格。

执行计划中的环境预检和初始化命令：

```bash
uv run capsule admin check
uv run capsule admin bootstrap-test-space --environment acceptance
```

预期结果：环境预检显示已授权身份、准确的目标知识空间、多维表格及必要能力。测试空间命令创建或确认三个知识库分支和终态数据表 Schema，不创建运行状态表、恢复字段或附件字段。

## 4. 验证 Agent 基座加载 Skill

Codex 在项目目录中直接读取 `skills/capsule-system/SKILL.md` 并调用当前开发环境中的 CLI。Hermes 兼容性测试可以把项目 `skills` 目录加入外部 Skill 目录：

```bash
hermes config set skills.external_dirs /Users/apple/Desktop/project/capsule-system/skills
hermes config check
```

新建一个 Hermes 会话，使 Skills 重新建立索引。确认可以发现 `/capsule-system`，并且 Hermes 能够运行只读检查：

```bash
uv run capsule admin check --json
```

这一步只验证开发态兼容性，不是面向用户的 Skill 安装流程。还应从真实 Hermes 飞书对话路径重复一次只读冒烟测试；本地终端成功，并不能证明飞书对话运行环境能够看到相同的 PATH 和工作目录。

## 5. 运行确定性测试

```bash
uv run pytest tests/unit tests/contract
uv run pytest tests/integration -m 'not live_feishu'
```

这些测试必须证明：

- Pydantic 请求和结果模型会拒绝附件持久化字段、恢复字段和未知审核决定。
- Python 需要通用模型推理时返回结构化 `next_action`，不会直接调用第二套通用大模型 API。
- `next_action` 只包含当前任务所需的动作、上下文引用、结果 Schema 和允许的后续命令，不包含持久操作 ID 或恢复位置。
- Agent 提交的结构化结果只有在动作代码、结果 Schema、证据依据和策略约束全部通过时才能进入最终写入。
- 在知识页演化完成以前，包含已采纳新增内容的资料不能形成 `completed` 终态记录。
- 过期的页面 revision 或审核项版本不能被覆盖。
- 一次审核决定不能启用全局规则。
- 缺少明确确认或确认对象不一致时，破坏性操作必须被拒绝。
- 成功、生成审核项、异常、取消和超时后，当前任务创建的临时文件都得到清理。

## 6. 运行内容提取样例

```bash
uv run pytest tests/integration/test_pdf_extraction.py
uv run pytest tests/integration/test_image_understanding.py
uv run pytest tests/integration/test_media_transcription.py
uv run pytest tests/integration/test_web_extraction.py
```

样例必须得到以下结果：

| 样例 | 预期结果 |
| --- | --- |
| 包含可用文字层和内嵌图片的 PDF | 生成文字证据；忽略内嵌图片并记录省略结果 |
| 只有扫描图片的 PDF | 不渲染页面、不执行 OCR；结果不完整并进入审核 |
| 能完整表达事实或步骤的独立图片 | 生成文字证据 |
| 装饰图或普通风景图 | 不生成证据，并记录原因 |
| 只能部分理解的独立图片 | 不补写未知内容，进入审核 |
| 中英文音频和视频 | 生成带分段信号的文字转写，不保留媒体文件 |
| 普通文章网页 | 提取正文和表格，只保存用户提交的网页 URL |
| 装饰性或重复正文的网页图片 | 忽略 |
| 包含必要信息且可可靠识别的网页图表 | 转换为文字、表格或文本定义的图形 |
| 仅 JavaScript 渲染、需要登录或核心图片无法识别的网页 | 进入审核，不临时增加浏览器兜底 |

启用可选 PaddleOCR 以前，先只使用 Hermes 视觉能力运行已标注的图片集。只有测量结果证明文字识别缺口足以支持增加这一依赖时，才能通过经过评审且符合 Plan/SPEC 的变更加入它。

## 7. 运行端到端验收

这些测试会创建和更新文档，因此必须使用专用的飞书测试空间：

```bash
uv run pytest tests/acceptance -m live_feishu
```

样例清单必须为每个样例保存预先标注的预期结果，并把案例映射到 SC-001 至 SC-015。每次运行都要生成可由程序读取的报告，列出每条标准的分子、分母以及非预期副作用。

端到端核心检查：

1. 分别提交新增、补充、重复和冲突资料。新增或补充内容推动主题页或概念页演化；重复文字不再次写入；冲突会暂停流程，而不是替换原有判断。
2. 新建 Agent 会话后检查已经形成终态的资料。即使原始对话不存在，最终处理结果、证据、知识页贡献和审核历史仍然可以读取；未完成任务不会恢复。
3. 模拟 Agent 会话在终态写入前中断，确认飞书没有运行状态、检查点或完成记录，用户重新提交后从头处理。
4. 模拟 Docx 或 Base 返回部分成功。系统不能把任务标记为完成，而应生成列出受影响资源和处理建议的“处理中断”审核项。
5. 通过对话处理“处理中断”和“知识冲突”审核。系统只接受后端提供的决定；需要继续处理时启动新任务，不恢复原执行现场。
6. 准备删除一份衍生证据，验证缺少明确对象确认时操作被拒绝；确认准确对象后，系统只处理该对象并留下决定结果。
7. 把重复出现的未知案例归入一个规则改进候选。确认在后续需求、SPEC 和验收条件完成更新以前，系统运行行为没有变化。

## 8. 验证没有附件被持久保存

每个二进制样例都有唯一的密码学哈希。处理成功、生成审核项、失败、取消和超时后，验收测试扫描应用拥有的目录；Hermes 兼容性测试还要检查其已知图片缓存目录：

```bash
uv run pytest tests/acceptance/test_no_persistent_attachments.py -m live_feishu
```

预期结果：

- 胶囊系统持久目录、Hermes 持久图片缓存和飞书测试资源中，相同哈希的 PDF、图片、音频或视频文件数量为 0。
- 多维表格附件字段数量为 0，云文档中嵌入或外部引用的来源图片数量为 0。
- Base、日志和 Agent 可持久记录中不存在附件字节、base64 或网页图片 URL。
- 用户拥有的原始文件没有被修改或删除。

操作系统级 `SIGKILL` 可能阻止 Python 执行 `finally`。第一版不为此建立持久租约或启动恢复机制；该限制必须如实记录，不能把操作系统临时文件描述成证据或系统管理的原始资料。

## 9. 通过条件

只有满足以下条件，功能才可以进入用户验收：

- 所有确定性测试、契约测试和内容提取测试通过。
- 完整的有限验收资料集满足 SC-001 至 SC-015 中规定的所有比例和数量。
- [plan.md](./plan.md) 中设计前、设计后的宪章检查都保持通过。
- 不存在尚未解决的 `NEEDS CLARIFICATION`，也不存在未经评审的依赖替换。
- 验收报告能够指出本次使用的准确飞书资源、代码版本和配置版本，同时不泄露凭证。

此外必须检查开发产物是否完整，而不只是测试是否通过：

- 当前 `uv` 开发环境能够运行 `capsule` CLI。
- Codex 能够读取仓库 Skill 并调用 CLI；Hermes 能够加载同一份 Skill 完成兼容性冒烟测试。
- `capsule admin bootstrap-test-space` 能够在空白测试空间建立或验证飞书所需结构，并且不存在运行状态表和附件字段。
- `next_action` 能在同一任务中完成多次 Agent 与 CLI 往返，但不会保存成可恢复工作流。
- 新建会话能够读取已有终态结果和审核项；未完成任务必须重新发起。
- `implementation-report.md` 能按模板列出实际实现、测试结果、未完成项和 SPEC 核对结果。
