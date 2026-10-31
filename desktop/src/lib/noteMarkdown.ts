import { CORE_SCHEMA, dump, load } from 'js-yaml'

export const NOTE_LINK_PREFIX = '#lumi-note/'
export const noteLinkHref = (target: string): string => NOTE_LINK_PREFIX + encodeURIComponent(target)

export function parseNote(text: string): { properties: Record<string, unknown>; body: string } {
  const match = /^\uFEFF?---[ \t]*\r?\n([\s\S]*?)^---[ \t]*(?:\r?\n|$)/m.exec(text)
  if (match?.index === 0) {
    try {
      const properties: unknown = load(match[1], { schema: CORE_SCHEMA }) ?? {}
      if (typeof properties === 'object' && !Array.isArray(properties)) {
        return { properties: properties as Record<string, unknown>, body: text.slice(match[0].length) }
      }
    } catch { /* Invalid properties remain part of the original document. */ }
  }
  return { properties: {}, body: text }
}

type NoteLink = { target: string; label: string }
// Property values are plain text; body links use the Markdown syntax extension.
export function splitNoteLinks(text: string): (string | NoteLink)[] {
  const parts: (string | NoteLink)[] = []
  let offset = 0
  for (const match of text.matchAll(/\[\[([^\]\n]+)\]\]/g)) {
    const prefix = text.slice(0, match.index)
    if (prefix.endsWith('!') || (prefix.match(/\\+$/)?.[0].length ?? 0) % 2) continue
    const [target, label = target] = match[1].split('|', 2)
    if (!target.trim()) continue
    if (match.index > offset) parts.push(text.slice(offset, match.index))
    parts.push({ target: target.trim(), label: label.trim() })
    offset = match.index + match[0].length
  }
  if (offset < text.length) parts.push(text.slice(offset))
  return parts
}

// YAML aliases may form cycles; dump preserves references without recursively expanding them.
export function formatNoteValue(value: unknown): string {
  return value !== null && typeof value === 'object'
    ? dump(value, { schema: CORE_SCHEMA, flowLevel: 0, lineWidth: -1 }).trim()
    : String(value)
}
