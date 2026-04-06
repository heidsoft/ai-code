# Claude Code 组件设计模式深度解析

---

## 一、React Compiler 的 `_c()` 缓存模式

Message.tsx 里到处是这样的代码：

```tsx
function MessageImpl(t0) {
  const $ = _c(94)  // 申请 94 个缓存槽

  // 每次渲染前检查依赖是否变化
  if ($[0] !== addMargin || $[1] !== isTranscriptMode || $[2] !== message.attachment) {
    // 依赖变了，重新计算
    t2 = <AttachmentMessage addMargin={addMargin} attachment={message.attachment} />
    $[0] = addMargin      // 缓存输入
    $[1] = isTranscriptMode
    $[2] = message.attachment
    $[4] = t2             // 缓存输出
  } else {
    t2 = $[4]             // 直接用缓存
  }
  return t2
}
```

这是 React Compiler 自动生成的 memoization，等价于手写 `useMemo`，但粒度更细——每个 JSX 表达式都有独立的缓存槽。

**为什么重要：** 终端渲染性能敏感。一个长对话可能有几千条消息，每次新消息到来都重新渲染所有消息会导致 CPU 飙升。React Compiler 确保只有真正变化的部分才重新渲染。

---

## 二、`areMessagePropsEqual`——精细的 memo 比较函数

```tsx
export function areMessagePropsEqual(prev: Props, next: Props): boolean {
  // 1. UUID 变了必须重渲染
  if (prev.message.uuid !== next.message.uuid) return false

  // 2. 只有包含 thinking 内容的消息才关心 lastThinkingBlockId 变化
  // 否则每次 streaming thinking 开始/停止，所有消息都会重渲染（CC-941）
  if (prev.lastThinkingBlockId !== next.lastThinkingBlockId
      && hasThinkingContent(next.message)) {
    return false
  }

  // 3. verbose 切换影响 thinking block 的可见性
  if (prev.verbose !== next.verbose) return false

  // 4. 只关心"这条消息是否是最新 bash 输出"的状态变化
  // 而不是全局 latestBashOutputUUID 的任何变化
  const prevIsLatest = prev.latestBashOutputUUID === prev.message.uuid
  const nextIsLatest = next.latestBashOutputUUID === next.message.uuid
  if (prevIsLatest !== nextIsLatest) return false

  // 5. 静态消息（已滚出视口的历史消息）只在终端宽度变化时重渲染
  if (prev.isStatic && next.isStatic) return true

  return false
}
```

**关键洞察：** 这个比较函数解决了一个具体的性能 bug（CC-941）：
- 问题：streaming thinking 时，`lastThinkingBlockId` 每次都变，导致所有消息重渲染
- 解法：只有包含 thinking 内容的消息才关心这个 prop 的变化

---

## 三、消息列表的虚拟化与 200 条上限

Messages.tsx 里有一段注释揭示了一个严重的性能问题：

```
// Safety cap for the non-virtualized render path.
// Ink mounts a full fiber tree per message (~250 KB RSS each);
// yoga layout height grows unbounded; the screen buffer is sized to fit
// every line. At ~2000 messages this is ~3000-line screens, ~500 MB of
// fibers, and per-frame write costs that push the process into a GC
// death spiral (observed: 59 GB RSS, 14k mmap/munmap/sec).
const MAX_MESSAGES_WITHOUT_VIRTUALIZATION = 200
```

每条消息约 250KB RSS，2000 条消息 = 500MB 内存，还会触发 GC 死亡螺旋（观测到 59GB RSS）。

**解决方案：UUID 锚点切片**

```tsx
// 不用 slice(-200)（每次新消息都移动切片边界，触发全量重渲染）
// 而是用 UUID 锚点，只在真正超出上限时才移动
export function computeSliceStart(collapsed, anchorRef, cap = 200, step = 50) {
  const anchor = anchorRef.current
  const anchorIdx = anchor
    ? collapsed.findIndex(m => m.uuid === anchor.uuid)
    : -1

  let start = anchorIdx >= 0
    ? anchorIdx
    : anchor
      ? Math.min(anchor.idx, Math.max(0, collapsed.length - cap))
      : 0

  // 只有超出 cap + step 时才推进锚点
  if (collapsed.length - start > cap + step) {
    start = collapsed.length - cap
  }

  return start
}
```

