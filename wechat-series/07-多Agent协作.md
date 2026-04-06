# 第七篇：多 Agent 协作

## ——同步、异步、Fork 三种模式

---

Claude Code 不只是一个 AI，它可以同时运行多个 AI Agent 协作完成任务。

这一篇，我们来看多 Agent 协作的三种模式，以及背后的工程设计。

---

## 为什么需要多 Agent？

单个 Agent 有几个局限：

**1. 上下文窗口限制**
一个复杂任务可能需要读几十个文件、执行几十次命令。单个 Agent 的上下文很快就满了。

**2. 并行效率**
"研究这个问题"和"实现那个功能"是两个独立的任务，完全可以同时进行。

**3. 专业分工**
不同的任务适合不同的 Agent：有的擅长代码审查，有的擅长测试，有的擅长文档。

---

## 三种协作模式

### 模式一：同步子 Agent

父 Agent 暂停，等待子 Agent 完成，拿到结果后继续。

```
父 Agent
  │
  ├─→ 启动子 Agent（研究任务）
  │     │
  │     └─→ 子 Agent 工作...
  │           │
  │           └─→ 返回结果
  │
  ├─→ 拿到结果，继续工作
  │
  └─→ 完成
```

**代码实现：**

```typescript
// AgentTool.tsx（简化）
const result = await runAgent({
  agentDefinition: selectedAgent,
  promptMessages: [{ content: prompt }],
  isAsync: false,  // 同步模式
  toolUseContext,
})

// 等待子 Agent 完成后，result 里有子 Agent 的输出
// 父 Agent 继续处理
```

**适用场景：** 需要子 Agent 的结果才能继续的任务。比如"先研究这个 API 的用法，再根据研究结果写代码"。

---

### 模式二：异步后台 Agent

父 Agent 启动子 Agent 后，**不等待**，继续做其他事情。子 Agent 在后台运行，完成后通知父 Agent。

```
父 Agent
  │
  ├─→ 启动子 Agent A（后台运行）
  ├─→ 启动子 Agent B（后台运行）
  ├─→ 启动子 Agent C（后台运行）
  │
  ├─→ 父 Agent 继续做其他事情
  │
  ├─→ 收到 Agent A 完成通知
  ├─→ 收到 Agent B 完成通知
  └─→ 收到 Agent C 完成通知
```

**代码实现：**

```typescript
// 启动异步 Agent
const result = await runAgent({
  isAsync: true,  // 异步模式
  agentAbortController: new AbortController(),  // 独立的控制器
  ...
})

// 立刻返回，不等待子 Agent 完成
// 子 Agent 在后台运行，结果写入文件
// 完成后通过通知系统告知父 Agent
```

**关键设计细节：**

异步 Agent 有**独立的 AbortController**。这意味着：
- 父 Agent 被中断，不会影响后台 Agent
- 后台 Agent 可以独立运行，直到完成

异步 Agent 的 `setAppState` 是 no-op（空操作）：
- 后台 Agent 不能修改父 Agent 的 UI 状态
- 防止后台操作干扰前台显示

**适用场景：** 独立的并行任务。比如同时运行"代码审查"、"测试"、"文档生成"三个任务。

---

### 模式三：Fork 子 Agent（最省 token）

Fork 是最特殊的模式。子 Agent **继承父 Agent 的完整对话历史**，然后独立运行。

```
父 Agent（有完整对话历史）
  │
  ├─→ Fork 子 Agent A（继承父 Agent 的历史）
  │     └─→ 在父 Agent 的基础上继续工作
  │
  ├─→ Fork 子 Agent B（继承父 Agent 的历史）
  │     └─→ 在父 Agent 的基础上继续工作
  │
  └─→ 父 Agent 等待两个 Fork 完成
```

**为什么 Fork 省 token？**

这涉及到 **Prompt Cache**（提示词缓存）的概念。

Anthropic API 有一个功能：如果你发送的请求前缀和之前的请求完全相同，API 会直接使用缓存，不需要重新处理这部分内容，费用大幅降低（约为正常费用的 1/10）。

