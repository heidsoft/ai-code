# 从 Claude Code 源码学习 AI Agent 构建模式

> 基于 Claude Code v2.1.88 泄漏源码的深度分析，提炼对构建 AI Agent 最有价值的工程模式。

---

## 一、核心架构：异步生成器作为统一数据管道

Claude Code 最值得学习的设计决策是：**用 AsyncGenerator 贯穿整个调用栈**。

```typescript
// query.ts - 核心循环
export async function* query(params: QueryParams): AsyncGenerator<
  StreamEvent | RequestStartEvent | Message | ToolUseSummaryMessage
> {
  yield* queryLoop(params, consumedCommandUuids)
}

// runAgent.ts - Agent 层
export async function* runAgent(...): AsyncGenerator<Message, void> {
  for await (const message of query({ ... })) {
    if (isRecordableMessage(message)) {
      yield message
    }
  }
}
```

**为什么这样设计？**

- 流式响应天然适配：API 返回 token 流，UI 消费 token 流，中间不需要缓冲
- 背压自动处理：消费者慢了，生成器自然暂停
- 错误传播清晰：异常沿 `yield*` 链向上冒泡
- 可组合：`yield*` 可以把子 generator 的输出透传给上层

**你的 Agent 也应该这样做：** 不要用回调，不要用 EventEmitter，用 AsyncGenerator。

---

## 二、多 Agent 编排：三种模式

Claude Code 实现了三种 Agent 协作模式，对应不同场景：

### 模式 1：同步子 Agent（Subagent）

```typescript
// 父 Agent 等待子 Agent 完成，拿到结果再继续
const result = await runAgent({
  agentDefinition: selectedAgent,
  promptMessages,
  isAsync: false,  // 同步
  toolUseContext,
  ...
})
```

适用：需要子 Agent 结果才能继续的场景（研究 → 实现）。

### 模式 2：异步后台 Agent

```typescript
// 子 Agent 在后台运行，父 Agent 继续处理其他事情
const result = await runAgent({
  isAsync: true,  // 异步
  agentAbortController: new AbortController(), // 独立的 abort 控制器
  ...
})
// 父 Agent 不等待，通过通知系统得知完成
```

关键实现细节：
- 异步 Agent 有**独立的 AbortController**，不受父 Agent 中断影响
- `setAppState` 是 no-op，不能修改父 Agent 的 UI 状态
- 通过文件系统（`outputFile`）传递结果，而不是内存

### 模式 3：Fork 子 Agent（最省 token 的方式）

```typescript
// Fork 继承父 Agent 的完整上下文，共享 prompt cache
const forkMessages = buildForkedMessages(prompt, assistantMessage)
// forkMessages 包含父 Agent 的完整对话历史
// → API 请求前缀完全相同 → prompt cache 命中
```

Fork 的核心价值：**共享 prompt cache**。父 Agent 已经花了 token 建立的 cache，Fork 子 Agent 可以直接复用，只需要付 cache read 的费用（约为 cache write 的 1/10）。

**设计原则：**
- 需要结果 → 同步 subagent
- 独立任务 → 异步 background agent
- 研究/探索类 → fork（省 token）

---

## 三、上下文管理：四层压缩策略

这是 Claude Code 最复杂也最值得学习的部分。当对话历史增长时，有四道防线：

```
原始消息积累
    ↓
[1] Snip：删除中间的冗余消息（保留头尾）
    ↓
[2] Microcompact：压缩单个超长工具结果
    ↓
[3] Context Collapse：折叠可折叠的历史段落
    ↓
[4] Auto Compact：用 AI 生成摘要替换全部历史
```

**Auto Compact 的触发逻辑：**

```typescript
// autoCompact.ts
export function getAutoCompactThreshold(model: string): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(model)
  return effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS  // 留 13000 token 缓冲
}

// 电路断路器：连续失败 3 次就停止重试
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
if (tracking?.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES) {
  return { wasCompacted: false }
}
```

