import { useCallback, useMemo } from 'react'
import { AlignLeft, ChevronDown, Link2, Tag } from 'lucide-react'
import { useI18n } from '../i18n'
import { formatNoteValue, noteLinkHref, parseNote, splitNoteLinks } from '../lib/noteMarkdown'
import { Markdown, MarkdownLink } from './Markdown'
import { toast } from './Toast'

function PropertyValue({ value, onNoteLink }: { value: unknown; onNoteLink: (target: string) => void }) {
  if (value === null) return <span className="note-empty">—</span>
  if (Array.isArray(value)) return <span className="note-tags">{value.map((item, index) => <span className="note-tag" key={index}>{formatNoteValue(item)}</span>)}</span>
  if (typeof value !== 'string') return <span>{formatNoteValue(value)}</span>
  return <>{splitNoteLinks(value).map((part, index) => typeof part === 'string' ? part :
    <MarkdownLink key={index} href={noteLinkHref(part.target)} onNoteLink={onNoteLink}>{part.label}</MarkdownLink>)}</>
}

export function NotePreview({ text }: { text: string }) {
  const { t } = useI18n()
  const note = useMemo(() => parseNote(text), [text])
  const showNoteTarget = useCallback((target: string) => toast.info(t('files.noteLinkTarget', { target })), [t])
  const entries = Object.entries(note.properties)
  return <article className="note-preview">
    {entries.length > 0 && <details className="note-properties" open>
      <summary>{t('files.noteProperties')}<ChevronDown size={13} /></summary>
      <dl>{entries.map(([key, value]) => <div className="note-property" key={key}>
        <dt>{key === 'tags' ? <Tag size={13} /> : key === 'source' ? <Link2 size={13} /> : <AlignLeft size={13} />}<span>{key}</span></dt>
        <dd><PropertyValue value={value} onNoteLink={showNoteTarget} /></dd>
      </div>)}</dl>
    </details>}
    <div className="md"><Markdown onNoteLink={showNoteTarget}>{note.body}</Markdown></div>
  </article>
}
