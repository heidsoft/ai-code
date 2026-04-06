# Claude Code TUI 实现深度解析

Claude Code 的终端 UI 是一个完整的自研渲染引擎，从 React 组件树到终端字符输出，每一层都有精心的工程设计。

---

## 整体架构：五层渲染管道

```
React 组件树
    ↓ React Reconciler（自定义）
DOM 树（ink-box, ink-text 等节点）
    ↓ Yoga 布局引擎（WebAssembly）
计算好位置和尺寸的节点树
    ↓ renderNodeToOutput（遍历节点树）
Output（操作队列：write/blit/clip/clear）
    ↓ Output.get()
Screen（二维字符缓冲区，TypedArray）
    ↓ LogUpdate.render()（差分算法）
ANSI 转义码序列
    ↓ 写入 stdout
终端显示
```

---

## 第一层：Screen 缓冲区——零 GC 的核心数据结构

Screen 是整个渲染引擎的核心，用 TypedArray 存储所有字符，完全避免 GC 压力。

### 每个 Cell 的存储格式

```
每个 Cell = 2 个 Int32（8 字节）

word0 (cells[ci]):     charId（32位，索引到 CharPool）
word1 (cells[ci+1]):   styleId[31:17] | hyperlinkId[16:2] | width[1:0]
```

200×120 的屏幕 = 24,000 个 Cell = 192KB，完全在 L2 缓存里。

对比：如果用 JS 对象，24,000 个对象 = 约 2.4MB，还有 GC 压力。

### CharPool：字符串 intern

```typescript
class CharPool {
  private ascii: Int32Array = initCharAscii()  // ASCII 快速路径

  intern(char: string): number {
    // ASCII 字符：直接数组查找，O(1)
    if (char.length === 1) {
      const code = char.charCodeAt(0)
      if (code < 128) {
        const cached = this.ascii[code]
        if (cached !== -1) return cached
        // 首次见到，存入
      }
    }
    // 非 ASCII：Map 查找
    const existing = this.stringMap.get(char)
    if (existing !== undefined) return existing
    // ...
  }
}
```

所有字符串都被 intern 成整数 ID，blitRegion 复制时直接复制整数，不需要字符串比较。

### StylePool：样式 intern + 差分缓存

```typescript
class StylePool {
  // 样式 ID 的 bit 0 编码"是否对空格可见"
  // 前景色样式 → 偶数 ID
  // 背景色/反色/下划线 → 奇数 ID
  // 渲染时可以用 bitmask 跳过不可见的空格
  intern(styles: AnsiCode[]): number {
    const rawId = this.styles.length
    id = (rawId << 1) | (hasVisibleSpaceEffect(styles) ? 1 : 0)
  }

  // 样式转换字符串缓存：(fromId, toId) → ANSI 字符串
  // 零分配，热路径命中率接近 100%
  transition(fromId: number, toId: number): string {
    const key = fromId * 0x100000 + toId
    let str = this.transitionCache.get(key)
    if (str === undefined) {
      str = ansiCodesToString(diffAnsiCodes(this.get(fromId), this.get(toId)))
      this.transitionCache.set(key, str)
    }
    return str
  }
}
```

---

## 第二层：Output——操作队列模式

Output 不直接写 Screen，而是先收集操作，再批量执行。

```typescript
type Operation =
  | WriteOperation   // 写文字
  | BlitOperation    // 从上一帧复制区域（最快）
  | ClipOperation    // 设置裁剪区域
  | ClearOperation   // 清除区域
  | ShiftOperation   // 滚动行（硬件加速）
  | NoSelectOperation // 标记不可选区域
```

### Blit 优化：最重要的性能优化

```typescript
// 如果一个节点的内容没有变化，直接从上一帧复制
output.blit(prevScreen, x, y, width, height)
// 等价于 TypedArray.set()，比逐字符写入快 10-100 倍
```

