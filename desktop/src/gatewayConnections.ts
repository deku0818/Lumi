import type { Dispatch, SetStateAction } from 'react'
import { Gateway } from './gateway.ts'
import { sessionKey } from './lib/utils.ts'
import { emptySession, hasStreaming, hydrateHistory, resumeAfterReconnect, type SessionState } from './sessionReducer.ts'
import type { BgTask, ConnState, GatewayConnection, GatewayOptions, HistorySnapshot, SessionModelWire, WireEvent } from './types.ts'

type Ref<T> = { current: T }

export function getControlConnection(
  backend: string,
  connections: Record<string, Gateway>,
  openings: Record<string, Promise<Gateway> | undefined>,
  create: () => Promise<Gateway>,
  reconnect: (backend: string) => Promise<void>,
): Promise<Gateway> {
  const pending = openings[backend]
  if (pending) return pending
  const existing = connections[backend]
  if (existing) {
    if (existing.dead) return reconnect(backend).then(() => existing)
    return Promise.resolve(existing)
  }
  const opening = create()
  openings[backend] = opening
  void opening.finally(() => { delete openings[backend] }).catch(() => { })
  return opening
}

export type ChatConnectionContext = {
  connsRef: Ref<Record<string, Gateway>>
  activeRef: Ref<string>
  setStore: Dispatch<SetStateAction<Record<string, SessionState>>>
  getSession?: (key: string) => SessionState | undefined
  setConn: (state: ConnState) => void
  setWorkspaceDir: (path: string) => void
  applySessionModel: (key: string, model: SessionModelWire) => void
  handleEvent: (event: WireEvent, backend: string) => void
  onTasks: (backend: string, tasks: BgTask[]) => void
  createGateway?: (connection: GatewayConnection, options: GatewayOptions) => Gateway
}