**Session Memory（跨会话记忆）：**

```typescript
// sessionMemory.ts - 后台定期提取关键信息
export function shouldExtractMemory(messages: Message[]): boolean {
  // 双重阈值：token 数 AND 工具调用次数都达到才触发
  const hasMetTokenThreshold = hasMetUpdateThreshold(currentTokenCount)
  const hasMetToolCallThreshold = toolCallsSinceLastUpdate >= getToolCallsBetweenUpdates()
  
  return (hasMetTokenThreshold && hasMetToolCallThreshold) ||
         (hasMetTokenThreshold && !hasToolCallsInLastTurn)
}
```

Session Memory 用一个独立的 forked agent 在后台运行，把关键信息写入 `~/.claude/projects/<id>/session-memory.md`，下次会话时注入到上下文。

**你的 Agent 应该实现：**
1. 监控 token 使用量，接近上限前主动压缩
2. 电路断路器防止无限重试
3. 跨会话的持久化记忆

---

## 四、权限系统：分层决策链

每次工具调用都经过一个决策链，从快到慢：

```
1. 检查 deny rules → 直接拒绝
2. 检查 allow rules → 直接允许
3. bypassPermissions 模式 → 直接允许
4. acceptEdits 模式 → 文件编辑类直接允许
5. 安全工具白名单 → 直接允许（跳过 AI 分类器）
6. AI 分类器（YOLO Classifier）→ 用 AI 判断是否安全
7. 询问用户
```

**AI 分类器（最有趣的部分）：**

```typescript
// permissions.ts - auto 模式下用 AI 判断工具调用是否安全
if (appState.toolPermissionContext.mode === 'auto') {
  // 先检查 acceptEdits 模式是否会允许（避免不必要的 AI 调用）
  const acceptEditsResult = await tool.checkPermissions(parsedInput, {
    ...context,
    getAppState: () => ({ ...state, toolPermissionContext: { ...ctx, mode: 'acceptEdits' } })
  })
  if (acceptEditsResult.behavior === 'allow') {
    return { behavior: 'allow', ... }  // 快速路径，不调用分类器
  }
  
  // 调用 AI 分类器
  const classifierResult = await classifyYoloAction(
    context.messages,  // 完整对话历史作为上下文
    formatActionForClassifier(tool.name, input),
    ...
  )
}
```

**连续拒绝追踪（防止 Agent 卡死）：**

```typescript
// denialTracking.ts
export const DENIAL_LIMITS = {
  consecutiveDenials: 3,   // 连续拒绝 3 次
  totalDenials: 10,        // 总拒绝 10 次
}

// 连续拒绝达到上限时，降级到询问用户
if (shouldFallbackToPrompting(denialState)) {
  return result  // 返回 'ask' 而不是 'deny'
}
```

**你的 Agent 应该实现：**
- 分层权限规则（deny > allow > ask）
- 对危险操作的 AI 二次确认
- 防止 Agent 因权限问题无限循环的断路器

---

## 五、Prompt Cache 优化：工程级别的 token 节省

Claude Code 在 prompt cache 上做了大量工程工作：

**1. Fork Agent 共享 cache**

```typescript
// forkedAgent.ts
export type CacheSafeParams = {
  systemPrompt: SystemPrompt    // 必须与父 Agent 完全相同
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  toolUseContext: ToolUseContext
  forkContextMessages: Message[]  // 父 Agent 的完整历史
}
```

Fork Agent 使用与父 Agent 完全相同的参数，确保 API 请求前缀一致，命中 prompt cache。

**2. 1小时 TTL cache（付费用户）**

```typescript
// claude.ts
function should1hCacheTTL(querySource?: QuerySource): boolean {
  // 订阅用户 + 在 GrowthBook 白名单中的 querySource
  let userEligible = isClaudeAISubscriber() && !currentLimits.isUsingOverage
  // 用 bootstrap state 锁定，防止会话中途变化导致 cache 失效
  setPromptCache1hEligible(userEligible)
  ...
}
```