Fork 子 Agent 使用与父 Agent **完全相同的系统提示和对话历史**，所以 API 请求的前缀完全一致，可以命中缓存。

```typescript
// forkedAgent.ts
export type CacheSafeParams = {
  systemPrompt: SystemPrompt    // 必须与父 Agent 完全相同
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  toolUseContext: ToolUseContext
  forkContextMessages: Message[]  // 父 Agent 的完整历史
}

// Fork 子 Agent 使用完全相同的参数
// → API 请求前缀相同
// → 命中 Prompt Cache
// → 费用降低 90%
```

**适用场景：** 研究类、探索类任务。比如"同时从三个角度分析这个问题"。

---

## 子 Agent 的状态隔离

多个 Agent 同时运行，如何防止它们互相干扰？

Claude Code 的答案是：**默认完全隔离，需要共享时显式声明**。

```typescript
// forkedAgent.ts - createSubagentContext（简化）
function createSubagentContext(parentContext, overrides?) {
  return {
    // 克隆，不共享（防止子 Agent 修改父 Agent 的文件缓存）
    readFileState: cloneFileStateCache(parentContext.readFileState),

    // 独立的 abort 控制器
    // 父 Agent 中断不影响子 Agent（异步模式）
    abortController: new AbortController(),

    // 默认 no-op，防止子 Agent 修改父 Agent 的 UI 状态
    setAppState: overrides?.shareSetAppState
      ? parentContext.setAppState  // 显式声明共享
      : () => {},                  // 默认 no-op

    // 独立的拒绝追踪
    // 异步 Agent 的 setAppState 是 no-op，需要本地追踪
    localDenialTracking: createDenialTrackingState(),
  }
}
```

**为什么要克隆 readFileState？**

`readFileState` 是文件读取缓存，记录了"哪些文件已经读过，内容是什么"。

如果子 Agent 修改了文件，父 Agent 的缓存就过期了。克隆一份，子 Agent 的修改不会影响父 Agent 的缓存。

---

## Agent 定义：用 YAML 配置

每个 Agent 的能力和行为，用 YAML 文件定义：

```yaml
# .claude/agents/code-reviewer.md
---
name: code-reviewer
description: 专门负责代码审查的 Agent
model: sonnet  # 使用哪个模型
tools:
  - FileRead    # 只允许读文件
  - GlobTool    # 允许搜索文件
  # 注意：没有 Bash、FileEdit，不能执行命令或修改文件
permissionMode: default  # 权限模式
whenToUse: 当需要审查代码质量、找 bug、提出改进建议时使用
---

你是一个专业的代码审查员。你的职责是：
1. 找出潜在的 bug
2. 指出不符合最佳实践的代码
3. 提出具体的改进建议

你只能读取文件，不能修改任何内容。
```

这个设计很优雅：
- 工具列表限制了 Agent 的能力范围
- 系统提示定义了 Agent 的行为方式
- `whenToUse` 告诉主 Agent 什么时候应该调用这个子 Agent

---

## 实际效果：并行加速

假设你让 Claude Code 做一个复杂任务："审查这个 PR，包括代码质量、安全性、性能三个维度"。

**串行方式（单 Agent）：**
```
代码质量审查（30秒）→ 安全性审查（30秒）→ 性能审查（30秒）= 90秒
```

**并行方式（多 Agent）：**
```
代码质量审查（30秒）
安全性审查（30秒）  → 同时运行 → 30秒完成
性能审查（30秒）
```

时间缩短了 3 倍。

---

## 小结

Claude Code 的三种 Agent 协作模式：

| 模式 | 特点 | 适用场景 |
|------|------|---------|
| 同步子 Agent | 等待结果 | 需要结果才能继续 |
| 异步后台 Agent | 不等待，后台运行 | 独立并行任务 |
| Fork 子 Agent | 继承父 Agent 历史，省 token | 研究探索类任务 |

核心设计原则：**默认完全隔离，需要共享时显式声明**。

---

## 下一篇预告

这是系列的最后一篇。

我们把从 Claude Code 学到的所有工程模式总结出来，整理成可以直接用于自己项目的设计指南。

---

*本系列文章基于泄漏源码进行技术分析，仅供学习研究。*
