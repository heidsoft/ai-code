# AI Agent Harness 工程设计笔记

> 基于 Claude Code (ai-code) 源码分析总结的 Agent 开发最佳实践

---

## 1. 核心循环架构 (Core Loop Pattern)

### AsyncGenerator 流式循环

```typescript
// query.ts — 核心查询循环
export async function* query(
  params: QueryParams,
): AsyncGenerator<StreamEvent | Message | TombstoneMessage, Terminal> {
  // 初始化状态
  let state: State = { messages, toolUseContext, turnCount: 1, ... }

  while (true) {
    // 1. 上下文压缩 (可选)
    const compactResult = await deps.autocompact(...)

    // 2. API 调用 + 流式处理
    for await (const message of deps.callModel({ ... })) {
      yield message  // 增量输出

      if (message.type === 'tool_use') {
        toolUseBlocks.push(message)
      }
    }

    // 3. 工具执行
    for await (const update of runTools(toolUseBlocks, ...)) {
      yield update.message
    }

    // 4. 递归继续
    state = { ...state, messages: [...], turnCount: turnCount + 1 }
  }
}
```

**关键设计：**
- `AsyncGenerator` 实现边流式边处理
- 每个 iteration 创建新 state 对象（Immutable）
- 支持中断 (`abortController.signal`)

---

## 2. 工具抽象 (Tool Abstraction)

### 2.1 Tool 接口设计

```typescript
// Tool.ts
export type Tool<
  Input extends AnyObject = AnyObject,
  Output = unknown,
  P extends ToolProgressData = ToolProgressData,
> = {
  // 核心执行
  call(
    args: z.infer<Input>,
    context: ToolUseContext,
    canUseTool: CanUseToolFn,
    parentMessage: AssistantMessage,
    onProgress?: ToolCallProgress<P>,
  ): Promise<ToolResult<Output>>

  // Schema 定义
  readonly inputSchema: Input
  readonly inputJSONSchema?: ToolInputJSONSchema
  readonly outputSchema?: z.ZodType<unknown>

  // 工具属性声明
  isConcurrencySafe(input: z.infer<Input>): boolean
  isReadOnly(input: z.infer<Input>): boolean
  isDestructive?(input: z.infer<Input>): boolean
  interruptBehavior?(): 'cancel' | 'block'

  // 验证与权限
  validateInput?(input, context): Promise<ValidationResult>
  checkPermissions(input, context): Promise<PermissionResult>

  // UI 渲染 (丰富的展示层)
  renderToolResultMessage(content, progressMessages, options): React.ReactNode
  renderToolUseMessage(input, options): React.ReactNode
  renderToolUseProgressMessage(progressMessages, options): React.ReactNode
  renderToolUseErrorMessage(result, options): React.ReactNode

  // 元数据
  readonly name: string
  readonly description: string
  readonly maxResultSizeChars: number
  readonly shouldDefer?: boolean    // 延迟加载
  readonly alwaysLoad?: boolean     // 始终加载
}
```

### 2.2 buildTool 工厂

```typescript
// Tool.ts — 统一工具构建入口
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input) => false,  // fail-closed
  isReadOnly: (_input) => false,
  isDestructive: (_input) => false,
  checkPermissions: (input, _ctx) =>
    Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: (_input) => '',
  userFacingName: (_input) => '',
}

export function buildTool<D extends ToolDef>(def: D): BuiltTool<D> {
  return { ...TOOL_DEFAULTS, userFacingName: () => def.name, ...def } as BuiltTool<D>
}
```

### 2.3 工具执行编排

```typescript
// toolOrchestration.ts
export async function* runTools(...): AsyncGenerator<MessageUpdate> {
  // 按并发安全性分区
  for (const batch of partitionToolCalls(toolUseMessages, toolUseContext)) {
    if (batch.isConcurrencySafe) {
      // 并发安全工具并行执行
      yield* runToolsConcurrently(batch.blocks, ...)
    } else {
      // 非并发安全工具串行执行
      yield* runToolsSerially(batch.blocks, ...)
    }
  }
}

function partitionToolCalls(toolUseMessages, toolUseContext): Batch[] {
  return toolUseMessages.reduce((acc, toolUse) => {
    const tool = findToolByName(toolUseContext.options.tools, toolUse.name)
    const isConcurrencySafe = tool?.isConcurrencySafe(toolUse.input) ?? false

    // 连续并发安全工具归为一组
    if (isConcurrencySafe && acc[acc.length - 1]?.isConcurrencySafe) {
      acc[acc.length - 1].blocks.push(toolUse)
    } else {
      acc.push({ isConcurrencySafe, blocks: [toolUse] })
    }
    return acc
  }, [])
}
```

