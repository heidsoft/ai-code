# PyAgent - AI Agent Framework for AutoOps

基于 Claude Code 架构设计的 Python AI Agent 框架，用于自动化运维场景。

## 特性

- **流式输出**: 基于 AsyncGenerator 的实时响应
- **工具编排**: 灵活的 Tool 接口，支持并发控制
- **运维场景**: 内置告警处理、工单管理、电商运营工具
- **多 LLM 支持**: Anthropic、OpenAI 等

## 安装

```bash
cd pyagent
uv sync
```

## 使用

### 交互模式

```bash
uv run python -m pyagent.cli.main
```

### 单次查询

```bash
uv run python -m pyagent.cli.main "处理告警 A123"
```

### API Key 配置

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run python -m pyagent.cli.main
```

## 内置工具

| 工具 | 功能 |
|------|------|
| `bash` | 执行 shell 命令 |
| `http` | HTTP 请求 |
| `search` | Web 搜索 |
| `alert_handler` | 告警处理 (ack/escalate/reassign) |
| `ticket_handler` | 工单管理 (create/update/close) |
| `ecom_ops` | 电商运营 (库存/订单/用户) |

## 架构

```
┌─────────────────────────────────────────────────┐
│                   QueryEngine                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────┐ │
│  │  Messages   │→ │ LangGraph    │→ │  LLM   │ │
│  └─────────────┘  └──────┬───────┘  └────────┘ │
│                           │                        │
│  ┌────────────────────────┼────────────────────┐│
│  ▼                        ▼                       ││
│  ┌──────────┐     ┌─────────────┐     ┌───────┐│
│  │  Tools   │←────│ToolExecutor │←────│Registry││
│  └──────────┘     └─────────────┘     └───────┘│
└─────────────────────────────────────────────────┘
```

## 开发

```bash
# 运行测试
uv test

# 类型检查
uv run mypy src/pyagent

# 代码格式化
uv run ruff format src/pyagent
```

## 笔记文档

参考 `docs/harness-engineering-notes.md` 了解架构设计细节。
