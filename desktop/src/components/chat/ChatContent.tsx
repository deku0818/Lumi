import type { ReactNode } from 'react'
import type { Artifact, AttachedFile } from '../../types'
import type { Segment } from '../../lib/chatSegments'
import { FileCards } from '../Artifacts'
import { AgentGroup } from './AgentViews'
import { ItemView } from './ItemView'
import { ToolGroup } from './ToolViews'

// 主会话与子代理共用排版和内容分派，操作权限由外层决定。
export function ChatContent({ children }: { children: ReactNode }) {
  return <div className="chat-body max-w-3xl mx-auto w-full px-6 py-8 space-y-5">{children}</div>
}

export function ChatSegment({ segment, remote, onOpenFile, activePath, fileSrc, onOpenImage }: {
  segment: Segment
  remote: boolean
  onOpenFile: (file: Artifact) => void
  activePath?: string
  fileSrc?: (path: string) => string | undefined
  onOpenImage?: (file: AttachedFile) => void
}) {
  switch (segment.kind) {
    case 'tools': return <ToolGroup tools={segment.tools} />
    case 'agent': return <AgentGroup items={segment.items} />
    case 'files': return <FileCards files={segment.files} onOpen={onOpenFile} activePath={activePath} remote={remote} />
    case 'item': return <ItemView item={segment.item} fileSrc={fileSrc} onOpenImage={onOpenImage} />
  }
}