---

## 3. 上下文传递 (Context Passing)

### ToolUseContext — 依赖注入容器

```typescript
// Tool.ts
export type ToolUseContext = {
  // 配置
  options: {
    tools: Tools
    commands: Command[]
    mainLoopModel: string
    thinkingConfig: ThinkingConfig
    mcpClients: MCPServerConnection[]
    agentDefinitions: AgentDefinitionsResult
    maxBudgetUsd?: number
    customSystemPrompt?: string
    refreshTools?: () => Tools
  }

  // 生命周期管理
  abortController: AbortController
  readFileState: FileStateCache

  // 状态读写
  getAppState(): AppState
  setAppState: (f: (prev: AppState) => AppState) => void
  setAppStateForTasks?: (f: (prev: AppState) => AppState) => void

  // 追踪
  queryTracking?: { chainId: string; depth: number }
  agentId?: AgentId
  toolUseId?: string

  // 内容管理
  contentReplacementState?: ContentReplacementState
  toolDecisions?: Map<string, { source: string; decision: 'accept' | 'reject'; timestamp: number }>

  // 回调
  handleElicitation?: (serverName, params, signal) => Promise<ElicitResult>
  requestPrompt?: (sourceName, toolInputSummary?) => (request) => Promise<PromptResponse>
}
```

**设计原则：**
- 单一上下文对象传递所有依赖
- 支持嵌套 Agent 的 context 克隆 (`createSubagentContext`)
- Immutable 更新模式

---

## 4. 子 Agent 管理 (Subagent)

### 4.1 Agent 启动

```typescript
// AgentTool/runAgent.ts
export async function* runAgent(params): AsyncGenerator<Message> {
  // 1. 创建子上下文 (隔离)
  const subagentContext = createSubagentContext(parentContext, {
    agentId: newAgentId,
    contentReplacementState: parentState,  // 可选共享
  })

  // 2. 初始化 Agent MCP 服务器
  const { clients, tools, cleanup } = await initializeAgentMcpServers(
    agentDefinition,
    parentContext.mcpClients,
  )

  // 3. 递归调用 query
  for await (const msg of query({ ...subagentContext, tools })) {
    yield msg
  }

  // 4. 清理
  await cleanup()
}
```

### 4.2 Fork 模式 (共享状态)

```typescript
// AgentTool/forkSubagent.ts
export function createSubagentContext(
  parent: ToolUseContext,
  options: {
    agentId: AgentId
    contentReplacementState?: ContentReplacementState  // 共享状态
    renderedSystemPrompt?: SystemPrompt  // 缓存共享
  }
): ToolUseContext {
  return {
    ...parent,
    agentId: options.agentId,
    contentReplacementState:
      options.contentReplacementState ?? parent.contentReplacementState,
    renderedSystemPrompt:
      options.renderedSystemPrompt ?? parent.renderedSystemPrompt,
  }
}
```

---

## 5. 消息类型系统

```typescript
// types/message.ts
export type Message =
  | AssistantMessage    // AI 响应 (含 content blocks: text, tool_use, thinking)
  | UserMessage        // 用户输入
  | SystemMessage      // 系统消息 (compact_boundary, warning, error)
  | ProgressMessage    // 工具执行进度
  | AttachmentMessage  // 附件 (structured_output, max_turns_reached)
  | TombstoneMessage   // 墓碑 (删除标记，用于 UI 同步)
```

### 消息流转

```
User Input → processUserInput() → QueryEngine.submitMessage()
  → query() [循环]
    → callModel() → AssistantMessage (流式)
      → tool_use blocks → runTools()
        → ToolResult → UserMessage (as tool_result)
    → [继续下一轮或结束]
  → result: { success, usage, stop_reason, ... }
```

