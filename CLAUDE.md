# Lumi 项目概览

Lumi 是一个基于 LangGraph 的 AI Agent 框架，提供终端 TUI 界面和 HTTP API 两种交互方式。

## 技术栈

- Python 3.12+，包管理用 uv
- LangGraph + LangChain（Agent 编排）
- Textual（TUI 界面）
- FastAPI + Uvicorn（HTTP API）
- pytest + pytest-asyncio（测试）
- ruff（格式化和 lint）

## 目录结构

```
lumi/
  agents/
    base/       # 基础 graph 和 response service
    core/       # 核心节点、消息处理、状态定义
    tools/      # 工具注册、会话、工作区管理
      providers/ # 各工具实现（bash、ask、filesystem 等）
  api/          # FastAPI HTTP 接口
  tui/          # Textual 终端 UI
    widgets/    # UI 组件
  utils/        # 通用工具（llm_chain、logger、config 等）
tests/          # pytest 测试套件
.lumi/prompts/  # Agent 系统提示（SOUL、AGENTS、GUARDRAILS）
```
## 代码风格

Lumi 的代码应当具备可读性、简洁性和高效性。
- 我们倾向于使用简洁明确的函数，每个函数专注于单一任务，并且其输入和输出类型应明确指定。
- 通常更倾向于组合而非继承，因为继承可能导致同一对象承载过多功能。
- 在可能的情况下，我们偏好不可变对象（即对象在初始化后不再发生变化）。代码的可重用性至关重要。
- 我们坚决避免使用可变的全局状态，应确保在同一进程中可以存在多个相互独立的代码实例。
- 架构应采用分层设计：底层基于基本操作和数据结构，当正确组合时，能够提供充分的灵活性；而高层则应提供一个更简单的 API，开箱即用，足以满足大多数使用场景。

项目是基于uv进行管理，添加依赖尽可能不要直接修改pyproject.toml, 而是使用uv add 添加