**为什么不用 `slice(-200)`：** 每次新消息到来，`slice(-200)` 会从前面删掉一条，导致所有消息的 React key 变化，触发全量重渲染和终端全量重绘（CC-941）。

---

## 四、消息折叠管道

Messages.tsx 里的消息处理是一个多步骤管道：

```tsx
const { collapsed, lookups } = useMemo(() => {
  // 步骤 1：过滤掉 compact boundary 之前的消息（已在终端滚动区）
  const compactAwareMessages = getMessagesAfterCompactBoundary(normalizedMessages)

  // 步骤 2：过滤掉不应显示的消息
  const messagesToShow = reorderMessagesInUI(
    compactAwareMessages
      .filter(msg => msg.type !== 'progress')
      .filter(msg => !isNullRenderingAttachment(msg))
      .filter(msg => shouldShowUserMessage(msg, isTranscriptMode))
  )

  // 步骤 3：Brief 模式过滤（只显示 Brief 工具输出）
  const briefFiltered = isBriefOnly
    ? filterForBriefTool(messagesToShow, briefToolNames)
    : dropTextInBriefTurns(messagesToShow, dropTextToolNames)

  // 步骤 4：工具调用分组（把相关的 tool_use + tool_result 合并显示）
  const { messages: groupedMessages } = applyGrouping(briefFiltered, tools, verbose)

  // 步骤 5：多种折叠
  const collapsed = collapseBackgroundBashNotifications(
    collapseHookSummaries(
      collapseTeammateShutdowns(
        collapseReadSearchGroups(groupedMessages, tools)
      )
    )
  )

  // 步骤 6：构建查找表（tool_use_id → tool_result 等）
  const lookups = buildMessageLookups(normalizedMessages, messagesToShow)

  return { collapsed, lookups }
}, [/* 依赖 */])
```

**五种折叠：**
- `collapseReadSearchGroups`：把连续的文件读取/搜索折叠成一行
- `collapseTeammateShutdowns`：把 teammate 关闭通知折叠
- `collapseHookSummaries`：把 hook 执行摘要折叠
- `collapseBackgroundBashNotifications`：把后台 bash 通知折叠
- `applyGrouping`：把 tool_use 和对应的 tool_result 分组显示

---

## 五、PromptInput 的输入高亮系统

PromptInput.tsx 里有一个精妙的输入高亮系统，在用户打字时实时高亮特殊语法：

```tsx
// 各种触发器的位置检测
const thinkTriggers = useMemo(
  () => findThinkingTriggerPositions(displayedValue),
  [displayedValue]
)
const ultraplanTriggers = useMemo(
  () => findUltraplanTriggerPositions(displayedValue),
  [displayedValue]
)
const slashCommandTriggers = useMemo(() => {
  const positions = findSlashCommandPositions(displayedValue)
  // 只高亮有效的命令
  return positions.filter(pos => {
    const commandName = displayedValue.slice(pos.start + 1, pos.end)
    return hasCommand(commandName, commands)
  })
}, [displayedValue, commands])
const tokenBudgetTriggers = useMemo(
  () => findTokenBudgetPositions(displayedValue),
  [displayedValue]
)
const slackChannelTriggers = useMemo(
  () => findSlackChannelPositions(displayedValue),
  [displayedValue]
)
```

这些触发器会在输入框里高亮对应的文字：
- `/commit` → 高亮为命令颜色
- `think` / `ultrathink` → 高亮为思考模式颜色（彩虹色！）
- `+500k` → 高亮为 token 预算颜色
- `@teammate-name` → 高亮为对应 teammate 的颜色

---