---

## 6. 状态管理 (State Management)

### QueryEngine — 对话状态

```typescript
// QueryEngine.ts
export class QueryEngine {
  private mutableMessages: Message[]
  private abortController: AbortController
  private totalUsage: NonNullableUsage
  private permissionDenials: SDKPermissionDenial[]
  private readFileState: FileStateCache

  async *submitMessage(prompt, options): AsyncGenerator<SDKMessage> {
    // 1. 构建 system prompt
    const { systemPrompt, userContext } = await fetchSystemPromptParts(...)

    // 2. 处理用户输入 (slash commands)
    const { messages, shouldQuery } = await processUserInput(...)

    // 3. 记录 transcript
    await recordTranscript(messages)

    // 4. 进入 query 循环
    for await (const msg of query({ messages, systemPrompt, ... })) {
      yield msg
    }

    // 5. 跟踪 usage
    this.totalUsage = accumulateUsage(this.totalUsage, currentUsage)
  }

  interrupt(): void {
    this.abortController.abort()
  }
}
```

### Immutable State 更新

```typescript
// 正确做法
state = { ...state, messages: newMessages, turnCount: nextTurnCount }

// 错误做法
state.messages = newMessages  // mutation!
```

---

## 7. 错误恢复机制

### 7.1 多层 Compaction

```typescript
// query.ts 中的 compaction 链
while (true) {
  // 1. HISTORY_SNIP — token 裁剪
  const snipResult = snipModule?.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages

  // 2. Microcompact — 小型摘要
  const microcompactResult = await deps.microcompact(messagesForQuery, ...)

  // 3. Context Collapse — 语义折叠
  if (feature('CONTEXT_COLLAPSE')) {
    const collapseResult = await contextCollapse.applyCollapsesIfNeeded(...)
  }

  // 4. Autocompact — 完整摘要
  const { compactionResult } = await deps.autocompact(...)
}
```

### 7.2 错误处理

```typescript
// 1. 可恢复错误 withheld 直到确认
let withheld = false
if (isWithheldPromptTooLong(message) || isWithheldMaxOutputTokens(message)) {
  withheld = true
}
if (!withheld) yield message

// 2. 恢复策略
if (isWithheld413) {
  // 尝试 context collapse drain
  const drained = contextCollapse.recoverFromOverflow(...)
  if (drained.committed > 0) continue

  // 尝试 reactive compact
  const compacted = await reactiveCompact.tryReactiveCompact(...)
  if (compacted) continue
}
```

---

## 8. 预算控制 (Budget Control)

### 8.1 USD 预算

```typescript
// QueryEngine.ts
if (maxBudgetUsd !== undefined && getTotalCost() >= maxBudgetUsd) {
  yield {
    type: 'result',
    subtype: 'error_max_budget_usd',
    ...
  }
  return
}
```

### 8.2 Token 预算

```typescript
// query/tokenBudget.ts
export function createBudgetTracker() {
  return {
    track(usage: NonNullableUsage) { ... },
    check(agentId?: AgentId, turnTokens?: number) {
      if (this.remaining < turnTokens) {
        return { action: 'stop', reason: 'token_budget_exceeded' }
      }
      return { action: 'continue', continuationCount, pct }
    }
  }
}
```

### 8.3 工具结果预算

```typescript
// utils/toolResultStorage.ts
// 大结果写入磁盘，只传递路径
const persistReplacements =
  querySource.startsWith('agent:') || querySource.startsWith('repl_main_thread')

messagesForQuery = await applyToolResultBudget(
  messagesForQuery,
  contentReplacementState,
  persistReplacements ? recordContentReplacement : undefined,
)
```

---

## 9. 特性标志系统

### 9.1 Feature Flag 定义

```typescript
// 通过 bun:bundle 在构建时注入
import { feature } from 'bun:bundle'

// Dead code elimination
if (feature('COORDINATOR_MODE')) {
  const { getCoordinatorUserContext } = require('./coordinator/coordinatorMode.js')
} else {
  const getCoordinatorUserContext = () => ({})
}
```

### 9.2 Polyfill (开发环境)

