import type { SshStatus } from '../types'

export function sshBusy(status: SshStatus | undefined): boolean {
  return !!status && !['ready', 'offline', 'error'].includes(status.phase)
}

// Subscribe before reading the snapshot; a late snapshot must not overwrite live progress.
export function watchSshStatuses(
  api: Pick<Window['lumi']['ssh'], 'statuses' | 'onStatus'>,
  onStatus: (status: SshStatus) => void,
): () => void {
  let active = true
  const changed = new Set<string>()
  const unsubscribe = api.onStatus(status => {
    changed.add(status.id)
    onStatus(status)
  })
  void api.statuses().then(statuses => {
    if (active) for (const status of statuses) {
      if (!changed.has(status.id)) onStatus(status)
    }
  }).catch(() => {}) // Live events still work if the initial snapshot is unavailable.
  return () => { active = false; unsubscribe() }
}