**3. 工具 schema 静态化**

```typescript
// prompt.ts - Agent 列表通过 attachment 注入而不是嵌入工具描述
export function shouldInjectAgentListInMessages(): boolean {
  // 把动态变化的 agent 列表从工具描述中移出
  // 工具描述变化 → tools block 变化 → cache 失效
  // 改为 attachment 注入 → tools block 不变 → cache 命中
  return getFeatureValue_CACHED_MAY_BE_STALE('tengu_agent_list_attach', false)
}
```

**你的 Agent 应该：**
- 把静态内容（系统提示、工具描述）和动态内容（用户上下文、当前状态）分开
- 动态内容通过 user message 注入，不要放在 system prompt 里
- 子 Agent 尽量复用父 Agent 的 cache

---

## 六、Context 构建：什么信息值得注入

```typescript
// context.ts
export const getUserContext = memoize(async () => {
  return {
    claudeMd: getClaudeMds(...),      // 项目级指令
    currentDate: `Today's date is ${getLocalISODate()}.`,  // 当前日期
  }
})

export const getSystemContext = memoize(async () => {
  return {
    gitStatus: await getGitStatus(),  // git 状态（会话开始时快照）
  }
})
```

注意几个细节：
- git status 只在**会话开始时**获取一次，后续不更新（避免 cache 失效）
- 对于只读 Agent（Explore、Plan），**不注入 git status**（节省 token）
- 对于只读 Agent，**不注入 CLAUDE.md**（节省 token）

```typescript
// runAgent.ts
const shouldOmitClaudeMd = agentDefinition.omitClaudeMd && !override?.userContext
const resolvedSystemContext =
  agentDefinition.agentType === 'Explore' || agentDefinition.agentType === 'Plan'
    ? systemContextNoGit  // 去掉 gitStatus
    : baseSystemContext
```

**你的 Agent 应该：**
- 根据 Agent 类型裁剪上下文，不要一刀切地注入所有信息
- 会话级别的信息（git status、项目结构）在开始时快照，不要每次重新获取

---

## 七、工具设计模式

每个工具的标准结构：

```typescript
// Tool.ts
type Tool = {
  name: string
  description: string           // 给模型看的描述
  inputSchema: ZodSchema        // 输入验证
  
  // 核心执行
  call(input, context, canUseTool, assistantMessage, onProgress?): AsyncGenerator<ToolResult>
  
  // 权限检查（在 call 之前）
  checkPermissions?(input, context): Promise<PermissionResult>
  
  // UI 渲染（可选）
  renderResult?: React.Component
  
  // 工具元数据
  maxResultSizeChars?: number   // 结果大小限制
  isReadOnly?: boolean          // 是否只读
  searchHint?: string           // 工具搜索提示
}
```

**BashTool 的安全设计（2593 行的 shell 注入防御）：**

```typescript
// bashSecurity.ts - 多层防御
1. 命令语义分析（AST 解析）
2. 危险命令模式匹配
3. 路径验证（防止越权访问）
4. 输出重定向检测
5. 沙箱模式（可选）
```

**工具结果大小控制：**

```typescript
// AgentTool.tsx
maxResultSizeChars: 100_000  // 10万字符上限

// 超出上限时，结果被存储到文件，返回文件路径
// 防止单个工具结果撑爆 context window
```

---

## 八、Hooks 系统：可扩展的生命周期

```typescript
// hooks 事件类型
type HookEvent =
  | 'PreToolUse'      // 工具调用前
  | 'PostToolUse'     // 工具调用后
  | 'PostToolUseFailure'
  | 'PreCompact'      // 压缩前
  | 'SessionStart'    // 会话开始
  | 'SessionEnd'      // 会话结束
  | 'Stop'            // 每次响应结束
  | 'SubagentStart'   // 子 Agent 启动
  | 'SubagentStop'    // 子 Agent 结束
