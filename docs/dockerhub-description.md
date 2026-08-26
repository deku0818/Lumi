# Lumi backend

[Lumi](https://github.com/deku0818/Lumi) is an open-source AI agent framework built on LangGraph,
with a desktop client (Electron) and an HTTP/WebSocket API. This image is the **backend**: it runs
`lumi serve`, and the Lumi desktop app connects to it — on this machine or over the network.

Source: **https://github.com/deku0818/Lumi** · License: MIT · 中文文档见仓库 [README](https://github.com/deku0818/Lumi/blob/main/README.md)

## Quick start

```bash
docker run -d --name lumi --restart unless-stopped \
  -p 8765:8765 \
  -e LUMI_TOKEN=<your-secret> \
  -v /opt/lumi/data:/root/.lumi \
  -v /opt/lumi/workspace:/workspace \
  ycw0818/lumi-harness
```

Then point the desktop app at `ws://<host>:8765/ws?token=<your-secret>` (Settings → Connections).
Model API keys are configured from the desktop app — nothing to edit on the server.

Prefer one command? The repo ships a deployment script that creates the data directory, generates a
token, installs the agent toolchain, and verifies the deployment with a real handshake:

```bash
sudo ./scripts/install.sh
```

See the [deployment guide](https://github.com/deku0818/Lumi/blob/main/docs/guides/deploy.md).

## Configuration

| | |
|---|---|
| Port | `8765` (WebSocket endpoint `/ws`) |
| `LUMI_TOKEN` | Access token. Clients must pass it as `?token=…`. **Always set it.** |
| `/root/.lumi` | All persistent data: credentials (`lumi.json`, 0600), sessions, memory, logs, toolbox |
| `/workspace` | Mount point for host directories the agent should reach. Projects are bound per session by absolute path, so mount as many directories as you need — matching host and container paths keeps registration simple |
| Architectures | `linux/amd64`, `linux/arm64` |

**When you mount `/root/.lumi`, write a `config.json` into it on first run** — the one baked into the
image is hidden by your mount, and the defaults it overrides matter:

```json
{"style": "code", "agents": {"checkpoint": "sqlite"}}
```

Without it the agent runs with no system prompt (`style: default` ships none) and sessions are kept in
memory only, so they vanish when the container stops.

## Tags

- `latest` — most recent release
- `0.2`, `0.2.119` — pinned major.minor / exact version
- `edge` — built manually from `main`; not a release

## Security

Lumi has **no sandbox**: the agent's `bash` and file tools act directly on this container and on
whatever you mount into it. Two consequences:

1. Always set `LUMI_TOKEN`. A tokenless server accepts anyone who can reach the port.
2. Never expose plain `ws://` to the internet. Terminate TLS in front of it, e.g. with Caddy:

   ```caddyfile
   lumi.example.com {
       reverse_proxy 127.0.0.1:8765
   }
   ```

   Then connect to `wss://lumi.example.com/ws?token=…` and close port 8765 at the firewall.

Mount only the directories you actually want the agent to work in.
