# Claude Code 源码中的冷门技术深度解析

---

## 一、投机执行（Speculation）——最惊艳的设计

**文件：** `src/services/PromptSuggestion/speculation.ts`

这是整个代码库里最出乎意料的技术。

**是什么：** 当用户在输入框里打字时，Claude Code 在后台**偷偷预测用户接下来要说什么，并提前开始执行**。

流程：
```
用户正在打字...
  ↓
AI 预测用户可能要说："帮我运行测试"
  ↓
后台悄悄启动一个 Agent，开始执行"运行测试"
  ↓
用户按下回车，确认了这个请求
  ↓
直接把后台已经执行的结果注入进来
  ↓
用户感觉"响应超快"
```

**Copy-on-Write 文件隔离：**

投机执行最难的问题是：如果预测错了，后台 Agent 修改了文件怎么办？

解法是 Copy-on-Write（写时复制）：

```typescript
// 后台 Agent 要写文件时
if (isWriteTool) {
  const rel = relative(cwd, filePath)
  
  // 第一次写这个文件：先把原文件复制到 overlay 目录
  if (!writtenPathsRef.current.has(rel)) {
    const overlayFile = join(overlayPath, rel)
    await copyFile(join(cwd, rel), overlayFile)  // 复制原文件
    writtenPathsRef.current.add(rel)
  }
  
  // 把写操作重定向到 overlay 目录，不碰真实文件
  input = { ...input, file_path: join(overlayPath, rel) }
}

// 后台 Agent 读文件时
if (isReadTool) {
  if (writtenPathsRef.current.has(rel)) {
    // 读 overlay 里的版本（已被后台修改过）
    input = { ...input, file_path: join(overlayPath, rel) }
  }
  // 否则读真实文件
}
```

用户确认接受时，才把 overlay 里的文件复制回真实目录：

```typescript
async function copyOverlayToMain(overlayPath, writtenPaths, cwd) {
  for (const rel of writtenPaths) {
    await copyFile(join(overlayPath, rel), join(cwd, rel))
  }
}
```

用户取消时，直接删掉 overlay 目录，真实文件完全没有被动过。

**流水线投机（Pipelined Speculation）：**

更进一步，当用户接受了一次投机执行后，系统立刻开始预测**下一个**用户输入，并提前执行：

```typescript
// 用户接受了投机结果后
void generatePipelinedSuggestion(
  context,
  suggestionText,
  speculatedMessages,  // 刚刚执行的结果
  setAppState,
  abortController,
)
// 不等待，立刻开始预测下一步
```

**为什么值得学：** 这是 AI 应用里的"预取"技术。用户感知到的延迟 = 实际延迟 - 预测命中节省的时间。

---

## 二、Prompt Cache Break 检测——调试 AI 成本的利器

**文件：** `src/services/api/promptCacheBreakDetection.ts`

**是什么：** 自动检测 prompt cache 是否意外失效，并精确定位原因。

Anthropic API 的 prompt cache 是按请求前缀缓存的。如果前缀变了，缓存就失效，需要重新处理所有 token，费用大幅增加。

这个模块在每次 API 调用前后做两件事：

**Phase 1（调用前）：** 记录当前状态的哈希值
```typescript
// 记录所有可能影响 cache key 的因素
const state = {
  systemHash: hash(systemPrompt),
  toolsHash: hash(toolSchemas),
  cacheControlHash: hash(cacheControlSettings),  // scope/TTL 变化
  model: currentModel,
  betas: sortedBetaHeaders,
  effortValue: effortSetting,
  extraBodyHash: hash(extraBodyParams),
  // ...
}
```

**Phase 2（调用后）：** 对比 API 返回的 cache token 数
```typescript
// 如果 cache read tokens 突然大幅下降，说明 cache 失效了
const tokenDrop = prevCacheReadTokens - cacheReadTokens
if (tokenDrop > MIN_CACHE_MISS_TOKENS) {
  // 找出是什么变了
  const reason = buildExplanation(pendingChanges)
  // 例如："system prompt changed (+234 chars)"
  // 或："tools changed (+1/-0 tools)"
  // 或："betas changed (+cache-editing-2025-01-01)"
  logEvent('tengu_prompt_cache_break', { reason, ... })
  
  // 生成 diff 文件，方便调试
  const patch = createPatch('prompt-state', prevContent, newContent)
  await writeFile(diffPath, patch)
}
```

**TTL 过期检测：**