```typescript
// entrypoints/cli.tsx — 开发时 feature() 始终返回 false
globalThis.feature = (flag: string) => false
```

---

## 10. MCP (Model Context Protocol)

### MCP 客户端架构

```typescript
// services/mcp/types.ts
export type MCPServerConnection =
  | { type: 'connected'; name: string; tools: Tool[]; resources: ServerResource[] }
  | { type: 'pending'; name: string }
  | { type: 'error'; name: string; error: string }

// 工具发现
export async function fetchToolsForClient(client: MCPClient): Promise<Tool[]> {
  const { tools } = await client.request('tools/list')
  return tools.map(tool =>
    buildTool({
      name: `mcp__${client.name}__${tool.name}`,
      inputSchema: tool.inputSchema,
      async call(args, context, canUseTool) {
        const result = await client.request('tools/call', { name: tool.name, arguments: args })
        return { data: result }
      },
      // ...
    })
  )
}
```

---

## 11. 任务系统 (Task System)

### Task 类型

```typescript
// Task.ts
export type TaskType =
  | 'local_bash'         // 本地 bash 进程
  | 'local_agent'        // 本地子 agent
  | 'remote_agent'       // 远程 agent
  | 'in_process_teammate' // 进程内队友
  | 'local_workflow'     // 本地工作流
  | 'monitor_mcp'       // MCP 监控
  | 'dream'              // 背景思考

export type TaskState = TaskStateBase & {
  status: 'pending' | 'running' | 'completed' | 'failed' | 'killed'
  outputFile: string   // 输出文件路径
  outputOffset: number // 读取偏移
}
```

---

## 12. Python 实现参考 (pyagent/)

基于 Claude Code 架构，使用 **LangGraph** 实现的 Python 版本：

```
pyagent/
├── src/pyagent/
│   ├── core/
│   │   ├── query_engine.py    # LangGraph StateGraph
│   │   └── message.py         # 消息类型
│   ├── tools/
│   │   ├── base.py            # Tool 接口 (ABC)
│   │   ├── registry.py        # 工具注册表
│   │   ├── executor.py        # 并发分区执行器
│   │   └── builtin/           # BashTool, HttpTool, SearchTool
│   ├── llm/
│   │   ├── base.py            # LLMClient 抽象
│   │   └── anthropic.py      # Anthropic 实现
│   └── ops/                   # 运维场景工具
│       ├── alert.py           # 告警处理
│       ├── ticket.py          # 工单处理
│       └── ecom.py            # 电商运营
```

**核心差异**：
- 使用 `abc.ABC` + `Generic` 替代 TypeScript interface
- 使用 `dataclass` 替代 TypeScript type
- 使用 `LangGraph.StateGraph` 管理状态转换
- 使用 `asyncio.Semaphore` 控制并发

---

## 12. 工程实践清单

### 架构设计

- [ ] 使用 AsyncGenerator 实现流式处理
- [ ] Immutable state 更新
- [ ] Context 传递依赖而非全局状态
- [ ] Tool 接口统一 (call, validate, render)

### 工具开发

- [ ] 实现 `isConcurrencySafe()` 声明并发安全性
- [ ] 实现 `validateInput()` 输入验证
- [ ] 实现 `renderToolResultMessage()` 结果展示
- [ ] 使用 `buildTool()` 工厂创建工具
- [ ] 设置 `maxResultSizeChars` 结果大小限制

### 错误处理

- [ ] 支持 `abortController` 中断
- [ ] 实现 `interruptBehavior()` 中断行为
- [ ] 工具超时处理
- [ ] 可恢复错误 withheld + retry

### 性能优化

- [ ] 并发安全工具并行执行
- [ ] 大结果写入磁盘 (toolResultStorage)
- [ ] 内容压缩 (snip, microcompact, collapse)
- [ ] 预算控制 (USD, token, turn)

### 可观测性

- [ ] Transcript 记录
- [ ] Usage 追踪
- [ ] 性能 Checkpoints (headlessProfiler)
- [ ] Analytics events

---

## 13. 关键文件索引