/** One chat connection owns its thread identity, reconnect recovery and history reconciliation. */
export function openChatConnection(targetThread: string | null, targetWorkspace: string, backendId: string, context: ChatConnectionContext): Promise<string> {
  const { connsRef, activeRef, setStore, setConn, setWorkspaceDir, applySessionModel, handleEvent, onTasks } = context
  return new Promise<string>((resolve, reject) => {
    void (async () => {
      const connection = await window.lumi.getConnection(backendId)
      // open 握手携带 workspace：后端据此把本会话引擎直接 pin 到该项目（项目随会话
      // 绑定，不动进程 cwd），省掉 ready 后再 switch 改项目的来回。重连复用同一 URL，
      // 故新 bridge 也会被重新 pin。
      const options = { workspace: targetWorkspace, thread: targetThread || '' }
      const gw = context.createGateway ? context.createGateway(connection, options) : new Gateway(connection, options)
      // 本连接所属会话的复合 key（backend+thread）——store/connsRef 皆以此为键；
      // 发给后端的 wire 仍用裸 thread（myThread）。
      const targetKey = targetThread ? sessionKey(backendId, targetThread) : ''
      // 创建即登记（不等 ready）：快速连点同一会话时 activate 的复用分支能立即命中，
      // 不会开出重复连接；boot 清理也能收殓在途连接。死连接由 activate 驱逐后重建。
      if (targetKey) connsRef.current[targetKey] = gw
      let myThread = ''
      let myKey = ''
      // 本连接所属项目；重连得到新 bridge 后据此切回原 thread。
      let myWorkspace = ''
      let ready = false
      // 本地视图是否已与后端历史对齐：初次加载被瞬断打断、重连（断开期事件已丢）、
      // 快照取自轮次进行中时保持 false，重连 ready / 轮次收尾时补拉
      let loaded = false
      // 补拉在途标记：防抖动连接下多个 ready 并发重复 loadHistory
      let loadingHistory = false
      // 历史快照统一落位（初次加载 / 补拉共用）。仅当快照被真正应用（无流式在途）且
      // 后端空闲（轮次进行中的快照缺在途调用的输出）时才算对齐——否则置 loaded 会把
      // 缺失内容永久关在补拉门外
      function applySnapshot(
        key: string,
        snapshot: HistorySnapshot,
        patch: Partial<SessionState> = {},
        revision?: number,
      ): void {
        setStore((s) => {
          const cur = s[key]
          if (!cur) return s
          if (revision !== undefined && (cur.historyRevision ?? 0) !== revision) return s
          const next = { ...hydrateHistory(cur, snapshot), ...patch }
          if (!hasStreaming(next) && !next.running) loaded = true
          return { ...s, [key]: next }
        })
      }
      function backfillHistory(): void {
        if (loaded || loadingHistory || !myThread) return
        loadingHistory = true
        const revision = context.getSession?.(myKey)?.historyRevision ?? 0
        void gw
          .loadHistory(myThread)
          .then((r) => applySnapshot(myKey, r, {}, context.getSession ? revision : undefined))
          .catch(() => { })
          .finally(() => {
            loadingHistory = false
          })
      }
      const offEvent = gw.onEvent((ev) => {
        if (ev.type === 'gateway.ready') {
          // workspace_bound=false 时 payload.workspace 只是进程 cwd 兜底展示值，不是真
          // 绑定的项目——写进 workspaceDir 会污染侧栏项目分组等展示，未绑定就不写。
          // workspaceDir = 活动会话的项目：重连时后台会话的 ready 不得覆盖它
          if (ev.payload.workspace_bound && ev.payload.workspace && myKey === activeRef.current) {
            setWorkspaceDir(ev.payload.workspace)
          }
          if (ready) {
            // 重连：服务端给的是全新 bridge（新 session_id），切回本连接原 thread
            // 恢复后端绑定，否则会丢弃原会话、并多出一个幽灵空会话。
            if (myThread) {
              // 重连拿到的是全新 bridge：切回原 thread 时后端会按会话解析模型，
              // 用返回值刷新显示（新 bridge 的 ready 报的是默认，不是本会话的）
              void gw
                .switchSession(myThread, myWorkspace)
                .then((r) => applySessionModel(myKey, r))
                .catch(() => { })
              // 复位运行态：断连时 sendMessage 的 catch 已 resetRunning(false)，续接后
              // 据后端实际运行态恢复 running（否则挂起轮被当空闲，stop 隐藏/输入栏启用）；
              // 后端已空闲则收掉断开前残留的流式气泡 / 运行中工具卡。
              setStore((s) =>
                s[myKey]
                  ? {
                    ...s,
                    [myKey]: resumeAfterReconnect(
                      s[myKey],
                      !!ev.payload.running,
                      ev.payload.run_started_at ?? undefined,
                    ),
                  }
                  : s,
              )
              // 断开期间的事件已丢：以历史快照对账补回 gap 期消息（真正对齐才置
              // loaded，失败或轮次仍在跑时留给下次重连 / 轮次收尾）
              loaded = false
              backfillHistory()
            }
            return
          }
          ready = true
          // 初始拉取后台任务快照（之后变更经 bg_tasks.update 推送）；按本机分段替换
          void gw
            .listBgTasks()
            .then((r) => onTasks(backendId, r.tasks))
            .catch(() => { })
          if (targetThread) {
            // 已有会话：切到该 thread 并加载历史。switchSession 可能因项目目录已被删/改名
            // 而被后端拒（set_workspace 抛错）；必须吞掉，否则 Promise 永不 resolve、会话卡死。
            void (async () => {
              // 先占位 store：使续接重发的审批卡有处可落（否则在 loadHistory
              // 完成前到达会被事件处理器的 `if (!s) return` 丢弃）。
              setStore((s) => ({ ...s, [targetKey]: s[targetKey] ?? emptySession() }))
              // 身份先行：下方 await 可能因瞬断 reject（sidecar 重启窗口），若身份
              // 未落，重连分支认不回本会话、resolve 永不调用，会话将永久空白。
              myThread = targetThread
              myKey = targetKey
              myWorkspace = targetWorkspace
              gw.bindThread(targetThread)
              try {
                // 结果带该会话生效的模型（ready 报的是默认，不是本会话的）
                applySessionModel(targetKey, await gw.switchSession(targetThread, targetWorkspace))
              } catch {
                /* 目录失效等：仍打开会话（后端已降级，不切 cwd） */
              }
              try {
                const revision = context.getSession?.(targetKey)?.historyRevision ?? 0
                const r = await gw.loadHistory(targetThread)
                // 水合历史（保留续接期间已落入的审批/澄清队列与流式在途内容）；
                // running 据后端运行态恢复（续接的挂起轮要显示运行态，否则被当空闲）。
                applySnapshot(
                  targetKey,
                  r,
                  {
                    running: !!ev.payload.running,
                    runStart: ev.payload.run_started_at ?? undefined,
                  },
                  context.getSession ? revision : undefined,
                )
              } catch {
                /* 瞬断等：历史未加载（loaded=false），重连 ready 分支补拉 */
              }
              // UI 已乐观切到本会话，连接问题由指示灯呈现；无论成败都 resolve，
              // 不让 activate 悬置
              resolve(targetKey)
            })().catch(reject)
          } else {
            // 新会话：用握手分配的 thread；记录其所在项目（open 已 pin，握手下发的
            // workspace 即本会话项目），供重连时切回原 thread。
            myThread = ev.session_id ?? ''
            myKey = sessionKey(backendId, myThread)
            myWorkspace = ev.payload.workspace || ''
            loaded = true // 新会话无历史可拉（重连后照常对账）
            gw.bindThread(myThread) // 重连携带 ?thread= → 后端断连续接
            connsRef.current[myKey] = gw
            setStore((s) => ({ ...s, [myKey]: emptySession() }))
            resolve(myKey)
          }
        } else {
          handleEvent(ev, backendId)
          // 补拉曾因流式在途被丢弃（loaded 仍 false）：轮次收尾后流式气泡已收，
          // 快照可安全整表替换，此时重试把掉线前的历史找回来
          if (ev.type === 'turn.complete') loaded = false
          if ((ev.type === 'turn.complete' || ev.type === 'error') && !loaded) {
            backfillHistory()
          }
        }
      })
      const offState = gw.onState((st) => {
        const key = myKey || targetKey
        if (key && key === activeRef.current) setConn(st)
      })
      gw.connect()
      void gw.whenReady().catch(error => {
        if (ready) return
        offEvent()
        offState()
        gw.close()
        if (targetKey && connsRef.current[targetKey] === gw) delete connsRef.current[targetKey]
        if (targetKey && targetKey === activeRef.current) setConn('failed')
        reject(error)
      })
    })().catch(reject)
  })
}
