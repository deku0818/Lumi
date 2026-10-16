import type { GatewayOptions } from './types.ts'

/** WebSocket-shaped renderer adapter; all remote bytes use Electron IPC and SSH. */
export class SshSocket {
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: ((error: unknown) => void) | null = null
  private channel = crypto.randomUUID()
  private unsubscribe: () => void

  constructor(backend: string, options: GatewayOptions) {
    this.unsubscribe = window.lumi.remote.onEvent(event => {
      if (event.channel !== this.channel || this.readyState === 3) return
      if (event.type === 'close') {
        this.finish()
        return
      }
      this.opened()
      this.onmessage?.({ data: JSON.stringify(event.frame) })
    })
    queueMicrotask(() => {
      if (this.readyState === 3) return
      void window.lumi.remote.open(backend, this.channel, options).then(() => {
        if (this.readyState !== 3) this.opened()
      }).catch(error => {
        this.onerror?.(error)
        this.finish()
      })
    })
  }
  private opened(): void {
    if (this.readyState !== 0) return
    this.readyState = 1
    this.onopen?.()
  }
  private finish(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.unsubscribe()
    // Detach the server session before Gateway opens a replacement channel.
    void window.lumi.remote.close(this.channel)
      .catch(() => {})
      .finally(() => this.onclose?.({ code: 1006 }))
  }
  send(data: string): void {
    if (this.readyState !== 1) throw Error('SSH channel is not connected')
    void window.lumi.remote.send(this.channel, JSON.parse(data)).catch(error => {
      this.onerror?.(error)
      this.finish()
    })
  }
  close(): void {
    this.finish()
  }
}