**什么时候可以 blit：**
- 节点的 React props 没有变化（React Compiler 的 `_c()` 保证）
- 节点不是 dirty 的（Yoga 布局没有变化）
- 上一帧的 screen 没有被污染（selection overlay 等操作会污染）

### charCache：行级缓存

```typescript
// Output 维护一个 Map<string, ClusteredChar[]>
// 相同的文字行只需要 tokenize + grapheme cluster 一次
// 大多数行在帧间不变，缓存命中率很高
let characters = charCache.get(line)
if (!characters) {
  characters = reorderBidi(
    styledCharsWithGraphemeClustering(
      styledCharsFromTokens(tokenize(line)),
      stylePool,
    ),
  )
  charCache.set(line, characters)
}
```

---

## 第三层：差分算法——只更新变化的部分

LogUpdate.render() 对比前后两帧的 Screen，只输出变化的部分。

### diffEach：逐 Cell 比较

```typescript
// screen.ts 里的 diffEach
// 利用 damage 区域（只有被写入的区域才可能变化）
// 跳过 damage 区域外的所有 Cell
diffEach(prev.screen, next.screen, (x, y, removed, added) => {
  // removed: 上一帧有，这一帧没有
  // added: 这一帧有，上一帧没有
  // 两者都有：内容变化了
})
```

### 光标移动优化

```typescript
// 不用绝对坐标（CSI row;col H），用相对移动
// 因为不知道光标的起始位置
function moveCursorTo(screen, targetX, targetY) {
  const dx = targetX - prev.x
  const dy = targetY - prev.y

  if (dy !== 0) {
    // 先 CR 回到列 0，再相对移动
    return [[CARRIAGE_RETURN, { type: 'cursorMove', x: targetX, y: dy }], ...]
  }
  // 同行移动
  return [[{ type: 'cursorMove', x: dx, y: 0 }], ...]
}
```

### 跳过不可见的空格

```typescript
// 前景色样式的空格在视觉上和无样式空格一样
// 可以用光标前进代替写入，节省字节
function visibleCellAtIndex(cells, charPool, hyperlinkPool, index, lastRenderedStyleId) {
  if (charId === 0 && (word1 & 0x3fffc) === 0) {
    // 空格，没有背景色/超链接
    const fgStyle = word1 >>> STYLE_SHIFT
    if (fgStyle === 0 || fgStyle === lastRenderedStyleId) {
      return undefined  // 跳过，光标自然前进
    }
  }
}
```

---

## 第四层：DECSTBM 硬件滚动

当 ScrollBox 滚动时，不重绘整个区域，而是用终端的硬件滚动：

```typescript
// 设置滚动区域
setScrollRegion(top + 1, bottom + 1)
// 硬件滚动 N 行
csiScrollUp(delta)  // CSI n S
// 重置滚动区域
RESET_SCROLL_REGION
// 光标回到左上角
CURSOR_HOME
```

然后 `shiftRows(prev.screen, top, bottom, delta)` 模拟这个滚动，让差分算法只需要处理滚入的新行。

---

## 第五层：全量重置的触发条件

有些情况下无法增量更新，必须清屏重绘（会闪烁）：

```typescript
function fullResetSequence_CAUSES_FLICKER(frame, reason, stylePool) {
  // reason 可以是：
  // 'resize'   - 终端宽度变化
  // 'offscreen' - 需要更新的内容已经滚出视口
}
```

触发条件：
1. 终端宽度变化（文字会重新换行）
2. 内容从超出视口高度缩小到视口内（需要显示滚动区的内容）
3. 需要更新的 Cell 已经在终端滚动区之外

---

## 宽字符处理：最复杂的边界情况

CJK 字符和 emoji 占 2 个终端列，需要特殊处理：