```typescript
// 如果距离上次调用超过 5 分钟，可能是 TTL 过期
if (timeSinceLastAssistantMsg > CACHE_TTL_5MIN_MS) {
  reason = 'possible 5min TTL expiry (prompt unchanged)'
}
// 超过 1 小时
if (timeSinceLastAssistantMsg > CACHE_TTL_1HOUR_MS) {
  reason = 'possible 1h TTL expiry (prompt unchanged)'
}
```

**为什么值得学：** 构建 AI 应用时，prompt cache 失效是隐性成本杀手。这套检测机制可以直接移植到自己的项目里。

---

## 三、`sequential()` 工具函数——防止并发竞争的优雅方案

**文件：** `src/utils/sequential.ts`

这是一个只有 40 行的工具函数，但解决了一个很常见的问题。

**问题：** Session Memory 的提取是异步的，如果用户操作很快，可能同时触发多次提取，导致文件写入冲突。

**解法：**

```typescript
export function sequential<T extends unknown[], R>(
  fn: (...args: T) => Promise<R>,
): (...args: T) => Promise<R> {
  const queue: QueueItem<T, R>[] = []
  let processing = false

  async function processQueue() {
    if (processing) return
    processing = true

    while (queue.length > 0) {
      const { args, resolve, reject, context } = queue.shift()!
      try {
        const result = await fn.apply(context, args)
        resolve(result)
      } catch (error) {
        reject(error)
      }
    }

    processing = false
  }

  return function (...args) {
    return new Promise((resolve, reject) => {
      queue.push({ args, resolve, reject, context: this })
      void processQueue()
    })
  }
}
```

**使用：**

```typescript
// 原来的函数
async function extractSessionMemory(context) { ... }

// 包装后：并发调用会自动排队，一个一个执行
const extractSessionMemory = sequential(async function(context) { ... })

// 同时触发 3 次，会按顺序执行，不会并发
extractSessionMemory(ctx1)  // 立刻执行
extractSessionMemory(ctx2)  // 等 ctx1 完成
extractSessionMemory(ctx3)  // 等 ctx2 完成
```

**为什么值得学：** 比 mutex/semaphore 更简洁，比 debounce/throttle 更精确（每次调用都会执行，只是排队）。

---

## 四、`semanticBoolean()`——防御 AI 输出的类型强制

**文件：** `src/utils/semanticBoolean.ts`

这个只有 20 行的工具函数解决了一个很具体的问题：

**问题：** AI 模型有时会把布尔值用字符串表示：
```json
{ "replace_all": "false" }  // 模型输出了字符串 "false"
```

但 `z.boolean()` 会拒绝这个输入，`z.coerce.boolean()` 更糟糕——它用 JS 的 truthy 规则，`"false"` 会被转成 `true`。

**解法：**

```typescript
export function semanticBoolean<T extends z.ZodType>(
  inner: T = z.boolean() as unknown as T,
) {
  return z.preprocess(
    // 在验证前预处理：把字符串 "true"/"false" 转成真正的布尔值
    (v: unknown) => (v === 'true' ? true : v === 'false' ? false : v),
    inner,
  )
}

// 使用
const schema = z.object({
  replace_all: semanticBoolean(),  // 接受 true/false/"true"/"false"
})
```

**为什么值得学：** 任何接收 AI 输出的工具参数都应该用这种防御性解析。

---

## 五、`lazySchema()`——避免模块加载时的性能问题

**文件：** `src/utils/lazySchema.ts`

只有 8 行，但背后有深刻的工程考量：

```typescript
export function lazySchema<T>(factory: () => T): () => T {
  let cached: T | undefined
  return () => (cached ??= factory())
}
```

**为什么需要它：**

Zod schema 的构建不是免费的，特别是复杂的嵌套 schema。如果在模块顶层定义：

```typescript
// ❌ 模块加载时立刻执行，即使这个 schema 从未被用到
const inputSchema = z.object({
  description: z.string(),
  prompt: z.string(),
  // ... 很多字段
})
```

Claude Code 有 40+ 个工具，每个工具都有 schema。如果全部在模块加载时构建，启动时间会显著增加。

```typescript
// ✅ 第一次调用时才构建，之后缓存
export const inputSchema = lazySchema(() => z.object({
  description: z.string(),
  prompt: z.string(),
  // ...
}))

// 只有真正用到这个工具时才会构建 schema
const schema = inputSchema()
```

**为什么值得学：** 对于有很多工具/命令的 CLI 应用，懒加载 schema 可以显著减少启动时间。

---

