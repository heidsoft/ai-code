# 第四篇：AsyncGenerator——AI 流式输出背后的秘密武器

---

上一篇讲了 Claude Code 的对话循环，代码里出现了很多 `yield`、`async function*`、`for await`。

这一篇，我们把这个技术从头讲清楚。不需要任何基础，看完你就懂了。

---

## 从一个问题开始

假设你要写一个函数，从数据库里读 1000 条记录，然后处理每一条。

**方案一：一次性读完**

```typescript
async function getAllRecords() {
  const records = await db.query('SELECT * FROM records')  // 读 1000 条
  return records  // 一次性返回
}

const records = await getAllRecords()
for (const record of records) {
  process(record)
}
```

问题：1000 条记录全部加载到内存里，如果每条记录很大，内存可能不够用。而且要等所有数据都读完才能开始处理。

**方案二：一条一条读**

```typescript
async function* getRecordsOneByOne() {
  let offset = 0
  while (true) {
    const record = await db.query(`SELECT * FROM records LIMIT 1 OFFSET ${offset}`)
    if (!record) break
    yield record  // 每次只返回一条
    offset++
  }
}

for await (const record of getRecordsOneByOne()) {
  process(record)  // 拿到一条，立刻处理一条
}
```

优点：内存里永远只有一条记录，处理完立刻释放。而且第一条记录读完就能立刻开始处理，不需要等所有数据。

这就是 **Generator** 的核心思想：**按需生产，用多少取多少**。

---

## Generator 是什么？

Generator 是一个**可以暂停和恢复**的函数。

普通函数：调用 → 执行到底 → 返回结果

Generator 函数：调用 → 执行到 `yield` → 暂停 → 等待下一次调用 → 继续执行 → 再次暂停 → ...

```typescript
// 普通函数
function normalFunc() {
  return 1  // 直接返回，结束
}

// Generator 函数（注意 function* 的星号）
function* generatorFunc() {
  yield 1  // 暂停，返回 1
  yield 2  // 暂停，返回 2
  yield 3  // 暂停，返回 3
  // 函数结束
}

const gen = generatorFunc()
console.log(gen.next())  // { value: 1, done: false }
console.log(gen.next())  // { value: 2, done: false }
console.log(gen.next())  // { value: 3, done: false }
console.log(gen.next())  // { value: undefined, done: true }
```

每次调用 `.next()`，函数从上次暂停的地方继续执行，直到下一个 `yield`。

---

## AsyncGenerator = Generator + 异步

普通 Generator 里不能用 `await`（等待异步操作）。

`async function*` 解决了这个问题：

```typescript
async function* fetchPages() {
  let page = 1
  while (true) {
    // 可以用 await 等待网络请求
    const response = await fetch(`/api/data?page=${page}`)
    const data = await response.json()

    if (data.items.length === 0) return  // 没数据了，结束

    yield data.items  // 暂停，把这页数据交出去
    page++
  }
}

// 消费：for await...of 自动处理异步
for await (const items of fetchPages()) {
  console.log('收到一页数据：', items)
  // 处理完这页，才会继续请求下一页
}
```

---

## 在 Claude Code 里的实际用法

### 场景：流式 API 响应

LLM API 是流式的，token 一个个返回。用 AsyncGenerator 完美匹配：

```typescript
// 简化版的 API 调用
async function* callModel(messages) {
  // 建立流式连接
  const stream = await anthropic.messages.stream({
    model: 'claude-opus-4-5',
    messages: messages,
  })

  // 逐个处理流式事件
  for await (const event of stream) {
    if (event.type === 'content_block_delta') {
      // 这是一个 token
      yield {
        type: 'token',
        content: event.delta.text
      }
    }

    if (event.type === 'message_stop') {
      // 模型回复完毕
      yield {
        type: 'done'
      }
    }
  }
}

// UI 消费
for await (const event of callModel(messages)) {
  if (event.type === 'token') {
    // 每收到一个 token，立刻显示
    process.stdout.write(event.content)
  }
}
```

用户看到的效果：文字一个字一个字地出现，而不是等待很久后突然全部出现。

---

## `yield*`：透传的魔法

Claude Code 里有一个特殊语法：`yield*`

```typescript
async function* queryLoop(params) {
  // yield* 把 callModel 的所有输出原封不动地透传出去
  yield* callModel(params.messages)
}
```

`yield*` 的意思是："把另一个 generator 的所有值，一个不差地传给我的调用者"。

不用 `yield*` 的写法：

```typescript
async function* queryLoop(params) {
  // 手动透传，效果相同但更啰嗦
  for await (const event of callModel(params.messages)) {
    yield event
  }
}
```

Claude Code 的调用链就是靠 `yield*` 串起来的：

```
UI 层
  ↑ yield*
queryLoop（对话循环）
  ↑ yield*
callModel（API 调用）
  ↑ yield*
anthropic.messages.stream（底层流）
```

每一层都不需要缓冲，token 从 API 直接流到 UI。

