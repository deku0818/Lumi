import { useState } from 'react'
import type { CronActiveRun, CronRun } from '../types'
import { useConnectedEffect } from '../components/MachineTabs'

export type CronRunEntry = CronActiveRun | CronRun
export interface CronRunsApi {
  listCronRuns(jobId: string, limit?: number): Promise<{ runs: CronRun[] }>
}

// 完成记录优先于稍晚移除的实时快照；历史未返回时也立即显示正在执行的条目。
export function mergeCronRuns(runs: CronRun[] | null, live: CronActiveRun[]): CronRunEntry[] | null {
  const done = new Set((runs ?? []).map(r => r.thread_id))
  const active = live.filter(r => r.thread_id && !done.has(r.thread_id))
  return runs === null && active.length === 0 ? null : [...active, ...(runs ?? [])]
}

// 输入为整台机器的快照，只在这里按任务筛选。收起右栏时暂停请求，重连后补拉历史。
export function useCronRuns(
  api: () => CronRunsApi | undefined,
  machine: string,
  jobId: string,
  version: number,
  machineRuns: CronActiveRun[] = [],
  enabled = true,
): CronRunEntry[] | null {
  const [history, setHistory] = useState<{ machine: string; jobId: string; runs: CronRun[] } | null>(null)
  useConnectedEffect(machine, () => {
    if (!enabled) return
    let current = true
    const apply = (runs: CronRun[]) => { if (current) setHistory({ machine, jobId, runs }) }
    api()?.listCronRuns(jobId, 50).then(r => apply(r.runs ?? [])).catch(() => apply([]))
    return () => { current = false }
  }, [api, machine, jobId, version, enabled])
  const runs = history?.machine === machine && history.jobId === jobId ? history.runs : null
  return mergeCronRuns(runs, machineRuns.filter(r => r.job_id === jobId))
}