## 六、消息指纹（Fingerprint）——归因追踪

**文件：** `src/utils/fingerprint.ts`

**是什么：** 给每次对话生成一个 3 字符的指纹，用于追踪 API 调用的来源。

```typescript
export function computeFingerprint(messageText: string, version: string): string {
  // 取消息文本的第 4、7、20 个字符
  const indices = [4, 7, 20]
  const chars = indices.map(i => messageText[i] || '0').join('')

  // SHA256(盐 + 字符 + 版本号) 的前 3 位
  const fingerprintInput = `${FINGERPRINT_SALT}${chars}${version}`
  const hash = createHash('sha256').update(fingerprintInput).digest('hex')
  return hash.slice(0, 3)  // 例如 "a3f"
}
```

这个指纹会附加在每次 API 请求的 metadata 里，让 Anthropic 的后端可以追踪"这个请求来自 Claude Code 的哪个版本"，同时不暴露用户的实际内容。

**设计亮点：**
- 只取 3 个字符位置，不是完整内容，保护隐私
- 加了盐（`FINGERPRINT_SALT`），防止逆向推导原始内容
- 只有 3 个十六进制字符（4096 种可能），足够区分来源但不足以识别用户

---

## 七、Undercover Mode——卧底模式的完整实现

**文件：** `src/utils/undercover.ts`

这是泄漏代码里最受关注的功能之一，现在可以看到完整实现。

**触发条件：**

```typescript
export function isUndercover(): boolean {
  if (process.env.USER_TYPE === 'ant') {
    // 强制开启
    if (isEnvTruthy(process.env.CLAUDE_CODE_UNDERCOVER)) return true
    
    // 自动检测：除非确认是内部仓库，否则默认开启
    // 'external'、'none'、null 都触发卧底模式
    // 只有 'internal' 才关闭
    return getRepoClassCached() !== 'internal'
  }
  return false  // 外部用户永远不会触发
}
```

**注入的系统提示：**

```
## UNDERCOVER MODE — CRITICAL

You are operating UNDERCOVER in a PUBLIC/OPEN-SOURCE repository.
Your commit messages, PR titles, and PR bodies MUST NOT contain
ANY Anthropic-internal information. Do not blow your cover.

NEVER include:
- Internal model codenames (animal names like Capybara, Tengu, etc.)
- Unreleased model version numbers (e.g., opus-4-7, sonnet-4-8)
- Internal repo or project names
- The phrase "Claude Code" or any mention that you are an AI
- Co-Authored-By lines or any other attribution

GOOD: "Fix race condition in file watcher initialization"
BAD: "Fix bug found while testing with Claude Capybara"
```

**设计细节：**
- 没有强制关闭的方式（`There is NO force-OFF`）
- 默认安全：不确定是否是内部仓库时，默认开启
- 只对 Anthropic 内部员工（`USER_TYPE === 'ant'`）生效，外部用户的构建里这段代码被完全删除

---

## 八、Prompt Cache Break 的 Diff 文件

**文件：** `src/services/api/promptCacheBreakDetection.ts`

当检测到 cache 失效时，会生成一个 unified diff 文件：

```typescript
async function writeCacheBreakDiff(prevContent, newContent) {
  const patch = createPatch(
    'prompt-state',
    prevContent,  // 上次的系统提示 + 工具 schema
    newContent,   // 这次的系统提示 + 工具 schema
    'before',
    'after',
  )
  await writeFile(diffPath, patch)
  return diffPath
}
```

diff 文件的内容大概是：

```diff
--- prompt-state/before
+++ prompt-state/after
@@ -1,5 +1,5 @@
 Model: claude-opus-4-5

 === System Prompt ===

-Today's date is 2026-03-31.
+Today's date is 2026-04-01.
```

一眼就能看出是日期变了导致 cache 失效。

**为什么值得学：** 调试 prompt cache 问题时，这种 diff 方式比看日志直观得多。

---

## 总结：最值得移植的 5 个技术

| 技术 | 代码量 | 移植难度 | 价值 |
|------|--------|---------|------|
| `sequential()` 串行化 | 40 行 | 极低 | 防止并发写入冲突 |
| `semanticBoolean()` | 20 行 | 极低 | 防御 AI 输出的类型问题 |
| `lazySchema()` | 8 行 | 极低 | 减少启动时间 |
| Prompt Cache Break 检测 | ~300 行 | 中 | 调试 AI 成本必备 |
| 投机执行（Speculation） | ~500 行 | 高 | 极致的响应速度优化 |
