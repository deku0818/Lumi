import { useCallback, useEffect, useState } from 'react'
import type { Gateway } from '../gateway'
import type { EnvInstallTarget, EnvProgress, EnvStatus, EnvToolStatus } from '../types'
import { MachineScope, useMachines } from './MachineTabs'
import { Section, SectionGroup, Card, ProgressBar } from './SettingsKit'
import { useEnvInstall } from './useEnvInstall'
import { Button } from '@/components/ui/button'

// 环境面板（设置 → 环境）。核心工具链（uv/rg/node）状态 + 一键装齐——纯机器级视图。
// 飞书组件（cli + 技能包）不在此处：技能包按项目装，归渠道体检的本地环境组。
// 探测/安装全在后端 toolbox 模块：系统已有的永不覆盖，安装落 <配置目录>/bin。
// 方案 A 行式列表（.demos/env-toolbox-panel.html）。

const TOOL_META: Record<string, { icon: string; label: string; hint: string }> = {
  uv: { icon: '🐍', label: 'uv', hint: 'Python 运行时与包管理 · agent 的 Python 任务全靠它' },
  rg: { icon: '🔎', label: 'ripgrep', hint: '高速代码搜索 · 缺失时自动降级为内置搜索（较慢）' },
  node: { icon: '⬢', label: 'Node.js', hint: 'JS 运行时（含 npm）· agent 的 JS 任务与 npm 生态' },
}

export function EnvPanel({
  gwFor,
  initialMachine,
}: {
  gwFor: (id: string) => Gateway | undefined
  // 由跳转来源指定（渠道体检在哪台机器上跑就装哪台）；空 = 默认第一台
  initialMachine?: string
}) {
  const machines = useMachines()
  const [machine, setMachine] = useState(initialMachine ?? machines[0]?.id ?? 'local')
  const gw = gwFor(machine)
  const [status, setStatus] = useState<EnvStatus | null>(null)
  const [error, setError] = useState<{ target: string; message: string } | null>(null)

  const { progress, install, seed } = useEnvInstall(gw, {
    onState: (payload) => {
      setStatus({ tools: payload.tools, bin_dir: payload.bin_dir, installing: '' })
      setError(payload.error ?? null)
    },
  })

  const refresh = useCallback(() => {
    gw
      ?.envStatus()
      .then((s) => {
        setStatus(s)
        if (s.installing) seed(s.installing) // 本面板打开前已有安装在跑：恢复进行中态
      })
      .catch(() => setStatus(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gw])

  useEffect(() => {
    setStatus(null)
    setError(null)
    refresh()
  }, [refresh])

  const installing = Object.keys(progress).length > 0
  const onInstall = (target: EnvInstallTarget) => {
    setError(null)
    install(target)
  }

  const toolOf = (name: string) => status?.tools.find((t) => t.name === name)
  const hasMissing = (status?.tools ?? []).some((t) => t.source === 'missing')
  // 进度事件按 target 记：单装看自己，「一键装齐」的 target 是 all，missing 行共用
  const progressOf = (name: string, tool?: EnvToolStatus): EnvProgress | undefined =>
    progress[name] ?? (tool?.source === 'missing' ? progress['all'] : undefined)

  return (
    <SectionGroup>
      <MachineScope value={machine} onChange={setMachine}>

      <Section
        title="核心工具链"
        desc={
          <>
            agent 执行 Python / JS / 代码搜索任务所需，缺失时对应能力受限。系统已有的不重复安装
            {/* 路径由后端下发（各平台真值不同），未拿到前整句略去免得跳字 */}
            {status?.bin_dir ? (
              <>
                ，其余装到 <code className="break-all">{status.bin_dir}</code>
              </>
            ) : (
              '。'
            )}
          </>
        }
        action={
          <Button size="sm" onClick={() => onInstall('all')} disabled={!status || installing || !hasMissing}>
            {installing ? '安装中…' : hasMissing ? '一键装齐' : '已就绪 ✓'}
          </Button>
        }
      >
        <Card className="px-4 py-1">
          {Object.keys(TOOL_META).map((name) => {
            const tool = toolOf(name)
            return (
              <ToolRow
                key={name}
                name={name}
                status={status ? (tool ?? null) : undefined}
                progress={progressOf(name, tool)}
                onInstall={() => onInstall(name as EnvInstallTarget)}
                busy={installing}
              />
            )
          })}
        </Card>
      </Section>

      {error && (
        <p className="text-xs text-error leading-relaxed">
          安装 {error.target} 失败：{error.message}
          <span className="text-muted-foreground">（可重试；如网络受限可设置 https_proxy 后重启）</span>
        </p>
      )}
      </MachineScope>
    </SectionGroup>
  )
}

// —— 状态徽章：missing 虚线 / system 蓝点（用户自装）/ toolbox 金点（Lumi 托管） ——
// status: undefined = 检测中，null = 后端未上报该工具（视同未安装）
function Badge({ status }: { status: EnvToolStatus | null | undefined }) {
  if (status === undefined)
    return <span className="text-[11px] px-2.5 py-0.5 rounded-full border border-separator/60 text-muted-foreground">检测中…</span>
  const version = status?.version ? ` v${status.version}` : ''
  if (status?.source === 'system')
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-0.5 rounded-full border border-separator text-muted-foreground">
        <i className="w-1.5 h-1.5 rounded-full bg-info" />
        系统{version}
      </span>
    )
  if (status?.source === 'toolbox')
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-0.5 rounded-full border border-primary/45 text-ink">
        <i className="w-1.5 h-1.5 rounded-full bg-primary shadow-[0_0_6px_var(--color-accent)]" />
        工具箱{version}
      </span>
    )
  return (
    <span className="text-[11px] px-2.5 py-0.5 rounded-full border border-dashed border-separator text-muted-foreground">
      未安装
    </span>
  )
}

function ToolRow({
  name,
  status,
  progress,
  onInstall,
  busy,
}: {
  name: string
  status: EnvToolStatus | null | undefined
  progress?: EnvProgress
  onInstall: () => void
  busy: boolean
}) {
  const meta = TOOL_META[name]
  return (
    <div className="flex items-center gap-3.5 py-3 border-b border-line/20 last:border-b-0">
      <div className="w-8 h-8 rounded-lg grid place-items-center bg-surface border border-line text-[15px] shrink-0">
        {meta.icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-ink">{meta.label}</div>
        <div className="text-[11.5px] text-muted-foreground truncate">{meta.hint}</div>
      </div>
      {progress ? (
        <ProgressBar progress={progress} className="w-52 justify-end" />
      ) : (
        <div className="flex items-center gap-2.5 shrink-0">
          <Badge status={status} />
          {status?.source === 'missing' && (
            <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
              安装
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