```

Hook 可以：
- 返回 `{ behavior: 'allow' }` 允许操作
- 返回 `{ behavior: 'deny', message: '...' }` 拒绝操作
- 返回额外上下文注入到 Agent

**结构化输出强制（防止 Agent 忘记调用工具）：**

```typescript
// hookHelpers.ts
export function registerStructuredOutputEnforcement(setAppState, sessionId) {
  addFunctionHook(
    setAppState,
    sessionId,
    'Stop',
    '',
    messages => hasSuccessfulToolCall(messages, SYNTHETIC_OUTPUT_TOOL_NAME),
    `You MUST call the ${SYNTHETIC_OUTPUT_TOOL_NAME} tool to complete this request.`,
    { timeout: 5000 },
  )
}
```

---

## 九、状态隔离：子 Agent 的黄金法则

```typescript
// forkedAgent.ts - createSubagentContext
export function createSubagentContext(parentContext, overrides?) {
  return {
    // 克隆，不共享
    readFileState: cloneFileStateCache(parentContext.readFileState),
    contentReplacementState: cloneContentReplacementState(...),
    
    // 独立的 abort 控制器（父 abort 会传播，但子 abort 不影响父）
    abortController: createChildAbortController(parentContext.abortController),
    
    // 默认 no-op，防止子 Agent 修改父 Agent 的 UI 状态
    setAppState: overrides?.shareSetAppState ? parentContext.setAppState : () => {},
    setInProgressToolUseIDs: () => {},
    
    // 独立的拒绝追踪（异步 Agent 的 setAppState 是 no-op，需要本地追踪）
    localDenialTracking: createDenialTrackingState(),
  }
}
```

**核心原则：子 Agent 默认完全隔离，需要共享时显式 opt-in。**

---

## 十、可以直接借鉴的设计模式总结

| 模式 | 问题 | Claude Code 的解法 |
|------|------|-------------------|
| 流式输出 | 如何传递流式数据 | AsyncGenerator 贯穿全栈 |
| 多 Agent | 如何协调多个 Agent | 同步/异步/Fork 三种模式 |
| Context 溢出 | 对话历史太长 | 四层压缩 + 电路断路器 |
| 跨会话记忆 | 信息跨会话丢失 | 后台 forked agent 定期提取 |
| 权限控制 | 危险操作如何处理 | 分层决策链 + AI 分类器 |
| Token 节省 | API 费用太高 | Prompt cache + Fork 共享 cache |
| 工具安全 | Shell 注入等风险 | 多层防御 + 沙箱 |
| 状态隔离 | 子 Agent 污染父状态 | 默认隔离，显式 opt-in 共享 |
| 无限循环 | Agent 卡在权限拒绝 | 连续拒绝计数 + 断路器 |
| 动态 context | 动态内容导致 cache 失效 | 静态/动态内容分离 |

---

## 附：最小可行 Agent 框架骨架

基于以上模式，一个最小可行的 Agent 框架应该包含：

```typescript
// 1. 核心循环（AsyncGenerator）
async function* agentLoop(params) {
  while (true) {
    const response = yield* callModel(params.messages)
    
    if (!response.hasToolCalls) break
    
    const toolResults = await executeTools(response.toolCalls)
    params.messages = [...params.messages, response, ...toolResults]
  }
}

// 2. 上下文管理
class ContextManager {
  shouldCompact(messages): boolean { /* token 计数 */ }
  async compact(messages): Promise<Message[]> { /* AI 摘要 */ }
}

// 3. 权限系统
class PermissionChecker {
  async check(tool, input): Promise<'allow' | 'deny' | 'ask'> {
    if (this.denyRules.matches(tool, input)) return 'deny'
    if (this.allowRules.matches(tool, input)) return 'allow'
    return 'ask'
  }
}

// 4. 子 Agent 隔离
function createSubagentContext(parent) {
  return {
    ...parent,
    state: deepClone(parent.state),  // 隔离状态
    abortController: new AbortController(),  // 独立控制
    setParentState: () => {},  // 默认 no-op
  }
}
```
