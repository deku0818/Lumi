# Lumi

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.99-orange.svg)](CHANGELOG.md)
[![LangGraph](https://img.shields.io/badge/LangGraph-powered-purple.svg)](https://langchain-ai.github.io/langgraph/)
[![Electron](https://img.shields.io/badge/Desktop-Electron-2ea6ff.svg)](https://www.electronjs.org/)

[简体中文](README.md) | **English**

**An open-source AI coworker.** Talk to it in a desktop app, or just @ it inside Feishu (Lark) — and actually hand off your daily work to get done.

It's more than chat: it has memory, uses tools, runs scheduled jobs, reads your files, and can automatically send you the notes after a meeting ends. Built for everyone, not just programmers.

<p align="center">
  <img src="docs/images/project-home.png" width="860" alt="Lumi project home">
</p>

---

## What it can do for you

No code required — just tell it what you need:

- 📝 **"Pull together this week's meeting notes and draft me a status report."**
- 📄 **"For this PDF contract, list the key terms and the risk points."**
- 💰 **"Log this taxi receipt into the expense spreadsheet — double-check the amount and date."**
- 📚 **"Read this paper and give me study notes with the key takeaways."**
- ✈️ **"Plan a 7-day Japan trip for next month, day by day."**
- ⏰ **"Every Friday before I leave, remind me to submit my report and summarize the week's progress."**

It remembers your preferences and project context ([persistent memory](docs/guides/memory.md)), can split complex tasks across [sub-agents](docs/guides/agents.md) running in parallel, and can run [on a schedule](docs/guides/cron.md) in the background.

## Use it right inside Feishu

No tool-switching — add Lumi to Feishu (Lark) and it's a coworker in your chats, always on call. DM it, or @ it in a group; send text, images, and files. See the [Feishu setup guide](docs/guides/feishu.md).

### ✨ The meeting ends, and the notes are already there

Turn on "Meeting Minutes" and just **record a voice memo** or **hold a meeting (with cloud recording on)** — Feishu generates the Minutes, and Lumi automatically fetches the **transcript** (with speakers and timestamps), writes it up, and pushes the notes to your DM with the bot. **In practice it lands ~20 seconds after you stop recording — with zero clicks from you.**

> By the time you walk out of the meeting room, the notes are already waiting in Feishu.

### And more

- **Collaborate by @-ing it in groups** — replies only when @-mentioned by default (or set it to answer everything); reads images directly, and auto-downloads and reads files (PDF / code / text).
- **Slash commands** — `/help` `/stop` `/clear` `/compact` and more, plus your project's own skill commands (like `/commit`).
- **Daily memory upkeep** — on schedule, it distills memory and compacts history for sessions with new messages, so long-running group chats don't grow forever.
- **One-stop setup check** — enter your App ID / Secret in the desktop app and it tells you in one click which step failed (credentials, permissions, event subscription, version release), with pre-filled links straight to the Feishu Open Platform.

> 🚧 **More channels coming.** Feishu is Lumi's first IM channel, and the channel layer is designed to be pluggable — Telegram, WeChat, Slack and others are on the roadmap. Which one should come first? Let us know in [Issues](https://github.com/deku0818/Lumi/issues).

## Why Lumi

- **For everyone** — reading notes, travel plans, status reports, expenses, household budgets… no code needed; when you *do* need to code, switch to the `code` style, which comes with a full coding prompt set plus explore/plan sub-agents.
- **Two ways in, one brain** — the desktop app for focused deep work, Feishu for quick hand-offs; same backend, same memory and config.
- **Your data stays on your machine** — open source, [MIT-licensed](LICENSE), runs locally; model keys and conversations live on your own machine. Self-hosted remote deployment is supported too.
- **Extensible** — [skills](docs/guides/slash-commands.md), [sub-agents](docs/guides/agents.md), the [MCP protocol](https://modelcontextprotocol.io/), [scheduled jobs](docs/guides/cron.md), [multi-agent workflows](docs/architecture/workflow.md) — most of it extends by config, no code changes.
- **Model freedom** — OpenAI / Anthropic / Bedrock / any OpenAI-compatible API, with per-model thinking levels, hot-swappable anytime.

<p align="center">
  <img src="docs/images/mcp.png" width="760" alt="Lumi MCP panel">
</p>

> Plug in external tools via MCP (Model Context Protocol): configured per machine and per "global / project" layer, over STDIO or HTTP, toggle and go.

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js (with npm), Python 3.12+ (uv installs it for you).

```bash
git clone https://github.com/deku0818/Lumi.git
cd Lumi
./dev.sh            # one shot: install backend/frontend deps → start vite + Electron + backend sidecar
```

`./dev.sh` runs `uv sync` for the backend and `npm install` for the frontend, and the Electron main process launches the backend for you — no separate startup needed.

**Configure a model**: on first launch, fill in your API key under "Settings → Models"; or pre-seed `~/.lumi/config.json`:

```json
{
  "style": "default",
  "env": {
    "LLM_MODEL_NAME": "gpt-4o",
    "OPENAI_API_KEY": "sk-xxx",
    "OPENAI_API_BASE": "https://api.openai.com/v1"
  }
}
```

Full config: [docs/guides/config.md](docs/guides/config.md); Feishu setup: [docs/guides/feishu.md](docs/guides/feishu.md).

## Core concepts

### Projects and three-layer config

Every chat must be bound to a project (working directory). Each project has its own config set (prompts / memory / schedules / skills / sub-agents), stacked in three layers where later layers override same-named entries:

```
style built-in  <  global layer ~/.lumi/  <  project layer <project>/.lumi/
```

Open a project and you land on its home: the input island on the left starts a chat right inside that project, and the five cards on the right (prompts / memory / schedules / skills / sub-agents) are exactly what the session loads — the project layer is editable. See the [styles guide](docs/guides/styles.md).

### Permissions and approval

> ⚠️ **No sandbox**: Lumi does not isolate anything — the agent's tools (`bash`, file read/write, etc.) **act directly on your real local environment**. Permission rules + approval mode + workspace boundary are the only safety boundary — keep the default approval mode, and use `Privileged` (allow everything) with care.

- **Permission rules** ([permissions.md](docs/guides/permissions.md)): loaded from user-level / project-shared / project-local, evaluated Deny → Allow → Unmatched, plus a workspace boundary check.
- **Approval modes**: `Default` (permission engine decides) · `Accept Edits` (in-workspace edits auto-approved) · `Privileged` (allow all, dangerous ops still blocked) · `Auto` (an AI classifier decides approve / ask / reject).

### Multi-machine / remote

The packaged desktop app is a **multi-machine client**: it connects to the local backend on startup, and you can add remote machines under "Settings → Connections" (`wss://…/ws` + token). Sessions are organized by "machine → project → session".

## Distribution / deployment

Lumi ships as two artifacts: the **backend `lumi`** (`lumi serve`, local or on a server) + the **desktop client** (Electron, connects to local/remote).

```bash
# Backend: install as a global command
uv build && uv tool install dist/lumi-*.whl
lumi serve --port 8765 --token <secret>

# Backend: server (Docker)
docker build -t lumi .
docker run -p 8765:8765 -v ~/.lumi:/root/.lumi -v "$PWD":/workspace lumi --token <secret>

# Desktop client installers (dmg / exe / AppImage)
cd desktop && npm install && npm run dist
```

For public deployments, always terminate TLS (`wss://`) behind Caddy/nginx and set `--token` — never expose plain `ws` directly.

## Built-in tools

| Tool | What it does |
|------|------|
| `read` / `write` / `edit` | Read (with line ranges) / write / precise string-replace editing |
| `glob` / `grep` | File pattern matching / text content search (ripgrep-based, with fallback) |
| `bash` | Run shell commands (persistent session) |
| `ask` | Ask the user a question and wait for the answer |
| `todos` | Task-list management (live progress in the desktop right rail) |
| `cron` | Scheduled jobs (create / delete / pause / run) |
| `skill` | Invoke a custom skill |
| `agent` | Delegate a task to a sub-agent |
| `workflow` | Multi-agent orchestration (a deterministic Python script scheduling a fleet of sub-agents) |
| `background_task` | Manage background-running tasks (bash commands / sub-agents) |
| `artifacts` | Surface produced files as artifacts in the UI to view / preview (incl. in-window Office preview) |
| `vision` | Image understanding (tool mode) |

Tool descriptions live in each tool function's docstring; external tools are wired in via MCP.

## Documentation

| Topic | Link | · | Topic | Link |
|------|------|---|------|------|
| Full config | [config.md](docs/guides/config.md) | · | Feishu channel | [feishu.md](docs/guides/feishu.md) |
| Permissions | [permissions.md](docs/guides/permissions.md) | · | Scheduled jobs | [cron.md](docs/guides/cron.md) |
| Sub-agents | [agents.md](docs/guides/agents.md) | · | Slash commands / skills | [slash-commands.md](docs/guides/slash-commands.md) |
| Styles | [styles.md](docs/guides/styles.md) | · | Persistent memory | [memory.md](docs/guides/memory.md) |
| Desktop architecture | [desktop.md](docs/architecture/desktop.md) | · | Multi-agent workflow | [workflow.md](docs/architecture/workflow.md) |
| Thinking management | [thinking.md](docs/architecture/thinking.md) | · | Conversation summary | [summary.md](docs/architecture/summary.md) |

> Guide docs are currently written in Chinese.

## Development

```bash
uv sync --all              # install dev dependencies
uv run pytest              # run tests
uv run ruff format .       # format code
uv run ruff check --fix .  # lint
```

## Tech stack

- [LangGraph](https://langchain-ai.github.io/langgraph/) + [LangChain](https://langchain.com/) — agent orchestration
- [FastAPI](https://fastapi.tiangolo.com/) — WebSocket / HTTP service
- [Electron](https://www.electronjs.org/) + React + TypeScript — desktop frontend
- [lark-oapi](https://github.com/larksuite/oapi-sdk-python) — Feishu long connection
- [APScheduler](https://apscheduler.readthedocs.io/) — job scheduling
- [MCP](https://modelcontextprotocol.io/) — Model Context Protocol integration
