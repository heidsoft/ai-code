# Claude Code 模块深度探索

---

## 模块一：Coordinator（多 Agent 协调器）

**文件：** `src/coordinator/coordinatorMode.ts`

这是 Claude Code 最复杂的功能之一，完整实现了一个**多 Agent 编排系统**。

### Coordinator 的系统提示（完整）

Coordinator 模式下，AI 的角色完全不同——它是一个**指挥官**，不直接执行任务，而是派遣 Worker 去做：

```
你是 Claude Code，一个跨多个 Worker 编排软件工程任务的 AI 助手。

## 1. 你的角色
你是一个协调者。你的工作是：
- 帮助用户实现目标
- 指导 Worker 研究、实现和验证代码变更
- 综合结果并与用户沟通
- 能直接回答的问题直接回答——不要把不需要工具的工作委托出去

你发送的每条消息都是给用户的。Worker 的结果和系统通知是内部信号，
不是对话伙伴——永远不要感谢或确认它们。

## 2. 你的工具
- Agent — 派遣新 Worker
- SendMessage — 继续现有 Worker（发送后续消息到其 agent ID）
- TaskStop — 停止运行中的 Worker
```

### 任务工作流的四个阶段

```
| 阶段       | 执行者          | 目的                           |
|-----------|----------------|-------------------------------|
| Research  | Workers（并行） | 调查代码库，找文件，理解问题      |
| Synthesis | 你（协调者）    | 读取发现，理解问题，制定实现规格  |
| Implementation | Workers  | 按规格进行针对性修改，提交        |
| Verification | Workers    | 测试变更是否有效                 |
```

### 最关键的设计原则：综合（Synthesis）

系统提示里有一段非常重要的规则：

```
当 Worker 报告研究发现时，你必须在指导后续工作之前理解它们。
读取发现。识别方法。然后写一个证明你理解的提示，
包含具体的文件路径、行号和确切要修改的内容。

永远不要写"根据你的发现"或"根据研究"。
这些短语把理解委托给了 Worker，而不是你自己去做。
你永远不要把理解传递给另一个 Worker。
```

**反模式（坏）：**
```
Agent({ prompt: "Based on your findings, fix the auth bug" })
```

**正确模式（好）：**
```
Agent({ prompt: "Fix the null pointer in src/auth/validate.ts:42.
The user field on Session (src/auth/types.ts:15) is undefined when
sessions expire but the token remains cached. Add a null check before
user.id access — if null, return 401 with 'Session expired'. Commit
and report the hash." })
```

### Continue vs Spawn 的决策矩阵

```
| 情况                              | 机制      | 原因                          |
|----------------------------------|-----------|------------------------------|
| 研究探索了需要编辑的文件           | Continue  | Worker 已有文件上下文          |
| 研究很广但实现很窄                 | Spawn     | 避免拖带探索噪音               |
| 纠正失败或扩展近期工作             | Continue  | Worker 有错误上下文            |
| 验证另一个 Worker 刚写的代码       | Spawn     | 验证者应该用新鲜眼光看代码       |
| 第一次实现用了完全错误的方法        | Spawn     | 错误方法的上下文会污染重试       |
| 完全不相关的任务                   | Spawn     | 没有可复用的上下文              |
```

### Worker 结果的格式

Worker 完成后，结果以 XML 格式作为 user message 发回：

```xml
<task-notification>
<task-id>agent-a1b</task-id>
<status>completed|failed|killed</status>
<summary>Agent "Investigate auth bug" completed</summary>
<result>Found null pointer in src/auth/validate.ts:42...</result>
<usage>
  <total_tokens>N</total_tokens>
  <tool_uses>N</tool_uses>
  <duration_ms>N</duration_ms>
</usage>
</task-notification>
```

**设计亮点：** Worker 结果通过 user message 传递，而不是工具结果。这样 Coordinator 可以在等待 Worker 时继续与用户对话。

---

## 模块二：Memory（记忆系统）

