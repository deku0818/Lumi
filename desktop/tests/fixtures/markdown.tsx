import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nProvider } from '../../src/i18n'
import { Markdown } from '../../src/components/Markdown'

export function renderMarkdown(text: string, preserveLineBreaks = false): string {
  return renderToStaticMarkup(createElement(I18nProvider, {
    children: createElement(Markdown, { children: text, preserveLineBreaks }),
  }))
}