## 六、LogoHeader 的 memo 优化

```tsx
// 注释解释了为什么要 memo：
// 这个 Box 是所有 MessageRow 之前的第一个兄弟节点。
// 如果它在每次 Messages 重渲染时变脏，renderChildren 的 seenDirtyChild
// 级联会禁用所有后续兄弟节点的 prevScreen（blit）——
// 每个 MessageRow 都从头重写而不是 blit。
// 在长会话（~2800 条消息）中，这是每帧 150K+ 次写入，CPU 100%。
const LogoHeader = React.memo(function LogoHeader({ agentDefinitions }) {
  // ...
}, /* 只在 agentDefinitions 变化时重渲染 */)
```

**关键洞察：** 终端渲染的"blit"优化——如果一个节点没有变化，可以直接复用上一帧的像素，不需要重新计算。但如果父节点或前面的兄弟节点变脏，这个优化就失效了。所以 LogoHeader 必须 memo，否则每次新消息到来都会导致所有消息重新绘制。

---

## 七、Brief 模式的消息过滤

```tsx
// Brief 模式：只显示 Brief 工具的输出，隐藏所有 AI 文字
export function filterForBriefTool(messages, briefToolNames) {
  const nameSet = new Set(briefToolNames)
  const briefToolUseIDs = new Set()

  return messages.filter(msg => {
    if (msg.type === 'assistant') {
      // 保留 API 错误消息（认证失败、限流等）
      if (msg.isApiErrorMessage) return true
      // 保留 Brief 工具调用
      if (block?.type === 'tool_use' && nameSet.has(block.name)) {
        briefToolUseIDs.add(block.id)
        return true
      }
      return false  // 丢弃所有 AI 文字
    }
    if (msg.type === 'user') {
      // 保留 Brief 工具结果
      if (block?.type === 'tool_result') {
        return briefToolUseIDs.has(block.tool_use_id)
      }
      // 只保留真实用户输入，丢弃 meta/tick 消息
      return !msg.isMeta
    }
  })
}
```

**设计意图：** Brief 模式下，AI 的工作过程（文字输出）被隐藏，只显示最终结果（通过 Brief 工具发送的内容）。这让用户专注于结果，而不是 AI 的思考过程。

---

## 八、流式工具调用的 UUID 稳定性

```tsx
// 流式工具调用消息的 UUID 必须稳定，否则 React key 变化导致组件重挂载
const syntheticStreamingToolUseMessages = useMemo(() =>
  streamingToolUsesWithoutInProgress.flatMap(streamingToolUse => {
    const msg = createAssistantMessage({ content: [streamingToolUse.contentBlock] })

    // 用内容块 ID 派生 UUID，而不是 randomUUID()
    // randomUUID() 每次 memo 重计算都会生成新值 → React key 变化 → 组件重挂载
    // → Ink 渲染损坏（旧 DOM 节点的文字重叠）
    msg.uuid = deriveUUID(streamingToolUse.contentBlock.id, 0)

    return normalizeMessages([msg])
  }),
  [streamingToolUsesWithoutInProgress]
)
```

**关键洞察：** 在流式渲染中，UUID 必须是确定性的（由内容派生），而不是随机的。否则每次 memo 重计算都会生成新的 UUID，导致 React 认为这是一个新组件，触发重挂载，产生渲染 bug。

---

## 总结：终端 UI 的核心挑战

Claude Code 的 UI 层解决了几个终端渲染特有的挑战：

| 挑战 | 解决方案 |
|------|---------|
| 长对话内存爆炸 | UUID 锚点切片 + 虚拟滚动 |
| 每帧重绘所有消息 | React Compiler 细粒度 memoization |
| Logo 变脏导致全量重绘 | React.memo + blit 优化 |
| 流式渲染 UUID 不稳定 | 从内容 ID 派生 UUID |
| thinking 变化触发全量重渲染 | 精细的 areMessagePropsEqual |
| 消息噪音太多 | 五层折叠管道 |