**文件：** `src/memdir/memdir.ts`, `src/memdir/memoryTypes.ts`

这是 Claude Code 最精心设计的模块之一，实现了一个**结构化的持久记忆系统**。

### 四种记忆类型

记忆被严格限制在四种类型，每种都有明确的边界：

**1. user（用户记忆）**
```
描述：用户的角色、目标、职责和知识
何时保存：了解到用户的角色、偏好、职责或知识时
如何使用：工作应该受用户背景影响时

示例：
用户："我是一个数据科学家，正在调查我们有哪些日志"
→ 保存：用户是数据科学家，目前专注于可观测性/日志
```

**2. feedback（反馈记忆）**
```
描述：用户给出的关于如何处理工作的指导——包括避免什么和继续做什么
何时保存：用户纠正你的方法时，OR 确认一个非显而易见的方法有效时
关键：不只记录失败，也记录成功！

示例：
用户："不要在这些测试里 mock 数据库——上季度我们被坑了，
      mock 测试通过了但生产迁移失败了"
→ 保存：集成测试必须使用真实数据库，不能用 mock。
  原因：之前的事故中 mock/生产差异掩盖了一个失败的迁移
```

**3. project（项目记忆）**
```
描述：关于正在进行的工作、目标、bug 或事故的信息，
      这些信息无法从代码或 git 历史中推导出来
关键：相对日期要转换为绝对日期！

示例：
用户："我们在周四之后冻结所有非关键合并——移动团队在切发布分支"
→ 保存：合并冻结从 2026-03-05 开始，用于移动端发布切割。
  标记任何计划在该日期之后的非关键 PR 工作
```

**4. reference（引用记忆）**
```
描述：指向外部系统中信息位置的指针
何时保存：了解到外部系统中的资源及其用途时

示例：
用户："如果你想了解这些 ticket 的背景，查看 Linear 项目 'INGEST'，
      那是我们追踪所有 pipeline bug 的地方"
→ 保存：pipeline bug 在 Linear 项目 "INGEST" 中追踪
```

### 什么不应该保存

这个列表非常重要，明确了记忆系统的边界：

```
不要保存：
- 代码模式、约定、架构、文件路径或项目结构——这些可以通过读取当前项目状态推导
- Git 历史、最近的变更或谁改了什么——git log/blame 是权威来源
- 调试解决方案或修复方法——修复在代码里；commit message 有上下文
- CLAUDE.md 文件中已有的任何内容
- 临时任务细节：进行中的工作、临时状态、当前对话上下文

即使用户明确要求保存，这些排除也适用。
如果他们要求保存 PR 列表或活动摘要，
问他们其中什么是令人惊讶或不明显的——那才是值得保留的部分。
```

### 记忆文件格式

```markdown
---
name: 不要 mock 数据库
description: 集成测试必须使用真实数据库，不能用 mock
type: feedback
---

集成测试必须使用真实数据库，不能用 mock。

**Why:** 上季度 mock/生产差异掩盖了一个失败的迁移，
导致 mock 测试通过但生产迁移失败。

**How to apply:** 任何涉及数据库操作的测试都应该使用真实数据库连接。
```

### 记忆的可信度规则

这是最精妙的设计之一：

```
## 从记忆推荐之前

一个命名了特定函数、文件或标志的记忆，是一个声明：
它在记忆被写入时存在。它可能已经被重命名、删除或从未合并。
在推荐之前：

- 如果记忆命名了文件路径：检查文件是否存在
- 如果记忆命名了函数或标志：grep 搜索它
- 如果用户即将根据你的推荐行动：先验证

"记忆说 X 存在"不等于"X 现在存在"。
```

### KAIROS 模式下的记忆：日志模式

在自主 Agent 模式下，记忆系统切换到**追加日志**模式：

