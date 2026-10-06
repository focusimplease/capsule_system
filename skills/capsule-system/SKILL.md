---
name: capsule-system
description: 胶囊系统：把用户的资料（文本/PDF/图片/音频/视频/网页 URL）处理成文字证据并演化知识页。用户提交资料、询问处理状态、处理审核项或管理证据时使用本 Skill。
---

# 胶囊系统 Skill

`capsule` CLI 是你与胶囊系统之间的唯一边界。你负责理解用户语言、完成语义任务、
向用户展示结果；Python 负责校验、确定性操作和最终写入。你不能绕过 CLI 直接
修改业务数据。

## 核心工作循环

```text
用户自然语言
  -> 你选择 capsule CLI 命令并整理参数
  -> CLI 返回结果信封（stdout 单个 JSON）
  -> 信封含 next_action 时：读取 context_refs，完成语义任务，
     通过 capsule ingest continue 提交结构化结果
  -> 信封为 review_required 时：向用户展示审核项，等待用户决定
  -> 信封为 success/failed 时：向用户解释结果
```

一次任务可以包含多次 CLI 调用，但全部往返必须在当前会话中完成。会话中断后
未形成终态的任务必须重新发起，没有恢复入口。

## 语义任务（next_action 的四种动作）

| action_code | 你要做什么 |
| --- | --- |
| `understand_image` | 读取输入图片，判断：完整表达知识（usable，写出识别文字）/ 无知识（no_evidence）/ 只能局部理解（review） |
| `understand_web_images` | 逐张判断网页图片候选：装饰或重复正文（ignored + 原因）/ 可靠转换为文字（text）/ 核心内容依赖且无法可靠转换（core_incomplete） |
| `compare_knowledge` | 阅读证据文字与候选页面，判断主题归属、关键判断（带证据锚点）、重复部分、新增价值、冲突 |
| `organize_pages` | 组织页面演化：安全更新（updates）与冲突挂起更新（pending_updates）分开提交 |

规则：
- 证据锚点必须是证据文字的**真实子串**，Python 会重新校验；
- 概念页内容必须包含定义、边界、相近概念、典型用法四个部分；
- 页面内容只能是纯文本（Markdown 标题/表格/文本定义的图形），
  不得包含图片引用或外部图片 URL；
- 冲突必须引用既有页面并把更新放入 pending_updates，不能静默覆盖。

## 决策规则（必须遵守）

1. 你可以理解用户语言和内容，但不能绕过 CLI 直接修改业务数据。
2. 审核回复存在歧义时继续询问，不调用写命令。
3. 只展示后端返回的允许决定，不能自行创造决定代码。
4. 遇到 `review_required` 时解释原因并返回审核项，不能继续猜测或无限重试。
5. 模型自报的置信度不能单独触发审核；必须给出缺少信息、校验失败、判断冲突
   或重试耗尽等可检查条件。
6. 单次用户决定不能被你泛化成全局规则。
7. 会话中断后，未形成终态的任务必须重新发起。

## 输入处理约定

- 纯文本：`input.text`；
- PDF、图片、音频、视频：先把用户文件放入当前任务可用的临时路径，
  再通过 `input.temporary_path` 提交；系统不保存这些文件；
- 网页：`input.url`；用户提供的来源说明放入 `user_context`。

## 参考资料

- [references/cli-contract.md](./references/cli-contract.md)：结果信封、错误码与调用规则
- [references/commands.md](./references/commands.md)：全部命令的请求字段与示例
- [references/review-format.md](./references/review-format.md)：审核项的展示与决定格式
