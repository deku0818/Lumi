import type { BackendRemote, SshStatus } from '../types.ts'

export type SshFormFields = {
  host: string
  name: string
  user: string
  port: string
  identityFile: string
  dataDir: string
  auth: NonNullable<BackendRemote['authMethod']>
}

export type SshFormCredentials = { privateKey: string; passphrase: string; keepKey: boolean }
export type SshConnectionProcess = {
  error: string
  busyId: string
  busy: boolean
  progress: boolean
  status: SshStatus | null
}

export function initialSshFields(initial?: BackendRemote): SshFormFields {
  return {
    host: initial?.host ?? '',
    name: initial?.name ?? '',
    user: initial?.user ?? '',
    port: initial?.port?.toString() ?? '',
    identityFile: initial?.identityFile ?? '',
    dataDir: initial?.dataDir ?? '',
    auth: initial?.authMethod || (initial?.identityFile ? 'file' : 'config'),
  }
}