```
这个会话是长期存在的。在工作时，通过追加到今天的日志文件来记录值得记住的内容：
~/.claude/projects/<id>/memory/logs/YYYY/MM/YYYY-MM-DD.md

每个条目写成一个简短的带时间戳的 bullet。
不要重写或重组日志——它是追加专用的。
一个单独的夜间进程将这些日志提炼成 MEMORY.md 和主题文件。
```

---

## 模块三：Scratchpad（临时工作目录）

**文件：** `src/utils/permissions/filesystem.ts`（通过 prompts.ts 注入）

```
# Scratchpad Directory

IMPORTANT: Always use this scratchpad directory for temporary files
instead of /tmp or other system temp directories:
~/.claude/tmp/<session-id>/

Use this directory for ALL temporary file needs:
- Storing intermediate results or data during multi-step tasks
- Writing temporary scripts or configuration files
- Saving outputs that don't belong in the user's project
- Creating working files during analysis or processing
- Any file that would otherwise go to /tmp

Only use /tmp if the user explicitly requests it.

The scratchpad directory is session-specific, isolated from the user's
project, and can be used freely without permission prompts.
```

**设计意图：** 给 Agent 一个安全的"草稿纸"，不需要权限确认，不会污染用户的项目目录。

---

## 模块四：Function Result Clearing（工具结果清理）

**文件：** `src/services/compact/cachedMCConfig.ts`（通过 prompts.ts 注入）

```
# Function Result Clearing

Old tool results will be automatically cleared from context to free up space.
The N most recent results are always kept.
```

配合系统提示里的另一条规则：

```
When working with tool results, write down any important information you
might need later in your response, as the original tool result may be
cleared later.
```

**设计意图：** 告诉 AI 工具结果会被清理，所以要主动把重要信息写进回复里，而不是依赖工具结果还在上下文里。

---

## 模块五：Token Budget（Token 预算）

**文件：** `src/query/tokenBudget.ts`（通过 prompts.ts 注入）

```
When the user specifies a token target (e.g., "+500k", "spend 2M tokens",
"use 1B tokens"), your output token count will be shown each turn.
Keep working until you approach the target — plan your work to fill it
productively. The target is a hard minimum, not a suggestion.
If you stop early, the system will automatically continue you.
```

**设计意图：** 让用户可以指定"花多少 token"来控制任务的深度。AI 会持续工作直到接近 token 目标，系统会自动续期。

---

## 模块六：Verification Agent（验证 Agent）

**文件：** `src/constants/prompts.ts`（内部 A/B 测试功能）

这是一个还在测试中的功能，但设计非常有意思：

```
The contract: when non-trivial implementation happens on your turn,
independent adversarial verification must happen before you report
completion — regardless of who did the implementing (you directly,
a fork you spawned, or a subagent). You are the one reporting to the
user; you own the gate.

Non-trivial means: 3+ file edits, backend/API changes, or infrastructure changes.

Spawn the Agent tool with subagent_type="verification-agent".
Your own checks, caveats, and a fork's self-checks do NOT substitute —
only the verifier assigns a verdict; you cannot self-assign PARTIAL.

On FAIL: fix, resume the verifier with its findings plus your fix,
repeat until PASS.
On PASS: spot-check it — re-run 2-3 commands from its report, confirm
every PASS has a Command run block with output that matches your re-run.
```

**设计意图：** 强制 AI 在报告完成之前进行独立验证。自己的检查不算，必须有独立的验证 Agent 给出 PASS/FAIL 判决。

---

## 总结：各模块的核心设计哲学

| 模块 | 核心哲学 |
|------|---------|
| Coordinator | 综合优先——协调者必须自己理解，不能把理解委托给 Worker |
| Memory | 四类型约束——只保存无法从代码推导的信息 |
| Scratchpad | 安全沙箱——给 Agent 一个不需要权限的工作空间 |
| Function Result Clearing | 主动记录——工具结果会消失，重要信息要写进回复 |
| Token Budget | 用户控制深度——花多少 token 由用户决定 |
| Verification Agent | 独立验证——自我检查不算，必须有独立的验证者 |