| 文件 | 职责 |
|------|------|
| `src/query.ts` | 核心 Agent 循环 |
| `src/QueryEngine.ts` | 对话状态管理 + SDK 封装 |
| `src/Tool.ts` | Tool 接口定义 + buildTool 工厂 |
| `src/tools.ts` | 工具注册表 |
| `src/services/tools/toolOrchestration.ts` | 工具执行编排 |
| `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行 |
| `src/tools/AgentTool/runAgent.ts` | 子 Agent 启动 |
| `src/context.ts` | System/User Context 构建 |
| `src/types/message.ts` | 消息类型定义 |
| `src/bootstrap/state.ts` | Session 全局状态 |
| `src/hooks/useCanUseTool.ts` | 权限检查钩子 |

---

## 14. 参考架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         QueryEngine                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ submitMessage │  │ mutableMsgs  │  │  abortController    │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
└─────────┼──────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      query() [AsyncGenerator]                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │  compaction │→ │ callModel()  │→ │ for await (message)   │ │
│  │  (多层)      │  │   (流式)     │  │   yield message       │ │
│  └─────────────┘  └──────────────┘  └────────────────────────┘ │
│                                              │                    │
│                          ┌──────────────────┼──────────────────┐│
│                          ▼                  ▼                  ▼│
│                    ┌──────────┐      ┌───────────┐     ┌────────┐│
│                    │ assistant│      │ tool_use  │     │ progress│
│                    │ message  │      │  block    │     │ message │
│                    └──────────┘      └─────┬─────┘     └────────┘│
│                                           │                    │
│                                           ▼                    │
│                                  ┌─────────────────┐            │
│                                  │ runTools()      │            │
│                                  │ - partition()   │            │
│                                  │ - concurrent?   │            │
│                                  │   / serial?     │            │
│                                  └────────┬────────┘            │
│                                           │                    │
│                    ┌──────────────────────┼──────────────────┐ │
│                    ▼                      ▼                  ▼ │
│              ┌──────────┐        ┌────────────┐      ┌────────┐│
│              │ToolResult│        │  progress  │      │  more  ││
│              │(UserMsg) │        │            │      │        ││
│              └────┬─────┘        └────────────┘      └────────┘│
│                   │                                            │
│                   └────────────────────► continue loop? ──────┘│
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                         ToolUseContext                           │
│  options: { tools, commands, model, mcpClients, ... }          │
│  abortController, getAppState, setAppState                       │
│  queryTracking: { chainId, depth }                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    StreamingToolExecutor                         │
│  - addTool() → queued                                           │
│  - 并发安全 → 并行执行                                           │
│  - 非并发安全 → 串行执行                                         │
│  - 结果 buffer → 按顺序 yield                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 15. 结语

这个架构的核心洞见：

1. **分离关注点**：Tool 接口统一但职责分明 (execute/validate/render)
2. **Immutable 优先**：状态不可变更新，便于追踪和回溯
3. **流式处理**：AsyncGenerator 实现边接收边处理
4. **可恢复错误**：多层 compaction + withheld + retry
5. **资源管控**：预算控制 (USD/token/turn)、结果分页
6. **隔离但可共享**：Subagent context 隔离，Prompt cache 可共享

## 16. Python 实现参考 (pyagent/)

基于 Claude Code 架构，使用 **LangGraph** 的 Python 实现：

```
pyagent/
├── src/pyagent/
│   ├── core/
│   │   ├── query_engine.py    # LangGraph StateGraph
│   │   └── message.py         # 消息类型
│   ├── tools/
│   │   ├── base.py            # Tool 接口 (ABC)
│   │   ├── registry.py        # 工具注册表
│   │   ├── executor.py        # 并发分区执行器
│   │   └── builtin/           # BashTool, HttpTool, SearchTool
│   ├── llm/
│   │   ├── base.py            # LLMClient 抽象
│   │   └── anthropic.py       # Anthropic 实现
│   └── ops/                   # 运维场景工具
│       ├── alert.py           # 告警处理
│       ├── ticket.py          # 工单处理
│       └── ecom.py           # 电商运营
```

**核心差异**：
- 使用 `abc.ABC` + `Generic` 替代 TypeScript interface
- 使用 `dataclass` 替代 TypeScript type
- 使用 `LangGraph.StateGraph` 管理状态转换
- 使用 `asyncio.Semaphore` 控制并发
