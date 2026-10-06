# AGENTS.md — Agent 协作开发约定

本文件面向参与开发、改进本项目的 AI Agent（及人类协作者）。开始任何工作前，先读完本文件和 [CONSTITUTION.md](CONSTITUTION.md)。

## 项目是什么

胶囊系统：把用户资料（文本/PDF/图片/音视频/网页）处理成文字证据并演化知识页。架构分工：**Python 负责校验、确定性操作、最终写入；Agent 负责语义理解**。`capsule` CLI 是唯一边界——业务数据不能绕过 CLI 直接修改。

当前状态：P1+P2 主流程已完成，235 项测试全部通过，真实飞书环境验证通过，可正式使用。已知限制见 [sdd/001-capsule-main-flow/implementation-report.md](sdd/001-capsule-main-flow/implementation-report.md) 第 6 节。

## 环境与常用命令

```bash
# 环境：macOS + uv + Python 3.12
uv sync --all-groups

# 运行 CLI
uv run capsule --help

# 分层测试（改代码后必须跑）
uv run pytest tests/unit tests/contract -q            # 快速
uv run pytest tests/integration -m 'not live_feishu' -q
uv run pytest -q                                       # 全量（不含 live_feishu）

# 真实飞书端到端验收（需要环境变量 + 专用测试空间，默认跳过）
uv run pytest tests/acceptance -m live_feishu
```

`live_feishu` 标记的测试会写真实飞书资源，只允许指向专用测试空间，绝不指向生产空间。

## 硬约束（来自宪章与项目记忆，违反即返工）

1. **资料必须融入主题**：新数据必须与既有主题整合，不能新增孤立记录；重复内容不堆积；冲突必须识别并明确处理。
2. **原始证据不可无确认删除**：系统可以建议不采纳/删除，但用户明确确认前不得永久删除任何原始证据。
3. **不确定即暂停**：无法依据现有规则可靠决定、且继续执行可能改变证据/知识/产生不可逆后果时，暂停操作、记录原因与状态、交给用户。不得静默忽略或无依据猜测。
4. **单次人工决定不得自动成为全局规则**：规则变更需用户明确批准，并同步更新相关 SPEC 与验收条件。
5. **不保存附件**：系统只持久化文字证据；不引入附件字段、不保存二进制、不保存网页图片 URL。
6. **无恢复现场**：不引入工作流恢复、检查点、运行状态表。会话中断后未终态的任务重新发起。

## 代码组织与契约

- `src/capsule_system/contracts/`：请求模型（Pydantic）、4 个语义动作结果、统一结果信封与稳定错误码。改契约必须同步 `sdd/001-capsule-main-flow/contracts/` 下的契约文档。
- `src/capsule_system/domain/policies.py`：全部策略校验（理解结果、页面方案、审核决定）。新校验规则先确认 SPEC 依据，再落测试。
- `src/capsule_system/storage/feishu/adapter.py`：飞书适配器，经 `lark-cli`（版本 1.0.94）调用。注意：**飞书 Base 搜索索引有秒级延迟**，read-after-write 场景依赖适配器内置的进程内写入缓存，勿移除。
- 测试四层：`tests/unit`（97）/ `tests/contract`（65）/ `tests/integration`（54）/ `tests/acceptance`（19）。契约测试使用替身 Runner，不发真实网络请求。
- `skills/capsule-system/`：面向 Agent 基座的 Skill，只依赖公开 CLI 契约。

## 工作流程

1. **改前先读**：相关模块源码 + 对应测试 + `sdd/001-capsule-main-flow/` 中的 SPEC 条目。不要凭猜测修改。
2. **SPEC 驱动**：行为变更先核对 `spec.md` 是否覆盖；需要新行为时，先确认是需求变更（走需求文档 + 用户批准），不是直接改代码。
3. **测试先行验证**：修复 bug 先写复现测试；新功能同步补三层以上测试。全量测试必须保持通过，基线 235 项（新增测试使数字增长是正常的，减少必须有解释）。
4. **实施报告**：完成一轮实现后更新 `sdd/001-capsule-main-flow/implementation-report.md`，如实记录通过/未通过，不夸大结果。
5. **真实环境验证**：涉及飞书适配器的变更，除替身测试外应在专用测试空间做一次真实往返验证后再宣称完成，验证数据要清理。

## 提交约定

- 提交信息用中文，说明动机而非罗列文件。
- 不提交：凭证、`.env`、`*.profraw`、`.venv`、`__pycache__`、`.DS_Store`、`.trae/`（见 `.gitignore`）。
- 飞书资源标识（token / space id / user id）只通过环境变量注入，绝不写入代码、测试、样例或文档。

## 已知陷阱（踩过的坑）

- 飞书 Base 新写入记录的搜索索引有秒级延迟，立即读会 not found——适配器已用写入缓存解决，改动 create/read 路径时要保持该行为（`adapter.py` 及 `test_feishu_adapter.py` 对应测试）。
- `decode_evidence_markdown` 已处理 markdown 转义与硬换行归一化（`adapter.py`）；证据特殊字符必须字节精确往返，改动编码路径后跑对应单测。
- macOS 上 `CONSTITUTION.md` 与 `constitution.md` 在大小写不敏感文件系统上是同一文件；`.specify/memory/constitution.md` 是指向根目录宪章的符号链接，勿破坏。
