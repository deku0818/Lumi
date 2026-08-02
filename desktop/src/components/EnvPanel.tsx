import { useCallback, useState } from 'react'
import { Hexagon, Presentation, Search, Zap, type LucideIcon } from 'lucide-react'
import type { Gateway } from '../gateway'
import type { EnvInstallTarget, EnvProgress, EnvStatus, EnvToolStatus } from '../types'
import { MachineScope, useConnectedEffect, useMachines } from './MachineTabs'
import { Card, Pill, ProgressBar, Section, SectionGroup } from './SettingsKit'
import { useEnvInstall } from './useEnvInstall'
import { Button } from '@/components/ui/button'

// 环境面板（设置 → 环境）。核心工具链 + 可选增强两栏，一键装齐覆盖全部——纯机器级视图。
// 飞书组件（cli + 技能包）不在此处：技能包按项目装，归渠道体检的本地环境组。
// 探测/安装全在后端 toolbox 模块：系统已有的永不覆盖，安装落 <配置目录>/bin。
// 方案 A 行式列表（.demos/env-toolbox-panel.html）；图标统一 lucide 线性（emoji 退场）。

const TOOL_META: Record<string, { icon: LucideIcon; label: string; hint: string }> = {
  uv: { icon: Zap, label: 'uv', hint: 'Python 运行时与包管理 · agent 的 Python 任务全靠它' },
  rg: { icon: Search, label: 'ripgrep', hint: '高速代码搜索 · 缺失时自动降级为内置搜索（较慢）' },
  node: { icon: Hexagon, label: 'Node.js', hint: 'JS 运行时（含 npm）· agent 的 JS 任务与 npm 生态' },
  officecli: {
    icon: Presentation,
    label: 'OfficeCLI',
    hint: 'Office 文档引擎 · Word/Excel/PPT 窗口内预览，缺失时用系统应用打开',
  },
}

// 分栏按用户视角的「缺失后果」划分：核心缺了对应能力残缺（Python/JS 任务跑不了）；
// 可选缺了自动降级（rg→内置搜索、officecli→系统应用打开），不装也能用。
// 分组是纯展示概念、本组件独家持有——后端只有 ALL_TOOLS 单枚举，装齐覆盖两栏全部缺失项
const CORE_ROWS = ['uv', 'node'] as const
const OPTIONAL_ROWS = ['rg', 'officecli'] as const

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
    // RPC 层被拒（旧版后端不认识该 target 等）也走同一条错误横幅，不再静默归零
    onError: (target, message) => setError({ target, message }),
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

  useConnectedEffect(
    machine,
    () => {
      setStatus(null)
      setError(null)
      refresh()
    },
    [refresh],
  )

  const installing = Object.keys(progress).length > 0
  const onInstall = (target: EnvInstallTarget) => {
    setError(null)
    install(target)
  }

  const toolOf = (name: string) => status?.tools.find((t) => t.name === name)
  // 未上报的行（旧版后端的 status_all 还不认识该工具）视同缺失：装齐按钮不误报
  // 「已就绪」，行内也给安装入口。全量集合即 TOOL_META 的键（两栏并集）
  const hasMissing =
    !!status &&
    Object.keys(TOOL_META).some((n) => {
      const t = toolOf(n)
      return !t || t.source === 'missing'
    })
  // 进度事件按工具名下发（装齐时后端逐个装、逐个报），各行只看自己；
  // 'all' 键只来自「一键装齐」的乐观种子/恢复态，充当还没轮到的 missing 行的排队显示
  const progressOf = (name: string, tool?: EnvToolStatus): EnvProgress | undefined =>
    progress[name] ?? (tool?.source === 'missing' ? progress['all'] : undefined)

  // 两栏共用的行渲染（唯一差异是行名数组），改 ToolRow 参数只动这一处
  const toolRows = (names: readonly string[]) => (
    <Card className="px-4 py-1">
      {names.map((name) => {
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
  )

  return (
    <SectionGroup>
      <MachineScope value={machine} onChange={setMachine}>

      <Section
        title="核心工具链"
        desc={
          <>
            agent 执行 Python / JS 任务所需，缺失时对应能力受限。系统已有的不重复安装
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
        {toolRows(CORE_ROWS)}
      </Section>

      <Section
        title="可选增强"
        desc="不装也能用，装上体验更好：ripgrep 提速内容搜索，OfficeCLI 让 Word / Excel / PPT 直接在窗口内预览。一键装齐会一并装上。"
      >
        {toolRows(OPTIONAL_ROWS)}
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

// —— 状态徽章（统一 Pill 族）：missing 虚线 / system 蓝点（用户自装）/ toolbox 金点（Lumi 托管） ——
// status: undefined = 检测中，null = 后端未上报该工具（视同未安装）
function Badge({ status }: { status: EnvToolStatus | null | undefined }) {
  if (status === undefined) return <Pill>检测中…</Pill>
  const version = status?.version ? ` v${status.version}` : ''
  if (status?.source === 'system') return <Pill dot="info">系统{version}</Pill>
  if (status?.source === 'toolbox') return <Pill dot="gold">工具箱{version}</Pill>
  return <Pill dashed>未安装</Pill>
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
      <div className="w-8 h-8 rounded-lg grid place-items-center bg-surface border border-line text-ink shrink-0">
        <meta.icon size={15} />
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
          {/* null = 后端未上报（旧版后端），徽章同显「未安装」，安装入口不能缺 */}
          {(status === null || status?.source === 'missing') && (
            <Button size="sm" variant="outline" onClick={onInstall} disabled={busy}>
              安装
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
