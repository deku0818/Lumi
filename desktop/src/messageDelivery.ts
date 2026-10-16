import type { Gateway } from './gateway.ts'

export type Attachment =
  | { id: number; kind: 'image'; dataUrl: string; name: string; blob: File }
  | { id: number; kind: 'file'; path: string; name: string; blob: File }

export type MessageDraft = { text: string; attachments: Attachment[] }
type DeliveryOptions = { remote: boolean; toolMode?: string; messageId?: string }

/** Upload remote attachments before sending paths; local images keep their inline payload. */
export async function deliverDraft(gateway: Gateway, draft: MessageDraft, options: DeliveryOptions): Promise<void> {
  const { text, attachments } = draft
  let content: string | unknown[] = text
  if (!options.remote) {
    const images = attachments.filter(attachment => attachment.kind === 'image')
    if (images.length) {
      const blocks: unknown[] = text ? [{ type: 'text', text }] : []
      for (const image of images) {
        const match = /^data:([^;]+);base64,(.*)$/s.exec(image.dataUrl)
        if (match) blocks.push({ type: 'image', source: { type: 'base64', media_type: match[1], data: match[2] } })
      }
      content = blocks
    }
  }
  const files = options.remote
    ? attachments.map(attachment => ({ path: attachment.kind === 'file' ? attachment.path : '', blob: attachment.blob }))
    : attachments.filter(attachment => attachment.kind === 'file')
  const paths = await gateway.resolveAttachments(files, options.remote)
  await gateway.sendMessage(content, options.toolMode, paths, options.messageId)
}

export function dispatchDraft(gateway: Gateway, draft: MessageDraft, options: DeliveryOptions, onFailure: (error: unknown) => void): void {
  void deliverDraft(gateway, draft, options).catch(onFailure)
}
