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

Not using Docker? `uv tool install lumi-harness` gives you the same backend as a plain CLI, with
`lumi update` for upgrades and `lumi status` for a real-handshake health check. See the
[deployment guide](https://github.com/deku0818/Lumi/blob/main/docs/guides/deploy.md) for that path
plus a systemd unit template.

## Configuration

| | |
|---|---|
| Port | `8765` (WebSocket endpoint `/ws`) |
| `LUMI_TOKEN` | Access token. Clients must pass it as `?token=…`. **Always set it.** |
| `/root/.lumi` | All persistent data: credentials (`lumi.json`, 0600), sessions, memory, logs, toolbox |
| `/workspace` | Mount point for host directories the agent should reach. Projects are bound per session by absolute path, so mount as many directories as you need — matching host and container paths keeps registration simple |
| Architectures | `linux/amd64`, `linux/arm64` |

**The image ships no `config.json`** — configuration lives in the data directory you mount. You may
not need one: prompts are layered `style < /root/.lumi < <project>/.lumi`, so a project that carries
its own `.lumi/prompts/SOUL.md` is already set. Otherwise the agent runs with **no system prompt at
all**, because `style` falls back to `default`, which ships none. To give every project a built-in
fallback style, write into your mounted data directory:

```json
{"style": "code"}
```

Session persistence needs no setting — `checkpoint` already defaults to `sqlite`.

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
