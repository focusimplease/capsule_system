# 胶囊系统（Capsule System）

Agent 基座驱动的个人知识内化系统：把用户提交的资料（文本 / PDF / 图片 / 音频 / 视频 / 网页 URL）处理成文字证据，并演化飞书知识库中的主题页与概念页。Python 负责校验、确定性操作和最终写入；Agent 负责语义理解任务。`capsule` CLI 是两者之间的唯一边界。

- **语言**：Python 3.12（uv 管理）
- **CLI 框架**：Typer + Pydantic
- **存储后端**：内存（测试/开发）+ 飞书 Wiki/云文档/Base（生产，经 `lark-cli` 1.0.94）
- **测试**：235 项（unit 97 / contract 65 / integration 54 / acceptance 19），全部通过；真实飞书环境端到端验证通过，达到正式使用状态

## 目录结构

```text
src/capsule_system/     # 产品源码
  cli/                  # capsule CLI（15 个命令）
  contracts/            # 请求/动作结果模型与统一结果信封
  domain/               # 任务引擎、策略校验、指纹去重
  extractors/           # 文本/PDF/图片/音视频/网页抽取
  knowledge/            # 结果与证据查询、删除建议
  review/               # 审核队列、决定执行、规则改进候选
  storage/              # 内存后端 + 飞书适配器
  temporary/            # 受控任务工作区（任务结束即清理）
tests/                  # unit / contract / integration / acceptance 四层
skills/capsule-system/  # 面向 Agent 基座的 Skill（CLI 契约封装）
sdd/001-capsule-main-flow/  # SPEC、plan、契约文档、快速开始、实施报告
requirements/           # 需求文档与产品 backlog
CONSTITUTION.md         # 项目宪章（硬约束）
AGENTS.md               # Agent 协作开发约定（先读这个）
```

## 快速开始

```bash
uv python install 3.12
uv sync --all-groups
uv run capsule --help
```

确定性测试（不需要飞书凭证）：

```bash
uv run pytest tests/unit tests/contract
uv run pytest tests/integration -m 'not live_feishu'
```

完整验证流程（含飞书配置、端到端验收、附件不落盘检查）见
[sdd/001-capsule-main-flow/quickstart.md](sdd/001-capsule-main-flow/quickstart.md)。

### 飞书存储配置

通过环境变量提供资源标识（不提交到版本库）：

```text
CAPSULE_FEISHU_WIKI_SPACE_ID
CAPSULE_FEISHU_BASE_APP_TOKEN
CAPSULE_FEISHU_USER_ID
```

初始化：`uv run capsule admin check` → `uv run capsule admin bootstrap-test-space --environment acceptance`。

## 核心文档

| 文档 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Agent 协作开发约定：命令、硬约束、验收流程 |
| [CONSTITUTION.md](CONSTITUTION.md) | 项目宪章：不可妥协的产品原则 |
| [sdd/001-capsule-main-flow/spec.md](sdd/001-capsule-main-flow/spec.md) | 功能 SPEC 与验收场景 |
| [sdd/001-capsule-main-flow/contracts/agent-cli.md](sdd/001-capsule-main-flow/contracts/agent-cli.md) | Agent-CLI 契约 |
| [sdd/001-capsule-main-flow/contracts/feishu-storage.md](sdd/001-capsule-main-flow/contracts/feishu-storage.md) | 飞书存储契约 |
| [sdd/001-capsule-main-flow/implementation-report.md](sdd/001-capsule-main-flow/implementation-report.md) | 实施报告（含已知限制，第 6 节） |
| [requirements/capsule-system-requirements.md](requirements/capsule-system-requirements.md) | 需求文档 |

## 设计要点

- **无恢复现场**：中间状态只存受控工作区，任务结束即清理；会话中断后未形成终态的任务重新发起
- **不保存附件**：系统只处理文字证据，PDF/图片/音视频文件不持久化；多维表格附件字段数为 0
- **审核优先**：冲突、不可逆操作、局部理解内容进入审核队列，等待用户决定；单次决定不会自动成为全局规则
- **证据锚点校验**：Agent 提交的理解结果必须附带证据文字的真实子串，Python 重新校验