```typescript
// Wide 字符存储为两个 Cell：
// Cell[x]:   Wide，存实际字符
// Cell[x+1]: SpacerTail，空字符串

// 渲染时跳过 SpacerTail
if (added.width === CellWidth.SpacerTail) return

// 宽字符在行末时，放 SpacerHead 标记
if (isWideCharacter && offsetX + 2 > screenWidth) {
  setCellAt(screen, offsetX, y, {
    char: ' ',
    width: CellWidth.SpacerHead,
  })
}
```

### Emoji 宽度补偿

某些终端的 wcwidth 表不包含新 emoji，会把 2 列宽的 emoji 当成 1 列：

```typescript
function needsWidthCompensation(char: string): boolean {
  const cp = char.codePointAt(0)
  // Unicode 12.0+ 的新 emoji
  if (cp >= 0x1fa70 && cp <= 0x1faff) return true
  // 文字默认 emoji + VS16（U+FE0F）变成 emoji 呈现
  if (char.length >= 2) {
    for (let i = 0; i < char.length; i++) {
      if (char.charCodeAt(i) === 0xfe0f) return true
    }
  }
  return false
}

// 补偿：写 emoji 后，用 CHA（光标绝对列定位）强制光标到正确位置
if (needsCompensation) {
  diff.push({ type: 'cursorTo', col: px + cellWidth + 1 })
}
```

---

## 搜索功能：离屏渲染

全文搜索需要知道每个字符在屏幕上的精确位置。Claude Code 的做法是**离屏渲染**：

```typescript
// render-to-screen.ts
export function renderToScreen(el: ReactElement, width: number) {
  // 1. 用 React Reconciler 渲染组件到 DOM 树
  reconciler.updateContainerSync(el, container, null, noop)
  reconciler.flushSyncWork()

  // 2. Yoga 计算布局
  root.yogaNode?.calculateLayout(width)
  const height = root.yogaNode?.getComputedHeight()

  // 3. 渲染到独立的 Screen 缓冲区
  const screen = createScreen(width, height, stylePool, charPool, hyperlinkPool)
  renderNodeToOutput(root, output, { prevScreen: undefined })

  // 4. 卸载，但保留 root/container/pools 供下次复用
  reconciler.updateContainerSync(null, container, null, noop)

  return { screen, height }
}

// 在 Screen 里搜索文字
export function scanPositions(screen: Screen, query: string): MatchPosition[] {
  // 逐行扫描，处理宽字符（CJK/emoji 占 2 列）
  // 返回 { row, col, len } 的数组
}
```

每次搜索约 1-3ms（Yoga 布局 + 渲染 + 扫描）。

---

## 关键性能数字

从代码注释和日志里提取的实际数据：

| 指标 | 数值 |
|------|------|
| 每条消息内存（fiber tree） | ~250KB RSS |
| 2000 条消息时的内存 | ~500MB |
| 观测到的最大 RSS | 59GB（GC 死亡螺旋） |
| 200×120 Screen 内存 | 192KB（TypedArray） |
| charCache 上限 | 16384 条 |
| 慢渲染阈值 | 50ms |
| 离屏渲染耗时 | 1-3ms/次 |
| StylePool 转换缓存 | (fromId × 0x100000 + toId) |

---

## 总结：TUI 渲染的核心思路

1. **双缓冲**：front frame（当前显示）和 back frame（下一帧），差分只更新变化的部分
2. **Blit 优化**：未变化的节点直接从上一帧复制，TypedArray.set() 比逐字符写入快 10-100 倍
3. **TypedArray 存储**：Cell 用 2 个 Int32 存储，避免 GC，支持 SIMD 比较
4. **字符串 intern**：所有字符串变成整数 ID，比较和复制都是整数操作
5. **damage 追踪**：只有被写入的区域才需要差分，跳过未变化的大片区域
6. **硬件滚动**：DECSTBM + CSI S/T，滚动时不重绘整个区域
7. **行级缓存**：相同文字行只 tokenize + grapheme cluster 一次