---

## 背压：消费者控制生产者的速度

这是 AsyncGenerator 最重要的特性，也是最容易被忽视的。

**什么是背压？**

想象一根水管，左边是水源（生产者），右边是水桶（消费者）：

- **没有背压**：水源一直开着，水桶装满了还在流，水溢出来了（内存溢出）
- **有背压**：水桶满了，水管自动关小；水桶有空间了，水管再开大

**代码里的背压问题：**

```typescript
// ❌ 没有背压 - EventEmitter 方式
const emitter = new EventEmitter()

// 生产者：疯狂发数据，不管消费者有没有处理完
llmAPI.on('token', (token) => {
  emitter.emit('token', token)  // 立刻发出去，不等待
})

// 消费者：处理很慢（比如要写数据库）
emitter.on('token', async (token) => {
  await writeToDatabase(token)  // 需要 100ms
  // 但生产者每 1ms 就发一个 token
  // 100ms 内积累了 100 个 token 在内存里等待
  // 越积越多 → 内存爆炸
})
```

**AsyncGenerator 自动解决背压：**

```typescript
// ✅ 有背压 - AsyncGenerator 方式
async function* producer() {
  for (let i = 0; i < 1000; i++) {
    await sleep(1)  // 每 1ms 生产一个
    yield i
    // yield 之后，函数暂停在这里
    // 必须等消费者调用 .next() 才会继续
  }
}

for await (const item of producer()) {
  await writeToDatabase(item)  // 处理需要 100ms
  // 处理完了，才进入下一次循环
  // 才调用 .next()，生产者才继续生产
}
```

执行时序：

```
消费者调用 .next()
  → 生产者运行，yield 出第 0 个
  → 消费者拿到，处理 100ms
  → 处理完，调用 .next()
  → 生产者继续，yield 出第 1 个
  → 消费者拿到，处理 100ms
  → ...
```

生产者和消费者**步调一致**，内存里永远只有一个待处理的 item。

**在 Claude Code 里的意义：**

LLM API 吐 token 很快（每秒几百个），但终端渲染可能慢（需要计算宽度、处理颜色等）。有了背压，API 流自然等待渲染完成，不会把几百个 token 堆在内存里。

---

## 为什么不用回调或 Promise？

对比三种方式处理流式数据：

```typescript
// ❌ 回调方式 - 嵌套地狱
callModel(messages, {
  onToken: (token) => {
    renderToken(token, {
      onDone: () => {
        // 嵌套越来越深...
      }
    })
  },
  onError: (err) => { /* 错误处理分散 */ }
})

// ❌ Promise 方式 - 无法处理多个值
const result = await callModel(messages)
// Promise 只能返回一个值，无法表达"一系列值"

// ✅ AsyncGenerator - 线性代码，自动背压
for await (const event of callModel(messages)) {
  renderToken(event)
}
// try/catch 正常工作，代码线性易读
```

---

## 一个完整的例子

把所有概念串起来，写一个简单的流式 AI 对话：

```typescript
// 模拟 LLM 流式响应
async function* streamAI(prompt: string) {
  const words = `你好！我是 AI 助手，你问的是：${prompt}`.split('')
  for (const char of words) {
    await new Promise(r => setTimeout(r, 50))  // 模拟网络延迟
    yield char  // 每次 yield 一个字符
  }
}

// 工具调用循环
async function* agentLoop(prompt: string) {
  console.log('用户：', prompt)
  process.stdout.write('AI：')

  // 透传流式输出
  for await (const char of streamAI(prompt)) {
    process.stdout.write(char)  // 实时打印每个字符
    yield char  // 同时传给上层
  }

  console.log()  // 换行
}

// 运行
async function main() {
  for await (const _ of agentLoop('你是谁？')) {
    // 消费，但这里不需要做额外处理
    // 打印已经在 agentLoop 里做了
  }
}

main()
```

运行效果：字符一个一个地出现，就像真实的 AI 在打字。

---

## 小结

| 概念 | 一句话解释 |
|------|-----------|
| Generator | 可以暂停的函数，用 `yield` 暂停 |
| AsyncGenerator | 可以暂停 + 可以 `await` 的函数 |
| `yield*` | 把另一个 generator 的所有值透传出去 |
| `for await...of` | 消费 AsyncGenerator 的方式 |
| 背压 | 消费者不 next()，生产者自动等待 |

Claude Code 用 AsyncGenerator 的原因很简单：**LLM API 本身就是流式的，AsyncGenerator 是流式数据最自然的表达方式**。从 API 到 UI，中间每一层都不需要缓冲，token 直接流过去。

---

## 下一篇预告

对话越来越长，token 越来越多，最终会超出模型的上下文窗口限制。

Claude Code 是怎么解决这个问题的？它有四层压缩策略，还有跨会话的"记忆"系统。

下一篇，我们来看 Claude Code 的上下文管理。

---

*本系列文章基于泄漏源码进行技术分析，仅供学习研究。*
