import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nProvider } from '../../src/i18n'
import { Markdown } from '../../src/components/Markdown'
import { NotePreview } from '../../src/components/NotePreview'

export function renderMarkdown(text: string, preserveLineBreaks = false, noteLinks = false): string {
  return renderToStaticMarkup(createElement(I18nProvider, {
    children: createElement(Markdown, { children: text, preserveLineBreaks, onNoteLink: noteLinks ? () => {} : undefined }),
  }))
}

export function renderNote(text: string): string {
  return renderToStaticMarkup(createElement(I18nProvider, { children: createElement(NotePreview, { text }) }))
}
